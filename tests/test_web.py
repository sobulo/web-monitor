"""Offline routes and templates using a persistence fake and real query service."""

from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from web_monitor.activityinfo.client import ActivityInfoError
from web_monitor.activityinfo.persistence import (
    CrawlRecord, MonitoredSiteRecord, SnapshotRecord, StoredSnapshot,
)
from web_monitor.app import create_app
from web_monitor.history import HistoryIntegrityError, HistoryService
from web_monitor.models import MonitoredSite, Snapshot, SnapshotItem
from web_monitor.web_services import WebServices


@pytest.fixture
def web():
    def instant(day):
        return datetime(2026, 9, day, 12, tzinfo=timezone.utc)
    site = MonitoredSiteRecord('site1', 'Test journal', MonitoredSite('https://example.invalid/', 2))
    a = Snapshot((SnapshotItem('https://example.invalid/common', 'Old title', 'old-hash'),
                  SnapshotItem('https://example.invalid/removed', 'Removed page', 'removed-hash')))
    b = Snapshot((SnapshotItem('https://example.invalid/common', 'New title', 'new-hash'),
                  SnapshotItem('https://example.invalid/added', 'Added page', 'added-hash')))
    snapshots = {name: StoredSnapshot(SnapshotRecord(name, 'site1', instant(14), instant(14), 2), snapshot, ())
                 for name, snapshot in [('stateA', a), ('stateB', b)]}
    store = Mock()
    store.list_monitored_sites.return_value = (site,)
    store.read_monitored_site.side_effect = lambda reference: {'site1':site}[reference]
    store.load_snapshot.side_effect = snapshots.__getitem__
    store.list_crawls.return_value = (
        CrawlRecord('crawl1','site1',instant(14),'initial','stateA',pages_crawled=2),
        CrawlRecord('crawl2','site1',instant(15),'no_change','stateA','stateA',pages_crawled=2),
        CrawlRecord('crawl3','site1',instant(17),'changed','stateB','stateA',1,1,1,2),
        CrawlRecord('crawl4','site1',instant(20),'error',previous_snapshot_id='stateB',error_message='Fetch timed out'),
    )
    services = WebServices(store, HistoryService(store))
    app = create_app({'TESTING':True}, services=services)
    return app.test_client(), store, services


def test_home_and_empty(web):
    client, store, _ = web
    response = client.get('/')
    assert response.status_code == 200
    html = response.text
    for text in ('Web Monitor','Monitored Site','Test journal','https://example.invalid/','Active','/sites/site1'):
        assert text in html
    store.list_monitored_sites.return_value = ()
    assert 'No Monitored Sites' in client.get('/').text


def test_overview_history_and_error(web):
    response = web[0].get('/sites/site1')
    assert response.status_code == 200
    for text in ('Test journal','Crawl depth','daily','Recent crawl history','Fetch timed out',
                 'Latest change','status-error','status-changed','status-no_change','status-initial'):
        assert text in response.text
    assert response.text.index('status-error') < response.text.index('status-changed') < response.text.index('status-initial')
    # Only the latest-change summary reconstructs its two snapshots. The table
    # itself must never load items for each listed Crawl.
    assert web[1].load_snapshot.call_count == 2


@pytest.mark.parametrize('path,text', [
    ('state?date=2026-09-16','Old title'),
    ('state?date=2026-09-13','No state available'),
    ('changes-on?date=2026-09-17','Removed page'),
    ('changes-on?date=2026-09-16','No changes recorded on this date.'),
    ('compare?start=2026-09-14&end=2026-09-17','Endpoint state comparison'),
    ('compare?start=2026-09-14&end=2026-09-15','No differences between these states.'),
    ('compare?start=2026-09-13&end=2026-09-17','No state available for start date.'),
    ('latest-change','New title'),
])
def test_history_pages(web, path, text):
    response = web[0].get('/sites/site1/' + path)
    assert response.status_code == 200
    assert text in response.text
    assert 'Historical dates are interpreted in UTC.' in response.text
    assert 'href="/sites/site1"' in response.text


