# Web Monitor

A modern Python implementation inspired by the 2004 monitoring system.
Includes a Flask web interface, local monitoring engine, and ActivityInfo history.

## Run locally

Requires Python 3.11 or newer. See [prerequisites](prerequisite.md) for ActivityInfo setup and secrets.

```sh
git clone https://github.com/sobulo/web-monitor.git
cd web-monitor
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
python main.py
```

Open <http://127.0.0.1:8080/>. Run the test suite with `python -m pytest`.

## Run on App Engine

See [prerequisites](prerequisite.md) before deploying.

```sh
gcloud config get-value project
gcloud app deploy app.yaml
```

The production app runs on App Engine Standard; Cloud Scheduler invokes the daily monitoring endpoint.

## Documentation

See [Architecture](docs/architecture.md) for the system design and integration boundaries. The repository also retains the staged execution record and condensed technical discussion notes under `docs/`.

## Notes

- This project was inspired by my 2004 Perl web-monitor thesis implementation, preserved in the [historical repository](https://github.com/sobulo/sobulo-web-monitor-2004).
- Both repositories are collaborative technical endeavors between Segun Sobulo, ChatGPT, and Codex.
- The modern rebuild came together over a weekend, with plenty of TV breaks and broader conversations about my Fall 2027 goals mixed in; I was impressed by what the assistants produced.
- We regularly had to reel one another back from useful tangents. The verbose conversations were distilled into technical discussion documents; the archive does not include Codex prompts. See [`docs/discussions/`](docs/discussions/).
- Special nod to [Google Cloud](https://cloud.google.com/) and [ActivityInfo](https://www.activityinfo.org/) for the runtime, scheduling, persistence, and reporting infrastructure. See [Architecture](docs/architecture.md) for how they fit together.
