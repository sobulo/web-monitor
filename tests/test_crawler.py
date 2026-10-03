"""Exercise the complete monitoring pipeline against an actual local server."""

from urllib.parse import urlsplit

import pytest
import requests

from web_monitor.crawler import CrawlError, USER_AGENT, crawl_site
from web_monitor.diff import compare_snapshots
from web_monitor.models import MonitoredSite, SnapshotDiff


def paths(snapshot):
    return [urlsplit(item.canonical_url).path for item in snapshot.items]


def test_identical_crawls_have_no_changes(fixture_site):
    site = MonitoredSite(fixture_site.url, crawl_depth=2)
    old = crawl_site(site)
    new = crawl_site(site)
    assert old == new
    assert compare_snapshots(old, new) == SnapshotDiff()


def test_second_state_reports_added_removed_and_changed(fixture_site):
    site = MonitoredSite(fixture_site.url, crawl_depth=2)
    old = crawl_site(site)
    fixture_site.state = "site_v2"
    new = crawl_site(site)
    diff = compare_snapshots(old, new)
    assert [item.canonical_url for item in diff.added] == [fixture_site.url + "/added.html"]
    assert [item.canonical_url for item in diff.removed] == [fixture_site.url + "/removed.html"]
    assert [item.new.canonical_url for item in diff.changed] == [fixture_site.url + "/changed.html"]
    assert diff.changed[0].old.content_hash != diff.changed[0].new.content_hash
    assert diff.added[0].title == "New feature"


@pytest.mark.parametrize("depth, expected", [
    (0, ["/"]),
    (1, ["/", "/changed.html", "/removed.html", "/section/branch.html", "/stable.html"]),
    (2, ["/", "/changed.html", "/removed.html", "/section/branch.html", "/section/deep.html", "/stable.html"]),
])
def test_depth_is_root_zero_and_fetches_are_unique(fixture_site, depth, expected):
    snapshot = crawl_site(MonitoredSite(fixture_site.url + "/#top", depth))
    assert paths(snapshot) == expected
    fetched_paths = [path for path, _ in fixture_site.requests]
    assert sorted(fetched_paths) == expected
    assert len(fetched_paths) == len(set(fetched_paths))
    assert all(agent == USER_AGENT for _, agent in fixture_site.requests)


def test_external_links_and_non_http_links_are_not_followed(fixture_site):
    crawl_site(MonitoredSite(fixture_site.url, 3))
    assert "/outside.html" not in [path for path, _ in fixture_site.requests]
    # The autouse network guard also fails if localhost (the foreign host) is requested.
    assert len(fixture_site.requests) == 6


def test_relative_redirect_preserves_depth_and_uses_final_url(fixture_site):
    fixture_site.routes["/start"] = (302, {"Location": "section/branch.html#top"}, "")
    snapshot = crawl_site(MonitoredSite(fixture_site.url + "/start", 0))
    assert paths(snapshot) == ["/section/branch.html"]
    assert [path for path, _ in fixture_site.requests] == ["/start", "/section/branch.html"]


def test_redirect_to_already_fetched_page_does_not_refetch(fixture_site):
    fixture_site.routes["/"] = (200, {"Content-Type": "text/html"}, '<a href="/alias">Alias</a><a href="/stable.html">Stable</a>')
    fixture_site.routes["/alias"] = (302, {"Location": "/stable.html"}, "")
    assert paths(crawl_site(MonitoredSite(fixture_site.url, 1))) == ["/", "/stable.html"]
    assert [path for path, _ in fixture_site.requests].count("/stable.html") == 1


@pytest.mark.parametrize("location", ["http://localhost:9/forbidden", "file:///forbidden", "/start"])
def test_unsafe_or_looping_redirect_fails_without_following(fixture_site, location):
    fixture_site.routes["/start"] = (302, {"Location": location}, "")
    with pytest.raises(CrawlError):
        crawl_site(MonitoredSite(fixture_site.url + "/start"))
    assert [path for path, _ in fixture_site.requests] == ["/start"]


@pytest.mark.parametrize("status, headers", [(500, {}), (404, {}), (200, {"Content-Type": "application/pdf"}), (302, {})])
def test_failed_observation_does_not_return_partial_snapshot(fixture_site, status, headers):
    fixture_site.routes["/changed.html"] = (status, headers, "Error")
    with pytest.raises(CrawlError):
        crawl_site(MonitoredSite(fixture_site.url, 1))


def test_timeout_is_explicit_and_wrapped(monkeypatch):
    def fail(session, url, **kwargs):
        assert kwargs == {"timeout": 0.25, "allow_redirects": False}
        assert session.trust_env is False
        assert session.headers["User-Agent"] == USER_AGENT
        raise requests.Timeout("controlled timeout")

    monkeypatch.setattr(requests.Session, "get", fail)
    with pytest.raises(CrawlError, match="controlled timeout"):
        crawl_site(MonitoredSite("http://127.0.0.1/"), timeout=0.25)


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_invalid_timeout_is_rejected(timeout):
    with pytest.raises(ValueError):
        crawl_site(MonitoredSite("http://127.0.0.1/"), timeout=timeout)
