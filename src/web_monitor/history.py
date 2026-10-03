"""Read-only historical queries. Calendar dates always mean UTC dates."""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Protocol

from web_monitor.activityinfo.persistence import CrawlRecord, StoredSnapshot
from web_monitor.diff import compare_snapshots
from web_monitor.models import Snapshot, SnapshotDiff


class HistoryIntegrityError(ValueError):
    """History cannot be interpreted without guessing or ignoring corrupt data."""


class HistoryStore(Protocol):
    def list_crawls(self, monitored_site_id: str) -> tuple[CrawlRecord, ...]: ...
    def load_snapshot(self, record_id: str) -> StoredSnapshot: ...


@dataclass(frozen=True)
class CrawlHistoryEntry:
    reference: str
    crawled_at: datetime
    status: str
    snapshot_reference: str | None
    previous_snapshot_reference: str | None
    added_count: int
    removed_count: int
    changed_count: int
    pages_crawled: int
    error_message: str | None


@dataclass(frozen=True)
class HistoricalState:
    requested_date: date
    crawl: CrawlHistoryEntry | None = None
    snapshot: Snapshot | None = None

    @property
    def available(self) -> bool:
        return self.snapshot is not None


class DiffCounts:
    @property
    def added_count(self) -> int:
        return len(self.diff.added)

    @property
    def removed_count(self) -> int:
        return len(self.diff.removed)

    @property
    def changed_count(self) -> int:
        return len(self.diff.changed)


@dataclass(frozen=True)
class ChangeTransition(DiffCounts):
    crawl: CrawlHistoryEntry
    previous_snapshot: Snapshot
    snapshot: Snapshot
    diff: SnapshotDiff


@dataclass(frozen=True)
class HistoricalDiff:
    start: HistoricalState
    end: HistoricalState
    diff: SnapshotDiff | None

    @property
    def missing_endpoints(self) -> tuple[str, ...]:
        return tuple(name for name in ("start", "end") if not getattr(self, name).available)

    @property
    def counts(self) -> tuple[int, int, int] | None:
        if self.diff is None:
            return None
        return len(self.diff.added), len(self.diff.removed), len(self.diff.changed)


def validate_date(value: date) -> None:
    if type(value) is not date:
        raise ValueError("Expected a calendar date (UTC), not a datetime")


def entry(record: CrawlRecord) -> CrawlHistoryEntry:
    return CrawlHistoryEntry(
        record.record_id, record.crawled_at.astimezone(timezone.utc), record.status,
        record.snapshot_id, record.previous_snapshot_id, record.added_count,
        record.removed_count, record.changed_count, record.pages_crawled, record.error_message,
    )


class HistoryService:
    def __init__(self, store: HistoryStore):
        self.store = store

    def _crawls(self, site: str) -> tuple[CrawlRecord, ...]:
        records = self.store.list_crawls(site)
        if len({record.record_id for record in records}) != len(records):
            raise HistoryIntegrityError("Duplicate Crawl references")
        for record in records:
            if record.monitored_site_id != site:
                raise HistoryIntegrityError("Crawl belongs to another site")
            if record.crawled_at.tzinfo is None or record.crawled_at.utcoffset() is None:
                raise HistoryIntegrityError("Crawl timestamp must be timezone-aware")
            if record.status not in {"initial", "no_change", "changed", "error"}:
                raise HistoryIntegrityError("Unknown Crawl status")
        return tuple(sorted(records, key=lambda record: (record.crawled_at, record.record_id)))

    def _snapshot(self, site: str, reference: str | None) -> Snapshot:
        if reference is None:
            raise HistoryIntegrityError("Successful Crawl is missing a Snapshot reference")
        stored = self.store.load_snapshot(reference)
        if stored.record.monitored_site_id != site or stored.record.record_id != reference:
            raise HistoryIntegrityError("Snapshot reference resolves to the wrong state or site")
        return stored.snapshot

    def _state(self, site: str, day: date, records: tuple[CrawlRecord, ...]) -> HistoricalState:
        candidates = [record for record in records if record.status != "error"
                      and record.crawled_at.astimezone(timezone.utc).date() <= day]
        if not candidates:
            return HistoricalState(day)
        chosen = candidates[-1]
        tied = [record for record in candidates if record.crawled_at == chosen.crawled_at]
        if len({record.snapshot_id for record in tied}) != 1:
            raise HistoryIntegrityError("Ambiguous state: equal Crawl timestamps reference different Snapshots")
        return HistoricalState(day, entry(chosen), self._snapshot(site, chosen.snapshot_id))

    def state_on_date(self, site: str, day: date) -> HistoricalState:
        validate_date(day)
        return self._state(site, day, self._crawls(site))

    def _transition(self, site: str, record: CrawlRecord) -> ChangeTransition:
        old = self._snapshot(site, record.previous_snapshot_id)
        new = self._snapshot(site, record.snapshot_id)
        diff = compare_snapshots(old, new)
        counts = len(diff.added), len(diff.removed), len(diff.changed)
        if not any(counts) or counts != (record.added_count, record.removed_count, record.changed_count):
            raise HistoryIntegrityError("Changed Crawl counts disagree with reconstructed Snapshot diff")
        return ChangeTransition(entry(record), old, new, diff)

    def changes_on_date(self, site: str, day: date) -> tuple[ChangeTransition, ...]:
        validate_date(day)
        records = [record for record in self._crawls(site) if record.status == "changed"
                   and record.crawled_at.astimezone(timezone.utc).date() == day]
        self._check_transition_ties(records)
        return tuple(self._transition(site, record) for record in records)

    @staticmethod
    def _check_transition_ties(records) -> None:
        if len({record.crawled_at for record in records}) != len(records):
            raise HistoryIntegrityError("Ambiguous transition order: equal changed Crawl timestamps")

    def changes_between_dates(self, site: str, start_date: date, end_date: date) -> HistoricalDiff:
        validate_date(start_date)
        validate_date(end_date)
        if end_date < start_date:
            raise ValueError("end_date must not precede start_date")
        records = self._crawls(site)
        start = self._state(site, start_date, records)
        end = self._state(site, end_date, records)
        diff = compare_snapshots(start.snapshot, end.snapshot) if start.available and end.available else None
        return HistoricalDiff(start, end, diff)

    def latest_change(self, site: str) -> ChangeTransition | None:
        records = [record for record in self._crawls(site) if record.status == "changed"]
        if not records:
            return None
        latest = records[-1]
        self._check_transition_ties([record for record in records if record.crawled_at == latest.crawled_at])
        return self._transition(site, latest)

    def recent_crawls(self, site: str, limit: int = 20) -> tuple[CrawlHistoryEntry, ...]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer from 1 to 1000")
        return tuple(entry(record) for record in reversed(self._crawls(site)))[:limit]


def main(argv: list[str] | None = None) -> int:
    from web_monitor.history_command import main as command_main
    return command_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
