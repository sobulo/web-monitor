# Prerequisites

Web Monitor can run locally against a development ActivityInfo database or in production on Google App Engine Standard.

## Local development

- Python 3.11 or newer.
- An existing ActivityInfo database; bootstrap creates the Web Monitor resources inside it.
- ActivityInfo credentials supplied through the ignored project-root `.env` using `.env.example` as the template.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
python -m web_monitor.activityinfo.bootstrap
python -m pytest
```

## Production

Production uses a separate ActivityInfo database and the Google Cloud configuration described in [Architecture](docs/architecture.md). Google Cloud project selection remains in `gcloud`; production credentials are supplied through Secret Manager rather than committed to the repository. Cloud Scheduler invokes the protected daily monitoring endpoint.

## Design discussion sources

The discussion PDFs are archived in [`docs/discussions/`](docs/discussions/). Editable sources:

- [Design Discussions - Web Monitor Snapshot, Diff & Persistence Model](https://docs.google.com/document/d/13n86M4rpnWFrmuusyYvIZC4ax4U8E3C-a1PqSm1tdLg/edit)
- [Implementation Discussions](https://docs.google.com/document/d/1QhkJ3PaOIAEUcw1KuvzGqH47NejYjj9FovFfHtSE3BA/edit)
- [Architecture Discussions - Persistence and Reporting Adapters](https://docs.google.com/document/d/1lYdVxdt8anpG1pjW-kH3ToyW-u4Hdgw3tKMcEK_Z-sk/edit)

The deployment sanity-check discussion is archived with them in the repository.
