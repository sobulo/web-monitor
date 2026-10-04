"""Application schema definitions and strict, non-destructive verification."""

from copy import deepcopy
from hashlib import sha256

from web_monitor.activityinfo.client import ActivityInfoClient

FORM_LABELS = {
    "monitored_site": "Monitored Site",
    "snapshot": "Snapshot",
    "snapshot_item": "Snapshot Item",
    "crawl": "Crawl",
}
STATUS_VALUES = ("initial", "no_change", "changed", "error")
SCHEDULER_FIELDS = frozenset({"scheduler_invocation", "scheduled_at"})


class SchemaError(ValueError):
    """Existing or read-back schema does not satisfy the persistence contract."""


def stable_id(*parts: str) -> str:
    """A database-scoped, CUID-compatible identity, independent of labels."""
    return "w" + sha256("web_monitor:".join(parts).encode()).hexdigest()[:24]


def form_ids(database_id: str) -> dict[str, str]:
    return {code: stable_id(database_id, "form", code) for code in FORM_LABELS}


def expected_schemas(database_id: str) -> dict[str, dict]:
    ids = form_ids(database_id)

    def field(form, code, field_type="FREE_TEXT", *, required=True, parameters=None):
        result = {
            "id": stable_id(ids[form], "field", code), "code": code,
            "label": code.replace("_", " ").capitalize(), "type": field_type,
            "required": required, "key": False,
        }
        if parameters is not None:
            result["typeParameters"] = parameters
        return result

    def reference(form, code, target, *, required=True):
        return field(form, code, "reference", required=required, parameters={
            "range": [{"formId": ids[target]}],
        })

    def choice(form, code, options):
        return field(form, code, "enumerated", parameters={
            "cardinality": "single",
            "values": [{"id": value.replace("_", ""), "label": value} for value in options],
        })

    elements = {
        "monitored_site": [
            field("monitored_site", "name"), field("monitored_site", "root_url"),
            field("monitored_site", "crawl_depth", "quantity"),
            field("monitored_site", "allowed_host"),
            choice("monitored_site", "include_subdomains", ("true", "false")),
            field("monitored_site", "schedule"),
            choice("monitored_site", "active", ("true", "false")),
        ],
        "snapshot": [
            reference("snapshot", "monitored_site", "monitored_site"),
            field("snapshot", "created_at"), field("snapshot", "effective_from"),
            field("snapshot", "item_count", "quantity"),
            field("snapshot", "items", "subform", required=False,
                  parameters={"formId": ids["snapshot_item"]}),
        ],
        "snapshot_item": [
            field("snapshot_item", "canonical_url"),
            field("snapshot_item", "title", required=False),
            field("snapshot_item", "content_hash"),
        ],
        "crawl": [
            reference("crawl", "monitored_site", "monitored_site"),
            field("crawl", "crawled_at"), choice("crawl", "status", STATUS_VALUES),
            reference("crawl", "snapshot", "snapshot", required=False),
            reference("crawl", "previous_snapshot", "snapshot", required=False),
            *[field("crawl", code, "quantity") for code in (
                "added_count", "removed_count", "changed_count", "pages_crawled",
            )],
            field("crawl", "error_message", "NARRATIVE", required=False),
            *[field("crawl", code, required=False) for code in sorted(SCHEDULER_FIELDS)],
        ],
    }
    schemas = {
        code: {"id": ids[code], "databaseId": database_id, "label": label,
               "elements": elements[code]}
        for code, label in FORM_LABELS.items()
    }
    schemas["snapshot_item"]["parentFormId"] = ids["snapshot"]
    # The parent's subform-field label becomes the child form's label on creation.
    schemas["snapshot"]["elements"][-1]["label"] = "Snapshot Item"
    return schemas


def fields_by_code(schema: dict) -> dict[str, dict]:
    if not isinstance(schema, dict) or not isinstance(schema.get("elements"), list):
        raise SchemaError("Malformed form schema: expected an elements list")
    fields = {}
    seen_ids = set()
    for element in schema["elements"]:
        if not isinstance(element, dict) or not element.get("id") or not element.get("type"):
            raise SchemaError("Malformed field definition")
        if element["id"] in seen_ids:
            raise SchemaError("Duplicate field ID in existing schema")
        seen_ids.add(element["id"])
        code = element.get("code")
        if code:
            if code in fields:
                raise SchemaError(f"Duplicate field code: {code}")
            fields[code] = element
    return fields


