"""Serialization and parent-child storage through scripted HTTP boundaries."""

from datetime import datetime, timezone

import pytest

from web_monitor.activityinfo.persistence import (
    ActivityInfoPersistence, CrawlRecord, PersistenceError, SnapshotItemRecord,
    SnapshotRecord, count, timestamp,
)
from web_monitor.models import Snapshot, SnapshotItem

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)


def test_snapshot_and_children_round_trip(api, schemas, record_response):
    store = ActivityInfoPersistence(api.client, schemas)
    snapshot = Snapshot((
        SnapshotItem("https://fixture.invalid/a", "Development A", "a" * 64),
        SnapshotItem("https://fixture.invalid/b", None, "b" * 64),
    ))
    for _ in range(3):
        api.expect("POST", "/update", {})
    saved = store.persist_snapshot(
        snapshot, monitored_site_id="site1", created_at=NOW,
        effective_from=NOW, record_id="snapshot1",
    )
    parent_change = api.calls[0].body["changes"][0]
    assert parent_change["fields"] == {
        "monitored_site": schemas["monitored_site"]["id"] + ":site1",
        "created_at": "2026-10-03T12:00:00Z", "effective_from": "2026-10-03T12:00:00Z",
        "item_count": 2,
    }
    for call in api.calls[1:]:
        assert call.body["changes"][0]["parentRecordId"] == "snapshot1"
        assert call.body["changes"][0]["formId"] == schemas["snapshot_item"]["id"]
    api.expect("GET", f"/form/{schemas['snapshot']['id']}/record/snapshot1", record_response(
        "snapshot", "snapshot1", parent_change["fields"],
    ))
    api.expect("POST", "/query/rows", [{"record_id": child.record_id} for child in saved.items])
    for child, call in zip(saved.items, api.calls[1:3]):
        api.expect("GET", f"/form/{schemas['snapshot_item']['id']}/record/{child.record_id}", record_response(
            "snapshot_item", child.record_id, call.body["changes"][0]["fields"], parent_id="snapshot1",
        ))
    assert store.load_snapshot("snapshot1") == saved
    assert api.calls[4].body["filter"] == 'parent._id == "snapshot1"'


@pytest.mark.parametrize("status", ["initial", "no_change", "changed", "error"])
def test_crawl_serialization_and_deserialization(api, schemas, record_response, status):
    store = ActivityInfoPersistence(api.client, schemas)
    record = CrawlRecord(
        "crawl1", "site1", NOW, status, "snapshot1", "snapshot0",
        1, 2, 3, 4, "DEVELOPMENT/TEST" if status == "error" else None,
    )
    api.expect("POST", "/update", {})
    store.create_crawl(record)
    values = api.calls[-1].body["changes"][0]["fields"]
    assert values["snapshot"] == schemas["snapshot"]["id"] + ":snapshot1"
    assert values["previous_snapshot"] == schemas["snapshot"]["id"] + ":snapshot0"
    assert values["status"] == [status.replace("_", "")]
    api.expect("GET", f"/form/{schemas['crawl']['id']}/record/crawl1", record_response("crawl", "crawl1", values))
    assert store.read_crawl("crawl1") == record


def test_incomplete_snapshot_is_rejected(api, schemas, record_response):
    store = ActivityInfoPersistence(api.client, schemas)
    api.expect("GET", f"/form/{schemas['snapshot']['id']}/record/snapshot1", record_response("snapshot", "snapshot1", {
        "monitored_site": schemas["monitored_site"]["id"] + ":site1",
        "created_at": timestamp(NOW), "effective_from": timestamp(NOW), "item_count": 1,
    }))
    api.expect("POST", "/query/rows", [])
    with pytest.raises(PersistenceError, match="Incomplete"):
        store.load_snapshot("snapshot1")


def test_child_parent_is_checked_on_read(api, schemas, record_response):
    store = ActivityInfoPersistence(api.client, schemas)
    api.expect("POST", "/query/rows", [{"record_id": "child1"}])
    api.expect("GET", f"/form/{schemas['snapshot_item']['id']}/record/child1", record_response("snapshot_item", "child1", {
        "canonical_url": "https://fixture.invalid/", "title": None, "content_hash": "abc",
    }, parent_id="wrongparent"))
    with pytest.raises(PersistenceError, match="parent"):
        store.list_snapshot_items("snapshot1")


