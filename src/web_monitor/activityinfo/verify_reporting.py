"""Retained development reporting fixture; cleanup only after visual acceptance.

No external websites are crawled. The manifest records exact fixture identities
and pre-existing sites before writes, allowing targeted recovery after timeouts.
"""

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from web_monitor.activityinfo.client import ActivityInfoClient, ActivityInfoError
from web_monitor.activityinfo.config import ActivityInfoConfig
from web_monitor.activityinfo.persistence import ActivityInfoPersistence, CrawlRecord, MonitoredSiteRecord
from web_monitor.activityinfo.reporting import ActivityInfoReportPublisher, matches_definition
from web_monitor.activityinfo.schema import stable_id
from web_monitor.diff import compare_snapshots
from web_monitor.models import MonitoredSite, Snapshot, SnapshotDiff, SnapshotItem
from web_monitor.reporting import ReportingMetrics, ReportingService

FIXTURE_VERSION = 'stage6-reporting-v1'
SITE_NAMES = {letter: f'DEVELOPMENT/TEST Reporting {letter}' for letter in 'ABC'}
MANIFEST = Path('build/reporting-fixture.json')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def instant(day):
    return datetime(2026, 9, day, 12, tzinfo=timezone.utc)


@dataclass(frozen=True)
class ReportingFixture:
    sites: tuple
    snapshots: tuple  # (record id, site id, observation day, Snapshot)
    crawls: tuple

    def record_ids(self):
        # Dependency order for deletion.
        return ([('crawl', c.record_id) for c in self.crawls]
                + [('snapshot_item', stable_id(identity, item.canonical_url))
                   for identity, _, _, snapshot in self.snapshots for item in snapshot.items]
                + [('snapshot', identity) for identity, _, _, _ in self.snapshots]
                + [('monitored_site', site.record_id) for site in self.sites])


def build_fixture(database_id):
    def identity(*parts):
        return stable_id(database_id, FIXTURE_VERSION, *parts)

    def item(site, path, text):
        return SnapshotItem(f'https://reporting-{site.lower()}.example.invalid/{path}',
                            text, sha256(text.encode()).hexdigest())

    sites = tuple(MonitoredSiteRecord(
        identity('site', letter), SITE_NAMES[letter],
        MonitoredSite(f'https://reporting-{letter.lower()}.example.invalid/'), active=False,
    ) for letter in 'ABC')
    a0 = Snapshot((item('A', 'main', 'Original'), item('A', 'old', 'Original page')))
    a1 = Snapshot((item('A', 'main', 'Modified'), item('A', 'new', 'New page')))
    b0 = Snapshot((item('B', 'main', 'Stable'), item('B', 'about', 'About')))
    states = {'a0': a0, 'a1': a1, 'a2': a0, 'b0': b0}
    snapshots = tuple((identity('snapshot', name), sites[site].record_id, day, states[name])
                      for name, site, day in (('a0', 0, 14), ('a1', 0, 16),
                                              ('a2', 0, 18), ('b0', 1, 14)))
    observations = (
        (0, 14, 'initial', 'a0', None), (0, 15, 'no_change', 'a0', 'a0'),
        (0, 16, 'changed', 'a1', 'a0'), (0, 17, 'no_change', 'a1', 'a1'),
        (0, 18, 'changed', 'a2', 'a1'), (0, 19, 'error', None, 'a2'),
        (1, 14, 'initial', 'b0', None), (1, 15, 'no_change', 'b0', 'b0'),
        (1, 16, 'error', None, 'b0'), (1, 17, 'error', None, 'b0'),
    )
    crawls = []
    for site, day, status, current, previous in observations:
        diff = compare_snapshots(states[previous], states[current]) if status == 'changed' else SnapshotDiff()
        crawls.append(CrawlRecord(
            identity('crawl', str(site), str(day)), sites[site].record_id, instant(day), status,
            identity('snapshot', current) if current else None,
            identity('snapshot', previous) if previous else None,
            len(diff.added), len(diff.removed), len(diff.changed), 2 if current else 0,
            'Synthetic reporting test failure' if status == 'error' else None,
        ))
    return ReportingFixture(sites, snapshots, tuple(crawls))


def expected_metrics():
    return {
        'A': ReportingMetrics(6, 5, 1, 2, 2, 1, instant(19), instant(18), instant(18), 2, 2, 2),
        'B': ReportingMetrics(4, 2, 2, 0, 1, 1, instant(17), instant(15), None, 0, 0, 0),
        'C': ReportingMetrics(),
        'total': ReportingMetrics(10, 7, 3, 2, 3, 2, instant(19), instant(18), instant(18), 2, 2, 2),
    }


def sites_json(sites):
    return json.loads(json.dumps([asdict(site) for site in sites]))


