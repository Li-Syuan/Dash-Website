## Current release authorization (2026-10-05)

The owner now explicitly requests publication of the completed report,
fault-drill/export fix, change-impact CLI and acceptance configuration to
`Li-Syuan/Dash-Website`. Publish a dedicated `validation/dash-handoff-*` branch,
require the exact candidate's Python 3.8/3.10 regression and all 50 native
browser scenarios, then normally fast-forward `main` and verify its exact SHA
and CI. Never force-push or deploy. This authorization supersedes only the
older local-only publication holds for this completed release; all technical,
privacy, dependency and company-integration restrictions remain in force.
See [release use and migration](docs/releases/2026-10-05.md) and the
[browser gate](docs/quality-actions/CI_PUBLICATION_GATE.md).

## Completed local change-impact tooling round (2026-10-05)

The owner approved agent code-change impact analysis: files → affected
pages/APIs/schedules → suggested tests. Read
[the CLI contract](docs/change-impact/README.md). Use
`python -B tools/change_impact.py --changed <relative-path>` before planning
focused checks. It only reads local Git/source; never executes product code or
commands from its report. Every suggestion needs source evidence. Unknown,
structural, dynamic and core/shared changes require full regression; reduced
hints never replace full regression before publication. Keep the reviewed
runtime bindings and their real-repository regression tests current.

This round is local implementation/testing and a local commit only: NO push,
upload or deployment, no company integrations or dependency/runtime changes.
Earlier publication authorization below does not authorize this new round.
Do not retry or bypass the already reported browser/socket restrictions.

## Current publication authorization (2026-10-04)

The owner explicitly confirmed publishing the cumulative v11, v12, ETL/
performance follow-up and one-page acceptance report to
`Li-Syuan/Dash-Website` branch `main`, using a normal fast-forward push,
then checking that exact commit in the existing Python 3.8/3.10 CI.
Use the clean publication commit based on `e4a9681`; local restore
checkpoints containing raw evidence must not be published. Preserve the
original checkout and prior evidence. No force push or deployment.

See [publication scope and validation](docs/acceptance/PUBLICATION.md). The 15% time / 10%
RSS policy remains unapproved and disabled; overall acceptance remains
INCOMPLETE. This authorization does not cover future rounds, dependency
changes, company integrations or system-clock changes.

The prior-round instructions and results below are historical snapshots.
Their publication holds are superseded only for this confirmed cumulative
release; their technical and safety constraints still apply.

---

# Dash QA Portal agent instructions

Read this file, `.github/copilot-instructions.md`, `docs/PROJECT_CONTEXT.md`,
`docs/AGENT_HANDOFF.md`, and the relevant module contract before changing code.
The user's explicit current instructions govern scope and approvals. This file
adds guidance; it does not replace the existing repository rules or authorize
publication, company-system access, dependency upgrades or deployment.

## Work scope and architecture

- This is an internal QA Portal integration foundation with synthetic local
  adapters. Preserve the single product entrypoint `app.py`, Flask `server`,
  Dash `app`, existing URLs/callback IDs and `/QA_portal/` feature routes.
- `reporting_workspace/application.py` owns construction and HTTP boundaries;
  `web.py`, `registry.py`, and `ui_pages/` own app-scoped UI registration.
  Never restore auto-discovered pages or a second product server.
- Keep maintenance policy/domain/repository/service layers separate. SQL belongs
  in repositories; callbacks must not reconstruct authorization from browser
  data, retain request-bound services, or cache identity snapshots across calls.
- Keep CRUD Create/Update/Delete/Upload modals, cancel/close behavior, Query ID
  checks, optimistic versions, history and report-wizard navigation intact.
- Same-organization sharing is read-only for peers. The owner or an authorized
  administrator in the same organization may write. Cross-tenant administrators
  have no implicit override. Current provider identities are authoritative.
- Preserve the narrower legacy QSL policy separately; do not equate general
  admin-page access with company CRUD permission.
