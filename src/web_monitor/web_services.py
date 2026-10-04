"""Request-scoped service composition for the read-only web interface."""

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from web_monitor.activityinfo.client import ActivityInfoClient
from web_monitor.activityinfo.config import ActivityInfoConfig
from web_monitor.activityinfo.persistence import ActivityInfoPersistence, MonitoredSiteRecord
from web_monitor.history import HistoryService
from web_monitor.reporting import ReportingService
from web_monitor.activityinfo.reporting import ActivityInfoReportPublisher


class SiteDirectory(Protocol):
    def list_monitored_sites(self) -> tuple[MonitoredSiteRecord, ...]: ...
    def read_monitored_site(self, record_id: str) -> MonitoredSiteRecord: ...


@dataclass(frozen=True)
class WebServices:
    sites: SiteDirectory
    history: HistoryService
    reporting: ReportingService | None = None
    report_provider: ActivityInfoReportPublisher | None = None


@contextmanager
def configured_services(env_file: Path):
    config = ActivityInfoConfig.from_environment(env_file=env_file)
    with ActivityInfoClient(config) as client:
        store = ActivityInfoPersistence(client)
        yield WebServices(store, HistoryService(store), ReportingService(store),
                          ActivityInfoReportPublisher(client, store.schemas))
