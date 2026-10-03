"""Offline transport, authentication, configuration, and JSON contracts."""

import json

import pytest
import requests

from web_monitor.activityinfo.client import ActivityInfoClient, ActivityInfoError
from web_monitor.activityinfo.config import ActivityInfoConfig, ConfigurationError


def test_auth_timeout_and_redirect_policy(api):
    api.expect("GET", "/databases/testdatabase", {"databaseId": "testdatabase"})
    assert api.client.get_database()["databaseId"] == "testdatabase"
    call = api.calls[0]
    assert call.request.headers["Authorization"] == "Bearer offline-test-token"
    assert call.options["timeout"] == 20
    assert call.options["allow_redirects"] is False
    assert api.client._session.trust_env is False
    assert "offline-test-token" not in repr(api.client)


@pytest.mark.parametrize("status", [400, 401, 403, 429, 500, 302])
def test_http_errors_are_useful_and_redact_token(api, status):
    api.expect("POST", "/update", {"message": "Rejected offline-test-token"}, status=status)
    with pytest.raises(ActivityInfoError) as raised:
        api.client.update_records([{"formId": "form", "recordId": "record", "fields": {}}])
    error = raised.value
    assert error.status == status
    assert error.path == "/update"
    assert "Rejected" in str(error)
    assert "offline-test-token" not in str(error)
    assert "offline-test-token" not in repr(error.__dict__)


def test_timeout_does_not_expose_request_exception(api):
    api.expect("GET", "/databases/testdatabase", error=requests.Timeout("offline-test-token"))
    with pytest.raises(ActivityInfoError, match="Timeout") as raised:
        api.client.get_database()
    assert "offline-test-token" not in str(raised.value)
    assert raised.value.__suppress_context__


def test_malformed_json(monkeypatch, api):
    api.expect("GET", "/databases/testdatabase", {"irrelevant": True})
    monkeypatch.setattr(requests.Response, "json", lambda self: (_ for _ in ()).throw(ValueError()))
    with pytest.raises(ActivityInfoError, match="Invalid JSON"):
        api.client.get_database()


def test_schema_operations_and_scope(api, schemas):
    schema = schemas["monitored_site"]
    api.expect("POST", "/databases/testdatabase/forms", {})
    api.client.add_form(schema)
    assert api.calls[-1].body == {
        "formResource": {
            "id": schema["id"], "label": schema["label"], "type": "FORM",
            "parentId": "testdatabase", "visibility": "PRIVATE",
        }, "formClass": schema,
    }
    api.expect("GET", f"/form/{schema['id']}/schema", schema)
    assert api.client.get_schema(schema["id"]) == schema
    api.expect("POST", f"/form/{schema['id']}/schema", {}, body=schema)
    api.client.update_schema(schema)
    with pytest.raises(ValueError, match="configured database"):
        api.client.add_form({**schema, "databaseId": "other"})
    with pytest.raises(ValueError, match="parent subform"):
        api.client.add_form(schemas["snapshot_item"])


def test_query_has_explicit_codes_and_no_truncation(api):
    api.expect("POST", "/query/rows", [{"record_id": "r1"}])
    assert api.client.query_rows("form", {"record_id": "_id", "name": "name"}) == [{"record_id": "r1"}]
    assert api.calls[-1].body["columns"][1] == {"id": "name", "expression": "name"}
    assert api.calls[-1].body["truncateStrings"] is False


def test_configuration_is_explicit_and_environment_wins(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("ACTIVITYINFO_DATABASE_ID=filedatabase\nACTIVITYINFO_API_TOKEN=file-token\n")
    monkeypatch.setenv("ACTIVITYINFO_DATABASE_ID", "environmentdatabase")
    monkeypatch.setenv("ACTIVITYINFO_API_TOKEN", "environment-token")
    config = ActivityInfoConfig.from_environment(env_file=env)
    assert config.database_id == "environmentdatabase"
    assert config.api_token == "environment-token"
    assert "environment-token" not in repr(config)
    monkeypatch.delenv("ACTIVITYINFO_DATABASE_ID")
    monkeypatch.delenv("ACTIVITYINFO_API_TOKEN")
    config = ActivityInfoConfig.from_environment(env_file=env)
    assert config.database_id == "filedatabase"
    assert config.api_token == "file-token"


def test_missing_configuration_fails_without_echoing_values(monkeypatch):
    monkeypatch.delenv("ACTIVITYINFO_DATABASE_ID", raising=False)
    monkeypatch.delenv("ACTIVITYINFO_API_TOKEN", raising=False)
    with pytest.raises(ConfigurationError):
        ActivityInfoConfig.from_environment()


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_invalid_timeout(timeout):
    with pytest.raises(ValueError):
        ActivityInfoClient(ActivityInfoConfig("database", "offline-token"), timeout=timeout)
