"""Known-data acceptance checks remain offline in pytest."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from web_monitor.activityinfo.verify_reporting import (
    build_fixture, cleanup, expected_metrics, load_manifest, pivot_cells, save_manifest,
)
from web_monitor.diff import compare_snapshots
from web_monitor.reporting import ReportingService


def test_fixture_has_independent_expected_metrics_and_consistent_transitions():
    fixture = build_fixture('testdatabase')
    store = SimpleNamespace(list_monitored_sites=lambda: fixture.sites,
        list_crawls=lambda site_id: [c for c in fixture.crawls if c.monitored_site_id == site_id])
    overview = ReportingService(store).overview()
    assert overview.totals == expected_metrics()['total']
    assert [s.metrics for s in overview.sites] == [expected_metrics()[key] for key in 'ABC']
    assert len(fixture.record_ids()) == len(set(fixture.record_ids())) == 25
    assert len(fixture.snapshots) == 4
    assert all(not site.active for site in fixture.sites)
    snapshots = {identity: state for identity, _, _, state in fixture.snapshots}
    for crawl in fixture.crawls:
        if crawl.status == 'changed':
            diff = compare_snapshots(snapshots[crawl.previous_snapshot_id], snapshots[crawl.snapshot_id])
            assert (len(diff.added), len(diff.removed), len(diff.changed)) == (1, 1, 1)
        elif crawl.status == 'no_change':
            assert crawl.snapshot_id == crawl.previous_snapshot_id
        elif crawl.status == 'error':
            assert crawl.snapshot_id is None and crawl.previous_snapshot_id is not None
    assert build_fixture('testdatabase') == fixture
    assert set(build_fixture('otherdatabase').record_ids()).isdisjoint(fixture.record_ids())


def test_pivot_decoder_reads_aggregate_cells():
    result = {'type': 'pivot', 'table': {'rowDimensions': [{}], 'columnDimensions': [{}],
        'rootRow': 0, 'rootColumn': 2, 'nodes': [
            {'children': [1]}, {'category': 'A', 'cells': {'3': {'value': 6}}},
            {'children': [3]}, {'category': 'changed'},
        ]}}
    assert pivot_cells(result) == {('A', 'changed'): 6}
    result['table']['nodes'][1]['cells']['3']['value'] = 'error'
    with pytest.raises(ValueError):
        pivot_cells(result)


def test_cleanup_requires_acceptance_before_any_access(tmp_path):
    store = Mock()
    with pytest.raises(ValueError, match='acceptance'):
        cleanup(store, build_fixture('testdatabase'), tmp_path / 'fixture.json')
    assert not store.mock_calls


def test_manifest_rejects_foreign_or_extra_cleanup_ids(tmp_path):
    fixture = build_fixture('testdatabase')
    path = tmp_path / 'fixture.json'
    manifest = {'version': 'stage6-reporting-v1', 'database_id': 'testdatabase',
                'records': fixture.record_ids()}
    save_manifest(path, manifest)
    assert load_manifest(path, 'testdatabase', fixture)['database_id'] == 'testdatabase'
    with pytest.raises(ValueError):
        load_manifest(path, 'otherdatabase', fixture)
    manifest['records'].append(('monitored_site', 'seededsite'))
    save_manifest(path, manifest)
    with pytest.raises(ValueError):
        load_manifest(path, 'testdatabase', fixture)


def test_pie_decoder_reads_column_series_without_row_facets():
    result = {'type': 'pivot', 'table': {'rowDimensions': [], 'columnDimensions': [{}],
        'rootRow': 0, 'rootColumn': 1, 'nodes': [
            {'cells': {'2': {'value': 2}, '3': {'value': 3}}},
            {'children': [2, 3]}, {'category': 'initial'}, {'category': 'error'},
        ]}}
    assert pivot_cells(result) == {('initial', None): 2, ('error', None): 3}