def verify_schema(actual: dict, expected: dict, *, allow_missing=False) -> list[dict]:
    """Return absent fields, but reject incompatible types/links without mutation."""
    for key in ("id", "databaseId", "parentFormId"):
        if actual.get(key) != expected.get(key):
            raise SchemaError(f"Incompatible {expected['label']} {key}")
    fields = fields_by_code(actual)
    missing = []
    for wanted in expected["elements"]:
        code = wanted["code"]
        found = fields.get(code)
        if found is None:
            if any(element["id"] == wanted["id"] for element in actual["elements"]):
                raise SchemaError(f"Field ID already used under another code: {code}")
            missing.append(deepcopy(wanted))
            continue
        if found.get("type") != wanted["type"]:
            raise SchemaError(f"Incompatible field type: {expected['label']}.{code}")
        if code in SCHEDULER_FIELDS and found.get("required", False):
            raise SchemaError(f"Scheduler provenance must remain optional: {code}")
        parameters = found.get("typeParameters", {})
        expected_parameters = wanted.get("typeParameters", {})
        if not isinstance(parameters, dict):
            raise SchemaError(f"Malformed type parameters: {code}")
        for key in ("range", "formId"):
            if parameters.get(key) != expected_parameters.get(key):
                raise SchemaError(f"Incompatible relationship: {expected['label']}.{code}")
        if wanted["type"] == "enumerated":
            if str(parameters.get("cardinality", "")).lower() != "single":
                raise SchemaError(f"Incompatible selection cardinality: {code}")
            values = parameters.get("values", [])
            if (
                not isinstance(values, list)
                or any(not isinstance(value, dict) for value in values)
            ):
                raise SchemaError(f"Malformed selection options: {code}")
            if {
                (value.get("id"), value.get("label")) for value in values
            } != {
                (value["id"], value["label"]) for value in expected_parameters["values"]
            } or len(values) != len(expected_parameters["values"]):
                raise SchemaError(f"Incompatible selection options: {code}")
    if missing and not allow_missing:
        raise SchemaError("Missing required field codes: " + ", ".join(f["code"] for f in missing))
    return missing


def inspect_schema(client: ActivityInfoClient, *, allow_missing=False,
                   allow_legacy_scheduler=False) -> dict[str, dict]:
    """Preflight every application resource before any bootstrap write."""
    expected = expected_schemas(client.database_id)
    tree = client.get_database()
    if not isinstance(tree, dict) or tree.get("databaseId") != client.database_id:
        raise SchemaError("Unexpected database tree")
    if not isinstance(tree.get("resources"), list):
        raise SchemaError("Malformed database resources")
    resources = {}
    for resource in tree["resources"]:
        if not isinstance(resource, dict) or not resource.get("id"):
            raise SchemaError("Malformed database resource")
        if resource["id"] in resources:
            raise SchemaError("Duplicate resource ID in database tree")
        resources[resource["id"]] = resource
        for code, schema in expected.items():
            if (
                resource.get("label") == schema["label"] or resource.get("code") == code
            ) and resource["id"] != schema["id"]:
                raise SchemaError(f"Conflicting existing application form: {code}")
    actual = {}
    for code, wanted in expected.items():
        resource = resources.get(wanted["id"])
        if resource is None:
            if not allow_missing:
                raise SchemaError(f"Missing application form: {code}")
            continue
        parent = wanted.get("parentFormId", client.database_id)
        kind = "SUB_FORM" if code == "snapshot_item" else "FORM"
        if resource.get("type") != kind or resource.get("parentId") != parent:
            raise SchemaError(f"Incompatible resource type/parent: {code}")
        schema = client.get_schema(wanted["id"])
        missing = verify_schema(schema, wanted, allow_missing=allow_missing or allow_legacy_scheduler)
        if not allow_missing and any(
            not (allow_legacy_scheduler and code == "crawl" and f["code"] in SCHEDULER_FIELDS)
            for f in missing
        ):
            raise SchemaError("Missing application fields; run bootstrap")
        actual[code] = schema
    if "snapshot_item" in actual:
        if "snapshot" not in actual:
            raise SchemaError("Snapshot Item has no Snapshot parent")
        field = fields_by_code(actual["snapshot"]).get("items")
        if field is None:
            raise SchemaError("Snapshot Item exists without its parent subform field")
    elif "snapshot" in actual and "items" in fields_by_code(actual["snapshot"]):
        raise SchemaError("Snapshot subform field points to a missing child form")
    return actual
