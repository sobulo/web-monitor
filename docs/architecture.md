# Architecture and project contract

Web Monitor is a modern Python implementation inspired by the 2004 monitoring
system, not a line-for-line Perl port. Historical behavior can inform future
requirements; the implementation will use simple Python components and explicit
boundaries.

## Stage 0

The only executable behavior is a Flask application with a JSON smoke endpoint.
The application factory in `web_monitor.app` constructs it without credentials,
network calls, or cloud SDKs. `main.py` exposes the WSGI application and starts
the local development server when run directly.

The repository is `web-monitor`; the import package is `web_monitor`.
Modules, functions, and variables use `snake_case`, classes use `CapWords`, and
constants use `UPPER_CASE`, following normal Python/Google Python conventions.

## Planned concepts

- **Monitored site:** a configured website or URL whose state should be observed.
- **Crawl:** one observation attempt for a monitored site, with its own timing,
  outcome, and eventual reference to the observed state. A failed crawl must not
  imply that the site changed.
- **Distinct snapshot/state:** a representation of observed content after an
  explicitly defined normalization step. Repeated crawls may observe the same
  state; a crawl and a distinct snapshot are separate concepts.
- **Change detection:** comparison of successful observations against a prior
  state to determine meaningful differences. Normalization, equality rules,
  first-observation behavior, and storage choices remain future design decisions.

These concepts are a design contract, not implemented models or services.

## Integration boundaries

Future domain logic should remain independent of Flask request handling and
external services. HTTP routes will invoke application operations. Future
ActivityInfo and cloud adapters will handle external APIs, credentials, and
storage behind explicit interfaces, without embedding those concerns in core
monitoring logic. Introduce those interfaces when the functionality is built.

Local startup and tests must continue to work without ActivityInfo or a cloud
account. `app.yaml` and `requirements.txt` prepare a future App Engine deployment;
they do not provision resources. `.env.example` contains placeholders only.

Stage 0 excludes crawling, ActivityInfo integration, persistence, cloud
deployment, reports, and scheduling. Do not begin Stage 1 as part of this work.
