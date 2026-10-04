# Architecture and project contract

Web Monitor is a modern Python implementation inspired by the 2004 monitoring
system, not a line-for-line Perl port. The repository is `web-monitor`; the
package is `web_monitor`. Modules, functions, and variables use `snake_case`,
classes use `CapWords`, and constants use `UPPER_CASE`.

## Local engine (Stage 1)

`crawler.crawl_site(MonitoredSite(...))` fetches and normalizes pages into an
in-memory `Snapshot`. `diff.compare_snapshots(old, new)` produces a
`SnapshotDiff`. Domain values, normalization, crawling, and comparison are
independent of Flask. The Flask factory remains; the original JSON smoke response is served at `/health`.

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
Crawl and Snapshot remain separate; unchanged crawls reference the existing
distinct Snapshot. The Stage 3 service below owns the lifecycle.

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

## Monitoring lifecycle (Stage 3)

`MonitoringService` loads one site's configuration and reconstructs its latest
persisted Snapshot through the adapter. Lookup filters by site, parses timestamps,
and orders by `effective_from`, then `created_at`, then record ID for ties. Missing
history establishes a baseline; malformed/incomplete history stops the run. The
service invokes the existing crawler and `diff.compare_snapshots`; domain objects
remain storage-independent. Flask and the HTTP client do not orchestrate runs.

Every completed monitoring attempt writes a Crawl:

- `initial`: create a complete baseline Snapshot/items; all diff counts are zero.
- `no_change`: create only a Crawl, pointing both references to the existing Snapshot.
- `changed`: create one complete new Snapshot/items; Crawl links old and new states
  and records the existing diff engine's counts.
- `error`: crawler failure creates no Snapshot; Crawl references the prior state
  only through `previous_snapshot`, with zero counts/pages and a sanitized error.

`crawled_at` marks the attempt's start. A new Snapshot's `effective_from` uses
that attempt time; `created_at` is taken immediately before persistence.
Unchanged runs never rewrite Snapshot timestamps or items. Error descriptions use
allowlisted failure categories and HTTP status codes, excluding raw exception
text, URLs, headers, and configuration.

The developer command is `python -m web_monitor.monitor <site-id>`. Attempts must
run serially per site; concurrency control is not implemented. ActivityInfo
writes remain non-atomic: storage failures propagate and must not be reported as
successful or as crawl failures. An interrupted write needs inspection before
retrying, and unavailable storage cannot guarantee a Crawl record.

Controlled local fixtures prove `initial → no_change → changed → no_change → error`.
The separate real-development checkpoint invokes the manual command twice,
checks persisted state/references, and removes only its generated test records.
Scheduling, deployment, reports, notifications, and query UI remain out of scope.

## Historical queries (Stage 4)

`HistoryService` selects observations from Crawl history and reconstructs distinct
Snapshots through the persistence boundary. Results contain domain snapshots,
structured diffs, and opaque references; consumers need no ActivityInfo formulas.
State on a date is the latest successful Crawl through that entire UTC calendar
date. Error Crawls never replace the last successful state. No successful Crawl
means an explicit unavailable state, not an empty or fabricated Snapshot.

Only `changed` Crawls are transitions. Each transition reconstructs both states,
uses the Stage 1 diff engine, and verifies stored counts against its result.
Between-date queries directly compare the two end-of-day states, never accumulated
transition counts. Missing endpoints are identified. Latest change excludes the
initial baseline. Recent history includes every status and loads no child items.

The adapter filters Crawl reads by site, then retrieves typed Crawl metadata.
The service parses/compares aware instants in UTC; timestamp text is never sorted
lexically. This currently reads a site's full Crawl metadata (with individual
record reads) for correctness; it does not scan other sites or preload Snapshots.
Equal timestamps use record reference ordering for listings and same-state ties.
Conflicting tied states or tied change events raise an explicit integrity error;
record IDs do not establish causal order. Queries assume serial monitoring and
immutable completed observations; concurrent edits are not transactionally isolated.
No schema/domain changes or Flask UI are introduced.

## Web presentation (Stage 5)

Flask/Jinja renders a read-only Monitored Site directory, overview, and historical
query pages. Routes use the site directory abstraction and `HistoryService`;
ActivityInfo HTTP, formulas, record decoding, crawling, and diff logic stay outside
presentation. Ordinary GET forms and HTML5 date inputs produce bookmarkable
results. Calendar dates and displayed Crawl times are UTC.

