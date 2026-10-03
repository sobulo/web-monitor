"""Immutable in-memory monitoring values, independent of Flask and storage."""

from dataclasses import dataclass
from urllib.parse import urlsplit

from web_monitor.urls import canonicalize_url


@dataclass(frozen=True)
class MonitoredSite:
    """Crawl configuration; an exact hostname is allowed by default."""

    root_url: str
    crawl_depth: int = 0
    allowed_host: str | None = None
    include_subdomains: bool = False

    def __post_init__(self) -> None:
        if (
            isinstance(self.crawl_depth, bool)
            or not isinstance(self.crawl_depth, int)
            or self.crawl_depth < 0
        ):
            raise ValueError("crawl_depth must be a nonnegative integer")
        object.__setattr__(self, "root_url", canonicalize_url(self.root_url))
        host = self.allowed_host or urlsplit(self.root_url).hostname
        host = host.encode("idna").decode("ascii").lower()
        if any(character in host for character in "/?#@"):
            raise ValueError("allowed_host must be a hostname, not a URL")
        object.__setattr__(self, "allowed_host", host)
        if not self.allows(self.root_url):
            raise ValueError("The root URL must be within the allowed host/domain")

    def allows(self, url: str) -> bool:
        """Match a hostname or, explicitly, its dot-delimited subdomains."""
        host = urlsplit(canonicalize_url(url)).hostname
        return host == self.allowed_host or (
            self.include_subdomains and host.endswith(f".{self.allowed_host}")
        )


@dataclass(frozen=True)
class SnapshotItem:
    """One canonical URL and the content observed there."""

    canonical_url: str
    title: str | None
    content_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "canonical_url", canonicalize_url(self.canonical_url))


@dataclass(frozen=True)
class Snapshot:
    """One observation, sorted by URL with no duplicate identities."""

    items: tuple[SnapshotItem, ...] = ()

    def __post_init__(self) -> None:
        items = tuple(sorted(self.items, key=lambda item: item.canonical_url))
        if len({item.canonical_url for item in items}) != len(items):
            raise ValueError("Snapshot URLs must be unique")
        object.__setattr__(self, "items", items)


@dataclass(frozen=True)
class ChangedItem:
    """Both observations for a changed URL."""

    old: SnapshotItem
    new: SnapshotItem


@dataclass(frozen=True)
class SnapshotDiff:
    """URL-sorted additions, removals, and changes; unchanged items are omitted."""

    added: tuple[SnapshotItem, ...] = ()
    removed: tuple[SnapshotItem, ...] = ()
    changed: tuple[ChangedItem, ...] = ()
