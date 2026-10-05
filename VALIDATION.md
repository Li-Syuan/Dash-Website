# Quickstart publication gate (2026-10-05)

Quickstart publication is now explicitly authorized. See
[the exact-SHA publication status](docs/quickstart/PUBLICATION.md).
The local-only statements below record the earlier ZIP delivery, not a current
push restriction. The published candidate must pass its own remote CI.

# Quickstart bundle (2026-10-05, local artifact only)

See [the source-bound quickstart validation](docs/quickstart/VALIDATION.md):
clean-copy Python 3.8.20 and 3.10.21 each ran 1233 tests, with 1227 passing
and six Windows-only skips. No new GitHub push, deployment or browser/Windows
certification is included. Historical evidence below remains source-specific.

# Current release candidate (2026-10-05)

The change-impact increment's local Python 3.8.20 and 3.10.21 runs each report
1192 total tests: 1186 passed, 6 Windows-only skips, no failures or errors.
See [that source-specific evidence](docs/change-impact/VALIDATION.md).
The exact publication candidate still requires fresh regression and 50 native
browser cases in GitHub CI; see [release status](docs/releases/2026-10-05.md).
Older blocked/failed/unrun records below remain historical and unchanged.

# Previous local integration (2026-10-05)

See [report handoff + fault-drill final integration](docs/quality-actions/INTEGRATED_VALIDATION.md):
Python 3.8.20 and 3.10.21 each ran 1162 tests (1156 passed, 6 Windows-only skips),
with 19 fault methods passing under a valid clock guard. Real browser execution
remains blocked; 50 planned scenarios are unrun. No publication or deployment.
Historical records below retain their own source and scope.

# Validation history

The current 2026-10-04 authorization/isolation and real-browser results are in
[docs/authorization/VALIDATION_V9.md](docs/authorization/VALIDATION_V9.md), including
explicit pass/fail/skip/unrun status and the Windows static transport limitation.
The sections below preserve prior-version validation history.

# Validation — offline demo first version

Validated 2026-10-02 on Linux x86_64 in isolated environments. No company
systems, credentials, database, mail server or scheduler were contacted.

## Passed

- 20 unittest cases passed under Python 3.8.20 and Python 3.10.21.
- Both runtimes used Dash 2.9.1, dash-bootstrap-components 1.4.1, Flask 2.2.3,
  Flask-Login 0.6.2, Werkzeug 2.2.3, Plotly 5.13.1 and setuptools 57.5.0.
- Tests use real Flask clients and real Dash callback dispatch, not only generic
  route HTML. Coverage includes Pages authorization for anonymous, admin,
  user-A/user-B, admin-B and unrelated-role-A; query parameters; logout/session
  revocation; malformed/Unicode login; button-only login trigger; anonymous UI
  controls; report/download/simulation transport authorization; CSV contents;
  legacy decorator OR/stacked AND; lease expiry/ownership; once-only mail sink.
- Python 3.8 AST syntax check passed for all new runtime modules.
- Live Python 3.8.20 loopback startup succeeded with debug disabled.
- Local asset and route checks passed; generated shell has no CDN links.
- git diff --check passed.
- Source/markup reviewed for consistent Bootstrap tokens, report KPI/chart,
  responsive breakpoints and input labels. This is not visual browser QA.

## Browser and deployment limits

The cloud browser refused http://127.0.0.1:8050/login with
net::ERR_BLOCKED_BY_CLIENT. This environment exposes no supported Flask preview
or port-forwarding route. No desktop/mobile screenshots, click-flow pass,
client-side table editing/filtering pass or browser download pass are claimed.
The corresponding server callback and CSV behavior is covered by tests.

This repository is runnable source, not a hosted mobile URL. The available Sites
publishing workflow requires a JavaScript Worker or static assets and cannot
host this pinned Flask/Dash server unchanged. No static facsimile was published.
A Python-capable host and an owner-only access gateway are still required for
safe remote testing; do not expose public demo credentials as production auth.

## Still unverified

