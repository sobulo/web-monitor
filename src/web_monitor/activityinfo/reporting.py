"""Deterministic ActivityInfo reports and publication verification."""

import argparse
from dataclasses import dataclass
import os
import re
from html.parser import HTMLParser
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlsplit

from web_monitor.activityinfo.client import ActivityInfoClient, ActivityInfoError
from web_monitor.activityinfo.config import ActivityInfoConfig
from web_monitor.activityinfo.schema import inspect_schema, stable_id

NOTEBOOK_REPORT_TITLE = "Web Monitor — Monitoring Overview"
SINGLE_REPORT_TITLE = "Web Monitor — Monitoring Activity"
NOTEBOOK_PUBLIC_URL = "https://www.activityinfo.org/published/we71c43ac5956505640290ecb"


class ReportValidationError(ValueError):
    """A provider contract or publication cannot be verified."""


class EmbedMetadataUnavailable(ReportValidationError):
    """No officially supplied Single-report embed location was found."""


def notebook_report_id(database_id: str) -> str:
    return stable_id(database_id, "report", "monitoring_overview")


def single_report_id(database_id: str) -> str:
    return stable_id(database_id, "report", "monitoring_activity")


def _dimension(report_identity, crawl_form, name, formula, axis):
    return {
        "id": stable_id(report_identity, name),
        "label": name,
        "mappings": [{"formId": crawl_form, "formula": formula, "type": "VALUE"}],
        "axis": axis,
        "totals": False,
        "missingIncluded": False,
        "categoryMappings": [],
    }


def _analysis(report_identity, crawl_form, name, visualization, dimensions):
    identity = stable_id(report_identity, name)
    model = {
        "visualization": visualization,
        "measures": [{
            "id": stable_id(identity, "count"),
            "code": "crawl_count",
            "label": "Crawls",
            "formId": crawl_form,
            "formula": "_id",
            "statistics": ["COUNT"],
            "display": "DEFAULT",
            "calculated": False,
        }],
        "dimensions": dimensions,
        "filters": [],
        "columnWidths": {},
    }
    return {"id": identity, "modelType": "pivot", "model": model}


def build_notebook_report(database_id: str, schemas: dict, *, publish: bool) -> dict:
    identity = notebook_report_id(database_id)
    crawl_form = schemas["crawl"]["id"]
    dated_crawls = stable_id(identity, "dated_crawls")
    sources = {
        "forms": sorted([crawl_form, schemas["monitored_site"]["id"]]),
        "calculatedTables": [{
            "id": dated_crawls,
            "alias": "dated_crawls",
            "formula": (f'SELECTCOLUMNS({crawl_form}, "crawl_id", _id, '
                        '"crawl_date", DATEVALUE(LEFT(crawled_at, 10)))'),
        }],
    }
    date_dimension = _dimension(
        identity, dated_crawls, "Crawl date (UTC)", "crawl_date", "ROW",
    )
    date_dimension["dateLevel"] = "DATE"
    changes = _analysis(identity, crawl_form, "change_activity", "BARCHART", [
        _dimension(identity, crawl_form, "Monitored Site", "monitored_site.name", "ROW"),
    ])
    changes["model"]["measures"][0].update(
        code="modified_pages", label="Modified pages", formula="changed_count",
        statistics=["SUM"],
    )
    daily = _analysis(identity, dated_crawls, "daily_activity", "LINECHART", [date_dimension])
    daily["model"]["measures"][0]["formula"] = "crawl_id"
    analyses = [
        _analysis(identity, crawl_form, "site_status", "TABLE", [
            _dimension(identity, crawl_form, "Monitored Site", "monitored_site.name", "ROW"),
            _dimension(identity, crawl_form, "Status", "status", "COLUMN"),
        ]),
        changes,
        daily,
        _analysis(identity, crawl_form, "status_distribution", "PIECHART", [
            _dimension(identity, crawl_form, "Status", "status", "COLUMN"),
        ]),
    ]
    components = [{
        "id": stable_id(identity, "intro"),
        "type": "TEXT",
        "visible": True,
        "content": (
            "initial = baseline; no_change = successful unchanged observation; "
            "changed = state transition; error = failed crawl. Counts describe attempts, "
            "not distinct snapshots. Modified pages sums changed_count across attempts; "
            "additions and removals are separate. Daily activity uses UTC dates. "
            "Sites without Crawls do not contribute to these aggregate charts."
        ),
    }]
    components.extend({
        "id": analysis["id"],
        "type": "ANALYSIS",
        "content": title,
        "visible": True,
        "analysisType": "pivot",
        "visualizationType": analysis["model"]["visualization"],
    } for analysis, title in zip(analyses, (
        "Monitoring activity by site and status",
        "Modified pages by Monitored Site",
        "Crawl activity by UTC date",
        "Crawl-status distribution",
    )))
    return {
        "id": identity,
        "label": NOTEBOOK_REPORT_TITLE,
        "sources": sources,
        "components": components,
        "analyses": analyses,
        "layout": "NOTEBOOK",
        "owner": {"type": "DATABASE", "id": database_id},
        "roleGrants": [],
        "pages": [],
        "publish": publish,
    }