`create_app` constructs without credentials. Tests inject `WebServices` or a
context-manager factory. Real requests lazily compose configuration, client,
persistence, and query service; teardown closes the client. Health and static
requests need no external services. Error pages and logs exclude exception text
and raw upstream responses. Templates autoescape stored text.

The recent-history table reads metadata only; the overview's separate latest-change
summary reconstructs that transition's two Snapshots via the query service.
No routes mutate records or trigger monitoring. Reporting is described below;
deployment, scheduling, and authentication remain outside this stage.

## Reporting boundary (Stage 6)

`ReportingService` owns per-site and overall operational counts and latest UTC
attempt/success/change times through a structural `ReportingStore` protocol.
It imports no ActivityInfo types, formulas, or report JSON. Initial observations
are successful baselines, not changes; errors are failed attempts. Stored diff
counts are summed as operational totals, not endpoint-state differences. A future
Flask chart renderer can reuse these metrics without a report provider.

`ActivityInfoReportPublisher` owns a deterministic database-owned NOTEBOOK report
with four native pivot analyses: Crawl counts by site/status (TABLE), summed
`changed_count` by site (BARCHART), daily Crawl counts (LINECHART), and overall
status counts (PIECHART). The bar measures modified pages, distinct from status
counts; repeated changes to a page count as repeated activity. Pie statuses use
the COLUMN/series axis to form one distribution, rather than ROW facets that
would produce separate 100% pies. The text component
explains baseline, unchanged, transition, and failure semantics.
Sources are Crawl and Monitored Site; no error text or Snapshot Items are included.
It verifies ownership, sources, components, analysis definitions, and publication
through read-back. ActivityInfo can reorder source IDs and adds nullable defaults to
analyses. Source-form membership is compared without relying on order; duplicates
and unexpected sources remain invalid. The analysis `modelType` discriminator shown in Get analysis is needed
on writes although the Update report schema omits it; `showHidden` belongs to the
Pivot query, not the persisted analysis model.

The revised presentation uses two deterministic, database-owned reports. The
existing Notebook remains the detailed report and is linked, never embedded.
A separate SINGLE report contains one site/status Crawl-count bar chart for the
`/reports` iframe. This split follows the user's manual ActivityInfo UI
verification: Single reports expose an embeddable snippet; Notebooks expose a
standalone page. The provider-independent metrics service remains unchanged.

The published API returns report structure without an embed URL. The exact
UI-generated Single iframe source and Notebook URL therefore come from trusted
local configuration. The adapter validates HTTPS, ActivityInfo's host, the
expected report identity, and the embed query. Templates render only the validated
source, never raw provider HTML. `/reports` renders application metrics even when
provider metadata is unavailable; the Notebook remains a separate external link.

The page is Web Monitor's UI. ActivityInfo is a replaceable visualization provider;
a future native renderer can use the same `ReportingService` results.