def test_missing_child_parent_fails(api, schemas, record_response):
    store = ActivityInfoPersistence(api.client, schemas)
    api.expect("GET", f"/form/{schemas['snapshot_item']['id']}/record/child1", record_response("snapshot_item", "child1", {
        "canonical_url": "https://fixture.invalid/", "title": None, "content_hash": "abc",
    }))
    with pytest.raises(PersistenceError, match="parentRecordId"):
        store.read_snapshot_item("child1")


def test_invalid_records_fail_before_writing(api, schemas):
    store = ActivityInfoPersistence(api.client, schemas)
    with pytest.raises(PersistenceError, match="status"):
        store.create_crawl(CrawlRecord("crawl1", "site1", NOW, "unknown"))
    with pytest.raises(PersistenceError, match="timezone"):
        store.create_snapshot(SnapshotRecord("snapshot1", "site1", NOW.replace(tzinfo=None), NOW, 0))
    assert api.calls == []


@pytest.mark.parametrize("value", [-1, 0.5, True, None, "1", float("inf")])
def test_invalid_counts(value):
    with pytest.raises(PersistenceError):
        count(value)


def test_latest_snapshot_none_filters_by_site(api, schemas):
    store = ActivityInfoPersistence(api.client, schemas)
    api.expect("POST", "/query/rows", [])
    assert store.load_latest_snapshot("site1") is None
    assert api.calls[-1].body["filter"] == 'monitored_site._id == "site1"'


def test_latest_snapshot_reconstructs_items_and_orders_parsed_timestamps(api, schemas, record_response):
    store = ActivityInfoPersistence(api.client, schemas)
    api.expect("POST", "/query/rows", [
        {"record_id": "older", "effective_from": "2026-10-03T14:00:00+02:00", "created_at": timestamp(NOW)},
        {"record_id": "latest", "effective_from": "2026-10-03T12:00:00.100000Z", "created_at": "2026-10-03T12:00:01Z"},
    ])
    api.expect("GET", f"/form/{schemas['snapshot']['id']}/record/latest", record_response("snapshot", "latest", {
        "monitored_site": schemas["monitored_site"]["id"] + ":site1",
        "effective_from": "2026-10-03T12:00:00.100000Z", "created_at": "2026-10-03T12:00:01Z", "item_count": 1,
    }))
    api.expect("POST", "/query/rows", [{"record_id": "child1"}])
    api.expect("GET", f"/form/{schemas['snapshot_item']['id']}/record/child1", record_response("snapshot_item", "child1", {
        "canonical_url": "https://fixture.invalid/", "title": "Saved title", "content_hash": "a" * 64,
    }, parent_id="latest"))
    stored = store.load_latest_snapshot("site1")
    assert stored.record.record_id == "latest"
    expected = Snapshot((SnapshotItem("https://fixture.invalid/", "Saved title", "a" * 64),))
    assert stored.snapshot == expected
    from web_monitor.diff import compare_snapshots
    from web_monitor.models import SnapshotDiff
    assert compare_snapshots(stored.snapshot, expected) == SnapshotDiff()


def test_latest_snapshot_rejects_cross_site_record(api, schemas, record_response):
    store = ActivityInfoPersistence(api.client, schemas)
    api.expect("POST", "/query/rows", [{"record_id": "latest", "effective_from": timestamp(NOW), "created_at": timestamp(NOW)}])
    api.expect("GET", f"/form/{schemas['snapshot']['id']}/record/latest", record_response("snapshot", "latest", {
        "monitored_site": schemas["monitored_site"]["id"] + ":other",
        "effective_from": timestamp(NOW), "created_at": timestamp(NOW), "item_count": 0,
    }))
    api.expect("POST", "/query/rows", [])
    with pytest.raises(PersistenceError, match="different monitored site"):
        store.load_latest_snapshot("site1")


def test_latest_incomplete_snapshot_is_not_ignored(api, schemas, record_response):
    store = ActivityInfoPersistence(api.client, schemas)
    api.expect("POST", "/query/rows", [{"record_id": "latest", "effective_from": timestamp(NOW), "created_at": timestamp(NOW)}])
    api.expect("GET", f"/form/{schemas['snapshot']['id']}/record/latest", record_response("snapshot", "latest", {
        "monitored_site": schemas["monitored_site"]["id"] + ":site1",
        "effective_from": timestamp(NOW), "created_at": timestamp(NOW), "item_count": 2,
    }))
    api.expect("POST", "/query/rows", [])
    with pytest.raises(PersistenceError, match="Incomplete"):
        store.load_latest_snapshot("site1")


