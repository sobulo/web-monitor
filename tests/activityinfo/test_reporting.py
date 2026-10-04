"""Documented two-report API contract through scripted HTTP responses."""

from copy import deepcopy

import pytest

from web_monitor.activityinfo.client import ActivityInfoError
from web_monitor.activityinfo.reporting import (
    ActivityInfoReportPublisher,
    NOTEBOOK_PUBLIC_URL,
    ReportValidationError,
    build_notebook_report,
    build_single_report,
    extract_embed_url,
    notebook_report_id,
    single_report_id,
    validate_embed_url,
)


def response(definition, published=False):
    return {
        key: deepcopy(definition[key])
        for key in ("id", "label", "sources", "components", "layout")
    } | {
        "ownerType": "DATABASE",
        "databaseId": "testdatabase",
        "published": published,
    }


def queue_report(api, definition, *, existing=False, publish_ok=True, metadata=None):
    identity = definition["id"]
    api.expect(
        "GET", "/reports/" + identity,
        response(definition, True) if existing else {},
        status=200 if existing else 404,
    )
    api.expect("POST", "/reports", response(definition, existing), body=definition)
    api.expect("GET", "/reports/" + identity, response(definition, existing))
    for analysis in definition["analyses"]:
        api.expect(
            "GET", f"/reports/{identity}/analyses/{analysis['id']}",
            analysis | {"modelType": "pivot"},
        )
        api.expect(
            "POST", "/reports/analyses/results", {},
            body={"reportId": identity, "analysisId": analysis["id"], "slicerValues": []},
        )
    published = definition | {"publish": True}
    api.expect("POST", "/reports", response(definition, publish_ok), body=published)
    api.expect("GET", "/reports/" + identity, response(definition, publish_ok))
    if publish_ok:
        api.expect("GET", f"/reports/{identity}/published", metadata if metadata is not None else {
            key: deepcopy(definition[key])
            for key in ("id", "label", "layout", "components", "pages")
        })


def queue_setup(api, schemas, *, existing=False, publish_ok=True, single_metadata=None):
    notebook = build_notebook_report("testdatabase", schemas, publish=existing)
    single = build_single_report("testdatabase", schemas, publish=existing)
    queue_report(api, notebook, existing=existing, publish_ok=publish_ok)
    queue_report(
        api, single, existing=existing, publish_ok=publish_ok,
        metadata=single_metadata,
    )
    tree = {"resources": [
        {"id": notebook["id"], "label": notebook["label"], "type": "REPORT", "parentId": "testdatabase"},
        {"id": single["id"], "label": single["label"], "type": "REPORT", "parentId": "testdatabase"},
    ]}
    api.expect("GET", "/databases/testdatabase", tree)


def test_two_report_payload_semantics_and_public_data_allowlist(schemas):
    notebook = build_notebook_report("testdatabase", schemas, publish=False)
    single = build_single_report("testdatabase", schemas, publish=False)
    assert notebook["id"] == notebook_report_id("testdatabase")
    assert single["id"] == single_report_id("testdatabase")
    assert notebook["id"] != single["id"]
    assert notebook["layout"] == "NOTEBOOK"
    assert single["layout"] == "SINGLE"
    assert len(notebook["analyses"]) == 4
    assert len(notebook["components"]) == 5
    assert len(single["analyses"]) == len(single["components"]) == 1
    assert single["analyses"][0]["model"]["visualization"] == "BARCHART"
    for report in (notebook, single):
        assert report["owner"] == {"type": "DATABASE", "id": "testdatabase"}
        assert report["sources"]["forms"] == sorted([
            schemas["crawl"]["id"], schemas["monitored_site"]["id"],
        ])
        for analysis in report["analyses"]:
            measure = analysis["model"]["measures"][0]
            if measure["code"] == "modified_pages":
                assert measure["formula"] == "changed_count"
                assert measure["statistics"] == ["SUM"]
            else:
                assert measure["formula"] in ("_id", "crawl_id")
                assert measure["statistics"] == ["COUNT"]
    text = str((notebook, single))
    assert all(value not in text for value in (
        "error_message", "content_hash", "ACTIVITYINFO_API_TOKEN",
        schemas["snapshot_item"]["id"],
    ))
    assert NOTEBOOK_PUBLIC_URL == (
        "https://www.activityinfo.org/published/we71c43ac5956505640290ecb"
    )


def test_idempotent_create_then_update_and_unique_tree(api, schemas):
    publisher = ActivityInfoReportPublisher(api.client, schemas)
    queue_setup(api, schemas)
    assert set(publisher.setup()) == {"NOTEBOOK", "SINGLE"}
    queue_setup(api, schemas, existing=True)
    assert set(publisher.setup()) == {"NOTEBOOK", "SINGLE"}
    writes = [
        call.body for call in api.calls
        if call.request.url.endswith("/reports") and call.request.method == "POST"
    ]
    assert {value["id"] for value in writes} == {
        notebook_report_id("testdatabase"), single_report_id("testdatabase"),
    }


