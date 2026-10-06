"""Lifecycle semantics using local HTTP fixtures and a small persistence boundary."""

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
import requests

from web_monitor import diff
from web_monitor.activityinfo.persistence import (
    MonitoredSiteRecord, PersistenceError, SnapshotItemRecord, SnapshotRecord,
    StoredSnapshot,
)
from web_monitor.crawler import CrawlError, crawl_site
from web_monitor.models import MonitoredSite, Snapshot, SnapshotDiff
from web_monitor.monitoring import MonitoringService, sanitized_crawl_error


class RecordingStore:
    """Four application operations; no HTTP, schema, or ActivityInfo emulation."""

    def __init__(self, sites):
        self.sites = {site.record_id: site for site in sites}
        self.snapshots = []
        self.crawls = []
        self.lookups = []

    def read_monitored_site(self, record_id):
        return self.sites[record_id]

    def load_latest_snapshot(self, monitored_site_id):
        self.lookups.append(monitored_site_id)
        matches = [saved for saved in self.snapshots if saved.record.monitored_site_id == monitored_site_id]
        return matches[-1] if matches else None

    def persist_snapshot(self, snapshot, *, monitored_site_id, created_at, effective_from, record_id=None):
        record = SnapshotRecord(
            record_id or f"snapshot{len(self.snapshots) + 1}", monitored_site_id,
            created_at, effective_from, len(snapshot.items),
        )
        items = tuple(SnapshotItemRecord(
            f"{record.record_id}item{index}", record.record_id, item,
        ) for index, item in enumerate(snapshot.items))
        stored = StoredSnapshot(record, Snapshot(tuple(child.item for child in items)), items)
        self.snapshots.append(stored)
        return stored

    def create_crawl(self, record):
        self.crawls.append(record)


@pytest.fixture
def store(fixture_site):
    return RecordingStore([MonitoredSiteRecord("site1", "Local fixture", MonitoredSite(fixture_site.url, 2))])


def counts(result):
    crawl = result.crawl
    return (crawl.added_count, crawl.removed_count, crawl.changed_count)


def test_initial_unchanged_changed_unchanged_error(fixture_site, store, monkeypatch):
    start = datetime(2026, 10, 3, tzinfo=timezone.utc)
    clock = iter(start + timedelta(seconds=index) for index in range(20))
    crawler = Mock(wraps=crawl_site)
    compare = Mock(wraps=diff.compare_snapshots)
    monkeypatch.setattr(diff, "compare_snapshots", compare)
    service = MonitoringService(store, crawler=crawler, clock=lambda: next(clock))

    first = service.run("site1")
    assert first.crawl.status == "initial"
    assert counts(first) == (0, 0, 0)
    assert first.crawl.previous_snapshot_id is None
    assert first.crawl.pages_crawled == 6
    assert len(store.crawls) == len(store.snapshots) == 1
    baseline = store.snapshots[0]
    assert len(baseline.items) == 6
    assert baseline.record.effective_from == first.crawl.crawled_at == start
    assert baseline.record.created_at == start + timedelta(seconds=1)
    assert compare.call_count == 0

    second = service.run("site1")
    assert second.crawl.status == "no_change"
    assert second.crawl.snapshot_id == second.crawl.previous_snapshot_id == baseline.record.record_id
    assert counts(second) == (0, 0, 0)
    assert len(store.crawls) == 2 and len(store.snapshots) == 1
    assert store.snapshots[0] == baseline

    fixture_site.state = "site_v2"
    third = service.run("site1")
    assert third.crawl.status == "changed"
    assert counts(third) == (1, 1, 1)
    assert len(store.crawls) == 3 and len(store.snapshots) == 2
    current = store.snapshots[1]
    assert third.crawl.previous_snapshot_id == baseline.record.record_id
    assert third.crawl.snapshot_id == current.record.record_id
    assert current.snapshot == crawl_site(store.sites["site1"].site)
    assert current.record.item_count == len(current.items) == 6
    assert current.record.effective_from == third.crawl.crawled_at
    assert current.record.created_at > current.record.effective_from

    fourth = service.run("site1")
    assert fourth.crawl.status == "no_change"
    assert fourth.crawl.snapshot_id == fourth.crawl.previous_snapshot_id == current.record.record_id
    assert len(store.crawls) == 4 and len(store.snapshots) == 2
    assert sum(len(saved.items) for saved in store.snapshots) == 12
    assert store.snapshots == [baseline, current]

    fixture_site.routes["/changed.html"] = (503, {}, "Controlled failure")
    fifth = service.run("site1")
    assert fifth.crawl.status == "error"
    assert fifth.crawl.snapshot_id is None
    assert fifth.crawl.previous_snapshot_id == current.record.record_id
    assert fifth.crawl.pages_crawled == 0 and counts(fifth) == (0, 0, 0)
    assert fifth.changes is None
    assert fifth.crawl.error_message == "Crawl failed because a required page returned HTTP 503."
    assert len(store.crawls) == 5 and store.snapshots == [baseline, current]
    assert len({crawl.record_id for crawl in store.crawls}) == 5
    assert store.lookups == ["site1"] * 5
    assert crawler.call_count == 5 and compare.call_count == 3
    assert all(call.args == (store.sites["site1"].site,) for call in crawler.call_args_list)
    assert [call.args[0] for call in compare.call_args_list] == [baseline.snapshot, baseline.snapshot, current.snapshot]


