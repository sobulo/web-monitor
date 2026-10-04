"""Script exact HTTP exchanges for bootstrap; real acceptance is a separate gate."""

from copy import deepcopy

import pytest

from web_monitor.activityinfo.bootstrap import DEMO_SITES, bootstrap
from web_monitor.activityinfo.client import ActivityInfoError
from web_monitor.activityinfo.schema import SchemaError, stable_id


def seed_records(schemas, record_response):
    records = []
    for name, url in DEMO_SITES:
        from web_monitor.models import MonitoredSite
        site = MonitoredSite(url)
        record_id = stable_id("testdatabase", "seed", url)
        records.append(record_response("monitored_site", record_id, {
            "name": name, "root_url": url, "crawl_depth": 0,
            "allowed_host": site.allowed_host, "include_subdomains": ["false"],
            "schedule": "daily", "active": ["true"],
        }))
    return sorted(records, key=lambda record: record["recordId"])


def expect_sites(api, schemas, records):
    api.expect("POST", "/query/rows", [{"record_id": record["recordId"]} for record in records])
    for record in records:
        api.expect("GET", f"/form/{schemas['monitored_site']['id']}/record/{record['recordId']}", record)


def test_existing_bootstrap_and_seeds_make_no_writes(api, schemas, expect_inspection, record_response):
    records = seed_records(schemas, record_response)
    for _ in range(2):
        expect_inspection()
        for schema in schemas.values():
            api.expect("GET", f"/form/{schema['id']}/schema", schema)
        expect_inspection()
        expect_sites(api, schemas, records)
        expect_sites(api, schemas, records)
        result = bootstrap(api.client)
        assert result.created_forms == ()
        assert result.added_fields == result.seeded_sites == 0
    assert not any(call.request.method == "POST" and not call.request.url.endswith("/query/rows") for call in api.calls)


def test_fresh_bootstrap_creates_child_through_parent_then_seeds(api, schemas, expect_inspection, record_response):
    api.expect("GET", "/databases/testdatabase", {"databaseId": "testdatabase", "resources": []})
    for code, schema in schemas.items():
        path = f"/form/{schema['id']}/schema"
        if code == "snapshot_item":
            empty_child = {**schema, "elements": []}
            api.expect("GET", path, empty_child)
            api.expect("POST", path, {}, body=schema)
        else:
            api.expect("POST", "/databases/testdatabase/forms", {})
            api.expect("GET", path, schema)
        api.expect("GET", path, schema)
    expect_inspection()
    expect_sites(api, schemas, [])
    for _ in DEMO_SITES:
        api.expect("POST", "/update", {})
    expect_sites(api, schemas, seed_records(schemas, record_response))
    result = bootstrap(api.client)
    assert len(result.created_forms) == 4
    assert result.added_fields == 3
    assert result.seeded_sites == 4
    creates = [call.body for call in api.calls if call.request.url.endswith("/forms")]
    assert len(creates) == 3
    assert not any(body["formClass"].get("parentFormId") for body in creates)
    parent = next(body["formClass"] for body in creates if body["formClass"]["label"] == "Snapshot")
    assert parent["elements"][-1]["type"] == "subform"


def test_rejected_subform_operation_stops_immediately(api, schemas):
    api.expect("GET", "/databases/testdatabase", {"databaseId": "testdatabase", "resources": []})
    site = schemas["monitored_site"]
    api.expect("POST", "/databases/testdatabase/forms", {})
    api.expect("GET", f"/form/{site['id']}/schema", site)
    api.expect("GET", f"/form/{site['id']}/schema", site)
    api.expect("POST", "/databases/testdatabase/forms", {"message": "subform rejected"}, status=400)
    with pytest.raises(ActivityInfoError, match="subform rejected"):
        bootstrap(api.client)
    assert len(api.calls) == 5


def test_incompatible_existing_field_prevents_all_writes(api, schemas, tree):
    broken = deepcopy(schemas["monitored_site"])
    broken["elements"][2]["type"] = "FREE_TEXT"
    api.expect("GET", "/databases/testdatabase", tree)
    api.expect("GET", f"/form/{broken['id']}/schema", broken)
    with pytest.raises(SchemaError, match="Incompatible"):
        bootstrap(api.client)
    assert all(call.request.method == "GET" for call in api.calls)


def test_optional_scheduler_migration_preserves_existing_fields(api, schemas, expect_inspection, record_response):
    legacy = deepcopy(schemas)
    legacy['crawl']['elements'] = [f for f in legacy['crawl']['elements']
                                  if f['code'] not in {'scheduler_invocation', 'scheduled_at'}]
    original = deepcopy(legacy['crawl'])
    records = seed_records(schemas, record_response)
    expect_inspection(actual=legacy)
    for code, schema in legacy.items():
        path = f"/form/{schema['id']}/schema"
        if code == 'crawl':
            api.expect('POST', path, {})
        api.expect('GET', path, schemas[code])
    expect_inspection()
    expect_sites(api, schemas, records)
    expect_sites(api, schemas, records)
    result = bootstrap(api.client)
    assert result.added_fields == 2 and result.seeded_sites == 0 and not result.created_forms
    mutations = [c for c in api.calls if c.request.method == 'POST' and not c.request.url.endswith('/query/rows')]
    assert len(mutations) == 1
    updated = mutations[0].body
    assert updated['elements'][:len(original['elements'])] == original['elements']
    assert all(not f['required'] for f in updated['elements'][len(original['elements']):])


def test_required_scheduler_field_is_incompatible(api, schemas, expect_inspection):
    broken = deepcopy(schemas)
    next(f for f in broken['crawl']['elements'] if f['code'] == 'scheduled_at')['required'] = True
    expect_inspection(actual=broken)
    with pytest.raises(SchemaError, match='optional'):
        bootstrap(api.client)
    assert all(c.request.method == 'GET' for c in api.calls)