- Exact target Python 3.8.13 / Linux deployment and Windows Python 3.10.4.
  Testing the same minor Python versions does not prove exact patch/OS parity.
- Full company dependency environment: requirements-demo.txt pins the minimal
  runtime subset, not every transitive package or the complete supplied stack.
- Actual browser rendering at desktop/mobile widths, keyboard input, history,
  repeated controls, table interactions and downloaded file handling.
- Company private code, LDAP, Oracle, persistent/cross-worker file locking,
  real scheduling/retries/timezones, SMTP/templates/recipients, DVC, proxy
  prefixes, multi-worker sessions, production security and concurrency.

Earlier handoff evidence: 10 tests and a loopback health check passed on Windows
Python 3.12.12 before cloud migration. No Windows 3.10 browser pass was claimed.

## CSS design refinement

The second pass is CSS-only: richer navigation and hero hierarchy, report cards,
table striping/filter/pagination states, forms, alerts, simulation empty state,
hover/focus/disabled states, print layout, and 1100/800/560px responsive rules.
Reduced-motion, high-contrast and forced-colors preferences are handled locally.
All 20 tests pass on both isolated runtimes; live HTTP endpoints/assets return
200; CSS delimiter/offline-reference/static accessibility checks pass. These
checks do not establish browser layout or visual correctness. No runtime
dependency, authentication rule, callback or report behavior changed in this pass.

## Modular foundation validation

The next phase replaces global demo runtime wiring with independently created
Flask/Dash apps, validated configuration and explicit identity/report providers,
plus optional same-host SQLite state. Final local runs pass **106 tests on each
of Python 3.8.20 and 3.10.21** using the same approved minimal package pins.

Coverage now includes independent app/session/callback state, shared-key workers,
production refusal of demo/missing providers and public/default secrets, secure
cookie settings, malformed/oversized/unknown-length callback rejection, provider
error sanitization, request/audit metadata privacy, logout during identity
outage, no implicit thread/job/network startup, and actual local SQLite-backed
login/export/simulation behavior.

Persistence tests exercise spawn/fork processes and threads, atomic job claims,
owner/token/fencing checks, expiry/restart, no automatic retry, ambiguous
completion persistence, strict schema constraints/index validation on every
transaction, future schema rejection, consistent backup and audit rollback.
A second source review reproduced and verified fixes for malformed-schema
claims, provider-outage logout and ambiguous job completion.

Live loopback HTTP checks passed for health, readiness, login, layout, callback
dependencies, CSS, authenticated login callback and authorized CSV using Python
3.8.20 with a temporary local SQLite store. New module/test syntax parses under
Python 3.8 grammar; git diff --check passes.

A least-privilege GitHub Actions matrix is included for Python 3.8/3.10; official
checkout/setup actions are pinned to verified commit references. Consult the
exact commit's Actions result for remote CI status. This does not validate all
company transitive packages, Windows or exact company patch versions.

Browser/mobile visual QA and real company integrations remain unverified as
described above. The SQLite backend is optional, local-host only; no live
scheduler, durable mail outbox, SMTP, LDAP, Oracle or DVC connector was enabled.
Read docs/INTEGRATION_CHECKLIST.md before treating this as production-ready.

## Registry, catalog, notifications, maintenance and theme increment

Final cloud runs pass **306 tests on each of Python 3.8.20 and 3.10.21**. The
approved minimal runtime now also includes the existing company version
`dash-mantine-components==0.12.0`; no version upgrade or other application
package was added. Node syntax/isolated JavaScript contract tests and Python
3.8 grammar checks pass. Whitespace checks pass.

This increment adds immutable app-scoped PageSpec and callback registries,
page-bound callback registration, duplicate/missing policy rejection, explicit
client-only presentation callbacks with HTTP denial, and an authorized card
catalog. Synthetic 50-page tests verify search/category/tags, pagination,
scoped IDs-only favorites/recent, forged-ID filtering and absence of unauthorized
metadata. The app itself contains only the real synthetic report and explicit
permission fixtures, not fifty fabricated business reports.

