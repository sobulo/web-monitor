"""Explicit development checkpoint: two real CLI runs and targeted cleanup."""

import argparse
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sys

from web_monitor.activityinfo.client import ActivityInfoClient, ActivityInfoError
from web_monitor.activityinfo.config import ActivityInfoConfig
from web_monitor.activityinfo.persistence import ActivityInfoPersistence, PersistenceError
from web_monitor.diff import compare_snapshots
from web_monitor.monitor import main as monitor_main


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PersistenceError(message)


def verify(site_id: str, env_file: Path) -> dict:
    config = ActivityInfoConfig.from_environment(env_file=env_file)
    with ActivityInfoClient(config) as client:
        store = ActivityInfoPersistence(client)
        sites_before = store.list_monitored_sites()
        site = store.read_monitored_site(site_id)
        require(store.load_latest_snapshot(site_id) is None,
                "Choose a development site with no existing Snapshot for the initial checkpoint")
        runs = []
        snapshots = {}
        completed = False
        try:
            for index in range(2):
                output = StringIO()
                with redirect_stdout(output):
                    exit_code = monitor_main([site_id, "--env-file", str(env_file)])
                require(bool(output.getvalue()), "Manual monitoring command did not return a persisted result")
                result = json.loads(output.getvalue())
                crawl = store.read_crawl(result["crawl_id"])
                require(crawl.monitored_site_id == site_id, "Unexpected site on Crawl")
                runs.append(crawl)
                print("DEVELOPMENT/TEST lifecycle result: " + json.dumps(result), file=sys.stderr)
                require(exit_code == 0, "The real crawl failed; inspect its sanitized error")
                saved = store.load_snapshot(crawl.snapshot_id)
                require(saved.record.monitored_site_id == site_id, "Unexpected site on Snapshot")
                snapshots.setdefault(crawl.snapshot_id, saved)
                require(crawl.pages_crawled == saved.record.item_count == len(saved.items),
                        "Snapshot/child/page counts disagree")
                require(saved.record.created_at >= saved.record.effective_from,
                        "Invalid Snapshot timestamp ordering")
                if index == 0:
                    require(crawl.status == "initial" and crawl.previous_snapshot_id is None,
                            "First real run did not establish an initial baseline")
                    require((crawl.added_count, crawl.removed_count, crawl.changed_count) == (0, 0, 0),
                            "Initial baseline counts must be zero")
                    require(saved.record.effective_from == crawl.crawled_at,
                            "Initial Snapshot effective time does not match its Crawl")
                else:
                    first = snapshots[runs[0].snapshot_id]
                    changes = compare_snapshots(first.snapshot, saved.snapshot)
                    expected_counts = (len(changes.added), len(changes.removed), len(changes.changed))
                    require(crawl.previous_snapshot_id == first.record.record_id,
                            "Second Crawl did not link to the prior Snapshot")
                    require((crawl.added_count, crawl.removed_count, crawl.changed_count) == expected_counts,
                            "Second Crawl diff counts disagree with stored states")
                    if crawl.status == "no_change":
                        require(crawl.snapshot_id == runs[0].snapshot_id and saved == first,
                                "Unchanged crawl modified or duplicated its Snapshot")
                    else:
                        require(crawl.status == "changed" and any(expected_counts)
                                and crawl.snapshot_id != runs[0].snapshot_id,
                                "Changed crawl did not create a complete distinct state")
            rows = client.query_rows(store.ids["snapshot"], {"record_id": "_id"},
                                     filter_formula=f"monitored_site._id == {json.dumps(site_id)}")
            require({row["record_id"] for row in rows} == set(snapshots),
                    "Unexpected duplicate Snapshot records")
            require(store.list_monitored_sites() == sites_before, "Monitored Site configuration changed")
            completed = True
        finally:
            # These IDs were returned and read back from our two CLI invocations.
            # Never delete the Monitored Site or any preexisting data/schema.
            cleanup = [("crawl", crawl.record_id) for crawl in runs]
            cleanup += [("snapshot_item", child.record_id)
                        for saved in snapshots.values() for child in saved.items]
            cleanup += [("snapshot", snapshot_id) for snapshot_id in snapshots]
            for form, record_id in cleanup:
                client.update_records([{
                    "formId": store.ids[form], "recordId": record_id,
                    "deleted": True, "fields": {},
                }])
            for form, record_id in cleanup:
                rows = client.query_rows(store.ids[form], {"record_id": "_id"},
                                         filter_formula=f"_id == {json.dumps(record_id)}")
                require(not rows, "A generated development record remains after cleanup")
            print(f"Cleaned up {len(cleanup)} identified development records.", file=sys.stderr)
        require(completed, "Lifecycle checkpoint did not finish")
        require(store.list_monitored_sites() == sites_before, "Cleanup altered monitored sites")
        return {
            "site": site.name, "site_id": site_id,
            "statuses": [crawl.status for crawl in runs],
            "crawl_ids": [crawl.record_id for crawl in runs],
            "distinct_snapshots": len(snapshots),
            "snapshot_items": sum(len(saved.items) for saved in snapshots.values()),
            "real_crawling": True, "cli_verified": True,
            "records_read_back": True, "test_records_deleted": True,
            "monitored_sites_unchanged": True,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("site_id")
    parser.add_argument("--env-file", type=Path, default=Path.cwd() / ".env")
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.site_id, args.env_file), indent=2))
        return 0
    except ActivityInfoError as error:
        print(f"STOP: ActivityInfo {error.method} {error.path}: HTTP {error.status or 'unavailable'}", file=sys.stderr)
        return 1
    except ValueError as error:
        print(f"STOP: {type(error).__name__}; lifecycle checkpoint incomplete.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
