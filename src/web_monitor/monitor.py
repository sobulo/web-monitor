"""Developer command: manually monitor one existing ActivityInfo site record."""

import argparse
import json
from pathlib import Path
import sys

from web_monitor.activityinfo.client import ActivityInfoClient, ActivityInfoError
from web_monitor.activityinfo.config import ActivityInfoConfig
from web_monitor.activityinfo.persistence import ActivityInfoPersistence
from web_monitor.monitoring import MonitoringResult, MonitoringService


def result_summary(result: MonitoringResult) -> dict:
    crawl = result.crawl
    return {
        "site": result.site.name, "site_id": result.site.record_id,
        "status": crawl.status, "crawl_id": crawl.record_id,
        "pages_crawled": crawl.pages_crawled, "snapshot_id": crawl.snapshot_id,
        "previous_snapshot_id": crawl.previous_snapshot_id,
        "added": crawl.added_count, "removed": crawl.removed_count,
        "changed": crawl.changed_count, "error_message": crawl.error_message,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("site_id", help="Existing ActivityInfo Monitored Site record ID")
    parser.add_argument("--env-file", type=Path, default=Path.cwd() / ".env")
    args = parser.parse_args(argv)
    try:
        config = ActivityInfoConfig.from_environment(env_file=args.env_file)
        with ActivityInfoClient(config) as client:
            result = MonitoringService(ActivityInfoPersistence(client)).run(args.site_id)
        print(json.dumps(result_summary(result)))
        return 1 if result.crawl.status == "error" else 0
    except ActivityInfoError as error:
        # Do not print the response body, request data, or configuration.
        print(f"STOP: ActivityInfo {error.method} {error.path} failed "
              f"(HTTP {error.status or 'unavailable'}).", file=sys.stderr)
        return 2
    except ValueError:
        print("STOP: invalid configuration or persisted state; no success reported.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
