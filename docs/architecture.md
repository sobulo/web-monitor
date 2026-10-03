# Architecture and project contract

Web Monitor is a modern Python implementation inspired by the 2004 monitoring
system, not a line-for-line Perl port. The repository is `web-monitor`; the
package is `web_monitor`. Modules, functions, and variables use `snake_case`,
classes use `CapWords`, and constants use `UPPER_CASE`.

## Local engine (Stage 1)

`crawler.crawl_site(MonitoredSite(...))` fetches and normalizes pages into an
in-memory `Snapshot`. `diff.compare_snapshots(old, new)` produces a
`SnapshotDiff`. Domain values, normalization, crawling, and comparison are
independent of Flask. The Stage 0 Flask factory and JSON endpoint remain intact.

- **Monitored site:** root URL, nonnegative maximum crawl depth, allowed hostname,
  and an explicit option to include that domain's subdomains. The root must match.
- **Crawl:** one breadth-first observation attempt. Root depth is zero. Relative
  anchor links resolve against the fetched URL. Only HTTP/HTTPS links within the
  hostname restriction are followed; ports do not affect hostname matching.
  Subdomains require opt-in and a dot boundary. No JavaScript or browser runs.
- **Snapshot item/state:** canonical URL, optional title, and SHA-256 of normalized
  UTF-8 text. A snapshot is an immutable, URL-sorted collection with unique URLs.
  Repeated crawls can yield the same state; an observation is not inherently a
  distinct state. No historical storage or record identifiers are introduced.
- **Diff:** URL-only membership determines additions/removals. Different hashes
  at the same URL produce a change retaining both items; equal hashes are omitted.
  Every category is sorted by URL. Comparing against an empty snapshot reports
  every observed item as added.

## Fetching and normalization rules

URL identity resolves relative references, lowercases the host, removes default
ports and fragments, and gives an empty path `/`. Paths and query ordering remain
significant. Credential-bearing URLs are rejected. Each canonical URL is fetched
at most once per crawl. Redirects retain depth, check domain restrictions before
fetching, and have a ten-hop limit; snapshot items use the final URL.

Requests sends `Web-Monitor/0.1`, with a configurable finite positive timeout
(default ten seconds for connection/read inactivity, not a whole-crawl deadline).
Environment proxies and `.netrc` authentication are disabled. HTTP failures,
timeouts, invalid/out-of-scope redirects, redirect loops, or non-HTML responses
raise `CrawlError`. No partial snapshot is returned to imply false removals.

Beautiful Soup's standard-library HTML parser removes scripts, styles, and
inert templates. Comments and attributes contribute no text. Entities are decoded,
text nodes are separated by spaces, and whitespace is collapsed. Title and link
labels contribute text; letter case, punctuation, and Unicode are preserved.
Link destinations alone do not change a page's hash. CSS visibility, semantic
extraction, HTML base elements, and canonical-link metadata are not interpreted.
An explicit HTTP charset is honored; otherwise the parser detects HTML encoding.

Tests serve two checked-in HTML states through an ephemeral loopback HTTP server.
They require no internet, external services, or credentials.

## Integration boundaries

`main.py` exposes the Flask WSGI application. Future routes may call domain
operations; future ActivityInfo/cloud adapters must keep APIs, credentials, and
storage concerns outside the domain. Introduce those interfaces when needed.
`app.yaml` is unchanged future-deployment configuration only. Stage 1 implements
no ActivityInfo integration, persistence, cloud deployment, scheduling, reports,
or historical query UI.
