"""Deterministic HTML text normalization without semantic extraction."""

from dataclasses import dataclass
from hashlib import sha256

from bs4 import BeautifulSoup


@dataclass(frozen=True)
class NormalizedPage:
    """Normalized text, optional title, and raw links from one HTML document."""

    title: str | None
    text: str
    links: tuple[str, ...]

    @property
    def content_hash(self) -> str:
        """Hash UTF-8 normalized text using SHA-256."""
        return sha256(self.text.encode("utf-8")).hexdigest()


def normalize_html(html: str | bytes) -> NormalizedPage:
    """Remove scripts/styles/templates and collapse whitespace, preserving case.

    HTML entities are decoded; comments and attributes do not contribute text.
    Title and link labels are included in document text. CSS is not evaluated.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["script", "style", "template"]):
        tag.decompose()
    title = " ".join(soup.title.get_text(" ").split()) if soup.title else None
    text = " ".join(soup.get_text(" ").split())
    links = tuple(sorted({str(tag["href"]) for tag in soup.find_all("a", href=True)}))
    return NormalizedPage(title or None, text, links)
