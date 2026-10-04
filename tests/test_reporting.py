"""Operational reporting semantics with no provider dependencies."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from web_monitor.activityinfo.persistence import CrawlRecord, MonitoredSiteRecord
from web_monitor.models import MonitoredSite
from web_monitor.reporting import ReportingIntegrityError, ReportingService

NOW = datetime(2026,10,4,tzinfo=timezone.utc)


def site(identity, name):
    return MonitoredSiteRecord(identity, name, MonitoredSite('https://example.invalid/'))


def test_empty_and_site_without_crawls():
    store = Mock()
    store.list_monitored_sites.return_value = ()
    result = ReportingService(store).overview()
    assert result.monitored_sites == result.totals.total_crawls == 0
    assert result.totals.last_crawl is None
    store.list_monitored_sites.return_value = (site('a','Alpha'),)
    store.list_crawls.return_value = ()
    result = ReportingService(store).overview()
    assert result.monitored_sites == 1
    assert result.sites[0].metrics.total_crawls == 0


def test_status_counts_totals_timestamps_and_site_order():
    store = Mock()
    store.list_monitored_sites.return_value = (site('z','Zulu'), site('a','Alpha'))
    records = [CrawlRecord('c'+str(index),'a', NOW+timedelta(hours=index), status,
                          added_count=2 if status == 'changed' else 0,
                          removed_count=3 if status == 'changed' else 0,
                          changed_count=4 if status == 'changed' else 0)
               for index, status in enumerate(('initial','no_change','changed','error'))]
    other = CrawlRecord('other','z',NOW.astimezone(timezone(timedelta(hours=2))),'initial')
    store.list_crawls.side_effect = lambda identity: records if identity == 'a' else [other]
    result = ReportingService(store).overview()
    assert [row.site_id for row in result.sites] == ['a','z']
    value = result.totals
    assert (value.total_crawls,value.successful_crawls,value.error_crawls,value.changed_crawls,
            value.no_change_crawls,value.initial_crawls) == (5,4,1,1,1,2)
    assert (value.total_added,value.total_removed,value.total_changed) == (2,3,4)
    assert value.last_crawl == NOW + timedelta(hours=3)
    assert value.last_successful_crawl == value.last_change == NOW + timedelta(hours=2)
    assert result.sites[1].metrics.last_crawl.tzinfo is timezone.utc
    assert result.sites[1].metrics.changed_crawls == 0
    assert result.sites[1].metrics.last_change is None


@pytest.mark.parametrize('changes', [{'status':'unknown'}, {'crawled_at':NOW.replace(tzinfo=None)},
    {'added_count':-1}, {'changed_count':True}, {'monitored_site_id':'other'}])
def test_corrupt_crawls_fail(changes):
    store = Mock()
    store.list_monitored_sites.return_value = (site('a','Alpha'),)
    store.list_crawls.return_value = (replace(CrawlRecord('c','a',NOW,'initial'), **changes),)
    with pytest.raises(ReportingIntegrityError):
        ReportingService(store).overview()


def test_duplicate_crawls_fail():
    store = Mock()
    store.list_monitored_sites.return_value = (site('a','Alpha'),)
    store.list_crawls.return_value = (CrawlRecord('c','a',NOW,'initial'),) * 2
    with pytest.raises(ReportingIntegrityError):
        ReportingService(store).overview()