def load_manifest(path, database_id, fixture):
    manifest = json.loads(path.read_text())
    require(manifest['database_id'] == database_id and manifest['version'] == FIXTURE_VERSION,
            'Fixture manifest belongs to a different database or version')
    require(manifest['records'] == [list(pair) for pair in fixture.record_ids()],
            'Fixture manifest identities do not match the deterministic dataset')
    return manifest


def save_manifest(path, manifest):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(manifest, indent=2) + '\n')
    temporary.replace(path)


def seed(store, fixture, manifest_path):
    require(not manifest_path.exists(), 'Fixture manifest exists; use verify or resume, not seed')
    before = store.list_monitored_sites()
    require(len(before) == 4, 'Expected exactly four existing seeded sites')
    require(not any(store.list_crawls(site.record_id) for site in before),
            'Existing Crawl data would change expected report totals; stop before writes')
    for form, identity in fixture.record_ids():
        require(not store.client.query_rows(store.ids[form], {'record_id': '_id'},
                    filter_formula=f'_id == {json.dumps(identity)}'), 'Fixture ID already exists')
    manifest = {'version': FIXTURE_VERSION, 'database_id': store.client.database_id,
                'records': fixture.record_ids(), 'seeded_sites': sites_json(before), 'phase': 'creating'}
    save_manifest(manifest_path, manifest)
    resume_seed(store, fixture, manifest_path)


def resume_seed(store, fixture, manifest_path):
    manifest = load_manifest(manifest_path, store.client.database_id, fixture)
    require(manifest['phase'] == 'creating', 'Only an interrupted seed can be resumed')
    verify_seeded_sites(store, fixture, manifest)
    for site in fixture.sites:
        store.create_monitored_site(site)
    for identity, site_id, day, snapshot in fixture.snapshots:
        store.persist_snapshot(snapshot, monitored_site_id=site_id, created_at=instant(day),
                               effective_from=instant(day), record_id=identity)
    for crawl in fixture.crawls:
        store.create_crawl(crawl)
    manifest['phase'] = 'awaiting_acceptance'
    save_manifest(manifest_path, manifest)


def verify_seeded_sites(store, fixture, manifest):
    fixture_ids = {site.record_id for site in fixture.sites}
    existing = tuple(site for site in store.list_monitored_sites() if site.record_id not in fixture_ids)
    require(sites_json(existing) == manifest['seeded_sites'], 'Pre-existing sites changed')


def pivot_cells(result):
    """Decode the native pivot tree for our one/two-dimensional analyses only."""
    require(isinstance(result, dict) and result.get('type') == 'pivot', 'Expected pivot results')
    table = result['table']
    nodes = table['nodes']
    row_categories = bool(table['rowDimensions'])
    require(len(table['rowDimensions']) <= 1 and len(table['columnDimensions']) <= 1
            and (row_categories or table['columnDimensions']),
            'Unexpected pivot dimensions')
    column_categories = bool(table['columnDimensions'])
    cells = {}
    rows = nodes[table['rootRow']].get('children', []) if row_categories else [table['rootRow']]
    for row in rows:
        node = nodes[row]
        require((not row_categories or 'category' in node) and not node.get('children'),
                'Unexpected pivot row hierarchy')
        for column, cell in node.get('cells', {}).items():
            category = nodes[int(column)].get('category') if column_categories else None
            key = (node['category'], category) if row_categories else (category, None)
            require(key not in cells and isinstance(cell.get('value'), (int, float)),
                    'Duplicate or nonnumeric pivot cell')
            cells[key] = cell['value']
    return cells


def expected_analyses():
    status = {
        SITE_NAMES['A']: {'initial': 1, 'no_change': 2, 'changed': 2, 'error': 1},
        SITE_NAMES['B']: {'initial': 1, 'no_change': 1, 'error': 2},
    }
    return {
        'site_status': {(site, status): count for site, counts in status.items()
                        for status, count in counts.items()},
        'change_activity': {(SITE_NAMES['A'], None): 2, (SITE_NAMES['B'], None): 0},
        'daily_activity': {(f'2026-09-{day}', None): count
                           for day, count in ((14, 2), (15, 2), (16, 2), (17, 2), (18, 1), (19, 1))},
        'status_distribution': {(status, None): count for status, count in
                                (('initial', 2), ('no_change', 3), ('changed', 2), ('error', 3))},
    }


