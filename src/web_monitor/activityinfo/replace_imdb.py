"""One-time production maintenance: replace the two-error IMDb seed with Planet Python.

Run explicitly with --apply; never imported by startup or request handling. The
operator must exclude concurrent manual writers. Two complete preflight reads
and a midnight Scheduler guard reduce races; ActivityInfo writes are not atomic.
On any failure after writes begin, inspect the recorded IDs; do not blindly rerun.
"""

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess

from web_monitor.activityinfo.bootstrap import DEMO_SITES
from web_monitor.activityinfo.client import ActivityInfoClient, ActivityInfoError, resource_id
from web_monitor.activityinfo.config import ActivityInfoConfig
from web_monitor.activityinfo.persistence import ActivityInfoPersistence, MonitoredSiteRecord
from web_monitor.activityinfo.schema import stable_id
from web_monitor.crawler import crawl_site
from web_monitor.models import MonitoredSite

PRODUCTION_DATABASE_ID = 'cfx7vfgmuttn7403'
OLD_SITE = ('IMDb Top 250', 'https://www.imdb.com/chart/top/')
NEW_SITE = ('Planet Python', 'https://planetpython.org/')
READERS = {
    'monitored_site': 'read_monitored_site', 'crawl': 'read_crawl',
    'snapshot': 'read_snapshot', 'snapshot_item': 'read_snapshot_item',
}


class MaintenanceError(ValueError):
    """An exact maintenance precondition failed; messages contain no credentials."""


def require(condition, message):
    if not condition:
        raise MaintenanceError(message)


def seed_record(database_id, definition):
    name, url = definition
    site = MonitoredSite(url)
    return MonitoredSiteRecord(stable_id(database_id, 'seed', site.root_url), name, site)


def read_state(store):
    """Read every application record, including unreferenced child records."""
    state = {}
    for form, reader in READERS.items():
        rows = store.client.query_rows(store.ids[form], {'record_id': '_id'})
        ids = [resource_id(row.get('record_id')) for row in rows]
        require(len(ids) == len(set(ids)), f'Duplicate IDs in {form}')
        state[form] = {identity: getattr(store, reader)(identity) for identity in sorted(ids)}
    return state


def verify_before(store, state):
    require(store.client.database_id == PRODUCTION_DATABASE_ID, 'Not the production database')
    old = seed_record(store.client.database_id, OLD_SITE)
    new = seed_record(store.client.database_id, NEW_SITE)
    good = [seed_record(store.client.database_id, definition)
            for definition in DEMO_SITES if definition != NEW_SITE]
    require(len(good) == 3 and NEW_SITE in DEMO_SITES, 'Unexpected bootstrap seed contract')
    expected = {record.record_id: record for record in [*good, old]}
    require(state['monitored_site'] == expected,
            'Monitored Sites differ from the four exact expected seeds (name, URL, identity or configuration)')
    crawls = [c for c in state['crawl'].values() if c.monitored_site_id == old.record_id]
    require(len(crawls) == 2, f'IMDb Crawl count must be exactly 2; found {len(crawls)}')
    require(all(c.status == 'error' for c in crawls), 'IMDb Crawls must both have status error')
    require(all(c.snapshot_id is None and c.previous_snapshot_id is None for c in crawls),
            'IMDb error Crawls unexpectedly reference state')
    require(not any(s.monitored_site_id == old.record_id for s in state['snapshot'].values()),
            'IMDb has Snapshots')
    require(not any(c.monitored_site_id == new.record_id for c in state['crawl'].values())
            and not any(s.monitored_site_id == new.record_id for s in state['snapshot'].values()),
            'Planet Python already has history')
    for snapshot in state['snapshot'].values():
        require(snapshot.monitored_site_id in expected, 'Snapshot references an unknown site')
    for item in state['snapshot_item'].values():
        parent = state['snapshot'].get(item.snapshot_id)
        require(parent is not None, 'Orphan Snapshot Item prevents safe maintenance')
        require(parent.monitored_site_id != old.record_id, 'IMDb has Snapshot Items')
    require(all(c.monitored_site_id in expected for c in state['crawl'].values()),
            'Crawl references an unknown site')
    return old, new, tuple(sorted(c.record_id for c in crawls))


def scheduler_window_guard():
    now = datetime.now(timezone.utc)
    minute = now.hour * 60 + now.minute
    require(10 <= minute < 23 * 60 + 50,
            'Refusing maintenance within ten minutes of the daily midnight UTC Scheduler run')


