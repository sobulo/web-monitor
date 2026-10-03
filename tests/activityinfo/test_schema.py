"""Schema verification rejects incompatibility before bootstrap writes."""

from copy import deepcopy

import pytest

from web_monitor.activityinfo.schema import (
    SchemaError, expected_schemas, form_ids, inspect_schema, verify_schema,
)


def test_ids_are_stable_scoped_and_fields_have_codes(schemas):
    assert form_ids("testdatabase") == form_ids("testdatabase")
    assert form_ids("testdatabase") != form_ids("otherdatabase")
    assert all(field.get("code") for schema in schemas.values() for field in schema["elements"])
    parent = schemas["snapshot"]
    child = schemas["snapshot_item"]
    assert parent["elements"][-1]["type"] == "subform"
    assert parent["elements"][-1]["typeParameters"] == {"formId": child["id"]}
    assert child["parentFormId"] == parent["id"]


@pytest.mark.parametrize("mutation", ["type", "reference", "subform", "parent", "duplicate", "options", "malformed"])
def test_incompatible_schema_fails(schemas, mutation):
    code = "snapshot"
    actual = deepcopy(schemas[code])
    if mutation == "type":
        actual["elements"][0]["type"] = "FREE_TEXT"
    elif mutation == "reference":
        actual["elements"][0]["typeParameters"]["range"][0]["formId"] = "wrong"
    elif mutation == "subform":
        actual["elements"][-1]["typeParameters"]["formId"] = "wrong"
    elif mutation == "parent":
        actual["parentFormId"] = "unexpected"
    elif mutation == "duplicate":
        field = deepcopy(actual["elements"][0])
        field["id"] = "differentid"
        actual["elements"].append(field)
    elif mutation == "options":
        code = "crawl"
        actual = deepcopy(schemas[code])
        actual["elements"][2]["typeParameters"]["values"].pop()
    else:
        actual["elements"] = None
    with pytest.raises(SchemaError):
        verify_schema(actual, schemas[code], allow_missing=True)


def test_absent_fields_can_be_added_but_not_silently_verified(schemas):
    actual = deepcopy(schemas["snapshot_item"])
    missing = actual["elements"].pop()
    assert verify_schema(actual, schemas["snapshot_item"], allow_missing=True) == [missing]
    with pytest.raises(SchemaError, match="Missing"):
        verify_schema(actual, schemas["snapshot_item"])


def test_readback_verifies_resource_tree_and_all_schemas(api, schemas, expect_inspection):
    expect_inspection()
    assert inspect_schema(api.client) == schemas


def test_conflicting_named_form_stops_without_schema_writes(api, tree):
    tree["resources"][0]["id"] = "conflictingid"
    api.expect("GET", "/databases/testdatabase", tree)
    with pytest.raises(SchemaError, match="Conflicting"):
        inspect_schema(api.client, allow_missing=True)
    assert all(call.request.method == "GET" for call in api.calls)


def test_child_resource_must_be_subform(api, schemas, tree):
    tree["resources"][2]["type"] = "FORM"
    api.expect("GET", "/databases/testdatabase", tree)
    for code in ("monitored_site", "snapshot"):
        api.expect("GET", f"/form/{schemas[code]['id']}/schema", schemas[code])
    with pytest.raises(SchemaError, match="type/parent"):
        inspect_schema(api.client)


@pytest.mark.parametrize("parameters", [None, [], {"cardinality": "single", "values": [None]}])
def test_malformed_type_parameters_have_clear_errors(schemas, parameters):
    actual = deepcopy(schemas["crawl"])
    actual["elements"][2]["typeParameters"] = parameters
    with pytest.raises(SchemaError, match="Malformed"):
        verify_schema(actual, schemas["crawl"], allow_missing=True)
