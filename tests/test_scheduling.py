"""Scheduled deliveries and retry boundaries, with no cloud or external HTTP."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from web_monitor.activityinfo.persistence import (
    MonitoredSiteRecord, PersistenceError, SnapshotItemRecord, SnapshotRecord, StoredSnapshot,
)
from web_monitor.activityinfo.schema import stable_id
from web_monitor.app import create_app
from web_monitor.crawler import CrawlError
from web_monitor.models import MonitoredSite, Snapshot, SnapshotItem
from web_monitor.monitoring import MonitoringService
from web_monitor.scheduler_identity import SchedulerInvocation
from web_monitor.scheduling import ScheduledMonitoringService

NOW = datetime(2026, 10, 4, tzinfo=timezone.utc)
JOB = 'projects/test-project/locations/europe-west1/jobs/web-monitor-daily'
HEADERS = {'X-CloudScheduler': 'true', 'X-CloudScheduler-JobName': JOB,
           'X-CloudScheduler-ScheduleTime': '2026-10-04T00:00:00Z'}
CONFIG = {'TESTING': True, 'GOOGLE_CLOUD_PROJECT': 'test-project',
          'SCHEDULER_LOCATION': 'europe-west1', 'SCHEDULER_JOB_NAME': 'web-monitor-daily'}
SNAPSHOT = Snapshot((SnapshotItem('https://fixture.invalid/', 'Fixture', 'a' * 64),))


class Store:
    """Durable records survive service reconstruction; assert against extra writes."""

    def __init__(self):
        self.sites = [MonitoredSiteRecord('site1', 'Fixture', MonitoredSite('https://fixture.invalid/'))]
        self.crawls = {}
        self.snapshots = {}
        self.writes = []

    def list_monitored_sites(self):
        return tuple(self.sites)

    def read_monitored_site(self, site_id):
        return next(site for site in self.sites if site.record_id == site_id)

    def find_crawl(self, record_id):
        return self.crawls.get(record_id)

    def find_snapshot(self, record_id):
        return self.snapshots.get(record_id)

    def load_latest_snapshot(self, site_id, *, exclude_snapshot_id=None):
        values = [s for s in self.snapshots.values() if s.record.monitored_site_id == site_id
                  and s.record.record_id != exclude_snapshot_id]
        return max(values, key=lambda s: (s.record.effective_from, s.record.created_at, s.record.record_id), default=None)

    def persist_snapshot(self, snapshot, *, monitored_site_id, created_at, effective_from, record_id=None):
        record_id = record_id or f'manual{len(self.snapshots)}'
        assert record_id not in self.snapshots, 'duplicate snapshot write'
        record = SnapshotRecord(record_id, monitored_site_id, created_at, effective_from, len(snapshot.items))
        children = tuple(SnapshotItemRecord(stable_id(record_id, i.canonical_url), record_id, i)
                         for i in snapshot.items)
        saved = StoredSnapshot(record, snapshot, children)
        self.snapshots[record_id] = saved
        self.writes.append(('snapshot', record_id))
        return saved

    def create_crawl(self, record):
        assert record.record_id not in self.crawls, 'duplicate crawl write'
        self.crawls[record.record_id] = record
        self.writes.append(('crawl', record.record_id))


def client_for(store, crawler, now=NOW):
    monitoring = MonitoringService(store, crawler=crawler, clock=lambda: now)
    services = SimpleNamespace(scheduler=ScheduledMonitoringService(store, monitoring))
    return create_app(CONFIG, services=services).test_client()


def test_first_retry_new_invocation_and_manual_remain_independent():
    store, crawler = Store(), Mock(return_value=SNAPSHOT)
    client = client_for(store, crawler)
    assert client.post('/tasks/monitor', headers=HEADERS).json['completed'] == 1
    assert len(store.crawls) == len(store.snapshots) == 1
    crawl = next(iter(store.crawls.values()))
    invocation = SchedulerInvocation(JOB, NOW)
    assert crawl.record_id == invocation.crawl_id('site1')
    assert crawl.snapshot_id == invocation.snapshot_id('site1')
    assert crawl.status == 'initial' and crawl.scheduled_at == NOW
    assert crawl.scheduler_invocation == invocation.key
    writes = list(store.writes)
    # Simulate a fresh process after a lost acknowledgement; equivalent offset.
    retry = client_for(store, crawler)
    response = retry.post('/tasks/monitor', headers={**HEADERS, 'X-CloudScheduler-ScheduleTime': '2026-10-04T01:00:00+01:00'})
    assert response.status_code == 200 and response.json['reused'] == 1
    assert crawler.call_count == 1 and store.writes == writes
    response = client.post('/tasks/monitor', headers={**HEADERS, 'X-CloudScheduler-ScheduleTime': '2026-10-05T00:00:00Z'})
    assert response.status_code == 200 and response.json['reused'] == 0
    assert crawler.call_count == 2 and len(store.crawls) == 2 and len(store.snapshots) == 1
    manual = MonitoringService(store, crawler=crawler, clock=lambda: NOW).run('site1')
    assert manual.crawl.scheduler_invocation is manual.crawl.scheduled_at is None
    assert len(store.crawls) == 3 and manual.crawl.status == 'no_change'


def test_partial_multisite_failure_retries_only_unfinished_site(caplog):
    store, crawler = Store(), Mock(return_value=SNAPSHOT)
    store.sites.extend([replace(store.sites[0], record_id='site2'),
                        replace(store.sites[0], record_id='site3', active=False),
                        replace(store.sites[0], record_id='site4', schedule='weekly')])
    original = store.load_latest_snapshot
    def fail_second(site_id, **kwargs):
        if site_id == 'site2':
            raise PersistenceError('sensitive infrastructure detail')
        return original(site_id, **kwargs)
    store.load_latest_snapshot = fail_second
    assert client_for(store, crawler).post('/tasks/monitor', headers=HEADERS).status_code == 500
    assert len(store.crawls) == len(store.snapshots) == crawler.call_count == 1
    assert 'sensitive infrastructure detail' not in caplog.text
    store.load_latest_snapshot = original
    response = client_for(store, crawler).post('/tasks/monitor', headers=HEADERS)
    assert response.status_code == 200 and response.json == {
        'completed': 2, 'reused': 1, 'invocation': SchedulerInvocation(JOB, NOW).key,
    }
    assert len(store.crawls) == len(store.snapshots) == crawler.call_count == 2


def test_normal_crawl_failure_is_durable_and_does_not_stop_others():
    store = Store()
    store.sites.append(replace(store.sites[0], record_id='site2'))
    crawler = Mock(side_effect=[CrawlError('secret detail'), SNAPSHOT])
    client = client_for(store, crawler)
    assert client.post('/tasks/monitor', headers=HEADERS).status_code == 200
    assert [c.status for c in store.crawls.values()] == ['error', 'initial']
    assert len(store.snapshots) == 1
    assert client.post('/tasks/monitor', headers=HEADERS).json['reused'] == 2
    assert crawler.call_count == 2


@pytest.mark.parametrize('changed', [False, True])
def test_complete_snapshot_recovers_after_failed_crawl_write(changed):
    store, crawler = Store(), Mock(return_value=SNAPSHOT)
    if changed:
        MonitoringService(store, crawler=lambda s: Snapshot(), clock=lambda: NOW - timedelta(days=1)).run('site1')
    write = store.create_crawl
    store.create_crawl = Mock(side_effect=PersistenceError('write unavailable'))
    client = client_for(store, crawler)
    assert client.post('/tasks/monitor', headers=HEADERS).status_code == 500
    saved = list(store.snapshots.values())
    assert len(saved) == (2 if changed else 1)
    store.create_crawl = write
    assert client_for(store, crawler).post('/tasks/monitor', headers=HEADERS).status_code == 200
    assert crawler.call_count == 1 and list(store.snapshots.values()) == saved
    crawl = store.crawls[SchedulerInvocation(JOB, NOW).crawl_id('site1')]
    assert crawl.status == ('changed' if changed else 'initial')
    assert crawl.added_count == (1 if changed else 0)
    assert crawl.crawled_at == NOW


def test_lost_crawl_write_ack_is_detected_without_rewriting():
    store, crawler = Store(), Mock(return_value=SNAPSHOT)
    write = store.create_crawl
    def lost_ack(record):
        write(record)
        raise PersistenceError('Lost acknowledgement')
    store.create_crawl = lost_ack
    client = client_for(store, crawler)
    assert client.post('/tasks/monitor', headers=HEADERS).status_code == 500
    assert client.post('/tasks/monitor', headers=HEADERS).json['reused'] == 1
    assert crawler.call_count == len(store.crawls) == len(store.snapshots) == 1


def test_incomplete_snapshot_fails_closed_before_crawl_or_writes():
    store, crawler = Store(), Mock(return_value=SNAPSHOT)
    store.find_snapshot = Mock(side_effect=PersistenceError('Incomplete Snapshot'))
    assert client_for(store, crawler).post('/tasks/monitor', headers=HEADERS).status_code == 500
    crawler.assert_not_called()
    assert store.writes == []


def test_recovery_rejects_a_newer_foreign_snapshot():
    store, crawler = Store(), Mock(return_value=SNAPSHOT)
    invocation = SchedulerInvocation(JOB, NOW)
    for key, instant in [(invocation.snapshot_id('site1'), NOW), ('newer', NOW + timedelta(hours=1))]:
        store.persist_snapshot(SNAPSHOT, monitored_site_id='site1', created_at=instant, effective_from=instant, record_id=key)
    assert client_for(store, crawler).post('/tasks/monitor', headers=HEADERS).status_code == 500
    crawler.assert_not_called()
    assert not store.crawls


@pytest.mark.parametrize('headers', [
    {}, {k: v for k, v in HEADERS.items() if k != 'X-CloudScheduler-JobName'},
    {k: v for k, v in HEADERS.items() if k != 'X-CloudScheduler-ScheduleTime'},
    {**HEADERS, 'X-CloudScheduler': 'false'},
    {**HEADERS, 'X-CloudScheduler-JobName': JOB.replace('test-project', 'another-project')},
    {**HEADERS, 'X-CloudScheduler-JobName': JOB.replace('daily', 'other')},
    *[{**HEADERS, 'X-CloudScheduler-ScheduleTime': value} for value in
      ['yesterday', '2026-10-04', '2026-10-04T00:00:00', '2026-13-04T00:00:00Z']],
])
def test_bad_headers_fail_before_any_services(headers):
    factory = Mock(side_effect=AssertionError('Must not open any clients'))
    client = create_app({**CONFIG, 'SERVICES_FACTORY': factory}).test_client()
    assert client.post('/tasks/monitor', headers=headers).status_code == 400
    factory.assert_not_called()


def test_only_post_and_optional_scheduler_marker():
    store, crawler = Store(), Mock(return_value=SNAPSHOT)
    client = client_for(store, crawler)
    assert client.get('/tasks/monitor', headers=HEADERS).status_code == 405
    assert client.post('/tasks/monitor', headers={k: v for k, v in HEADERS.items() if k != 'X-CloudScheduler'}).status_code == 200
    assert crawler.call_count == 1


def test_job_name_also_changes_invocation_identity():
    assert SchedulerInvocation(JOB, NOW).key != SchedulerInvocation(JOB + '-other', NOW).key


def test_short_provider_job_name_normalizes_to_full_identity():
    store, crawler = Store(), Mock(return_value=SNAPSHOT)
    client = client_for(store, crawler)
    short = {**HEADERS, 'X-CloudScheduler-JobName': 'web-monitor-daily'}
    assert client.post('/tasks/monitor', headers=short).status_code == 200
    assert client.post('/tasks/monitor', headers=HEADERS).json['reused'] == 1
    assert crawler.call_count == 1
