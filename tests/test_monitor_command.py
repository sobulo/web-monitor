"""The developer entry point does not log configuration or sensitive errors."""

from datetime import datetime, timezone
import json
from unittest.mock import Mock

from web_monitor import monitor
from web_monitor.activityinfo.client import ActivityInfoError
from web_monitor.activityinfo.config import ActivityInfoConfig
from web_monitor.activityinfo.persistence import CrawlRecord, MonitoredSiteRecord
from web_monitor.models import MonitoredSite, SnapshotDiff
from web_monitor.monitoring import MonitoringResult


def setup_command(monkeypatch, status="initial"):
    config = ActivityInfoConfig("database", "private-test-secret")
    monkeypatch.setattr(monitor.ActivityInfoConfig, "from_environment", Mock(return_value=config))
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=None)
    monkeypatch.setattr(monitor, "ActivityInfoClient", Mock(return_value=client))
    monkeypatch.setattr(monitor, "ActivityInfoPersistence", Mock())
    result = MonitoringResult(
        MonitoredSiteRecord("site1", "Fixture site", MonitoredSite("https://example.invalid/")),
        CrawlRecord("crawl1", "site1", datetime.now(timezone.utc), status,
                    snapshot_id="snapshot1" if status != "error" else None),
        SnapshotDiff() if status != "error" else None,
    )
    service = Mock()
    service.run.return_value = result
    monkeypatch.setattr(monitor, "MonitoringService", Mock(return_value=service))
    return service


def test_command_runs_requested_site_and_prints_concise_result(monkeypatch, capsys):
    service = setup_command(monkeypatch)
    assert monitor.main(["site1"]) == 0
    service.run.assert_called_once_with("site1")
    output = capsys.readouterr()
    summary = json.loads(output.out)
    assert summary["site"] == "Fixture site" and summary["status"] == "initial"
    assert summary["snapshot_id"] == "snapshot1"
    assert "private-test-secret" not in output.out + output.err


def test_persisted_crawl_error_has_nonzero_exit(monkeypatch, capsys):
    setup_command(monkeypatch, "error")
    assert monitor.main(["site1"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "error"


def test_activityinfo_failure_does_not_print_response_body(monkeypatch, capsys):
    service = setup_command(monkeypatch)
    service.run.side_effect = ActivityInfoError("POST", "/update", 403, "Authorization private-test-secret")
    assert monitor.main(["site1"]) == 2
    output = capsys.readouterr()
    assert "403" in output.err and "private-test-secret" not in output.err
    assert output.out == ""
