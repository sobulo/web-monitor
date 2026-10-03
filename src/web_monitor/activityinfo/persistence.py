"""Application-facing persistence DTOs and operations, separate from the domain."""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from uuid import uuid4

from web_monitor.activityinfo.client import ActivityInfoClient, resource_id
from web_monitor.activityinfo.schema import (
    STATUS_VALUES, fields_by_code, inspect_schema, stable_id,
)
from web_monitor.models import MonitoredSite, Snapshot, SnapshotItem


class PersistenceError(ValueError):
    """Invalid or incomplete stored data; never treated as a valid observation."""


def new_record_id() -> str:
    return "w" + uuid4().hex[:24]


def timestamp(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PersistenceError("Timestamps must be timezone-aware datetimes")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def read_timestamp(value) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        timestamp(result)
        return result
    except (AttributeError, TypeError, ValueError):
        raise PersistenceError("Invalid stored timestamp") from None


def count(value) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise PersistenceError("Counts must be nonnegative integers")
    if not float(value).is_integer():
        raise PersistenceError("Counts must be nonnegative integers")
    return int(value)


def read_choice(value, options: dict[str, str]) -> str:
    # Record API encodes a single selection as an array of option IDs.
    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    if not isinstance(value, str) or value not in options:
        raise PersistenceError("Invalid stored selection")
    return options[value]


@dataclass(frozen=True)
class MonitoredSiteRecord:
    record_id: str
    name: str
    site: MonitoredSite
    schedule: str = "daily"
    active: bool = True


@dataclass(frozen=True)
class SnapshotRecord:
    record_id: str
    monitored_site_id: str
    created_at: datetime
    effective_from: datetime
    item_count: int


@dataclass(frozen=True)
class SnapshotItemRecord:
    record_id: str
    snapshot_id: str
    item: SnapshotItem


@dataclass(frozen=True)
class StoredSnapshot:
    record: SnapshotRecord
    snapshot: Snapshot
    items: tuple[SnapshotItemRecord, ...]


@dataclass(frozen=True)
class CrawlRecord:
    record_id: str
    monitored_site_id: str
    crawled_at: datetime
    status: str
    snapshot_id: str | None = None
    previous_snapshot_id: str | None = None
    added_count: int = 0
    removed_count: int = 0
    changed_count: int = 0
    pages_crawled: int = 0
    error_message: str | None = None


class ActivityInfoPersistence:
    """Use verified existing forms. Construction never creates or changes schema."""

    def __init__(self, client: ActivityInfoClient, schemas: dict | None = None):
        self.client = client
        self.schemas = inspect_schema(client) if schemas is None else schemas
        self.ids = {code: schema["id"] for code, schema in self.schemas.items()}
        self.fields = {code: fields_by_code(schema) for code, schema in self.schemas.items()}

    def _reference(self, form: str, record_id: str | None):
        return None if record_id is None else f"{self.ids[form]}:{resource_id(record_id)}"

    def _read_reference(self, form: str, value, *, optional=False):
        if value is None and optional:
            return None
        if isinstance(value, list) and len(value) == 1:
            value = value[0]
        prefix = self.ids[form] + ":"
        if not isinstance(value, str) or not value.startswith(prefix):
            raise PersistenceError(f"Invalid stored reference to {form}")
        return resource_id(value[len(prefix):])

    def _get(self, form: str, record_id: str) -> tuple[dict, dict]:
        raw = self.client.get_record(self.ids[form], record_id)
        if (
            not isinstance(raw, dict) or raw.get("formId") != self.ids[form]
            or raw.get("recordId") != record_id or not isinstance(raw.get("fields"), dict)
        ):
            raise PersistenceError(f"Malformed {form} record")
        # Actual field IDs can differ from generated defaults; application uses codes.
        values = {
            code: raw["fields"].get(field["id"], raw["fields"].get(code))
            for code, field in self.fields[form].items()
        }
        return raw, values

    def _write(self, form: str, record_id: str, values: dict, *, parent_id=None):
        for code, value in values.items():
            field = self.fields[form][code]
            if field["type"] in {"FREE_TEXT", "NARRATIVE"} and value is not None:
                limit = 1024 if field["type"] == "FREE_TEXT" else 65536
                if not isinstance(value, str) or len(value) > limit:
                    raise PersistenceError(f"Text exceeds field constraints: {code}")
        change = {
            "formId": self.ids[form], "recordId": resource_id(record_id),
            "deleted": False, "fields": values,
        }
        if parent_id is not None:
            change["parentRecordId"] = resource_id(parent_id)
        self.client.update_records([change])

    def list_monitored_sites(self) -> tuple[MonitoredSiteRecord, ...]:
        rows = self.client.query_rows(self.ids["monitored_site"], {"record_id": "_id"})
        ids = [resource_id(row.get("record_id")) for row in rows]
        if len(ids) != len(set(ids)):
            raise PersistenceError("Duplicate monitored-site IDs in query")
        return tuple(self.read_monitored_site(record_id) for record_id in sorted(ids))

    def read_monitored_site(self, record_id: str) -> MonitoredSiteRecord:
        _, values = self._get("monitored_site", record_id)
        for code in ("name", "root_url", "allowed_host", "schedule"):
            if not isinstance(values[code], str) or not values[code]:
                raise PersistenceError(f"Missing monitored-site value: {code}")
        boolean = {"true": "true", "false": "false"}
        return MonitoredSiteRecord(
            record_id, values["name"], MonitoredSite(
                values["root_url"], count(values["crawl_depth"]),
                values["allowed_host"],
                read_choice(values["include_subdomains"], boolean) == "true",
            ), values["schedule"], read_choice(values["active"], boolean) == "true",
        )

    def create_monitored_site(self, record: MonitoredSiteRecord) -> None:
        self._write("monitored_site", record.record_id, {
            "name": record.name, "root_url": record.site.root_url,
            "crawl_depth": record.site.crawl_depth, "allowed_host": record.site.allowed_host,
            "include_subdomains": [str(record.site.include_subdomains).lower()],
            "schedule": record.schedule, "active": [str(record.active).lower()],
        })

    def create_snapshot(self, record: SnapshotRecord) -> None:
        self._write("snapshot", record.record_id, {
            "monitored_site": self._reference("monitored_site", record.monitored_site_id),
            "created_at": timestamp(record.created_at),
            "effective_from": timestamp(record.effective_from),
            "item_count": count(record.item_count),
        })

    def read_snapshot(self, record_id: str) -> SnapshotRecord:
        _, values = self._get("snapshot", record_id)
        return SnapshotRecord(
            record_id, self._read_reference("monitored_site", values["monitored_site"]),
            read_timestamp(values["created_at"]), read_timestamp(values["effective_from"]),
            count(values["item_count"]),
        )

    def create_snapshot_item(self, record: SnapshotItemRecord) -> None:
        self._write("snapshot_item", record.record_id, {
            "canonical_url": record.item.canonical_url, "title": record.item.title,
            "content_hash": record.item.content_hash,
        }, parent_id=record.snapshot_id)

    def read_snapshot_item(self, record_id: str) -> SnapshotItemRecord:
        raw, values = self._get("snapshot_item", record_id)
        parent_id = raw.get("parentRecordId")
        if not parent_id:
            raise PersistenceError("Snapshot Item has no parentRecordId")
        if not isinstance(values["content_hash"], str) or not values["content_hash"]:
            raise PersistenceError("Snapshot Item is missing its hash")
        return SnapshotItemRecord(
            record_id, resource_id(parent_id), SnapshotItem(
                values["canonical_url"], values["title"], values["content_hash"],
            ),
        )

    def list_snapshot_items(self, snapshot_id: str) -> tuple[SnapshotItemRecord, ...]:
        resource_id(snapshot_id)
        rows = self.client.query_rows(
            self.ids["snapshot_item"], {"record_id": "_id"},
            filter_formula=f"parent._id == {json.dumps(snapshot_id)}",
        )
        records = tuple(self.read_snapshot_item(row["record_id"]) for row in rows)
        if any(record.snapshot_id != snapshot_id for record in records):
            raise PersistenceError("Snapshot Item parent does not match requested Snapshot")
        return tuple(sorted(records, key=lambda record: record.item.canonical_url))

    def persist_snapshot(
        self, snapshot: Snapshot, *, monitored_site_id: str,
        created_at: datetime, effective_from: datetime, record_id: str | None = None,
    ) -> StoredSnapshot:
        """Write explicit parent and children. No crawl comparison or lifecycle.

        Separate requests are not an atomic transaction. If interrupted, keep the
        supplied/returned parent ID for inspection; load_snapshot detects incompleteness.
        """
        record = SnapshotRecord(
            record_id or new_record_id(), monitored_site_id, created_at,
            effective_from, len(snapshot.items),
        )
        children = tuple(SnapshotItemRecord(
            stable_id(record.record_id, item.canonical_url), record.record_id, item,
        ) for item in snapshot.items)
        self.create_snapshot(record)
        for child in children:
            self.create_snapshot_item(child)
        return StoredSnapshot(record, snapshot, children)

    def load_snapshot(self, record_id: str) -> StoredSnapshot:
        record = self.read_snapshot(record_id)
        items = self.list_snapshot_items(record_id)
        if len(items) != record.item_count:
            raise PersistenceError("Incomplete Snapshot: child count differs from item_count")
        return StoredSnapshot(record, Snapshot(tuple(child.item for child in items)), items)

    def load_latest_snapshot(self, monitored_site_id: str) -> StoredSnapshot | None:
        """Reconstruct the latest distinct state for exactly one monitored site.

        Parse timestamps before ordering (text ordering mishandles offsets and
        fractional seconds). Never fall back past an incomplete/corrupt latest state.
        """
        resource_id(monitored_site_id)
        rows = self.client.query_rows(
            self.ids["snapshot"],
            {"record_id": "_id", "effective_from": "effective_from",
             "created_at": "created_at"},
            filter_formula=f"monitored_site._id == {json.dumps(monitored_site_id)}",
        )
        if not rows:
            return None
        candidates = [
            (read_timestamp(row.get("effective_from")),
             read_timestamp(row.get("created_at")), resource_id(row.get("record_id")))
            for row in rows
        ]
        if len({candidate[2] for candidate in candidates}) != len(candidates):
            raise PersistenceError("Duplicate Snapshot IDs in query")
        effective_from, created_at, record_id = max(candidates)
        stored = self.load_snapshot(record_id)
        if stored.record.monitored_site_id != monitored_site_id:
            raise PersistenceError("Latest Snapshot belongs to a different monitored site")
        if (stored.record.effective_from, stored.record.created_at) != (effective_from, created_at):
            raise PersistenceError("Snapshot metadata changed during prior-state lookup")
        return stored

    def create_crawl(self, record: CrawlRecord) -> None:
        if record.status not in STATUS_VALUES:
            raise PersistenceError("Invalid Crawl status")
        self._write("crawl", record.record_id, {
            "monitored_site": self._reference("monitored_site", record.monitored_site_id),
            "crawled_at": timestamp(record.crawled_at),
            "status": [record.status.replace("_", "")],
            "snapshot": self._reference("snapshot", record.snapshot_id),
            "previous_snapshot": self._reference("snapshot", record.previous_snapshot_id),
            "added_count": count(record.added_count),
            "removed_count": count(record.removed_count),
            "changed_count": count(record.changed_count),
            "pages_crawled": count(record.pages_crawled),
            "error_message": record.error_message,
        })

    def read_crawl(self, record_id: str) -> CrawlRecord:
        _, values = self._get("crawl", record_id)
        return CrawlRecord(
            record_id, self._read_reference("monitored_site", values["monitored_site"]),
            read_timestamp(values["crawled_at"]),
            read_choice(values["status"], {s.replace("_", ""): s for s in STATUS_VALUES}),
            self._read_reference("snapshot", values["snapshot"], optional=True),
            self._read_reference("snapshot", values["previous_snapshot"], optional=True),
            count(values["added_count"]), count(values["removed_count"]),
            count(values["changed_count"]), count(values["pages_crawled"]),
            values["error_message"],
        )