Mantine notification tests use its actual 0.12 component API. Fixed catalog
messages, request IDs, identity scope, bounded deduplication and no raw exception
content are tested through the common renderer. Theme tests verify OS fallback,
local preference, storage failure, keyboard/ARIA state metadata, Mantine theme
sync and trace-preserving Plotly color changes in an isolated JavaScript runtime.
These checks are not browser screenshots or accessibility certification.

SQLite schema 2, validated v1 migration, maintenance tenant/owner authorization,
strict editable fields, version conflicts, soft-delete/restore, mutation/audit
atomicity and two-process races are tested. Per-form creation keys prevent
repeated Save requests from making duplicate records; internal keys are not
returned as record fields. The unpublished v2 schema was finalized before this
release; temporary developer preview databases from an earlier uncommitted v2
iteration are not a supported migration source. Do not weaken validation to
adopt them.

Additional real callback tests cover foreign/null/cross-site origins, explicit
cross-origin logout rejection, unknown/replaced/forged callback dispatch,
initial/open/close/repeated-close assistant states and synchronized ARIA fields.
The assistant panel is disabled and has no outbound backend. Core review found
and verified fixes for a page-registrar binding gap and repeated-create handling.

The prior Windows screenshot at commit 1825644 showed a collapsed/blank chart
area. The figure validates as two six-point bar traces and its correct Dash
async assets return HTTP 200 on the cloud server. That does not prove browser
execution or establish the failure's cause. No blind chart fix or successful
final-commit browser-rendering claim is made. A fresh desktop/mobile pass must
verify chart rendering, catalog interactions, dark/light, toast dismissal, drawer
keyboard behavior and complete CRUD flows before calling the UI visually tested.

`.github/copilot-instructions.md` contains only confirmed project constraints.
The user's additional special company rules are still awaiting specification.

## Dash 2.9.1 notification wire-ID compatibility fix

The MSI browser check found that dotted notification pattern IDs were escaped
in multioutput dependency keys and then rejected by the renderer's JSON parser.
`notification_store_id` now uses collision-free ASCII hex for its action value;
semantic callback registry identifiers and event catalog codes are unchanged.
No dependency pins changed.

The new regression reproduced `Invalid \\escape` before the fix by parsing the
actual `/_dash-dependencies` response with Dash 2.9.1's multioutput split,
last-dot property split and JSON parse semantics. After the fix, **308 tests
pass on each of Linux Python 3.8.20 and 3.10.21** with Dash 2.9.1 and DMC 0.12.0.
The regression covers all dependency outputs and explicitly requires the six
report, administration and maintenance notification callbacks. Encoding tests
cover dots, repeated dots, hyphens, underscores, case and boundary lengths.
An additional Node check using the installed renderer's extracted splitting
functions and native `JSON.parse` successfully parsed all 61 output IDs.
Python 3.8 grammar checks and `git diff --check` also pass.

This is wire-format and server regression evidence, not a completed browser
retest. Restart the MSI app at the fixed commit, hard-refresh the login page,
then verify login, report refresh/export, manual simulation, and maintenance
list/select/create/edit/archive/restore with notifications. Check the browser
console for dependency parse errors, chart rendering, light/dark and toast
dismissal. Full visual and target-Windows verification remains pending that pass.

## Focused web interaction refinement

Maintenance name/description/cadence fields now display local inline feedback
through the approved DBC 1.4.1 API. A policy-guarded presentation callback reuses
the service's field validators; service-side writes remain independently
validated and authorized. Tests cover invalid/corrected fields, fresh selection,
disabled controls, safe fixed feedback, no feedback-side writes and denied roles.

The assistant is modal only at widths up to 650px. Local JavaScript contains
mobile Tab/Shift+Tab focus, excludes background branches, releases restrictions
on resize/close/shell replacement and preserves the existing idempotent Escape
close callback. Desktop remains nonmodal and the assistant remains unconnected.
Dropdown, table, filter and pager states use existing light/dark tokens. No
production dependencies or company package pins changed. Implementations are
original; version-matched upstream references are in
`docs/WEB_INTERACTION_REFERENCES.md`.