def test_publication_readback_failure(api, schemas):
    notebook = build_notebook_report("testdatabase", schemas, publish=False)
    queue_report(api, notebook, publish_ok=False)
    with pytest.raises(ReportValidationError, match="Publication"):
        ActivityInfoReportPublisher(api.client, schemas).setup()


def test_permission_failure_is_not_retried(api, schemas):
    identity = notebook_report_id("testdatabase")
    api.expect("GET", "/reports/" + identity, {}, status=404)
    api.expect(
        "POST", "/reports",
        {"code": "PUBLISHING_FORBIDDEN", "message": "offline-test-token"}, status=403,
    )
    with pytest.raises(ActivityInfoError) as caught:
        ActivityInfoReportPublisher(api.client, schemas).setup()
    assert "offline-test-token" not in str(caught.value)
    assert len(api.calls) == 2


@pytest.mark.parametrize("change", [
    {"ownerType": "PERSONAL"}, {"databaseId": "other"}, {"sources": {}},
    {"components": []}, {"published": "true"}, {"layout": "SINGLE"},
])
def test_malformed_existing_notebook_stops_before_write(api, schemas, change):
    definition = build_notebook_report("testdatabase", schemas, publish=False)
    api.expect("GET", "/reports/" + definition["id"], response(definition) | change)
    with pytest.raises(ReportValidationError):
        ActivityInfoReportPublisher(api.client, schemas).setup()
    assert len(api.calls) == 1


def test_embed_metadata_must_be_provider_supplied_and_trusted():
    url = "https://www.activityinfo.org/published/providerid?embed=true"
    assert extract_embed_url({"embeddableHtml": f'<iframe src="{url}"></iframe>'}) == url
    assert extract_embed_url({"embedUrl": url}) == url
    assert extract_embed_url({"url": url}) is None
    assert extract_embed_url({"id": "providerid"}) is None
    for bad in (
        "http://www.activityinfo.org/published/id?embed=true",
        "https://evil.example/published/id?embed=true",
        "https://www.activityinfo.org/published/id",
        "https://www.activityinfo.org/published/id?embed=true&token=",
        "https://www.activityinfo.org.evil.example/published/id?embed=true",
    ):
        with pytest.raises(ReportValidationError):
            validate_embed_url(bad)


def test_malformed_published_metadata(api, schemas):
    definition = build_notebook_report("testdatabase", schemas, publish=False)
    queue_report(api, definition, metadata={"id": "wrong"})
    with pytest.raises(ReportValidationError, match="Published report metadata"):
        ActivityInfoReportPublisher(api.client, schemas).setup()


def test_published_metadata_accepts_server_defaults(api, schemas):
    notebook = build_notebook_report("testdatabase", schemas, publish=False)
    metadata = {key: deepcopy(notebook[key])
                for key in ("id", "label", "layout", "components", "pages")}
    for component in metadata["components"]:
        component["subtitle"] = None
    queue_report(api, notebook, metadata=metadata)
    publisher = ActivityInfoReportPublisher(api.client, schemas)
    assert publisher._setup_report(notebook) == metadata


def test_notebook_questions_have_distinct_semantics(schemas):
    report = build_notebook_report("testdatabase", schemas, publish=False)
    table, changes, daily, distribution = [a["model"] for a in report["analyses"]]
    assert [a["visualization"] for a in (table, changes, daily, distribution)] == [
        "TABLE", "BARCHART", "LINECHART", "PIECHART"]
    assert [d["mappings"][0]["formula"] for d in table["dimensions"]] == [
        "monitored_site.name", "status"]
    assert changes["measures"][0]["formula"] == "changed_count"
    assert daily["dimensions"][0]["dateLevel"] == "DATE"
    assert daily["dimensions"][0]["mappings"][0]["formula"] == "crawl_date"
    dated = report["sources"]["calculatedTables"][0]
    assert '"crawl_date", DATEVALUE(LEFT(crawled_at, 10))' in dated["formula"]
    assert daily["measures"][0]["formId"] == dated["id"]
    assert daily["measures"][0]["formula"] == "crawl_id"
    assert daily["dimensions"][0]["mappings"][0]["formId"] == dated["id"]
    assert distribution["dimensions"][0]["mappings"][0]["formula"] == "status"
    assert distribution["dimensions"][0]["axis"] == "COLUMN"


def test_setup_accepts_content_refinement_but_web_validation_is_strict(api, schemas):
    report = build_notebook_report("testdatabase", schemas, publish=False)
    old = response(report, True)
    old["components"] = old["components"][:3]
    publisher = ActivityInfoReportPublisher(api.client, schemas)
    publisher._validate_report(old, report, check_components=False)
    with pytest.raises(ReportValidationError):
        publisher._validate_report(old, report)
