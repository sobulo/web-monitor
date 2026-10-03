"""Scripted HTTP responses, not an in-memory ActivityInfo implementation."""

from collections import deque
from copy import deepcopy
import json
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import requests

from web_monitor.activityinfo.client import ActivityInfoClient
from web_monitor.activityinfo.config import ActivityInfoConfig
from web_monitor.activityinfo.schema import expected_schemas


@pytest.fixture(autouse=True)
def block_external_http(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Unmocked HTTP is forbidden in offline ActivityInfo tests")
    monkeypatch.setattr(requests.Session, "send", blocked)


@pytest.fixture
def api(monkeypatch, block_external_http):
    queue = deque()
    calls = []

    def expect(method, path, result=None, *, status=200, body=None, error=None):
        queue.append((method, path, result, status, body, error))

    def send(session, request, **kwargs):
        assert queue, f"Unexpected HTTP {request.method} {urlsplit(request.url).path}"
        method, path, result, status, body, error = queue.popleft()
        assert request.method == method
        assert request.url == "https://www.activityinfo.org/resources" + path
        payload = json.loads(request.body) if request.body else None
        if body is not None:
            assert payload == body
        calls.append(SimpleNamespace(request=request, body=payload, options=kwargs))
        if error is not None:
            raise error
        response = requests.Response()
        response.status_code = status
        response._content = json.dumps(result).encode() if result is not None else b""
        response.headers["Content-Type"] = "application/json"
        response.request = request
        return response

    monkeypatch.setattr(requests.Session, "send", send)
    with ActivityInfoClient(ActivityInfoConfig("testdatabase", "offline-test-token")) as client:
        yield SimpleNamespace(client=client, expect=expect, calls=calls, queue=queue)
    assert not queue, "Not all expected HTTP calls were made"


@pytest.fixture
def schemas():
    result = expected_schemas("testdatabase")
    for schema in result.values():
        schema["schemaVersion"] = "1"
    return result


@pytest.fixture
def tree(schemas):
    return {
        "databaseId": "testdatabase",
        "resources": [{
            "id": schema["id"], "label": schema["label"],
            "parentId": schema.get("parentFormId", "testdatabase"),
            "type": "SUB_FORM" if code == "snapshot_item" else "FORM",
        } for code, schema in schemas.items()],
    }


@pytest.fixture
def expect_inspection(api, schemas, tree):
    def add(*, actual=None, resources=None):
        actual = schemas if actual is None else actual
        api.expect("GET", "/databases/testdatabase", tree if resources is None else resources)
        for schema in actual.values():
            api.expect("GET", f"/form/{schema['id']}/schema", deepcopy(schema))
    return add


@pytest.fixture
def record_response(schemas):
    def make(form, record_id, values, *, parent_id=None):
        fields = {element["code"]: element["id"] for element in schemas[form]["elements"]}
        result = {
            "formId": schemas[form]["id"], "recordId": record_id,
            "fields": {fields[code]: value for code, value in values.items()},
        }
        if parent_id is not None:
            result["parentRecordId"] = parent_id
        return result
    return make
