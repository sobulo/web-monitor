"""One-site monitoring lifecycle, independent of HTTP and Flask routing."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

import requests

from web_monitor import diff
from web_monitor.activityinfo.persistence import (
    CrawlRecord, MonitoredSiteRecord, StoredSnapshot, new_record_id,
)
from web_monitor.crawler import CrawlError, crawl_site
from web_monitor.models import MonitoredSite, Snapshot, SnapshotDiff


class MonitoringStore(Protocol):
    """Only the persistence operations needed by one monitoring cycle."""

    def read_monitored_site(self, record_id: str) -> MonitoredSiteRecord: ...

    def load_latest_snapshot(self, monitored_site_id: str) -> StoredSnapshot | None: ...

    def persist_snapshot(
        self, snapshot: Snapshot, *, monitored_site_id: str,
        created_at: datetime, effective_from: datetime, record_id: str | None = None,
    ) -> StoredSnapshot: ...

    def create_crawl(self, record: CrawlRecord) -> None: ...


@dataclass(frozen=True)
class MonitoringResult:
    """Persisted attempt and its domain diff (absent if crawling failed)."""

    site: MonitoredSiteRecord
    crawl: CrawlRecord
    changes: SnapshotDiff | None

    @property
    def snapshot_created(self) -> bool:
        return self.crawl.status in {"initial", "changed"}


def sanitized_crawl_error(error: CrawlError) -> str:
    """Describe the failure using allowlisted categories, never raw exception text.

    URLs, request headers, response bodies, and configuration can contain secrets.
    Only a valid HTTP status number is carried across from the underlying exception.
    """
    cause = error.__cause__
    if isinstance(cause, requests.Timeout):
        return "Crawl timed out before a complete snapshot was available."
    if isinstance(cause, requests.exceptions.SSLError):
        return "Crawl failed because TLS verification failed."
    if isinstance(cause, requests.ConnectionError):
        return "Crawl failed because a network connection could not be established."
    if isinstance(cause, requests.HTTPError) and cause.response is not None:
        status = cause.response.status_code
        if isinstance(status, int) and 100 <= status <= 599:
            return f"Crawl failed because a required page returned HTTP {status}."
    for prefix, message in (
        ("Redirect leaves allowed host/domain:", "A redirect left the configured host/domain."),
        ("Redirect loop or limit exceeded:", "A redirect loop or redirect limit stopped the crawl."),
        ("Redirect without Location:", "A redirect was missing its destination."),
        ("Invalid redirect from", "A redirect had an invalid destination."),
        ("Expected HTML content:", "A required page did not return HTML content."),
        ("Expected HTTP 200:", "A required page did not return HTTP 200."),
    ):
        if str(error).startswith(prefix):
            return message
    return "Crawl failed; no complete snapshot was produced."


class MonitoringService:
    """Run serial attempts. Persistence errors propagate, never masquerade as crawls."""

    def __init__(
        self, store: MonitoringStore, *,
        crawler: Callable[[MonitoredSite], Snapshot] = crawl_site,
        clock: Callable[[], datetime] | None = None,
    ):
        self.store = store
        self.crawler = crawler
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def run(self, monitored_site_id: str) -> MonitoringResult:
        site = self.store.read_monitored_site(monitored_site_id)
        # Resolve persisted state before crawling so malformed history cannot be
        # mistaken for an initial observation or overwritten with a new baseline.
        previous = self.store.load_latest_snapshot(site.record_id)
        previous_id = previous.record.record_id if previous is not None else None
        crawled_at = self.clock()
        crawl_id = new_record_id()
        try:
            observed = self.crawler(site.site)
        except CrawlError as error:
            crawl = CrawlRecord(
                crawl_id, site.record_id, crawled_at, "error",
                previous_snapshot_id=previous_id,
                error_message=sanitized_crawl_error(error),
            )
            self.store.create_crawl(crawl)
            return MonitoringResult(site, crawl, None)

        changes = (
            diff.compare_snapshots(previous.snapshot, observed)
            if previous is not None else SnapshotDiff()
        )
        changed = bool(changes.added or changes.removed or changes.changed)
        status = "initial" if previous is None else "changed" if changed else "no_change"
        snapshot_id = previous_id
        if status in {"initial", "changed"}:
            saved = self.store.persist_snapshot(
                observed, monitored_site_id=site.record_id,
                created_at=self.clock(), effective_from=crawled_at,
            )
            snapshot_id = saved.record.record_id
        crawl = CrawlRecord(
            crawl_id, site.record_id, crawled_at, status,
            snapshot_id=snapshot_id, previous_snapshot_id=previous_id,
            added_count=len(changes.added), removed_count=len(changes.removed),
            changed_count=len(changes.changed), pages_crawled=len(observed.items),
        )
        self.store.create_crawl(crawl)
        return MonitoringResult(site, crawl, changes)