def test_list_crawls_scoped_metadata_only(api, schemas, record_response):
    store = ActivityInfoPersistence(api.client, schemas)
    api.expect("POST", "/query/rows", [{"record_id": "crawl1"}])
    api.expect("GET", f"/form/{schemas['crawl']['id']}/record/crawl1", record_response("crawl", "crawl1", {
        "monitored_site": schemas['monitored_site']['id'] + ':site1',
        "crawled_at": '2026-09-14T23:59:59.999999Z', 'status': ['initial'],
        'snapshot': schemas['snapshot']['id'] + ':snapshot1', 'previous_snapshot': None,
        'added_count': 0, 'removed_count': 0, 'changed_count': 0, 'pages_crawled': 1,
    }))
    records = store.list_crawls('site1')
    assert records[0].snapshot_id == 'snapshot1'
    assert records[0].crawled_at.microsecond == 999999
    assert api.calls[0].body['filter'] == 'monitored_site._id == "site1"'
    assert len(api.calls) == 2


def test_list_crawls_rejects_duplicates(api, schemas):
    store = ActivityInfoPersistence(api.client, schemas)
    api.expect('POST', '/query/rows', [{'record_id': 'crawl1'}] * 2)
    with pytest.raises(PersistenceError, match='Duplicate Crawl'):
        store.list_crawls('site1')


def test_scheduler_provenance_round_trip(api, schemas, record_response):
    store = ActivityInfoPersistence(api.client, schemas)
    record = CrawlRecord('scheduled1', 'site1', NOW, 'error',
                         scheduler_invocation='invocation1', scheduled_at=NOW)
    api.expect('POST', '/update', {})
    store.create_crawl(record)
    values = api.calls[-1].body['changes'][0]['fields']
    assert values['scheduler_invocation'] == 'invocation1'
    assert values['scheduled_at'] == timestamp(NOW)
    api.expect('GET', f"/form/{schemas['crawl']['id']}/record/scheduled1",
               record_response('crawl', 'scheduled1', values))
    assert store.read_crawl('scheduled1') == record


def test_legacy_schema_supports_manual_writes_but_not_scheduler(api, schemas):
    from copy import deepcopy
    legacy = deepcopy(schemas)
    legacy['crawl']['elements'] = [f for f in legacy['crawl']['elements']
                                   if f['code'] not in {'scheduler_invocation', 'scheduled_at'}]
    store = ActivityInfoPersistence(api.client, legacy)
    api.expect('POST', '/update', {})
    store.create_crawl(CrawlRecord('manual1', 'site1', NOW, 'error'))
    with pytest.raises(PersistenceError, match='run bootstrap'):
        store.create_crawl(CrawlRecord('scheduled1', 'site1', NOW, 'error',
                                      scheduler_invocation='invocation1', scheduled_at=NOW))


def test_find_crawl_only_treats_404_as_absent(api, schemas):
    from web_monitor.activityinfo.client import ActivityInfoError
    store = ActivityInfoPersistence(api.client, schemas)
    path = f"/form/{schemas['crawl']['id']}/record/crawl1"
    api.expect('GET', path, {}, status=404)
    assert store.find_crawl('crawl1') is None
    api.expect('GET', path, {}, status=503)
    with pytest.raises(ActivityInfoError):
        store.find_crawl('crawl1')


def test_find_snapshot_does_not_hide_missing_children(api, schemas, record_response):
    store = ActivityInfoPersistence(api.client, schemas)
    path = f"/form/{schemas['snapshot']['id']}/record/snapshot1"
    raw = record_response('snapshot', 'snapshot1', {
        'monitored_site': schemas['monitored_site']['id'] + ':site1',
        'created_at': timestamp(NOW), 'effective_from': timestamp(NOW), 'item_count': 1,
    })
    api.expect('GET', path, raw)
    api.expect('GET', path, raw)
    api.expect('POST', '/query/rows', [])
    with pytest.raises(PersistenceError, match='Incomplete'):
        store.find_snapshot('snapshot1')
