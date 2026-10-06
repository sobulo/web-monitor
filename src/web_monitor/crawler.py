"""Synchronous, bounded-depth HTML crawling with no persistence or Flask."""

from collections import deque
import math

import requests

from web_monitor.models import MonitoredSite, Snapshot, SnapshotItem
from web_monitor.normalization import normalize_html
from web_monitor.urls import canonicalize_url

USER_AGENT = "Web-Monitor/0.1"
DEFAULT_TIMEOUT = 10.0
MAX_REDIRECTS = 10
REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class CrawlError(RuntimeError):
    """The observation failed; callers must not compare a partial snapshot."""


def crawl_site(site: MonitoredSite, *, timeout: float = DEFAULT_TIMEOUT) -> Snapshot:
    """Visit HTML pages breadth-first, with root depth zero and unique fetches.

    Redirects retain the link's depth and are checked before every request.
    Request failures, invalid redirects, and non-HTML responses fail the crawl.
    """
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be finite and positive")
    pending = deque([(site.root_url, 0)])
    fetched: set[str] = set()
    items: list[SnapshotItem] = []
    with requests.Session() as session:
        # Do not read proxy credentials or .netrc authentication from the environment.
        session.trust_env = False
        session.headers["User-Agent"] = USER_AGENT
        while pending:
            url, depth = pending.popleft()
            redirect_chain: set[str] = set()
            while url not in fetched:
                fetched.add(url)
                redirect_chain.add(url)
                try:
                    with session.get(url, timeout=timeout, allow_redirects=False) as response:
                        if response.status_code in REDIRECT_STATUSES:
                            location = response.headers.get("Location")
                            if not location:
                                raise CrawlError(f"Redirect without Location: {url}")
                            try:
                                target = canonicalize_url(location, url)
                            except ValueError as error:
                                raise CrawlError(f"Invalid redirect from {url}") from error
                            if not site.allows(target):
                                raise CrawlError(f"Redirect leaves allowed host/domain: {url}")
                            if (
                                target in redirect_chain
                                or len(redirect_chain) > MAX_REDIRECTS
                            ):
                                raise CrawlError(f"Redirect loop or limit exceeded: {url}")
                            url = target
                            continue
                        response.raise_for_status()
                        if response.status_code != 200:
                            raise requests.HTTPError("Expected HTTP 200", response=response)
                        content_type = response.headers.get("Content-Type", "")
                        media_type = content_type.split(";", 1)[0].strip().lower()
                        if media_type not in {"text/html", "application/xhtml+xml"}:
                            raise CrawlError(f"Expected HTML content: {url}")
                        # Honor an explicit HTTP charset; otherwise let the HTML parser
                        # inspect document encoding rather than Requests' Latin-1 default.
                        html = (
                            response.text
                            if "charset=" in content_type.lower()
                            else response.content
                        )
                        page = normalize_html(html)
                except requests.RequestException as error:
                    raise CrawlError(f"Failed to fetch {url}: {error}") from error
                items.append(SnapshotItem(url, page.title, page.content_hash))
                if depth < site.crawl_depth:
                    links: set[str] = set()
                    for link in page.links:
                        try:
                            target = canonicalize_url(link, url)
                        except ValueError:
                            continue
                        if site.allows(target):
                            links.add(target)
                    pending.extend((target, depth + 1) for target in sorted(links))
                break
    return Snapshot(tuple(items))