def test_first_crawl_failure_does_not_create_baseline(store):
    result = MonitoringService(store, crawler=Mock(side_effect=CrawlError("sensitive"))).run("site1")
    assert result.crawl.status == "error"
    assert result.crawl.snapshot_id is result.crawl.previous_snapshot_id is None
    assert counts(result) == (0, 0, 0) and result.crawl.pages_crawled == 0
    assert store.snapshots == [] and len(store.crawls) == 1
    assert "sensitive" not in result.crawl.error_message


@pytest.mark.parametrize("status, content_type, expected", [
    (202, "text/html", "Crawl failed because a required page returned HTTP 202."),
    (403, "text/html", "Crawl failed because a required page returned HTTP 403."),
    (200, "application/pdf", "A required page did not return HTML content."),
    (200, "text/html", None),
])
def test_http_response_stores_only_safe_error_details(fixture_site, status, content_type, expected):
    path = "/private?token=url-secret"
    fixture_site.routes[path] = (
        status,
        {"Content-Type": content_type, "x-amzn-waf-action": "challenge-header-secret"},
        "<html><body>body-secret</body></html>",
    )
    store = RecordingStore([
        MonitoredSiteRecord("site1", "Local fixture", MonitoredSite(fixture_site.url + path)),
    ])

    result = MonitoringService(store).run("site1")

    assert store.crawls == [result.crawl]
    assert result.crawl.error_message == expected
    assert result.crawl.status == ("initial" if expected is None else "error")
    assert len(store.snapshots) == (1 if expected is None else 0)
    if expected is not None:
        assert all(detail not in result.crawl.error_message for detail in (
            fixture_site.url, path, "url-secret", "x-amzn-waf-action",
            "challenge-header-secret", "body-secret",
        ))


def test_failure_does_not_stop_next_successful_attempt(store, fixture_site):
    service = MonitoringService(store)
    first = service.run("site1")
    fixture_site.routes["/"] = (500, {}, "Error")
    assert service.run("site1").crawl.status == "error"
    fixture_site.routes.clear()
    recovered = service.run("site1")
    assert recovered.crawl.status == "no_change"
    assert recovered.crawl.snapshot_id == first.crawl.snapshot_id
    assert len(store.snapshots) == 1 and len(store.crawls) == 3


def test_site_state_is_isolated(store):
    store.sites["site2"] = MonitoredSiteRecord("site2", "Second site", MonitoredSite("https://example.invalid/"))
    service = MonitoringService(store, crawler=lambda site: Snapshot())
    first = service.run("site1")
    second = service.run("site2")
    assert first.crawl.status == second.crawl.status == "initial"
    assert first.crawl.snapshot_id != second.crawl.snapshot_id
    assert service.run("site1").crawl.snapshot_id == first.crawl.snapshot_id


def test_corrupt_prior_state_stops_before_crawl_or_writes(store):
    store.load_latest_snapshot = Mock(side_effect=PersistenceError("Incomplete Snapshot"))
    crawler = Mock()
    with pytest.raises(PersistenceError, match="Incomplete"):
        MonitoringService(store, crawler=crawler).run("site1")
    crawler.assert_not_called()
    assert store.snapshots == store.crawls == []


def test_persistence_error_is_not_reported_as_crawl_failure(store):
    store.persist_snapshot = Mock(side_effect=PersistenceError("Storage failed"))
    with pytest.raises(PersistenceError, match="Storage failed"):
        MonitoringService(store, crawler=lambda site: Snapshot()).run("site1")
    assert store.crawls == []


@pytest.mark.parametrize("cause, fragment", [
    (requests.Timeout("token=secret"), "timed out"),
    (requests.ConnectionError("Authorization: secret"), "connection"),
    (requests.exceptions.SSLError("password=secret"), "TLS"),
])
def test_error_categories_do_not_include_sensitive_text(cause, fragment):
    error = CrawlError("https://user:secret@example.invalid/?api_key=secret")
    error.__cause__ = cause
    message = sanitized_crawl_error(error)
    assert fragment in message
    assert "secret" not in message and "https" not in message


def test_generic_failure_and_redirect_errors_never_copy_messages():
    for detail in ("Authorization: Bearer secret\nACTIVITYINFO_API_TOKEN=secret",
                   "Redirect leaves allowed host/domain: https://secret.invalid/?token=secret"):
        assert "secret" not in sanitized_crawl_error(CrawlError(detail))
