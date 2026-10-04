"""Serial daily orchestration using the existing one-site monitoring lifecycle."""

import logging
from threading import Lock

from web_monitor.monitoring import MonitoringService
from web_monitor.scheduler_identity import SchedulerInvocation

_LOG = logging.getLogger(__name__)
# Shared across request-scoped services, not a distributed transaction/lease.
_INVOCATION_LOCK = Lock()


class ScheduledMonitoringService:
    def __init__(self, store, monitoring: MonitoringService | None = None):
        self.store = store
        self.monitoring = monitoring or MonitoringService(store)

    def run(self, invocation: SchedulerInvocation):
        results = []
        with _INVOCATION_LOCK:
            for site in sorted(self.store.list_monitored_sites(), key=lambda s: s.record_id):
                if not site.active or site.schedule != 'daily':
                    continue
                _LOG.info('Scheduled monitoring invocation=%s site=%s', invocation.key, site.record_id)
                try:
                    result = self.monitoring.run(site.record_id, invocation=invocation)
                except Exception as error:
                    _LOG.error('Scheduled monitoring failed invocation=%s site=%s category=%s',
                               invocation.key, site.record_id, type(error).__name__)
                    raise
                _LOG.info('Scheduled monitoring invocation=%s site=%s status=%s reused=%s',
                          invocation.key, site.record_id, result.crawl.status, result.reused)
                results.append(result)
        return tuple(results)
