"""Explicit, rerunnable schema bootstrap inside one existing database."""

import argparse
from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
import sys

from web_monitor.activityinfo.client import ActivityInfoClient, ActivityInfoError
from web_monitor.activityinfo.config import ActivityInfoConfig, ConfigurationError
from web_monitor.activityinfo.persistence import (
    ActivityInfoPersistence, MonitoredSiteRecord, PersistenceError,
)
from web_monitor.activityinfo.schema import (
    SchemaError, expected_schemas, inspect_schema, stable_id, verify_schema,
)
from web_monitor.models import MonitoredSite

DEMO_SITES = (
    ("Python.org", "https://www.python.org/"),
    ("Python Insider", "https://blog.python.org/"),
    ("IMDb Top 250", "https://www.imdb.com/chart/top/"),
    ("IANA Reserved Domains", "https://www.iana.org/domains/reserved"),
)


@dataclass(frozen=True)
class BootstrapResult:
    created_forms: tuple[str, ...]
    added_fields: int
    seeded_sites: int


def seed_sites(store: ActivityInfoPersistence) -> int:
    """Seed only absent URLs. Never overwrite existing site configuration."""
    existing = store.list_monitored_sites()
    planned = []
    for name, url in DEMO_SITES:
        canonical = MonitoredSite(url)
        matches = [record for record in existing if record.site.root_url == canonical.root_url]
        if len(matches) > 1:
            raise PersistenceError(f"Duplicate demo monitored site: {name}")
        if matches:
            continue
        record_id = stable_id(store.client.database_id, "seed", canonical.root_url)
        if any(record.record_id == record_id or record.name == name for record in existing):
            raise PersistenceError(f"Conflicting demo site identity: {name}")
        planned.append(MonitoredSiteRecord(record_id, name, canonical))
    for record in planned:
        store.create_monitored_site(record)
    # Verify actual records, not just successful write responses.
    refreshed = store.list_monitored_sites()
    for name, url in DEMO_SITES:
        if sum(record.site.root_url == url for record in refreshed) != 1:
            raise PersistenceError(f"Demo site read-back verification failed: {name}")
    return len(planned)


def bootstrap(client: ActivityInfoClient) -> BootstrapResult:
    expected = expected_schemas(client.database_id)
    actual = inspect_schema(client, allow_missing=True)
    initially_present = set(actual)
    added_fields = 0
    # Dependency order is significant: the Snapshot field creates the subform;
    # only then can its schema be read and populated. No standalone child POST.
    for code in ("monitored_site", "snapshot", "snapshot_item", "crawl"):
        wanted = expected[code]
        found = actual.get(code)
        if found is None and code != "snapshot_item":
            client.add_form(wanted)
            found = client.get_schema(wanted["id"])
        elif found is None:
            found = client.get_schema(wanted["id"])
        missing = verify_schema(found, wanted, allow_missing=True)
        if missing:
            updated = deepcopy(found)
            updated["elements"].extend(missing)
            client.update_schema(updated)
            added_fields += len(missing)
        actual[code] = client.get_schema(wanted["id"])
        verify_schema(actual[code], wanted)
    # Inspect the database resource tree as well as all four form schemas.
    verified = inspect_schema(client)
    store = ActivityInfoPersistence(client, verified)
    seeded = seed_sites(store)
    return BootstrapResult(tuple(code for code in expected if code not in initially_present), added_fields, seeded)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path.cwd() / ".env")
    args = parser.parse_args()
    try:
        config = ActivityInfoConfig.from_environment(env_file=args.env_file)
        with ActivityInfoClient(config) as client:
            result = bootstrap(client)
        print(json.dumps({
            "created_forms": result.created_forms, "added_fields": result.added_fields,
            "seeded_sites": result.seeded_sites, "schema_verified": True,
        }))
        return 0
    except ActivityInfoError as error:
        print(f"STOP: {error}", file=sys.stderr)
        if error.request_body is not None:
            print("Request body: " + json.dumps(error.request_body), file=sys.stderr)
        print("No workaround attempted. Inspect the official schema API before proceeding.", file=sys.stderr)
        return 1
    except (ConfigurationError, SchemaError, PersistenceError, ValueError) as error:
        print(f"STOP: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
