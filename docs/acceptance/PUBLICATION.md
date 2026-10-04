# Cumulative v11/v12 publication

The owner confirmed `Li-Syuan/Dash-Website` branch `main` as the publication
destination for v11, v12, the ETL/performance follow-up and the one-page report.
This clean release starts at published main
`e4a9681137f4762e4fcdcfee356351b7786a9420`. Local checkpoint commits containing
execution archives are not part of its ancestry. Publication uses a normal
fast-forward push; no deployment or external company integration is included.

## Changes and migration

- Large QSL XLSX exports use a bounded 1,024-row snapshot spool before serialization.
  Current provider authorization is rechecked during generation and before delivery.
  The default 50,000-row cap, literal cell values and existing download contract remain.
- Opt-in demo report template uses the existing app.py, PageSpec, query/service/
  repository layers and tenant-scoped CSV export. It defaults off and is rejected
  in production mode. See [template contract](../report-template/README.md).
- Offline deployment checks, liveness/readiness, coordinated SQLite backup and
  new-destination restore preserve leases, uncertain outcomes and review markers.
  These are tools and isolated drills, not an actual deployment. See
  [operations](../deployment/OPERATIONS.md) and [migration](../v11/CHANGES_AND_MIGRATION.md).
- Fixed acceptance profiles bind source, runtime, raw evidence and owned-process
  cleanup. Clock-invalid or incomplete evidence cannot become whole-project success.
- The ETL recovery fixture waits for observed wall-clock lease expiry with a bounded
  monotonic deadline; product lease/fencing semantics are unchanged. The prior WSL
  result remains INVALID. See [follow-up](FOLLOWUP.md).
- The configurable performance gate requires independent calibration, comparable
  evidence and prior explicit policy approval. **15% time / 10% RSS is unapproved
  and disabled.** Example policies and manufactured unit fixtures are not approval
  or measurements. No new heavy benchmark was performed for publication.
- The offline HTML acceptance report separates current evidence from historical
  results, displays causes and fixed reproduction commands, masks credential forms
  and never embeds raw logs or executes report metadata. See [report usage](REPORT.md).

No new database schema migration or dependency/workflow upgrade is introduced.
Existing CRUD modals, same-organization shared reads, owner/same-organization admin
writes, narrower legacy QSL rules, ETL and app.py remain in place. Use an isolated
synthetic environment for the documented commands; company adapters are unverified.

## Validation

Fresh publication-copy results (2026-10-04, Windows Python 3.10.22):

| Check | Passed | Failed | Skipped / unrun |
| --- | ---: | ---: | --- |
| Complete unittest discovery | 1,118 | 0 | 4 platform skips; 1,122 total |
| Focused authorization | 97 | 0 | 0 |
| Default HTTP entrypoint matrix | 263 checks | 0 | 0 |
| Opt-in template HTTP entrypoint matrix | 271 checks | 0 | 0 |
| Real Chrome portal operations | 40 | 0 | 0 |
| Real Chrome static-report rendering | 12 | 0 | 0 |

The full unittest run took 132.058 seconds. The 97 focused cases and HTTP cases
overlap full discovery and must not be added to the 1,122 total. Both acceptance
profiles and independent regression verification return INCOMPLETE/3, with valid
evidence and no clock errors. Program digest:
`f7e6798ae9853f28197e8f2ee4340712aa75ae9b7b38958768f84279415fd1ed`.

Regression run: `20261004T192027Z-27632ae0ecf2`; authorization/coverage run:
`20261004T192534Z-c3a0f80ac482`. Portal browser run:
`2026-10-04T19-20-28-151Z`, native Chrome 154.0.8037.95, Node 24.21.0 and
Playwright 1.62.1. Owned fixture stopped and synthetic state removed; no page
errors, unexpected console errors or external origins. Expected authorization
401s and aborted requests during navigation remain in the full local log.

The static-report browser run `acceptance-report-2026-10-04T19-27-57-834Z-qOlfYe`
checked desktop/mobile layout, keyboard disclosures and the script-free network
boundary. No page/console/network errors occurred and the owned browser closed.
It checks rendering, not portal behavior or acceptance completeness. HTML SHA256:
`b7aa1ad84fb119d8f7eede9d2ed604c35bf2181412da8acc1af423872407148c`.

Native regression, HTTP matrices, real portal browser checks and static-report
rendering are distinct checks. GitHub CI is checked after pushing the exact
commit; the run link and terminal matrix results are returned with the delivery
evidence. CI preserves its existing workflow and does not run browser or
performance checks or wrap unittest with the optional clock guard.

Overall acceptance remains **INCOMPLETE**: platform skips, unapproved performance
criteria, exact target OS/Python patches and company integration gaps are not erased
by a passing regression or CI matrix. The old WSL invalid run is never relabeled.

## Evidence retained separately

Public source contains necessary tests, sanitized numeric benchmark fixtures and
the two [entrypoint coverage matrices](../v11/ENTRYPOINT_COVERAGE.md). The single
sanitized v11 browser results JSON is a required validator test fixture, explicitly
historical. The test suite does not require raw captured logs or DOM dumps.

New execution logs, screenshots, DOM dumps, generated HTML, state databases,
local checkpoint inventories, source ZIPs and downloaded runtimes are excluded
from this commit. Full local evidence, original failures and prior delivery
archives remain preserved. Existing public historical evidence from main is
unchanged; public Git history is not rewritten.

Prior delivery archive identities (not uploaded by this publication step):

| Archive | Bytes | SHA256 |
| --- | ---: | --- |
| v12 follow-up complete source | 2,538,259 | `e1779cf4a470d3a71b84268b23f17e00dcaebd95be62cd5c8ff5514f4bda5cd8` |
| One-page report review | 4,017,077 | `7680cd2b62bdc720e2c8aff2d0f63c97a04585d9a18f106cc959ec95eb55d7e5` |

The original D-drive checkout, all earlier working copies and their uncommitted
changes remain untouched. Local restore hashes in historical documents identify
those preserved checkpoints; they are not missing public release commits.
