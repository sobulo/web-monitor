"""Canonical identity and host/domain boundary checks without network calls."""

import pytest

from web_monitor.models import MonitoredSite
from web_monitor.urls import canonicalize_url


@pytest.mark.parametrize("url, base, expected", [
    ("HTTP://EXAMPLE.INVALID:80#top", None, "http://example.invalid/"),
    ("https://EXAMPLE.INVALID:443/page?q=1#top", None, "https://example.invalid/page?q=1"),
    ("../page#part", "https://example.invalid/section/index", "https://example.invalid/page"),
    ("//EXAMPLE.INVALID/path", "https://example.invalid/", "https://example.invalid/path"),
    ("http://[::1]:8080/", None, "http://[::1]:8080/"),
])
def test_canonicalization(url, base, expected):
    assert canonicalize_url(url, base) == expected


@pytest.mark.parametrize("url", ["file:///tmp/page", "mailto:a@example.invalid", "javascript:void(0)", "https://user:password@example.invalid", "http://example.invalid:bad/"])
def test_unsupported_urls_are_rejected(url):
    with pytest.raises(ValueError):
        canonicalize_url(url)


def test_host_and_optional_subdomains_use_label_boundaries():
    exact = MonitoredSite("https://example.invalid")
    domain = MonitoredSite("https://www.example.invalid", allowed_host="example.invalid", include_subdomains=True)
    assert exact.allows("http://example.invalid:8080/a")
    assert not exact.allows("https://www.example.invalid/")
    assert domain.allows("https://child.example.invalid/")
    assert not domain.allows("https://notexample.invalid/")
    assert not domain.allows("https://example.invalid.evil.invalid/")


@pytest.mark.parametrize("depth", [-1, 1.5, True])
def test_invalid_depth_is_rejected(depth):
    with pytest.raises(ValueError):
        MonitoredSite("https://example.invalid/", depth)


def test_root_must_be_within_scope():
    with pytest.raises(ValueError):
        MonitoredSite("https://example.invalid/", allowed_host="other.invalid")
