"""Developer text interface for the historical query service."""

import argparse
from datetime import date
from pathlib import Path
import sys

from web_monitor.activityinfo.client import ActivityInfoClient, ActivityInfoError
from web_monitor.activityinfo.config import ActivityInfoConfig
from web_monitor.activityinfo.persistence import ActivityInfoPersistence
from web_monitor.history import HistoryService


def show_diff(diff) -> None:
    print(f"Added {len(diff.added)}, removed {len(diff.removed)}, changed {len(diff.changed)}")
    for label, items in (("+", diff.added), ("-", diff.removed)):
        for item in items:
            print(f"  {label} {item.canonical_url}")
    for item in diff.changed:
        print(f"  ~ {item.new.canonical_url}")


def show_transition(result) -> None:
    print(f"{result.crawl.crawled_at.isoformat()} "
          f"{result.crawl.previous_snapshot_reference} -> {result.crawl.snapshot_reference}")
    show_diff(result.diff)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Query monitoring history using UTC calendar dates")
    parser.add_argument("--env-file", type=Path, default=Path.cwd() / ".env")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("state", "changes-on", "changes-between", "latest-change", "recent"):
        command = commands.add_parser(name)
        command.add_argument("site", help="Monitored Site record ID")
        if name in {"state", "changes-on", "changes-between"}:
            command.add_argument("date", type=date.fromisoformat)
        if name == "changes-between":
            command.add_argument("end_date", type=date.fromisoformat)
        if name == "recent":
            command.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)
    try:
        config = ActivityInfoConfig.from_environment(env_file=args.env_file)
        with ActivityInfoClient(config) as client:
            store = ActivityInfoPersistence(client)
            store.read_monitored_site(args.site)
            service = HistoryService(store)
            if args.command == "state":
                result = service.state_on_date(args.site, args.date)
                if not result.available:
                    print("No state available on or before this UTC date.")
                else:
                    print(f"Snapshot {result.crawl.snapshot_reference}: {len(result.snapshot.items)} items; "
                          f"observed {result.crawl.crawled_at.isoformat()}")
            elif args.command == "changes-on":
                results = service.changes_on_date(args.site, args.date)
                if not results:
                    print("No changes on this UTC date.")
                for result in results:
                    show_transition(result)
            elif args.command == "changes-between":
                result = service.changes_between_dates(args.site, args.date, args.end_date)
                if result.missing_endpoints:
                    print("No state available for: " + ", ".join(result.missing_endpoints))
                else:
                    print(f"{result.start.crawl.snapshot_reference} -> {result.end.crawl.snapshot_reference}")
                    show_diff(result.diff)
            elif args.command == "latest-change":
                result = service.latest_change(args.site)
                if result is None:
                    print("No change recorded yet.")
                else:
                    show_transition(result)
            else:
                results = service.recent_crawls(args.site, args.limit)
                if not results:
                    print("No Crawls recorded.")
                for crawl in results:
                    print(f"{crawl.crawled_at.isoformat()} {crawl.status} "
                          f"pages={crawl.pages_crawled} +{crawl.added_count} "
                          f"-{crawl.removed_count} ~{crawl.changed_count} "
                          f"snapshot={crawl.snapshot_reference or '-'}")
        return 0
    except ActivityInfoError as error:
        print(f"STOP: ActivityInfo request failed (HTTP {error.status or 'unavailable'}).", file=sys.stderr)
        return 2
    except ValueError:
        # Stored values and remote error bodies can contain sensitive text.
        print("STOP: invalid query, configuration, or history integrity; no result reported.", file=sys.stderr)
        return 2
