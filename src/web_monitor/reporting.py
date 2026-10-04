"""Provider-independent operational metrics over persisted monitoring attempts."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol, Sequence


class ReportingIntegrityError(ValueError):
    """Stored monitoring data cannot be counted reliably."""


class ReportingSite(Protocol):
    record_id: str
    name: str


class ReportingCrawl(Protocol):
    record_id: str
    monitored_site_id: str
    crawled_at: datetime
    status: str
    added_count: int
    removed_count: int
    changed_count: int


class ReportingStore(Protocol):
    def list_monitored_sites(self) -> Sequence[ReportingSite]: ...
    def list_crawls(self, monitored_site_id: str) -> Sequence[ReportingCrawl]: ...


@dataclass(frozen=True)
class ReportingMetrics:
    total_crawls: int = 0
    successful_crawls: int = 0
    error_crawls: int = 0
    changed_crawls: int = 0
    no_change_crawls: int = 0
    initial_crawls: int = 0
    last_crawl: datetime | None = None
    last_successful_crawl: datetime | None = None
    last_change: datetime | None = None
    total_added: int = 0
    total_removed: int = 0
    total_changed: int = 0


@dataclass(frozen=True)
class SiteReportingMetrics:
    site_id: str
    site_name: str
    metrics: ReportingMetrics


@dataclass(frozen=True)
class ReportingOverview:
    sites: tuple[SiteReportingMetrics, ...]
    totals: ReportingMetrics

    @property
    def monitored_sites(self) -> int:
        return len(self.sites)


def summarize(crawls: Sequence[ReportingCrawl]) -> ReportingMetrics:
    counts = dict.fromkeys(('initial', 'no_change', 'changed', 'error'), 0)
    timestamps = {status: [] for status in counts}
    added = removed = changed = 0
    for crawl in crawls:
        if crawl.status not in counts:
            raise ReportingIntegrityError('Unknown Crawl status')
        when = crawl.crawled_at
        if not isinstance(when, datetime) or when.tzinfo is None or when.utcoffset() is None:
            raise ReportingIntegrityError('Crawl timestamps must be timezone-aware')
        counts[crawl.status] += 1
        timestamps[crawl.status].append(when.astimezone(timezone.utc))
        values = (crawl.added_count, crawl.removed_count, crawl.changed_count)
        if any(type(value) is not int or value < 0 for value in values):
            raise ReportingIntegrityError('Crawl counts must be nonnegative integers')
        added += crawl.added_count
        removed += crawl.removed_count
        changed += crawl.changed_count
    successful = timestamps['initial'] + timestamps['no_change'] + timestamps['changed']
    return ReportingMetrics(
        sum(counts.values()), len(successful), counts['error'], counts['changed'],
        counts['no_change'], counts['initial'],
        max(successful + timestamps['error'], default=None), max(successful, default=None),
        max(timestamps['changed'], default=None), added, removed, changed,
    )


class ReportingService:
    """Count attempts, not distinct states. Initial establishes a baseline."""

    def __init__(self, store: ReportingStore):
        self.store = store

    def overview(self) -> ReportingOverview:
        sites = self.store.list_monitored_sites()
        if len({site.record_id for site in sites}) != len(sites):
            raise ReportingIntegrityError('Duplicate Monitored Site identifiers')
        all_crawls = []
        results = []
        seen = set()
        for site in sorted(sites, key=lambda value: (value.name.casefold(), value.record_id)):
            crawls = self.store.list_crawls(site.record_id)
            for crawl in crawls:
                if crawl.monitored_site_id != site.record_id or crawl.record_id in seen:
                    raise ReportingIntegrityError('Duplicate or cross-site Crawl')
                seen.add(crawl.record_id)
            results.append(SiteReportingMetrics(site.record_id, site.name, summarize(crawls)))
            all_crawls.extend(crawls)
        return ReportingOverview(tuple(results), summarize(all_crawls))