def verify(store, fixture, manifest_path):
    manifest = load_manifest(manifest_path, store.client.database_id, fixture)
    require(manifest['phase'] == 'awaiting_acceptance', 'Fixture is not ready for acceptance')
    verify_seeded_sites(store, fixture, manifest)
    for site in fixture.sites:
        require(store.read_monitored_site(site.record_id) == site, 'Synthetic site mismatch')
        require(set(store.list_crawls(site.record_id)) ==
                {c for c in fixture.crawls if c.monitored_site_id == site.record_id}, 'Synthetic Crawl mismatch')
    for identity, site_id, day, snapshot in fixture.snapshots:
        loaded = store.load_snapshot(identity)
        require(loaded.snapshot == snapshot and loaded.record.monitored_site_id == site_id
                and loaded.record.effective_from == instant(day), 'Synthetic Snapshot mismatch')
    overview = ReportingService(store).overview()
    expected = expected_metrics()
    require(overview.monitored_sites == 7, 'Expected four seeded plus three synthetic sites')
    require(overview.totals == expected['total'], 'Overall ReportingService totals mismatch')
    by_site = {site.site_id: site.metrics for site in overview.sites}
    for letter, site in zip('ABC', fixture.sites):
        require(by_site[site.record_id] == expected[letter], f'Site {letter} metrics mismatch')
    print('ReportingService: 7 sites; 10 Crawls; 7 successful; 2 changed; 3 errors; diff totals 2/2/2.')
    publisher = ActivityInfoReportPublisher(store.client, store.schemas)
    publisher._verify_tree()
    results = []
    for definition in publisher.definitions:
        publisher._validate_report(store.client.get_report(definition['id']), definition, published=True)
        for analysis in definition['analyses']:
            name = next(name for name in expected_analyses()
                        if stable_id(definition['id'], name) == analysis['id'])
            result = store.client.get_analysis_results(definition['id'], analysis['id'])
            require(result.get('visualization') == analysis['model']['visualization'],
                    'Native visualization type mismatch')
            actual = pivot_cells(result)
            wanted = expected_analyses()[name]
            # Native pivots may omit structural zero cells. Reject unexpected
            # categories even at zero; allow a zero only in known site/status cells.
            allowed = set(wanted)
            if name == 'site_status':
                allowed |= {(SITE_NAMES['B'], 'changed')}
            require(set(actual) <= allowed, f'{name}: unexpected native categories')
            require(all(actual.get(key, 0) == value for key, value in wanted.items())
                    and all(value == wanted.get(key, 0) for key, value in actual.items()),
                    f'{name}: native values differ from known expectations')
            results.append({'report': definition['layout'], 'analysis': name, 'result': result})
            print(f'{definition["layout"]} {name}: known aggregates verified.')
    manifest_path.with_name('reporting-results.json').write_text(json.dumps(results, indent=2) + '\n')
    print('Fixture retained for user inspection. No cleanup performed.')


def cleanup(store, fixture, manifest_path, *, accepted=False):
    require(accepted, 'Cleanup requires explicit user visual acceptance')
    manifest = load_manifest(manifest_path, store.client.database_id, fixture)
    verify_seeded_sites(store, fixture, manifest)
    for form, identity in fixture.record_ids():
        store.client.update_records([{'formId': store.ids[form], 'recordId': identity,
                                      'deleted': True, 'fields': {}}])
    for form, identity in fixture.record_ids():
        require(not store.client.query_rows(store.ids[form], {'record_id': '_id'},
                    filter_formula=f'_id == {json.dumps(identity)}'), 'Synthetic record remains')
    require(sites_json(store.list_monitored_sites()) == manifest['seeded_sites'], 'Seeded sites changed')
    publisher = ActivityInfoReportPublisher(store.client, store.schemas)
    publisher._verify_tree()
    for expected in publisher.definitions:
        publisher._validate_report(store.client.get_report(expected['id']), expected, published=True)
        for analysis in expected['analyses']:
            require(matches_definition(store.client.get_analysis(expected['id'], analysis['id']), analysis),
                    'Analysis changed during acceptance')
    manifest['phase'] = 'cleaned'
    save_manifest(manifest_path, manifest)
    print('Removed only synthetic records; four seeded sites and both report definitions verified.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('seed', 'resume', 'verify', 'cleanup'))
    parser.add_argument('--env-file', type=Path, default=Path('.env'))
    parser.add_argument('--manifest', type=Path, default=MANIFEST)
    parser.add_argument('--accepted', action='store_true', help='User has accepted the populated visuals')
    args = parser.parse_args()
    try:
        with ActivityInfoClient(ActivityInfoConfig.from_environment(env_file=args.env_file)) as client:
            store = ActivityInfoPersistence(client)
            fixture = build_fixture(client.database_id)
            if args.action == 'cleanup':
                cleanup(store, fixture, args.manifest, accepted=args.accepted)
            else:
                {'seed': seed, 'resume': resume_seed, 'verify': verify}[args.action](store, fixture, args.manifest)
        return 0
    except (ActivityInfoError, ValueError, KeyError, TypeError) as error:
        print(f'STOP: reporting checkpoint failed ({type(error).__name__}). Fixture manifest retained.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
