"""Explicit development-only synthetic history checkpoint with targeted cleanup."""

import argparse
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys

from web_monitor.activityinfo.client import ActivityInfoClient, ActivityInfoError
from web_monitor.activityinfo.config import ActivityInfoConfig
from web_monitor.activityinfo.persistence import (
    ActivityInfoPersistence, CrawlRecord, MonitoredSiteRecord, new_record_id,
)
from web_monitor.activityinfo.schema import stable_id
from web_monitor.activityinfo.verify_lifecycle import require
from web_monitor.diff import compare_snapshots
from web_monitor.history import HistoryService
from web_monitor.models import MonitoredSite, Snapshot, SnapshotDiff, SnapshotItem


def verify(env_file: Path) -> None:
    config = ActivityInfoConfig.from_environment(env_file=env_file)
    with ActivityInfoClient(config) as client:
        store = ActivityInfoPersistence(client)
        before = store.list_monitored_sites()
        site_id = new_record_id()
        cleanup = []
        print(f"DEVELOPMENT/TEST Stage 4 site: {site_id}", flush=True)

        def instant(day):
            return datetime(2026, 9, day, 12, tzinfo=timezone.utc)

        def item(path, content):
            return SnapshotItem('https://example.invalid/' + path, content,
                                sha256(content.encode()).hexdigest())

        a = Snapshot((item('a', 'Baseline'), item('removed', 'Original page')))
        b = Snapshot((item('a', 'Modified'), item('added', 'New page')))
        c = a
        states = dict(zip('ABC', (a, b, c)))
        ids = {name: new_record_id() for name in states}
        try:
            cleanup.append(('monitored_site', site_id))
            store.create_monitored_site(MonitoredSiteRecord(
                site_id, f'DEVELOPMENT/TEST Stage 4 {site_id}',
                MonitoredSite('https://example.invalid/'), active=False,
            ))
            for name, day in zip('ABC', (14, 17, 19)):
                snapshot = states[name]
                # Track IDs before sending writes, including interrupted responses.
                cleanup.append(('snapshot', ids[name]))
                cleanup.extend(('snapshot_item', stable_id(ids[name], value.canonical_url))
                               for value in snapshot.items)
                store.persist_snapshot(snapshot, monitored_site_id=site_id,
                                       created_at=instant(day), effective_from=instant(day),
                                       record_id=ids[name])
            for day, status, current, previous in (
                (14,'initial','A',None), (15,'no_change','A','A'), (16,'no_change','A','A'),
                (17,'changed','B','A'), (18,'no_change','B','B'), (19,'changed','C','B'),
                (20,'error',None,'C'),
            ):
                diff = compare_snapshots(states[previous], states[current]) if status == 'changed' else SnapshotDiff()
                crawl_id = new_record_id()
                cleanup.append(('crawl', crawl_id))
                store.create_crawl(CrawlRecord(
                    crawl_id, site_id, instant(day), status, ids.get(current), ids.get(previous),
                    len(diff.added), len(diff.removed), len(diff.changed),
                    len(states[current].items) if current else 0,
                    'Synthetic development failure' if status == 'error' else None,
                ))
            service = HistoryService(store)
            for day, name in zip(range(14, 21), 'AAABBCC'):
                result = service.state_on_date(site_id, date(2026, 9, day))
                require(result.crawl.snapshot_reference == ids[name] and result.snapshot == states[name],
                        'Real state selection or child reconstruction failed')
            print('UTC state selection and complete Snapshot reconstruction passed.', flush=True)
            for day, refs in ((16,None),(17,('A','B')),(19,('B','C')),(20,None)):
                results = service.changes_on_date(site_id, date(2026,9,day))
                require(len(results) == bool(refs), 'Real transition selection failed')
                if refs:
                    require(results[0].diff == compare_snapshots(*(states[name] for name in refs)),
                            'Real transition diff failed')
            for start, end, old, new in ((14,16,'A','A'),(14,17,'A','B'),(17,19,'B','C'),(14,19,'A','C')):
                result = service.changes_between_dates(site_id, date(2026,9,start), date(2026,9,end))
                require(result.diff == compare_snapshots(states[old], states[new]), 'Real endpoint comparison failed')
            latest = service.latest_change(site_id)
            require(latest.crawl.crawled_at == instant(19) and latest.diff == compare_snapshots(b,c),
                    'Real latest-change failed')
            require([row.status for row in service.recent_crawls(site_id)] ==
                    ['error','changed','no_change','changed','no_change','no_change','initial'],
                    'Real recent Crawl ordering failed')
            require(len(service.recent_crawls(site_id, 2)) == 2, 'Real recent limit failed')
            print('All five historical queries passed against real ActivityInfo.', flush=True)
        finally:
            # Remove references before targets, and children before their parents.
            order = {'crawl':0, 'snapshot_item':1, 'snapshot':2, 'monitored_site':3}
            for form, record_id in sorted(cleanup, key=lambda pair: order[pair[0]]):
                client.update_records([{'formId':store.ids[form], 'recordId':record_id,
                                        'deleted':True, 'fields':{}}])
            for form, record_id in cleanup:
                require(not client.query_rows(store.ids[form], {'record_id':'_id'},
                                              filter_formula=f'_id == {json.dumps(record_id)}'),
                        'Synthetic record remains after cleanup')
            require(store.list_monitored_sites() == before, 'Seeded sites changed')
            print(f'Cleaned {len(cleanup)} synthetic records; existing sites unchanged.', flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, default=Path.cwd() / '.env')
    args = parser.parse_args()
    try:
        verify(args.env_file)
        return 0
    except ActivityInfoError as error:
        print(f'STOP: ActivityInfo checkpoint failed (HTTP {error.status or "unavailable"}).', file=sys.stderr)
        return 1
    except ValueError:
        print('STOP: historical checkpoint failed validation.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