@pytest.mark.parametrize('path', ['changes-on?date=2026-09-17', 'compare?start=2026-09-14&end=2026-09-17','latest-change'])
def test_detailed_diff(web, path):
    html = web[0].get('/sites/site1/' + path).text
    for value in ('Added page','Removed page','Old title','New title','old-hash','new-hash',
                  '>Added<','>Removed<','>Changed<'):
        assert value in html


def test_state_has_complete_clickable_items(web):
    html = web[0].get('/sites/site1/state?date=2026-09-16').text
    assert 'href="https://example.invalid/common"' in html
    assert 'href="https://example.invalid/removed"' in html
    assert 'old-hash' in html and 'removed-hash' in html
    assert '2026-09-15 12:00:00' in html
    assert '<strong>2</strong> Snapshot items' in html


@pytest.mark.parametrize('path', ['state?date=not-a-date','state?date=2026-02-30',
    'state?date=', 'state?other=1','changes-on?date=20260917',
    'compare?start=2026-09-17','compare?start=2026-09-17&end=2026-09-14'])
def test_invalid_dates(web, path):
    response = web[0].get('/sites/site1/' + path)
    assert response.status_code == 400
    assert 'role="alert"' in response.text
    if 'end=2026-09-14' in path:
        assert 'End date must be on or after start date.' in response.text
    web[1].list_crawls.assert_not_called()


@pytest.mark.parametrize('path', ['state','changes-on','compare'])
def test_blank_form_navigation(web, path):
    response = web[0].get('/sites/site1/' + path)
    assert response.status_code == 200
    assert 'method="get"' in response.text and 'type="date"' in response.text
    assert 'role="alert"' not in response.text


def test_no_history(web):
    web[1].list_crawls.return_value = ()
    assert 'No change recorded yet.' in web[0].get('/sites/site1/latest-change').text
    assert 'No Crawls recorded yet.' in web[0].get('/sites/site1').text


@pytest.mark.parametrize('path', ['/sites/unknown','/sites/bad-id','/missing'])
def test_unknown_site_and_page(web, path):
    response = web[0].get(path)
    assert response.status_code == 404
    assert 'could not be found' in response.text


@pytest.mark.parametrize('error', [ActivityInfoError('GET','/secret',403,'Authorization private-test-secret'),
                                     HistoryIntegrityError('private-test-secret'), RuntimeError('private-test-secret')])
def test_errors_hide_secrets(web, caplog, error):
    web[1].list_crawls.side_effect = error
    response = web[0].get('/sites/site1')
    assert response.status_code == 503
    assert 'Monitoring data could not be loaded' in response.text
    assert 'private-test-secret' not in response.text + caplog.text
    assert 'Authorization' not in response.text + caplog.text
    assert 'Web query failed' in caplog.text


def test_upstream_unknown_site(web):
    web[1].read_monitored_site.side_effect = ActivityInfoError('GET','/record',404,'private-test-secret')
    assert web[0].get('/sites/unknown').status_code == 404


def test_html_escaping(web):
    from dataclasses import replace
    site = web[1].list_monitored_sites.return_value[0]
    web[1].list_monitored_sites.return_value = (replace(site, name='<script>alert(1)</script>'),)
    html = web[0].get('/').text
    assert '<script>' not in html and '&lt;script&gt;' in html


def test_health_and_static_do_not_construct_dependencies():
    factory = Mock(side_effect=AssertionError('No external services'))
    client = create_app({'TESTING':True,'SERVICES_FACTORY':factory}).test_client()
    assert client.get('/health').json == {'service':'web-monitor','status':'ok'}
    assert client.get('/static/style.css').status_code == 200
    factory.assert_not_called()


def test_request_services_close_on_success_and_failure(web):
    events = []
    @contextmanager
    def factory():
        events.append('open')
        try:
            yield web[2]
        finally:
            events.append('close')
    client = create_app({'TESTING':True,'SERVICES_FACTORY':factory}).test_client()
    assert client.get('/').status_code == 200
    web[1].list_monitored_sites.side_effect = RuntimeError('private-test-secret')
    assert client.get('/').status_code == 503
    assert events == ['open','close','open','close']