Final runs passed **329 tests on each of Linux Python 3.8.20 and 3.10.21**
with the approved minimal dependency pins, including 16 deterministic drawer
checks. Python 3.8 grammar, Node syntax and `git diff --check` passed.
These are server/component/isolated JavaScript and static CSS checks, not visual
browser QA. The cloud browser access restriction remains in effect; no alternate
route or sandbox bypass was used and no screenshot/click-flow pass is claimed.
The separate reusable screenshot helper now requires noncollapsed chart geometry,
twelve rendered positive-size bar paths and two six-point traces before its
report screenshots. Its seven isolated tests pass, but the browser workflow is
still unrun and must execute in an environment with permitted localhost access.

## Administration, governed reports and mock schedule configuration

Final local runs pass **389 tests on each of Linux x86_64 Python 3.8.20 and
3.10.21** with unchanged Dash 2.9.1, Flask 2.2.3, DBC 1.4.1, DMC 0.12.0,
Plotly 5.13.1 and setuptools 57.5.0 pins. Python 3.8 grammar checks for all 39
runtime/test modules, existing local JavaScript syntax checks and
`git diff --check` pass. No production dependency was added or upgraded.

New acceptance evidence includes:

- Real admin/managed Dash callback dispatch, versioned create/edit/archive/restore,
  persistent double-create protection, conflict-preserved form data, safe failed
  selection clearing and positive-click guards for dynamically remounted controls
- Additive role/user/org grants, explicit default denial, no cross-organization
  admin bypass, former-user grant revocation, nonadmin metadata-only maintenance,
  disabled report/export protection and no identity-provider role modification
- Governed cards in the existing catalog, strict URL selection, server-owned CSV,
  formula-cell protection, provider-result validation, and fresh identity/ACL
  checks after provider reads so intervening revocation cannot release data
- Daily/weekly preview calculations in explicit UTC/Asia-Taipei zones, strict
  synthetic-recipient validation, stopped-engine state, disabled/stale schedule
  denial, sanitized error/run history and actor/actual-changed-field audit
- Two spawned processes capturing only one mock message for one schedule version;
  durable duplicate claims across service restart; failed completion retaining
  running/uncertain state without replay; schedule changes during provider work
  denying capture; mutation and terminal-run audit rollback behavior
- Exact v1/v2 schema migration into schema 3, preserving legacy metadata, lease
  fences, running job tokens and audit sequence; malformed-schema and migration
  rollback tests; composite tenant foreign keys and consistent backup

An independent read-only code pass identified and verified fixes for deleted-user
revocation, a post-provider schedule/identity race, source/text validation and
remounted-button callback loops. Final mock checks and the fixed in-memory capture
are serialized with a SQLite `BEGIN IMMEDIATE` transaction. This is not a review
or certification of real mail/outbox behavior.

No browser or mobile visual/click-flow pass was performed: cloud localhost remains
blocked and the user's desktop run was stopped. No alternate route or access
restriction bypass was used. Exact Python 3.8.13, Windows 3.10.4, full company
package compatibility and real company adapters remain unverified. `/page3`
keeps its original admin-only contract. New reports use synthetic data in demo;
unsupported production providers fail closed. Schedules are settings and manual
mock captures only, with no live engine, SMTP, LDAP or account administration.
See `docs/ADMINISTRATION.md` for the walkthrough and production acceptance gates.

## 2026-10-02 QSL revision confirmation and report wizard increment

Final integrated snapshot: **520 tests passed on Python 3.8.20 and Python 3.10.21, Linux x86_64**. This preserves all 443 baseline cases (the direct-update HTTP test now exercises preview plus confirmation) and adds 77: 35 revision/import-preview service tests, 22 report-builder service tests and 20 HTTP UI flow tests. Final runs: 14.373 s (3.8), 14.053 s (3.10), both exit 0.

Commands on each pinned runtime:

