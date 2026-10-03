"""Small, explicit URL identity rules for monitoring."""

from urllib.parse import urljoin, urlsplit, urlunsplit


def canonicalize_url(url: str, base_url: str | None = None) -> str:
    """Resolve links, lowercase hosts, remove default ports and fragments.

    Paths and query ordering remain significant. Credentials are not supported.
    """
    parts = urlsplit(urljoin(base_url, url) if base_url else url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("An absolute HTTP or HTTPS URL is required")
    if parts.username is not None or parts.password is not None:
        raise ValueError("URLs containing credentials are not supported")
    host = parts.hostname.encode("idna").decode("ascii").lower()
    if ":" in host:
        host = f"[{host}]"
    port = parts.port
    if port is not None and (parts.scheme, port) not in {("http", 80), ("https", 443)}:
        host = f"{host}:{port}"
    return urlunsplit((parts.scheme, host, parts.path or "/", parts.query, ""))
