# Prerequisites

Web Monitor can run locally with only Python for the offline test suite. The ActivityInfo-backed UI and production deployment require additional setup.

## Local development

- Python 3.11 or newer.
- Git.
- An existing ActivityInfo database for development.
- An ActivityInfo personal API token with access to that database.

Copy `.env.example` to an ignored project-root `.env` and set:

```text
ACTIVITYINFO_API_TOKEN=...
ACTIVITYINFO_DATABASE_ID=...
ACTIVITYINFO_SINGLE_EMBED_URL=...
ACTIVITYINFO_NOTEBOOK_PUBLIC_URL=...
```

The report URLs are only needed after the corresponding ActivityInfo reports have been published. Use the exact URLs exposed by ActivityInfo rather than deriving them from report IDs.

Bootstrap the database schema and seed sites with:

```sh
python -m web_monitor.activityinfo.bootstrap
```

The bootstrap is idempotent: rerunning it verifies or adds expected resources without duplicating the seeded sites.

## Google Cloud production setup

Production uses App Engine Standard, Secret Manager, and Cloud Scheduler.

1. Create or select a Google Cloud project and attach billing.
2. Create the App Engine application in the intended region.
3. Store the ActivityInfo token in Secret Manager as `activityinfo-api-token` and grant the App Engine runtime service account Secret Manager Secret Accessor on that secret.
4. Create a separate production ActivityInfo database and bootstrap it before deployment.
5. Publish the production Single analysis and Notebook, then copy the exact embed and standalone URLs into the production App Engine configuration.
6. Deploy the default App Engine service and configure a Cloud Scheduler App Engine target for the monitoring endpoint.

Do not commit API tokens, local `.env` files, or other credentials. The Google Cloud project is selected through the local `gcloud` configuration rather than being hard-coded in application source.

## Design discussion sources

The discussion PDFs under `docs/discussions/` are condensed exports of the design trail. The source Google Docs are retained here:

- [Design Discussions - Web Monitor Snapshot, Diff & Persistence Model](https://docs.google.com/document/d/13n86M4rpnWFrmuusyYvIZC4ax4U8E3C-a1PqSm1tdLg/edit)
- [Implementation Discussions](https://docs.google.com/document/d/1QhkJ3PaOIAEUcw1KuvzGqH47NejYjj9FovFfHtSE3BA/edit)
- [Architecture Discussions - Persistence and Reporting Adapters](https://docs.google.com/document/d/1lYdVxdt8anpG1pjW-kH3ToyW-u4Hdgw3tKMcEK_Z-sk/edit)

The deployment sanity-check discussion is retained as the fourth PDF in `docs/discussions/`.
