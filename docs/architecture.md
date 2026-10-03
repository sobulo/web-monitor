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

## ActivityInfo persistence (Stage 2)

`activityinfo/` owns configuration, HTTP, schema bootstrap, and persistence DTOs.
It depends on domain values; the crawler, normalization, diff, domain model, and
Flask routes do not depend on it. Runtime adapters verify existing schema but do
not create it. The database is created manually and supplied through
`ACTIVITYINFO_DATABASE_ID`; there is no database creation/deletion operation.
The token comes from configuration, with explicit local `.env` loading only.
Environment values take precedence, allowing later App Engine environment and
Secret Manager wiring without changes to the domain.

Bootstrap uses database-scoped deterministic form IDs (ActivityInfo has field
codes, not form codes), plus stable field codes. Conflicting IDs, labels, codes,
types, or links stop bootstrap. Missing fields are appended while existing fields
are preserved. The preflight checks all existing application forms before writes;
read-back checks resource types, references, field codes, and subform ownership.
Run bootstrap serially; this is not a concurrent schema migration system.

Monitored Site stores configuration. Crawl references Monitored Site and optional
current/previous Snapshots; its status vocabulary is `initial`, `no_change`,
`changed`, and `error`. Snapshot references Monitored Site. Snapshot Item is a
real child/subform: Snapshot's `items` field creates it, its schema identifies
Snapshot as `parentFormId`, and child records carry `parentRecordId`.
Crawl and Snapshot remain separate: later unchanged crawls will reference an
existing distinct Snapshot. This stage does not choose or persist that lifecycle.

Booleans use single selections (`true`/`false`), status uses a single selection,
counts use quantities, and timestamps use ISO-8601 UTC text to preserve time of
day (the documented `date` type stores only calendar dates). Reference values
include form and record IDs. Storage IDs stay in persistence DTOs, outside Stage 1
objects. Snapshot writes create the parent then child records in separate
requests; they are not atomic. Interrupted writes require inspection of the
parent ID, and loading rejects an incomplete child count.

Offline tests script HTTP responses; they do not prove server acceptance. The
explicit `verify_persistence` command checks real schema, a non-duplicating
bootstrap rerun, four seeds, record read/write, and parent-child linkage using
synthetic data. It cleans up only its generated records after successful checks.
No live crawling is used for that checkpoint.

API contracts follow the official [schema API](https://www.activityinfo.org/support/docs/api/reference/updateFormSchema.html),
[add-form API](https://www.activityinfo.org/support/docs/api/reference/addForm.html),
[record update API](https://www.activityinfo.org/support/docs/api/reference/updateRecords.html),
and [query API](https://www.activityinfo.org/support/docs/api/reference/queryRows.html).
Schema rejection stops work; it never triggers a replacement model.

Stage 3 lifecycle, scheduling, deployment, reports, and query UI remain out of scope.
