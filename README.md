# Web Monitor

A modern Python implementation inspired by the 2004 monitoring system. Includes a Flask web interface, local monitoring engine, and ActivityInfo history.

**Live application:** https://web-monitor-510601.ew.r.appspot.com/

## Run locally

Requires Python 3.11 or newer.

```sh
git clone https://github.com/sobulo/web-monitor.git
cd web-monitor
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
python main.py
```

For the ActivityInfo-backed interface, configure the development database and token in an ignored project-root `.env` file. See [prerequisites](docs/prerequisite.md) for the one-time setup.

## Run on App Engine

After completing the [prerequisites](docs/prerequisite.md):

```sh
gcloud app deploy app.yaml
gcloud app browse
```

- App Engine Standard runs the Flask application; production secrets come from Google Cloud Secret Manager.
- Cloud Scheduler invokes the monitoring endpoint daily.

## Verify

```sh
python -m pytest
python -c "import web_monitor; from web_monitor.app import create_app; print(create_app().name)"
```

## Project documentation

The [architecture notes](docs/architecture.md) describe the monitoring model, persistence and reporting boundaries, and production runtime. Development followed a deliberately staged execution sequence retained under `docs/`.

## Notes

1. This project was inspired by my 2004 Perl web-monitor thesis project, preserved in [sobulo-web-monitor-2004](https://github.com/sobulo/sobulo-web-monitor-2004).
2. Both repositories are collaborative technical endeavors between me, ChatGPT, and Codex.
3. Credit is hard to split cleanly; I was impressed by what the collaboration produced. The historical reconstruction and modern implementation came together over a weekend, with plenty of TV breaks and side conversations about my Fall 2027 plans.
4. We also had to reel one another in from tangents. The [`docs/discussions/`](docs/discussions/) PDFs condense the technical discussions that shaped the implementation; they intentionally omit Codex prompts.
5. A special nod to [Google Cloud](https://cloud.google.com/docs) and [ActivityInfo](https://www.activityinfo.org/support/docs/api/) for the technical and API documentation that supported the deployment and integration work. The resulting boundaries and tradeoffs are summarized in the [architecture notes](docs/architecture.md).
