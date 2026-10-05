# Staged execution

The modern implementation was built and verified incrementally so that persistence, history, reporting, and deployment behavior could be checked before later layers depended on them.

## Stage 0 - Repository and project contract

Established the Python package layout, Flask application factory, App Engine entry point, configuration conventions, documentation skeleton, and offline pytest baseline.

## Stage 1 - Local monitoring engine

Implemented deterministic crawling, URL canonicalization, normalization, snapshot construction, and snapshot comparison. Crawls fail as a whole on fetch, redirect, or non-HTML errors rather than returning partial state that could imply false removals.

Offline fixtures run through an ephemeral loopback HTTP server and require no external services.

## Stage 2 - ActivityInfo persistence

ActivityInfo is used as the structured persistence layer. Bootstrap creates and verifies the application forms inside an existing database and seeds four demonstration sites. Runtime adapters verify schema but do not create it implicitly.

The persisted model separates Monitored Site, Crawl, Snapshot, and Snapshot Item. Every crawl is recorded, while unchanged observations reuse the existing Snapshot.

Development bootstrap and persistence checkpoints are available through:

```sh
python -m web_monitor.activityinfo.bootstrap
python -m web_monitor.activityinfo.verify_persistence
```

## Stage 3 - Monitoring lifecycle

The monitoring service connects crawling to persistence and implements the lifecycle:

- `initial`: successful baseline observation;
- `no_change`: successful observation that reuses the current Snapshot;
- `changed`: new distinct Snapshot plus transition counts;
- `error`: failed observation without replacing the last successful state.

A developer can run one monitored site explicitly with:

```sh
python -m web_monitor.monitor <monitored-site-record-id>
```

## Stage 4 - Historical queries

Historical services resolve state from Crawl history and reuse the same snapshot diff engine for comparisons.

```sh
python -m web_monitor.history state <site-id> 2026-09-16
python -m web_monitor.history changes-on <site-id> 2026-09-17
python -m web_monitor.history changes-between <site-id> 2026-09-14 2026-09-19
python -m web_monitor.history latest-change <site-id>
python -m web_monitor.history recent <site-id> --limit 20
```

Dates are interpreted in UTC. A state-on-date query resolves the latest successful observation through that date; error crawls do not become state.

## Stage 5 - Web interface

Added the server-rendered Flask/Jinja interface for site selection, recent history, state on a date, changes on a date, date-to-date comparison, and latest change. Browser routes remain read-only and do not trigger crawls.

The development checkpoint:

```sh
python -m web_monitor.activityinfo.verify_web
```

uses synthetic records and cleans them up after verification.

## Stage 6 - Reporting

Added provider-independent reporting metrics plus ActivityInfo-specific publishing. The production shape uses two reports:

- a Single analysis embedded on `/reports`;
- a Notebook with the fuller set of monitoring analyses, linked from the application.

The publishing adapter uses exact ActivityInfo-supplied publication URLs rather than inferring provider URL patterns. Populated reporting behavior was verified against a deterministic fixture before cleanup; the retained expectations are in `reporting-acceptance.md`.

```sh
python -m web_monitor.activityinfo.reporting
```

## Stage 6.5 - Deployment-readiness review

Before deployment, the project checked the Google Cloud project and billing context, App Engine Standard region/service, runtime identity, Secret Manager access, production ActivityInfo separation, Cloud Scheduler requirements, and basic cost safeguards.

The resulting deployment sanity checks are summarized in `docs/discussions/4_Deployment Discussions - Sanity Checks to Verify Expected Behavior and Alignment with Best Practices.pdf`.

## Stage 7 - Production deployment and scheduling

Production runs on App Engine Standard with an F1 instance class, automatic scaling from zero to one instance, and a separate ActivityInfo production database. The ActivityInfo API token is loaded lazily from Google Cloud Secret Manager; local `.env` behavior is unchanged.

Cloud Scheduler invokes `POST /tasks/monitor` daily. Scheduler identity combines the job name and `X-CloudScheduler-ScheduleTime`; scheduled Crawl and Snapshot identities are deterministic so retries can reuse completed work instead of duplicating state.

A genuine scheduled delivery was used for acceptance because App Engine manual-run delivery omitted the required schedule-time header. The first production run produced three successful baselines and one recorded IMDb error. Retry replay reused all four Crawl records without crawling or writing again.

The complete offline suite passed with 269 tests at the Stage 7 gate.
