"""Small JSON client for the official ActivityInfo resources API."""

import json
import math
import re
from typing import Any

import requests

from web_monitor.activityinfo.config import ActivityInfoConfig

API_ROOT = "https://www.activityinfo.org/resources"


class ActivityInfoError(RuntimeError):
    """Safe diagnostic containing method/path/status and redacted response only."""

    def __init__(self, method, path, status, detail, request_body=None):
        self.method = method
        self.path = path
        self.status = status
        self.detail = detail
        self.request_body = request_body
        super().__init__(f"{method} {path}: HTTP {status or 'unavailable'}: {detail}")


def resource_id(value: str) -> str:
    """Validate identifiers before putting them in paths or reference values."""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,31}", value):
        raise ValueError("Invalid ActivityInfo resource identifier")
    return value


class ActivityInfoClient:
    """Authenticated client scoped to one existing database, with no DB mutation API."""

    def __init__(self, config: ActivityInfoConfig, *, timeout: float = 20.0):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        self.database_id = config.database_id
        self._token = config.api_token
        self.timeout = timeout
        self._session = requests.Session()
        self._session.trust_env = False
        self._session.headers.update({
            "Authorization": f"Bearer {config.api_token}",
            "Accept": "application/json",
            "User-Agent": "Web-Monitor/0.1",
        })

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._session.close()

    def _redact(self, text: str) -> str:
        return text.replace(self._token, "[REDACTED]")

    def _request(self, method: str, path: str, body=None) -> Any:
        # Paths are constructed exclusively by the methods below. Never follow
        # redirects with credentials or retain raw Requests exceptions.
        try:
            response = self._session.request(
                method, API_ROOT + path, json=body,
                timeout=self.timeout, allow_redirects=False,
            )
        except requests.RequestException as error:
            raise ActivityInfoError(
                method, path, None, type(error).__name__,
            ) from None
        with response:
            safe_body = json.loads(self._redact(json.dumps(body)))
            if not 200 <= response.status_code < 300:
                detail = self._redact(response.text)[:4000]
                raise ActivityInfoError(
                    method, path, response.status_code, detail, safe_body,
                ) from None
            if not response.content:
                return None
            try:
                result = response.json()
            except ValueError:
                raise ActivityInfoError(
                    method, path, response.status_code, "Invalid JSON response",
                ) from None
            # Some API validation errors may be carried in a JSON error envelope.
            if isinstance(result, dict) and result.get("code") in {
                "BAD_REQUEST", "FORBIDDEN", "AUTHENTICATION_REQUIRED", "NOT_FOUND",
            }:
                raise ActivityInfoError(
                    method, path, response.status_code,
                    self._redact(json.dumps(result))[:4000], safe_body,
                )
            return result

    def get_database(self) -> dict:
        return self._request("GET", f"/databases/{self.database_id}")

    def get_schema(self, form_id: str) -> dict:
        schema = self._request("GET", f"/form/{resource_id(form_id)}/schema")
        if not isinstance(schema, dict) or schema.get("databaseId") != self.database_id:
            raise ValueError("Form schema is outside the configured database or malformed")
        return schema

    def add_form(self, schema: dict) -> None:
        self._check_schema_scope(schema)
        if schema.get("parentFormId"):
            raise ValueError("Create subforms through a parent subform field")
        body = {
            "formResource": {
                "id": schema["id"], "parentId": self.database_id,
                "type": "FORM", "label": schema["label"], "visibility": "PRIVATE",
            },
            "formClass": schema,
        }
        self._request("POST", f"/databases/{self.database_id}/forms", body)

    def update_schema(self, schema: dict) -> None:
        self._check_schema_scope(schema)
        self._request("POST", f"/form/{schema['id']}/schema", schema)

    def _check_schema_scope(self, schema: dict) -> None:
        resource_id(schema["id"])
        if schema.get("databaseId") != self.database_id:
            raise ValueError("Schema writes must target the configured database")

    def get_record(self, form_id: str, record_id: str) -> dict:
        return self._request(
            "GET", f"/form/{resource_id(form_id)}/record/{resource_id(record_id)}",
        )

    def query_rows(self, form_id: str, columns: dict[str, str], *, filter_formula=None):
        body = {
            "rowSources": [{"rootFormId": resource_id(form_id)}],
            "columns": [{"id": code, "expression": formula} for code, formula in columns.items()],
            "truncateStrings": False,
            "sort": [{"formula": "_id", "dir": "ASC"}],
        }
        if filter_formula is not None:
            body["filter"] = filter_formula
        result = self._request("POST", "/query/rows", body)
        if not isinstance(result, list) or any(not isinstance(row, dict) for row in result):
            raise ValueError("Malformed ActivityInfo query response")
        return result

    def update_records(self, changes: list[dict]) -> None:
        """Submit a small batch; callers use only verified application form IDs."""
        if not 1 <= len(changes) <= 10:
            raise ValueError("Submit between one and ten record changes per request")
        for change in changes:
            resource_id(change["formId"])
            resource_id(change["recordId"])
            if change.get("parentRecordId"):
                resource_id(change["parentRecordId"])
        self._request("POST", "/update", {"changes": changes})
