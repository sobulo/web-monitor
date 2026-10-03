"""Explicit real-development checkpoint; never collected by normal pytest."""

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from web_monitor.activityinfo.bootstrap import DEMO_SITES, bootstrap
from web_monitor.activityinfo.client import ActivityInfoClient, ActivityInfoError
from web_monitor.activityinfo.config import ActivityInfoConfig
from web_monitor.activityinfo.persistence import (
    ActivityInfoPersistence, CrawlRecord, PersistenceError, new_record_id,
)
from web_monitor.activityinfo.schema import fields_by_code, inspect_schema
from web_monitor.models import Snapshot, SnapshotItem
from web_monitor.normalization import normalize_html


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PersistenceError(message)


def verify(client: ActivityInfoClient) -> dict:
    """Bootstrap twice, check invariants, round-trip synthetic records, clean up."""
    first = bootstrap(client)
    before = inspect_schema(client)
    store = ActivityInfoPersistence(client, before)
    sites_before = store.list_monitored_sites()
    second = bootstrap(client)
    after = inspect_schema(client)
    sites_after = store.list_monitored_sites()
    require(before == after, "Rerun changed the verified schemas")
    require(sites_before == sites_after, "Rerun changed the monitored-site records")
    require(not second.created_forms and not second.added_fields and not second.seeded_sites,
            "Bootstrap rerun was not a no-op")
    for name, url in DEMO_SITES:
        matches = [record for record in sites_after if record.site.root_url == url]
        require(len(matches) == 1, f"Expected exactly one demo site: {name}")
        require(matches[0].site.crawl_depth == 0 and matches[0].schedule == "daily"
                and matches[0].active and not matches[0].site.include_subdomains,
                f"Unexpected demo configuration: {name}")

    # These IDs and synthetic child URLs identify only this verification run.
    snapshot_id = new_record_id()
    crawl_id = new_record_id()
    now = datetime.now(timezone.utc)
    snapshot = Snapshot(tuple(
        SnapshotItem(
            f"https://example.invalid/development-test/{snapshot_id}/{index}",
            f"DEVELOPMENT/TEST Stage 2 item {index}",
            normalize_html(f"<p>Synthetic development item {index}</p>").content_hash,
        ) for index in (1, 2)
    ))
    # Predeclare every generated child ID so interrupted writes remain identifiable.
    from web_monitor.activityinfo.schema import stable_id
    child_ids = [stable_id(snapshot_id, item.canonical_url) for item in snapshot.items]
    generated = {"snapshot": snapshot_id, "snapshot_items": child_ids, "crawl": crawl_id}
    print("DEVELOPMENT/TEST record IDs: " + json.dumps(generated), file=sys.stderr)
    saved = store.persist_snapshot(
        snapshot, monitored_site_id=sites_after[0].record_id,
        created_at=now, effective_from=now, record_id=snapshot_id,
    )
    require(store.load_snapshot(snapshot_id) == saved, "Snapshot/child round trip differs")
    crawl = CrawlRecord(
        crawl_id, sites_after[0].record_id, now, "initial", snapshot_id,
        added_count=2, pages_crawled=2,
        error_message="DEVELOPMENT/TEST Stage 2 persistence check; no crawl performed.",
    )
    store.create_crawl(crawl)
    require(store.read_crawl(crawl_id) == crawl, "Crawl round trip differs")

    # Delete only the exact generated records, in reference-safe order.
    cleanup = [("crawl", crawl_id)] + [("snapshot_item", item_id) for item_id in child_ids] + [("snapshot", snapshot_id)]
    for form, record_id in cleanup:
        client.update_records([{
            "formId": store.ids[form], "recordId": record_id,
            "deleted": True, "fields": {},
        }])
    for form, record_id in cleanup:
        rows = client.query_rows(
            store.ids[form], {"record_id": "_id"},
            filter_formula=f"_id == {json.dumps(record_id)}",
        )
        require(not rows, f"Development record remains after cleanup: {record_id}")
    return {
        "verified_at": now.isoformat(), "database_id": client.database_id,
        "first_bootstrap": asdict(first), "second_bootstrap": asdict(second),
        "forms": {code: {"id": schema["id"], "field_codes": sorted(fields_by_code(schema))}
                  for code, schema in after.items()},
        "snapshot_item_parent_form_id": after["snapshot_item"]["parentFormId"],
        "demo_sites": [{"name": record.name, "root_url": record.site.root_url,
                        "record_id": record.record_id} for record in sites_after],
        "snapshot_round_trip": True, "child_parent_links_verified": True,
        "crawl_round_trip": True, "development_records": generated,
        "development_records_deleted": True, "live_crawling_performed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path.cwd() / ".env")
    args = parser.parse_args()
    try:
        config = ActivityInfoConfig.from_environment(env_file=args.env_file)
        with ActivityInfoClient(config) as client:
            result = verify(client)
        print(json.dumps(result, indent=2))
        return 0
    except ActivityInfoError as error:
        print(f"STOP: {error}", file=sys.stderr)
        if error.request_body is not None:
            print("Request body: " + json.dumps(error.request_body), file=sys.stderr)
        return 1
    except ValueError as error:
        print(f"STOP: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