References: [Update report](https://www.activityinfo.org/support/docs/api/reference/updateReport.html),
[Get analysis](https://www.activityinfo.org/support/docs/api/reference/getAnalysis.html),
[Pivot](https://www.activityinfo.org/support/docs/api/reference/pivot.html),
[Get published report](https://www.activityinfo.org/support/docs/api/reference/getPublishedReport.html),
and [Publishing a Report](https://www.activityinfo.org/support/docs/reports/publishing-a-report.html).

Daily grouping uses a report-only calculated table with `SELECTCOLUMNS` to
project Crawl ID and `DATEVALUE(LEFT(crawled_at, 10))` as a typed date. The line
analysis counts that ID and groups the date using documented `dateLevel: DATE`.
A direct formula dimension saved successfully but was omitted from live pivot
results; projecting the date as a calculated-table column produced the verified
six daily groups. No form or persistence schema is changed. Persistence already
writes ISO timestamps in UTC. Manually imported timestamps must follow
that UTC storage contract. Setup verifies report identity/ownership before
refining content, then verifies the complete new definition and analyses after
writing; the read-only web path always checks the current definition strictly.

The retained [reporting fixture](reporting-acceptance.md) checks fixed expected
metrics against every native analysis. User visual acceptance precedes cleanup.
Formula references: [DATEVALUE](https://www.activityinfo.org/support/docs/formulas/datevalue.html)
and [LEFT](https://www.activityinfo.org/support/docs/formulas/left.html).


## Production runtime and scheduling (Stage 7)

App Engine Standard runs Python 3.11/Gunicorn in the default service: F1,
automatic scaling, minimum zero/maximum one instance. One Gunicorn worker with
four threads keeps the public UI responsive during the daily crawl. The
production ActivityInfo database is separate from local development; bootstrap
adds forms, optional provenance fields, four seed configurations, and the two
published reports, never development history. Production deploys only after the
user supplies the provider's exact Single embed URL and Notebook standalone URL.
Both passes completed on 2026-10-04, including a real scheduled delivery,
production report/history checks, and a guarded retry replay.

Configuration first uses an explicit token. Only an App Engine Standard runtime
without that variable loads Secret Manager via its official client and platform
credentials, using `GOOGLE_CLOUD_PROJECT`. Production pins the newest enabled
secret version during bootstrap. This avoids needing version-list permissions
beyond Secret Accessor and makes rotation explicit. Failed/disabled versions
fail closed; no token or raw cloud exception is logged. Imports, `/health`, and
offline tests need no credentials. App Engine ignores local dotenv files, which
are also excluded from upload.

Cloud Scheduler's App Engine target calls `POST /tasks/monitor`. The dedicated
`login: admin` handler supplies platform authorization; the public UI remains
public. Scheduler headers are invocation metadata, not cryptographic proof.
The route validates the configured project/location/job, the optional
`X-CloudScheduler: true` marker, and a timezone-aware RFC3339 schedule time.
It delegates orchestration to `ScheduledMonitoringService`, which selects active
`daily` sites and calls the existing `MonitoringService` serially. Ordinary
`CrawlError` results persist error Crawls and processing continues. Storage or
infrastructure failures return 500 instead of acknowledging incomplete work.

The identity is a hash of the full job name plus normalized UTC
`X-CloudScheduler-ScheduleTime`. Crawl stores optional `scheduler_invocation`
and `scheduled_at`; bootstrap appends those fields without rewriting records or
accepting incompatible definitions. Legacy development schemas still support
manual monitoring; scheduled writes require the migrated schema. Each site's
scheduled Crawl and potential new Snapshot have deterministic IDs; Snapshot
Item IDs remain derived from Snapshot ID and URL. Manual runs retain random
attempt IDs and their original lifecycle semantics.

On retry, an existing matching Crawl is reused without crawling or writing. A
multi-site retry skips completed sites and continues the remainder. If a complete
scheduled Snapshot survived a failed Crawl write, the service checks it is still
the latest state, loads the preceding state, recomputes the diff, and finalizes
the Crawl without recrawling or rewriting Snapshot/items. Its original observation
time is preserved. Incomplete Snapshots, conflicting provenance, ambiguous order,
or an intervening newer state fail closed for inspection; retries do not silently
skip corrupt history or reinterpret a partially written Snapshot as a baseline.

A process-wide lock serializes scheduled deliveries in the one worker. ActivityInfo
has no transaction/conditional-create guarantee here: rare cross-instance or
cross-version concurrency can still race, and manual writers must not overlap
scheduled monitoring. Deterministic IDs are retry deduplication, not mathematically
exactly-once execution or distributed locking. During deployments, stop old
versions after traffic migration to retain the intended cost/concurrency bounds.
Logs include hashed invocation ID, site ID, status, retry detection, and sanitized
failure category, never headers, token payloads, or raw upstream exceptions.

References: [Scheduler App Engine targets and handler protection](https://docs.cloud.google.com/scheduler/docs/creating),
[App Engine configuration](https://docs.cloud.google.com/appengine/docs/standard/reference/app-yaml),
and [Secret Manager version access](https://docs.cloud.google.com/secret-manager/docs/access-secret-version).

App Engine uses `europe-west`, while Scheduler names the same region
`europe-west1`. The job-name header accepts the configured short name or its full
resource name; both normalize to the configured project/location/job identity.
Scheduled infrastructure failures return HTTP 500: App Engine targets treat 503
as a system failure and retry it outside the job's configured retry count.

Production App Engine manual-run deliveries supplied the configured short job
name but omitted `X-CloudScheduler-ScheduleTime`. Missing times remain rejected;
a near-term clock-triggered delivery is used for production acceptance, followed
by restoration of the daily midnight UTC schedule. A separate guarded replay
checks the persisted invocation without permitting any crawl or record writes.

Acceptance observed four durable Crawls (three `initial`, one `error`), three
complete Snapshots and three child items. All five native analyses matched the
stored counts, the Single iframe and full Notebook rendered the populated data,
and a replay reused all four attempts with crawler and write operations forbidden.
Only the default F1 service/version was present; minimum instances zero (the API
omits this default), maximum one. The staging bucket grants the runtime/build
service account Object Viewer for source upload consumption. No project ID or
secret payload is checked into application configuration.
