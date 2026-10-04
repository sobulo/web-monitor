# Web Monitor

A modern Python implementation inspired by the 2004 monitoring system.
Includes a Flask web interface, local monitoring engine, and ActivityInfo history.

## Run locally

Use Python 3.11 or newer (the future App Engine configuration targets 3.11).

```sh
cd /Users/olusegunsobulo/Documents/projects/web-monitor
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
python main.py
```

For the real web interface, configure the existing development ActivityInfo database
and token in the ignored project-root `.env` (see `.env.example` and bootstrap below).
Open <http://127.0.0.1:8080/> to choose a Monitored Site, inspect recent Crawls,
view state or changes on a date, compare dates, and view the latest change.
Historical dates are UTC; GET result URLs can be bookmarked. Stop with Ctrl-C.

`/health` returns `{"service":"web-monitor","status":"ok"}` without credentials.
Offline tests and the local engine also need no credentials. The real UI and
ActivityInfo commands load `.env` explicitly; environment variables take precedence.

## Verify

With the virtual environment active:

```sh
python -m pytest
python -c "import web_monitor; from web_monitor.app import create_app; print(create_app().name)"
```

## Layout and configuration

- `src/web_monitor/`: installed application package and Flask factory.
- `main.py`: local development and WSGI entry point.
- `tests/`: Flask smoke test and controlled local monitoring fixtures/tests.
- `requirements.txt`: runtime dependencies, also read by `pyproject.toml`.
- `app.yaml`: future App Engine Standard runtime and Gunicorn entry point.
- `.gcloudignore`: upload exclusions, including local environments and secrets.
- [Architecture](docs/architecture.md): scope, concepts, and integration boundaries.

The `src` layout requires installing the package for local imports. The future
Gunicorn entry point explicitly includes `src` on its Python path because
App Engine installs runtime dependencies from `requirements.txt`.
See Google's [Python runtime documentation](https://docs.cloud.google.com/appengine/docs/standard/python3/runtime).
No cloud resources are created or deployed in Stage 0.

## Stage 1 tests

With the virtual environment active, install the test dependencies and run:

```sh
python -m pip install -e '.[test]'
python -m pytest tests/test_crawler.py tests/test_normalization.py tests/test_diff.py tests/test_urls.py
python -m pytest
```

The fixtures run on an ephemeral local HTTP server. No internet or credentials
are required to run the tests; the full suite includes the Stage 0 Flask smoke test.

## ActivityInfo bootstrap (Stage 2)

Use an **existing development database**. From this project root, set
`ACTIVITYINFO_API_TOKEN` and `ACTIVITYINFO_DATABASE_ID` in the ignored `.env`
(using `.env.example` as a guide), then run with the virtual environment active:

```sh
python -m pip install -e '.[test]'
python -m web_monitor.activityinfo.bootstrap
```

Bootstrap creates/verifies the application forms inside that database and seeds
four demo site configurations. It never creates a database or crawls the sites.
Rerunning it adds only missing schema/seed records; incompatible objects stop it.
Run bootstrap serially, not concurrently. Never share or commit `.env`.

Offline tests require no ActivityInfo credentials:

```sh
python -m pytest
```

The explicit **development-only** integration checkpoint bootstraps twice, checks
for duplicates, writes/reads synthetic Snapshot, child items, and Crawl records,
then deletes only those generated test records:

```sh
python -m web_monitor.activityinfo.verify_persistence
```

It prints generated test record IDs so interrupted checks can be inspected.
Both commands accept `--env-file /absolute/path/to/.env`. The token is never
included in command output. Do not run the checkpoint against production data.

## Run one monitored site (Stage 3)

With the existing ActivityInfo configuration and virtual environment active, run
one Monitored Site by its ActivityInfo record ID:

```sh
python -m web_monitor.monitor <monitored-site-record-id>
```

The command prints the status, Crawl/Snapshot IDs, page count, and diff counts.
Initial runs establish a baseline with zero diff counts. Unchanged runs reuse
that Snapshot; changed runs save a complete new state. Failed crawls save an
error Crawl without a Snapshot. Exit codes: `0` success, `1` recorded crawl error,
`2` configuration/persistence failure. Run attempts for a site serially.
Manual runs are explicit: scheduling and the `active` flag do not trigger them.

The optional development checkpoint runs the same command twice against a site
with no previous Snapshot, verifies the records, and deletes only its generated
test records. It leaves the site configuration intact:

```sh
python -m web_monitor.activityinfo.verify_lifecycle <monitored-site-record-id>
```

Deployment, scheduling, reports, and notifications are not implemented.
The web interface is read-only; monitoring remains developer-triggered.

## Historical queries (Stage 4)

With the existing development `.env` and virtual environment active, query a
Monitored Site record ID. Dates are UTC; commands are read-only:

```sh
python -m web_monitor.history state <site-id> 2026-09-16
python -m web_monitor.history changes-on <site-id> 2026-09-17
python -m web_monitor.history changes-between <site-id> 2026-09-14 2026-09-19
python -m web_monitor.history latest-change <site-id>
python -m web_monitor.history recent <site-id> --limit 20
```

Missing state or changes are reported explicitly. Date comparisons use the last
successful observation through each UTC date, including unchanged observations.
Run offline coverage with `python -m pytest`. The explicit development checkpoint
`python -m web_monitor.activityinfo.verify_history` writes synthetic history,
verifies all five queries, and deletes its generated records, preserving the seeds.

## Web interface verification (Stage 5)

Run `python -m pytest` for offline route/template and regression tests. With the
development `.env` configured, `python -m web_monitor.activityinfo.verify_web`
starts a temporary local Flask server, verifies the four seeded sites and history
pages using synthetic records, then cleans up those records. It leaves the seeded
site configurations unchanged. No web route creates records or triggers crawls.

## Reporting (Stage 6)

Stage 6 passed populated-data verification and user visual acceptance on
2026-10-04. Synthetic records were removed; the four seeded sites and both
published report definitions remain intact. Deployment readiness is a separate
Stage 6.5 review; no deployment has been performed.

```sh
python -m web_monitor.activityinfo.reporting
python main.py
```

Open <http://127.0.0.1:8080/reports> for monitoring totals and the ActivityInfo
visualization. Setup maintains two deterministic database-owned reports: the
**Monitoring Overview** Notebook for detail and **Monitoring Activity** Single
bar chart for embedding. Reruns update the same reports. Publishing requires
ActivityInfo's **Publish reports** permission; setup never changes permissions.

Set `ACTIVITYINFO_SINGLE_EMBED_URL` to the exact iframe `src` copied from the
Single report's **Sharing & Publishing → Publishing** screen, and
`ACTIVITYINFO_NOTEBOOK_PUBLIC_URL` to the Notebook's standalone published URL.
Keep these in local `.env`. The adapter validates the host and report identity;
URLs are never inferred. The local summary remains available if the provider
cannot load. The Notebook opens through **View the full monitoring report**.

Run the offline reporting tests with `pytest tests/test_reporting.py tests/activityinfo/test_reporting.py tests/test_reports_web.py`,
or `pytest` for the complete regression suite.

For the retained three-site dataset and expected chart values, see
[populated reporting acceptance](docs/reporting-acceptance.md).