```sh
python -B -m unittest discover -s tests -q
```

Verified coverage:

- Preview makes no persistent record/audit/history change; confirm applies only server-staged values. Owner/target-bound expiry, cancel, superseded token, replay, malformed state, revoked permission and optimistic version conflicts.
- Immutable snapshots, actor/time/source-version, current-baseline migration, restore as new version, soft-deleted recovery, atomic rollback and concurrent winners. Import preview exercises the same normalization/lock/uniqueness checks as submission without retaining writes; preview counts cover the full batch and details cap at 10 rows.
- Allowlisted synthetic source/columns/filter operations, strict schema version and unknown-setting rejection; literal text AND filtering, grouped counts, source/preview limits; owner-plus-organization isolation, versioned saves and persisted reload. No arbitrary SQL/Python/provider selection.
- Real Flask test-client POST requests through Dash callback dispatch for the four-step wizard, save/load, cleared old previews, denied read/write, action-token mismatch, history, cancellation and stale updates.
- Persistent launcher path survives restart without reseeding records; definitions and revisions remain. Temporary per-test application isolation is preserved.
- Actual loopback launcher start with a temporary data directory: index, login, Dash layout and callback-dependency endpoints returned HTTP 200. Python 3.8 grammar parse passed for 31 runtime files, and tracked/untracked source whitespace checks passed.

Limitations: these are offline service/HTTP tests, not browser-rendering or screenshot verification. The Windows command launcher and exact company Python 3.8.13 / Windows 3.10.4 were not executed. Oracle/LDAP/SMTP, actual company routes/adapters and deployment remain untested and unchanged. No new package dependency, external integration, live mail/scheduler operation or public deployment was introduced.

New features are in `qa_portal_demo.py` at port 8051 `/QA_portal/`; `app.py` at port 8050 remains the prior reporting catalog. Read `docs/opus/REVISION_WIZARD_UPGRADE.md` before integration or choosing a persistent data directory.

## 2026-10-02 single-entry app.py integration and cleanup

The previous separate-entry limitation above is superseded: `app.py` now directly
constructs the main factory and serves login, catalog, administration, maintenance,
QSL CRUD/import/export, revision restore and the report wizard on port 8050.
The QSL page is `/QA_portal/maintenance`; it shares the main Flask-Login identity,
server callback registry and persistent workspace state directory. No second
identity chooser, iframe, external-port link or separate launcher is required.

Final regression runs: **542 tests passed** on Python **3.8.20** (19.519 s) and
**3.10.21** (18.006 s), Linux x86_64. Command:
`python -B -m unittest discover -s tests -q`.
The former 520 cases were retained except one obsolete decorator-only test;
three single-launcher tests and 20 main-application integration cases were added.

New real SQLite / Flask HTTP checks cover main login/navigation/catalog policy,
CRUD and CSV import, readonly controls and forged writes, current role/org/account
revocation, logout, cross-origin requests, token replay/cross-user rejection,
preview/confirm/restore history, private wizard saves/loads, restart persistence,
mock-job identity and ambiguous multi-action rejection. Shared wizard outputs are
consolidated before registration; the registry's uniqueness rule is unchanged.

A fresh ZIP extraction was started through **app.py** using the supported Python
3.8 runtime. Health, readiness, login, root, QSL route, Dash layout and dependency
endpoints returned HTTP 200; persistent QSL and report-builder SQLite files were
created. This is server startup/HTTP verification, not browser rendering. Direct
launch binds 127.0.0.1 only, disables debug, and preserves explicit configuration.

Cleanup archived 42 obsolete files before removal and removed the alternate
example launch block; see `docs/opus/REMOVED_FILES.md`. User data, migrations,
active services, useful tests and Git history are preserved. Only app.py remains
an executable application launcher. Whitespace and Python 3.8 syntax checks pass.

Not verified: real browser clicks/appearance, Windows runtime, company Oracle,
LDAP, SMTP, production reverse-proxy routes or production deployment. Current
local data and identity providers remain explicitly synthetic; no setting silently
turns these into company integrations. The production factory does not register
the synthetic QSL page without a reviewed integration adapter.


