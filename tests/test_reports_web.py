"""Reports presentation remains useful independently of provider availability."""

from unittest.mock import Mock

import pytest

from web_monitor.app import create_app
from web_monitor.reporting import ReportingMetrics, ReportingOverview
from web_monitor.web_services import WebServices
from web_monitor.activityinfo.reporting import PublishedReports

EMBED = 'https://www.activityinfo.org/published/single1?embed=true'
NOTEBOOK = 'https://www.activityinfo.org/published/notebook1'


def services():
    metrics = Mock()
    metrics.overview.return_value = ReportingOverview((), ReportingMetrics(total_crawls=12, successful_crawls=9, error_crawls=3, changed_crawls=2))
    provider = Mock()
    provider.published_reports.return_value = PublishedReports('single1','notebook1',EMBED,NOTEBOOK)
    return WebServices(Mock(),Mock(),metrics,provider)


def test_reports_metrics_embed_and_notebook_link():
    deps = services()
    response = create_app({'TESTING':True},services=deps).test_client().get('/reports?embed=https://evil.invalid')
    assert response.status_code == 200
    html = response.text
    assert 'Total Crawls' in html and '<dd>12</dd>' in html
    assert 'Successful Crawls' in html and '<dd>9</dd>' in html
    assert 'Errors' in html and '<dd>3</dd>' in html
    assert f'src="{EMBED}"' in html and f'href="{NOTEBOOK}"' in html
    assert f'src="{NOTEBOOK}"' not in html
    assert 'loading="lazy"' in html and 'title="ActivityInfo monitoring' in html
    assert 'href="/reports"' in html and 'evil.invalid' not in html


def test_missing_embed_keeps_summary_and_details_link():
    deps = services()
    deps.report_provider.published_reports.return_value = PublishedReports('single1','notebook1',None,NOTEBOOK)
    response=create_app(services=deps).test_client().get('/reports')
    assert response.status_code == 200 and '<iframe' not in response.text
    assert 'not been configured' in response.text and NOTEBOOK in response.text


@pytest.mark.parametrize('bad', ['javascript:alert(1)', 'http://www.activityinfo.org/published/single1?embed=true',
    'https://evil.invalid/?embed=true','https://www.activityinfo.org/published/notebook1?embed=true',
    'https://www.activityinfo.org/published/single1?embed=true&token=private-test-secret'])
def test_invalid_embed_never_rendered(bad, caplog):
    deps=services()
    deps.report_provider.published_reports.return_value=PublishedReports('single1','notebook1',bad,NOTEBOOK)
    response=create_app(services=deps).test_client().get('/reports')
    assert response.status_code == 200 and '<iframe' not in response.text
    assert 'temporarily unavailable' in response.text and 'Total Crawls' in response.text
    assert 'private-test-secret' not in response.text + caplog.text


def test_provider_error_is_sanitized(caplog):
    deps=services()
    deps.report_provider.published_reports.side_effect=RuntimeError('Authorization private-test-secret')
    response=create_app(services=deps).test_client().get('/reports')
    assert response.status_code == 200 and 'temporarily unavailable' in response.text
    assert 'private-test-secret' not in response.text + caplog.text


def test_metrics_error_returns_friendly_failure(caplog):
    deps=services()
    deps.reporting.overview.side_effect=ValueError('private-test-secret')
    response=create_app(services=deps).test_client().get('/reports')
    assert response.status_code == 503
    assert 'private-test-secret' not in response.text+caplog.text