- The company uses Flask-RESTX, SQLALCHEMY_BINDS, Oracle and LDAP. Their actual
  private integrations are absent from this source. Preserve confirmed adapter
  contracts and bind names when supplied; never claim synthetic tests prove
  those integrations. Read `docs/authorization/FLASK_RESTX_COMPATIBILITY.md`.

## Persistence, failures and concurrency

- SQLite stores are trusted-local, single-host state, not NFS/distributed locks.
  Preserve owner/token/expiry checks, transaction fencing and durable receipts.
- Data changes and their payload-free audit records commit atomically. Stale
  versions, losing writers and denied requests must not create success audits.
- Never delete/reset an uncertain claim to make a retry pass. Unknown outcomes
  stay visible for reconciliation. Retry only explicitly eligible failed work;
  preserve successful upstream snapshots and provenance.
- No external exactly-once claim follows from a SQLite claim. SMTP/Oracle writes
  need separately approved outbox/idempotency contracts and remain disconnected.
- Use real independent connections/processes and bounded synchronization for
  concurrency/recovery tests. Do not replace failures with sleeps or retries.

## Runtime and side effects

- Target Linux kernel 3.10.0-1160 with Python 3.8.13; historical Windows target
  is Python 3.10.4. Record actual test interpreters separately. Passing 3.8.20
  or 3.10.22 does not certify those exact OS/patch targets.
- Preserve the approved pins in `requirements-demo.txt` and the QA subset.
  `requirements.txt` is historical, not permission to upgrade dependencies.
  Do not invent a Flask-RESTX version or install new packages without approval.
- Use isolated synthetic users/data and existing approved runtimes. No Oracle,
  LDAP, SMTP, real notifications, production data, credentials or external APIs.
- Factories/imports never start workers. Background execution is only the
  explicitly owned launcher lifecycle. Test fixtures do not start real jobs.
- Do not change host/guest clocks, networking or security settings to pass tests.
  Detect and report invalid clock environments rather than weakening session
  signatures, replaying suites silently, or relabeling an invalid run as passed.

## Validation and performance

- Run `python -B -m unittest discover -s tests -v` and `git diff --check` for
  behavior changes. Add tests for the user-visible failure or boundary, not
  tests that simply mirror new implementation lines.
- If UI/callback/export behavior changes, run the relevant real browser flows
  with `tests/browser/acceptance.cjs`; instructions are in `tests/browser/README.md`.
  HTTP test-client checks are not browser evidence. Report pass/fail/skip/unrun.
- Use deterministic benchmark fixtures, fixed query/export/callback scenarios,
  warmups and repeated samples. Report dataset size, interpreter, dependencies,
  median/p95 and comparable before/after runs. Optimize measured bottlenecks;
  do not relax tenant checks, body limits or output contracts for speed.
- Preserve original failed results and explain later fixes. Identify the exact
  source/commit and environment used for each result. Do not sum subchecks and
  unittest cases as if they were separate tests.

## Change and delivery discipline

- Inspect the worktree and applicable instructions first. Never reset, clean,
  overwrite or delete the user's uncommitted work. Use an independent worktree
  when necessary; coordinate explicit file ownership across parallel agents.
- Keep changes within the currently approved round. Superset, self-service
  reporting, finance strategies, an AI-agent platform and external API hookups
  are outside this project scope. Do not invent new architecture or business
  rules as part of continuous optimization.
- The v9 main push was separately approved and completed. After reviewing
  the local v10 optimization, the user also explicitly approved this round's
  normal main push and Python 3.8/3.10 GitHub CI verification. Never force-push
  or overwrite others' changes. This approval does not cover deployment,
  new scope, or future rounds.
- The current v11 round starts from published main e4a9681. Its approved scope
  is measured large-XLSX export improvement, isolated deployment/backup/restore
  and rollback drills, and a runnable governed report template. Do not push,
  merge or deploy this round until separately authorized. Benchmark runs must
  have an exclusive quiet window; other agents may only read/edit during it.