def build_single_report(database_id: str, schemas: dict, *, publish: bool) -> dict:
    identity = single_report_id(database_id)
    crawl_form = schemas["crawl"]["id"]
    analysis = _analysis(identity, crawl_form, "site_status", "BARCHART", [
        _dimension(identity, crawl_form, "Monitored Site", "monitored_site.name", "ROW"),
        _dimension(identity, crawl_form, "Status", "status", "COLUMN"),
    ])
    return {
        "id": identity,
        "label": SINGLE_REPORT_TITLE,
        "sources": {
            "forms": sorted([crawl_form, schemas["monitored_site"]["id"]]),
            "calculatedTables": [],
        },
        "components": [{
            "id": analysis["id"],
            "type": "ANALYSIS",
            "content": "Monitoring activity by site and status",
            "visible": True,
            "analysisType": "pivot",
            "visualizationType": "BARCHART",
        }],
        "analyses": [analysis],
        "layout": "SINGLE",
        "owner": {"type": "DATABASE", "id": database_id},
        "roleGrants": [],
        "pages": [],
        "publish": publish,
    }


def matches_definition(actual, expected):
    """Allow server-added defaults, but preserve all requested semantics."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and matches_definition(actual[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            matches_definition(a, b) for a, b in zip(actual, expected)
        )
    return type(actual) is type(expected) and actual == expected


class _IframeParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sources = []

    def handle_starttag(self, tag, attrs):
        if tag.casefold() == "iframe":
            values = dict(attrs)
            if values.get("src"):
                self.sources.append(values["src"])


def validate_embed_url(value: str, expected_id: str | None = None) -> str:
    """Accept only an HTTPS ActivityInfo iframe source marked for embedding."""
    if not isinstance(value, str):
        raise ReportValidationError("ActivityInfo embed URL must be text")
    if any(char.isspace() or ord(char) < 32 for char in value) or '\\' in value:
        raise ReportValidationError("Malformed ActivityInfo URL")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "www.activityinfo.org"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or parsed.fragment
        or parse_qs(parsed.query, keep_blank_values=True) != {"embed": ["true"]}
        or not re.fullmatch(r"/published/[A-Za-z][A-Za-z0-9]{0,31}", parsed.path)
        or (expected_id is not None and parsed.path.rsplit('/', 1)[-1] != expected_id)
    ):
        raise ReportValidationError("Untrusted or malformed ActivityInfo embed URL")
    return value


def extract_embed_url(metadata) -> str | None:
    """Extract only provider-returned iframe/embed fields; never derive a URL."""
    candidates = []

    def visit(value, key=""):
        if isinstance(value, dict):
            for child_key, child in value.items():
                visit(child, str(child_key))
        elif isinstance(value, list):
            for child in value:
                visit(child, key)
        elif isinstance(value, str) and ("embed" in key.casefold() or "iframe" in key.casefold()):
            parser = _IframeParser()
            parser.feed(value)
            if parser.sources:
                candidates.extend(parser.sources)
            elif value.startswith("https://"):
                candidates.append(value)

    visit(metadata)
    valid = {validate_embed_url(value) for value in candidates}
    if len(valid) > 1:
        raise ReportValidationError("Conflicting ActivityInfo embed URLs")
    return next(iter(valid), None)


@dataclass(frozen=True)
class PublishedReports:
    """Validated presentation metadata, never raw provider HTML."""

    single_id: str
    notebook_id: str
    embed_url: str | None
    notebook_url: str | None
    provider: str = "ActivityInfo"

    def validate(self):
        if self.embed_url is not None:
            validate_embed_url(self.embed_url, self.single_id)
        if self.single_id == self.notebook_id:
            raise ReportValidationError("Notebook must not be embedded")
        if self.notebook_url is not None:
            # Validate the supplied standalone URL; do not manufacture one.
            parsed = urlsplit(self.notebook_url)
            if (parsed.scheme != 'https' or parsed.netloc != 'www.activityinfo.org'
                    or parsed.query or parsed.fragment
                    or parsed.path != '/published/' + self.notebook_id
                    or any(char.isspace() for char in self.notebook_url)):
                raise ReportValidationError("Invalid Notebook public URL")
        return self


class ActivityInfoReportPublisher:
    def __init__(self, client: ActivityInfoClient, schemas=None):
        self.client = client
        self.schemas = inspect_schema(client) if schemas is None else schemas
        self.definitions = (
            build_notebook_report(client.database_id, self.schemas, publish=False),
            build_single_report(client.database_id, self.schemas, publish=False),
        )

    def _validate_report(self, value, expected, *, published=None, check_components=True):
        if not isinstance(value, dict) or not isinstance(value.get("sources"), dict):
            raise ReportValidationError("Malformed report or sources")
        # Source forms are a collection. The provider may return a different
        # order for different databases; preserve strict membership/uniqueness.
        sources = dict(value["sources"])
        forms = sources.get("forms")
        if not isinstance(forms, list) or any(not isinstance(form, str) for form in forms):
            raise ReportValidationError("Malformed report source forms")
        sources["forms"] = sorted(forms)
        if any((
            value.get("id") != expected["id"],
            value.get("ownerType") != "DATABASE",
            value.get("databaseId") != self.client.database_id,
            value.get("label") != expected["label"],
            value.get("layout") != expected["layout"],
            not isinstance(value.get("sources"), dict),
            (sources != expected["sources"] if check_components else
             sources["forms"] != expected["sources"]["forms"]),
            type(value.get("published")) is not bool,
        )):
            raise ReportValidationError("Report identity, ownership, sources, or layout mismatch")
        if published is not None and value["published"] is not published:
            raise ReportValidationError("Publication state was not confirmed")
        components = value.get("components")
        if not isinstance(components, list) or not components or any(
            not isinstance(component, dict) or not component.get("id")
            for component in components
        ):
            raise ReportValidationError("Malformed report components")
        if not check_components:
            return value
        if len(components) != len(expected["components"]):
            raise ReportValidationError("Report components mismatch")
        for actual, wanted in zip(components, expected["components"]):
            if not isinstance(actual, dict) or any(actual.get(key) != val for key, val in wanted.items()):
                raise ReportValidationError("Report component semantics mismatch")
        return value

    def _verify_tree(self):
        tree = self.client.get_database()
        if not isinstance(tree, dict) or not isinstance(tree.get("resources"), list):
            raise ReportValidationError("Malformed database tree")
        for expected in self.definitions:
            matches = [value for value in tree["resources"] if (
                value.get("id") == expected["id"] or value.get("label") == expected["label"]
            )]
            if len(matches) != 1 or any((
                matches[0].get("id") != expected["id"],
                matches[0].get("type") != "REPORT",
                matches[0].get("parentId") != self.client.database_id,
            )):
                raise ReportValidationError(
                    "Database report ownership or uniqueness could not be verified"
                )

    def _setup_report(self, expected):
        try:
            existing = self.client.get_report(expected["id"])
        except ActivityInfoError as error:
            if error.status != 404:
                raise
            existing = None
        if existing is not None:
            # Setup may refine content on the same owned report. Read-only web
            # access and post-write checks still require the exact current content.
            self._validate_report(existing, expected, check_components=False)
        definition = dict(expected, publish=existing["published"] if existing else False)
        self.client.update_report(definition)
        self._validate_report(self.client.get_report(expected["id"]), expected)
        for wanted in definition["analyses"]:
            actual = self.client.get_analysis(expected["id"], wanted["id"])
            if (
                not isinstance(actual, dict)
                or actual.get("id") != wanted["id"]
                or actual.get("modelType") != "pivot"
            ):
                raise ReportValidationError("Analysis identity or type mismatch")
            if not matches_definition(actual.get("model"), wanted["model"]):
                raise ReportValidationError(
                    "Analysis source, measure, dimension, or visualization mismatch"
                )
            if not isinstance(
                self.client.get_analysis_results(expected["id"], wanted["id"]), dict
            ):
                raise ReportValidationError("Malformed analysis results")
        self.client.update_report(dict(definition, publish=True))
        self._validate_report(
            self.client.get_report(expected["id"]), expected, published=True
        )
        metadata = self.client.get_published_report(expected["id"])
        if (not isinstance(metadata, dict)
                or any(not matches_definition(metadata.get(key), expected[key])
                       for key in ("id", "label", "layout", "components", "pages"))):
            raise ReportValidationError("Published report metadata mismatch")
        return metadata

    def published_reports(self):
        """Read-only web path: verify publication, then use supplied exact URLs."""
        for expected in self.definitions:
            self._validate_report(self.client.get_report(expected['id']), expected, published=True)
        return PublishedReports(
            single_report_id(self.client.database_id), notebook_report_id(self.client.database_id),
            os.environ.get('ACTIVITYINFO_SINGLE_EMBED_URL') or None,
            os.environ.get('ACTIVITYINFO_NOTEBOOK_PUBLIC_URL') or None,
        ).validate()

    def setup(self):
        metadata = {
            expected["layout"]: self._setup_report(expected)
            for expected in self.definitions
        }
        self._verify_tree()
        return metadata


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path.cwd() / ".env")
    args = parser.parse_args(argv)
    try:
        config = ActivityInfoConfig.from_environment(env_file=args.env_file)
        with ActivityInfoClient(config) as client:
            publisher = ActivityInfoReportPublisher(client)
            metadata = publisher.setup()
            print(
                "Notebook and Single reports: definitions, analyses, ownership, "
                "uniqueness, and publication verified."
            )
            print(f"Notebook report: {notebook_report_id(client.database_id)}")
            print(f"Single report: {single_report_id(client.database_id)}")
            single_metadata = metadata["SINGLE"]
            print("Single published metadata type: " + type(single_metadata).__name__)
            if isinstance(single_metadata, dict):
                print("Single published metadata fields: " + ", ".join(sorted(single_metadata)))
            embed_url = extract_embed_url(single_metadata)
            configured = os.environ.get('ACTIVITYINFO_SINGLE_EMBED_URL')
            if configured:
                configured = validate_embed_url(configured, single_report_id(client.database_id))
                if embed_url is not None and configured != embed_url:
                    raise ReportValidationError("Supplied embed URL disagrees with provider metadata")
                embed_url = configured
            if embed_url is None:
                raise EmbedMetadataUnavailable(
                    "published Single report exposes no exact iframe/embed URL through the API"
                )
            print("Single embed URL validated (provider metadata or exact supplied snippet): " + embed_url)
        return 0
    except ActivityInfoError as error:
        code = "PUBLISHING_FORBIDDEN" if "PUBLISHING_FORBIDDEN" in error.detail else "REQUEST_FAILED"
        print(
            f"STOP: {code}; HTTP {error.status or 'unavailable'}. No permissions changed.",
            file=sys.stderr,
        )
        return 2
    except ReportValidationError as error:
        print(f"STOP: {error}", file=sys.stderr)
        return 2
    except ValueError:
        print("STOP: invalid report configuration.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
