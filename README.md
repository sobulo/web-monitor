# Web Monitor

A modern Python implementation inspired by the 2004 monitoring system.
Stage 0 establishes the application and project contract only.

## Run locally

Use Python 3.11 or newer (the future App Engine configuration targets 3.11).

```sh
cd /Users/olusegunsobulo/Documents/projects/web-monitor
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
python main.py
```

Open <http://127.0.0.1:8080/>. The response is
`{"service":"web-monitor","status":"ok"}`. Stop the server with Ctrl-C.
No credentials or `.env` file are required. `.env.example` lists placeholders
for future integrations; the application does not load it or `.env`.

## Verify

With the virtual environment active:

```sh
python -m pytest
python -c "import web_monitor; from web_monitor.app import create_app; print(create_app().name)"
```

## Layout and configuration

- `src/web_monitor/`: installed application package and Flask factory.
- `main.py`: local development and WSGI entry point.
- `tests/`: HTTP smoke test.
- `requirements.txt`: runtime dependencies, also read by `pyproject.toml`.
- `app.yaml`: future App Engine Standard runtime and Gunicorn entry point.
- `.gcloudignore`: upload exclusions, including local environments and secrets.
- [Architecture](docs/architecture.md): scope, concepts, and integration boundaries.

The `src` layout requires installing the package for local imports. The future
Gunicorn entry point explicitly includes `src` on its Python path because
App Engine installs runtime dependencies from `requirements.txt`.
See Google's [Python runtime documentation](https://docs.cloud.google.com/appengine/docs/standard/python3/runtime).
No cloud resources are created or deployed in Stage 0.

Crawling, ActivityInfo integration, persistence, change detection, reports,
and scheduling are planned work and are not implemented here.