- Preserve the default QSL 50,000-row export cap. Larger synthetic benchmarks
  use an explicit server-owned constructor limit; do not imply the normal UI
  now exports 100,000 rows or silently relax limits for a faster result.
- The report template is an explicit demo-only opt-in through Settings and
  the existing factory/app.py. It is a developer example, not a dynamic report
  designer, arbitrary query system or production-company adapter.
- Deployment utilities only create new backup/restore destinations. Exercise
  them on isolated synthetic stores and owned processes. Preserve all sibling
  stores, uncertain outcomes and fencing state. Restored or partial sets must
  not start the app before offline reconciliation. A matching local snapshot
  does not establish arbitrary schema downgrade or external exactly-once safety.
- Publish source, necessary tests and sanitized docs only. Exclude databases,
  instance state, credentials, private paths, logs containing payloads, caches,
  downloaded runtimes and large screenshot/export bundles. Preserve complete
  evidence separately when requested, with byte counts and hashes.
- Update the handoff and validation records when behavior or known limits
  change. Record user-provided project facts as confirmed, historical, or
  awaiting verification. Never include unrelated private memories or secrets.

## Current v12 local acceptance round

Use the fixed [acceptance tool](docs/acceptance/README.md) for the approved agent
acceptance work. The isolated restore checkpoint is `1e7983962a19438d37d1d97f173ec0fe643249c9`;
preserve the original v11 worktree and its uncommitted changes. This round has
NO push, merge or deployment authorization. Historical v10/v11 approval does
not authorize v12 publication.

Record fresh full/focused profile evidence, source/runtime hashes, real command
results, skipped/unrun checks and before/after performance/RSS. Never execute
commands from report JSON or accept a public digest as a signed attestation.
Partial profiles, platform skips, missing tools/budgets, changed source and
expired or inconsistent evidence cannot become whole-project PASSED. Preserve
failed runs; repair the demonstrated cause before a justified new invocation.
Use approved local runtimes and synthetic fixtures only; no LLM/API calls,
company adapters or automatic installation. See the tool contract for exit
codes, fixed scope, ownership/cleanup and freshness limits.

## Approved v12 follow-up: ETL diagnosis and performance gate

The user approved both the bounded ETL restart diagnosis/repair and configurable
performance tolerances. Work only in the separate follow-up checkout based on
local restore checkpoint `50e6d185c616ef14d331289f894c7e52222fb3fd`. Preserve
the completed v12 checkout, archives and every initial failure. No push, merge,
deployment, dependency installation or system-clock change is authorized.

Read [the follow-up contract](docs/acceptance/FOLLOWUP.md). Fix the demonstrated
test fixture assumption without changing durable lease policy or retrying
unknown operations. The historical WSL run remains INVALID; only a separately
approved clean Python 3.8 environment can supply new compatibility evidence.

Performance evidence requires at least three comparable baseline invocations,
15 latency or seven XLSX samples per invocation, explicit approved limits and
approval before the candidate capture. Missing data/limits and environment
mismatch are INCOMPLETE. Excessive calibration noise never widens a tolerance.
Do not select limits from candidate outcomes or claim one candidate is stable.
Old numeric CLI budgets are descriptive only; use `assess-performance` for the
separate calibrated gate. That result never certifies the whole project.

## Current local one-page acceptance report round

The user selected a one-page acceptance report showing failure causes,
reproduction commands, measured performance differences and unverified scope.
Use the existing isolated follow-up checkout; its prior dirty files were saved
before editing. Read [REPORT.md](docs/acceptance/REPORT.md). Keep report evidence
inert, escape HTML, mask common credential forms and never embed arbitrary raw
logs or execute stored command metadata. Preserve current/historical separation.
Missing, stale, skipped or invalid evidence cannot become a passing result.

The 15% time / 10% RSS policy is not approved and must not be enabled. This
round authorizes local implementation, tests, report viewing and reviewable
changes only. The parent conversation is resolving publication authorization;
do not push, upload, merge or deploy based on historical approvals. No runtime
installation or host/guest clock changes are authorized.