## 2026-10-02 Original CRUD modal workflow restoration

The integrated QSL page now has independent Create / Update / Delete / Upload
modals and Query-ID-before-update/delete. Direct Submit to update does not
require the optional diff-preview step. Failed submission stays open.

New real main-app HTTP + SQLite tests cover modal declaration/button contract,
cancel/reopen, stale fields, signed ID/version/user mismatch, denied readers,
real SQLite-trigger write rollback, canceled previews, invalid replacement
upload revocation, upload-clear no-op and atomic import rollback reporting.
Local dbc modal CSS is served without a CDN.

See docs/opus/ORIGINAL_CRUD_PARITY.md for exact intentional behavior changes
and remaining generic-CRUD/company adapter limitations. These are transport
and storage tests, NOT browser visual tests. Windows and company services
remain unverified.

Final full-suite result: **558 tests / OK** on Linux Python 3.8.20 and
3.10.21. Python 3.8 AST and git diff --check passed.

## 2026-10-03 營運中心與 ETL 增量（ZIP v4）

- Final full suite: 669 tests passed on Python 3.8.20 and Python 3.10.21, Linux x86_64.
- Command: `python -B -m unittest discover -s tests -q` in the existing pinned environments.
- New focused suites: operations 38, maintenance registry 25, scheduler/diagnostics/notifications 36, HTTP integration 12.
- Actual direct `python -B app.py` smoke: loopback healthz succeeded; real 60-second interval fired with trigger=timer; source_snapshot, clean_validate, atomic_publish all succeeded, publishing 4 synthetic rows. See `docs/opus/OPERATIONS_TIMER_EVIDENCE.json`. Process shut down after the test.
- Main app retains original QSL modal regression tests. New features use the same session and app-owned callback registry.
- Verified organization/authentication boundaries, per-request current identities, owner-private report suggestions, sanitized diagnostic ZIP, controlled failure notifications and no ETL replay after notification failure.
- 100 synthetic maintenance definitions: 75 real SQLite tables exercised across three binds, 25 Oracle metadata-only unavailable entries. Oracle adapter fake-session contract tests are not a real Oracle acceptance test.
- Scheduler is for one host with SQLite rollback-journal storage; no NFS, cluster, or WAL guarantee. Durable lease sidecar fences stale workers. Defaults disabled/300 seconds; public minimum 60 seconds. Actual company ETL jobs were not connected or changed.
- No company SMTP/LDAP/database connection, live email, GitHub push, or production deployment occurred. No Windows/browser visual acceptance is claimed.
- Python 3.8 compilation and `git diff --check` passed. Existing temporary-directory ResourceWarnings appeared during tests; all assertions passed and no background test workers remained.

## 2026-10-03 多 pipeline ETL 調度中心

Final frozen full regression: **779 tests passed on each of Linux x86_64 Python 3.8.20 and 3.10.21**. Command: `python -B -m unittest discover -s tests -q`. Measured durations: 33.874 seconds (3.8), 30.068 seconds (3.10); exit 0. This retains the 669-test v4 baseline and adds 110 checks, including the additive production-page isolation assertion.

- 41 backend cases: registered DAG validation, two jobs, real interval lifecycle, safe quality/count gates, durable requests, date bounds, capped retry provenance, stale lease fencing, current permissions, strict SQLite/registry validation.
- 45 independent acceptance cases: process crash/restart and same-DB exclusion, late-worker fencing, real timers, malformed backfill dates, corrupted origin counts/checks and unrelated-run provenance, HTTP quality failure then safe retry, current tenant/action denial, repeat/cancel/stale inputs.
- 19 UI/lifecycle cases plus 4 executable read-only SQLite integration-example cases. Existing navigation expectations add the new route without dropping old pages. The production factory does not register the synthetic ETL page/service/callbacks.
- Fresh candidate ZIP extraction started via actual `python -B app.py` using Python 3.8.20. All eight HTTP endpoints returned 200; anonymous ETL callback was denied with 401; authenticated ETL page and manual execution succeeded. Repeating the same manual request returned the same run ID. A public-minimum **60-second timer** then completed source → validate → summary with step row counts 4, 4, 1. The process exited and port 8050 closed. Runtime Python sources match the tested artifact. Machine-readable evidence: `docs/etl/LAUNCHER_EVIDENCE.json`.
- Python 3.8 grammar and whitespace checks passed. No packages were installed/upgraded. Existing implicit temporary-directory ResourceWarnings remain, with no failed assertions.