def replace_imdb(store, *, emit=print, guard=scheduler_window_guard):
    """Verify all preconditions before the first tombstone; no generic delete API."""
    require(store.client.database_id == PRODUCTION_DATABASE_ID, 'Not the production database')
    before = read_state(store)
    old, new, crawl_ids = verify_before(store, before)
    emit(json.dumps({'phase': 'verified_before', 'database_id': store.client.database_id,
                     'imdb': asdict(old), 'crawl_records': [{'record_id': i, 'status': before['crawl'][i].status,
                                        'crawled_at': str(before['crawl'][i].crawled_at)} for i in crawl_ids],
                     'crawl_count': 2, 'statuses': ['error', 'error'], 'successful_crawls': 0,
                     'snapshots': 0, 'snapshot_items': 0}, default=str))
    guard()
    require(read_state(store) == before, 'Production records changed during preflight; no writes made')
    guard()
    # Reuse the existing narrow record-update API. Delete children before the site.
    store.client.update_records([{'formId': store.ids['crawl'], 'recordId': identity,
                                  'deleted': True, 'fields': {}} for identity in crawl_ids])
    emit(json.dumps({'phase': 'deleted_crawls', 'record_ids': crawl_ids}))
    expected = {form: dict(records) for form, records in before.items()}
    for identity in crawl_ids:
        del expected['crawl'][identity]
    require(read_state(store) == expected, 'Crawl deletion read-back mismatch; stopped before site deletion')
    store.client.update_records([{'formId': store.ids['monitored_site'], 'recordId': old.record_id,
                                  'deleted': True, 'fields': {}}])
    emit(json.dumps({'phase': 'deleted_site', 'record_id': old.record_id}))
    del expected['monitored_site'][old.record_id]
    require(read_state(store) == expected, 'Site deletion read-back mismatch; stopped before replacement')
    store.create_monitored_site(new)
    emit(json.dumps({'phase': 'created_site', 'record_id': new.record_id}))
    expected['monitored_site'][new.record_id] = new
    after = read_state(store)
    require(after == expected, 'Final read-back mismatch; inspect production before any further action')
    result = {'phase': 'verified_after', 'sites': [asdict(s) for s in sorted(
        after['monitored_site'].values(), key=lambda s: s.name)],
        'imdb_absent': True, 'planet_python_count': 1,
        'planet_python_crawls': 0, 'planet_python_snapshots': 0, 'planet_python_snapshot_items': 0,
        'three_good_sites_and_all_retained_history_unchanged': True,
        'deleted_crawl_ids': crawl_ids, 'deleted_site_id': old.record_id, 'created_site_id': new.record_id}
    emit(json.dumps(result, default=str))
    return result


def production_config(app_yaml, gcloud):
    """Read only the intended deployment settings; never load or alter dotenv."""
    text = app_yaml.read_text()
    database = re.findall(r'^  ACTIVITYINFO_DATABASE_ID: "([A-Za-z0-9]+)"$', text, re.M)
    version = re.findall(r'^  ACTIVITYINFO_SECRET_VERSION: "([1-9][0-9]*)"$', text, re.M)
    require(database == [PRODUCTION_DATABASE_ID] and len(version) == 1,
            'app.yaml production database or pinned secret version is unexpected')
    process = subprocess.run([gcloud, 'secrets', 'versions', 'access', version[0],
                              '--secret=activityinfo-api-token', '--quiet'], capture_output=True)
    require(process.returncode == 0, 'Secret Manager access failed; output suppressed')
    return ActivityInfoConfig(database[0], process.stdout.decode('utf-8'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', required=True)
    parser.add_argument('--gcloud', default='gcloud')
    args = parser.parse_args()
    try:
        # A fresh depth-zero crawl must succeed before opening production storage.
        snapshot = crawl_site(MonitoredSite(NEW_SITE[1]))
        require(len(snapshot.items) == 1 and snapshot.items[0].canonical_url == NEW_SITE[1],
                'Planet Python did not produce the expected depth-zero page')
        print(json.dumps({'phase': 'planet_python_crawl_verified', 'pages': 1}), flush=True)
        config = production_config(Path('app.yaml'), args.gcloud)
        with ActivityInfoClient(config) as client:
            replace_imdb(ActivityInfoPersistence(client), emit=lambda line: print(line, flush=True))
        return 0
    except Exception as error:
        # Never print subprocess stderr, raw HTTP diagnostics, or credential data.
        detail = str(error) if isinstance(error, MaintenanceError) else 'Details suppressed'
        print(json.dumps({'phase': 'stopped', 'category': type(error).__name__, 'reason': detail,
                          'http_status': error.status if isinstance(error, ActivityInfoError) else None}), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
