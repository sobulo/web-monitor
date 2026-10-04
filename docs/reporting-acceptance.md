# Populated reporting acceptance

Stage 6 remains pending user visual acceptance and subsequent targeted cleanup.
Application metrics and all five native analyses have passed the known-value
checks, including anonymous published results. The populated Notebook charts,
aggregate table, Single iframe, and Notebook link have been inspected in-browser.
The full offline suite passes (231 tests).
The development fixture is intentionally retained until that acceptance. It does
not crawl websites or alter schema, lifecycle code, or the four seeded sites.

## Reproduce and inspect

With the development `.env` configured:

```sh
python -m web_monitor.activityinfo.verify_reporting seed
python -m web_monitor.activityinfo.reporting
python -m web_monitor.activityinfo.verify_reporting verify
python main.py
```

`seed` refuses an existing manifest or pre-existing Crawl history. The ignored
`build/reporting-fixture.json` records all 25 synthetic identities and the original
four sites before any writes. If creation is interrupted, `resume` finishes that
same dataset; it does not create a second copy. `verify` is read-only and compares
stored fixtures, all application metrics, and each native analysis to fixed
expectations. Its aggregate results are saved in `build/reporting-results.json`.

All synthetic sites are inactive and named `DEVELOPMENT/TEST Reporting A`, `B`,
and `C`. Their URLs use the reserved `.invalid` domain. Four snapshots with two
items each represent A's baseline, two successive changes, and B's stable state.
Each A transition adds one page, removes one, and modifies one. C has no history.

## Known observations

All observations are at **12:00 UTC in September 2026**.

| Date | Site A | Site B | Daily Crawls |
| --- | --- | --- | ---: |
| 14 | initial | initial | 2 |
| 15 | no_change | no_change | 2 |
| 16 | changed | error | 2 |
| 17 | no_change | error | 2 |
| 18 | changed | — | 1 |
| 19 | error | — | 1 |

| Site | initial | no_change | changed | error | Total | Successful |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| A | 1 | 2 | 2 | 1 | 6 | 5 |
| B | 1 | 1 | 0 | 2 | 4 | 2 |
| C | 0 | 0 | 0 | 0 | 0 | 0 |
| Total | 2 | 3 | 2 | 3 | 10 | 7 |

Overall and Site A added/removed/modified totals are **2/2/2**. Site B/C totals
are zero. Overall latest Crawl is September 19; latest success and latest change
are September 18. B's latest Crawl is September 17 and latest success September
15, with no changes. C has no timestamps.

## Visual acceptance

- Open [Web Monitor Reports](http://127.0.0.1:8080/reports). Expect **7 sites**
  (four seeds plus three synthetic), **10 Crawls, 7 successful, 2 changed, 3 errors**.
  The embedded Single chart groups A/B by status with the counts above.
- Follow **View the full monitoring report** to the published Notebook:
  [Monitoring Overview](https://www.activityinfo.org/published/we71c43ac5956505640290ecb).
  It has one text component and four analyses:
  1. An aggregate site/status table: two site rows, not ten raw Crawl rows.
  2. Modified pages by site: A = 2, B = 0. This sums `changed_count`, not attempts.
  3. Daily activity: six dates with counts **2, 2, 2, 2, 1, 1**.
  4. Status pie: initial **20%**, no_change **30%**, changed **20%**, error **30%**.
- C and the four untouched seeds have no Crawls. They count in application site
  totals but do not create categories in Crawl-based native reports. Zero-valued
  cells/bars may be visually empty; this differs from missing nonzero results.

The Single report retains exactly one analysis. Publishing/data caches may need
refreshing; acceptance requires populated results, not just a successful HTTP response.

## After user acceptance only

```sh
python -m web_monitor.activityinfo.verify_reporting cleanup --accepted
```

Cleanup uses only the deterministic fixture allowlist, removes references before
targets, and verifies every synthetic ID is absent. It compares the four original
sites with the saved baseline and verifies both reports exist exactly once with
unchanged definitions and analyses. The reports remain published. Interrupted
cleanup can be retried with the same manifest. Do not delete the manifest before
cleanup is verified. Do not proceed to Stage 7 from this checkpoint.