The dispatcher is an offline, same-host/local-SQLite foundation. All jobs default disabled; interval schedules are not cron or company production schedules. No Oracle/LDAP/SMTP/company ETL adapter, external side effect, distributed queue or deployment was enabled. Explicit retries preserve verified successful snapshots and are limited to three attempts per lineage; uncertain/interrupted work is not replayed. Callable timeouts are cooperative: a lease can fence a late result but cannot forcibly kill arbitrary Python code. Stored provenance/receipts are retained; last-100 UI is not a retention cap.

Browser rendering/screenshots, Windows, exact company patch versions, full company dependency compatibility and live-company integration remain unverified. Prior cloud-browser localhost blocking was respected without an alternate-route bypass. Read `docs/etl/INTEGRATION.md` and `docs/etl/QA_EVIDENCE.md` for the integration and operational boundaries. No Git commit, push or deployment was performed for this increment.

## Request-isolated maintenance architecture (2026-10-04)

The complete `/maintenance` CRUD workflow now uses one shared authorization
policy, immutable request-bound identities, a separate application service and
a tenant/actor-bound SQLite repository. Existing routes, UI outputs, owner/org
semantics, service APIs, dependency pins and database schema remain compatible.
The preserved ETL/QSL increments and `app.py` were not modified.

Final aggregate runs pass **818 tests on each of Python 3.8.20 and 3.10.21**,
including 39 new independent adversarial request/isolation/repository cases.
Actual `app.py` loopback HTTP checks complete authenticated CRUD through archive
and restore with a persisted version-4 row. All 81 Python files parse on 3.8.20;
JavaScript syntax and whitespace checks pass. Review findings on copied-context
lifetime and malformed trigger handling were fixed and regression-tested.

See [architecture/migration notes](docs/architecture/MAINTENANCE_SLICE.md) and
[full validation evidence](docs/architecture/VALIDATION.md). This is server and
HTTP evidence; actual browser rendering, exact company runtimes and production
integrations remain unverified. No remote publication or deployment occurred.

## 2026-10-05 四情境故障演練（本機未發布）

基於 `45918b243af32cf8d4db966e82bfc6b634bf3771` 的隔離合成演練，詳見
[故障／復原指引](docs/fault-drills/README.md) 與 [結果摘要](docs/fault-drills/RESULTS.json)。
ETL 中斷、兩個獨立程序重複排程、SQLite 鎖及匯出中撤權的一鍵入口為
`python -B tools/fault_drills.py`。重用有效案例，新增同 due-slot 雙程序競爭、
XLSX archive 期間的真實 HTTP 撤權，以及最終 refresh 撤權 regression。

實際找出並修正 QSL 查詢只在 SQL 前驗權的缺口：最後 refresh 期間撤權原會
交付 CSV／XLSX／template download；修正後 query 交付前重驗現時權限與原 actor。
Python 3.8.20／3.10.21 各 19 項故障測試全過；完整 suite 各 1125 項，
1119 通過、6 項 Windows 原生功能 skip、0 failure/error。clock guard 有效。

真實 Chromium preflight 在啟動時受 `socket() Operation not permitted` 阻塞，
兩次原始結果保留；未 render、未通過任何 browser scenario。HTTP 結果不充當
browser QA。Windows、公司精確版本及 Oracle／LDAP／SMTP／RESTX 仍未測。
沒有 push/deploy/upload；沒有啟用未批准的 15% 時間／10% RSS 門檻。
