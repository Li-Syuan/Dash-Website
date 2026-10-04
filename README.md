## Current cumulative release (2026-10-04)

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

> 本機 v11 續作：大型 XLSX 量測與改善、隔離部署／回滾演練、可執行報表範本。基線為已發布 `e4a9681`；本輪尚未批准推送或部署。系統 agent 先讀 [AGENTS.md](AGENTS.md)、[完整專案脈絡](docs/PROJECT_CONTEXT.md)、[接手指南](docs/AGENT_HANDOFF.md) 與 [本輪驗證](docs/v11/VALIDATION.md)。

Local agent acceptance: [profiles, one-command runs and evidence verification](docs/acceptance/README.md).
The v12 tooling round is local and unpushed; partial/skip/unrun results are never whole-project success.

Current follow-up: [ETL diagnosis and calibrated performance gate](docs/acceptance/FOLLOWUP.md).
The completed v12 delivery is preserved; the follow-up has no push, merge or deployment authorization.

New local review view: [generate and inspect a one-page acceptance report](docs/acceptance/REPORT.md).


> 本機候選 v10：[完整驗證與 WSL 限制](docs/optimization/VALIDATION_V10.md)、[修改與遷移](docs/optimization/CHANGES_AND_MIGRATION.md)、[待推清單](docs/optimization/PENDING_PUSH.md)、[下一輪 CI 驗收配置](docs/optimization/CI_ACCEPTANCE.md)。Windows 900 PASS／1 SKIP；真實 Chrome 32 PASS；Python 3.8 尚缺正常時鐘環境的合格驗證。

> 2026-10-04 全站授權與資料隔離更新：見 [修改／遷移](docs/authorization/CHANGES_AND_MIGRATION.md)、[入口 coverage](docs/authorization/COVERAGE.md) 與 [第 9 版完整驗收](docs/authorization/VALIDATION_V9.md)。原生 Chrome 32/32；Python 回歸與 HTTP 證據分別記錄。

> CRUD 架構升級：`/maintenance` 已完成共用授權、請求隔離與 service/repository 分層。見 [架構與遷移說明](docs/architecture/MAINTENANCE_SLICE.md)。既有 QSL 彈窗與 ETL 保留。

> 多 Job ETL 調度中心（本次增量）：`/QA_portal/etl`。沿用同一 app.py 與登入；接線方式見 [ETL 整合指南](docs/etl/INTEGRATION.md)。

> 新功能實作與驗收狀態：[FEATURE_TODO.md](FEATURE_TODO.md)。營運中心入口：`/QA_portal/operations`，共用既有 app.py 與登入。

> 維護表已恢復獨立 Create / Update / Delete / Upload 彈窗，Update/Delete 先 Query ID。見[原版操作對照](docs/opus/ORIGINAL_CRUD_PARITY.md)。

# Report workspace — modular reporting foundation

**QA Portal 現已接入本包主站：** 執行 `python -B app.py`，開啟 `http://127.0.0.1:8050/login`，以 `demo-admin`／`demo-only` 登入，從導覽或目錄進入 `/QA_portal/maintenance`。同一登入即可操作 QSL CRUD、CSV／XLSX 匯入匯出、修改差異確認、歷史還原、四步報表精靈與 mock 郵件。先讀 [中文啟動指南](START_HERE_QA_PORTAL.md) 與 [主站／公司整合指南](docs/opus/MAIN_APP_INTEGRATION.md)。`app.py` 是唯一網站啟動入口，無須另開網站或切換第二套身分。

A modular Flask + Dash reporting foundation with one application entrypoint and
offline synthetic adapters. It preserves the
known public example's routes, callback IDs and role/organization rules, with a
consistent Bootstrap component shell, local design tokens and scoped in-app notifications. Default report data is
synthetic. No company identity provider, database, mail server or production scheduler
is connected. The local synthetic ETL workers run only when app.py explicitly starts them; job schedules are disabled until an administrator enables them. This is a tested integration foundation, **not a completed company-system
migration or a production deployment**.

## Run in an isolated compatible environment

```sh
python -B app.py
```

Open http://127.0.0.1:8050/login. The default binds only to loopback and disables
debug mode. There are no CDN stylesheets, remote fonts or network CSV downloads.
The local CSS supplies the Bootstrap classes needed by this version. The direct
main launcher creates a private Git-ignored `instance/workspace.sqlite` for
persisted maintenance data. The integrated QSL services use the sibling directory
`instance/workspace.sqlite.qa/` for QSL/history, report definitions and mock job
state. With an explicit `REPORTING_STATE_PATH`, that directory is the full state
filename plus `.qa`. Back up both stores while stopped before an upgrade.
Direct execution also defaults to a 15 MiB request-envelope limit for base64 QSL
uploads, preserving any explicit `REPORTING_MAX_CONTENT_LENGTH`. The general
factory default remains 1 MiB. Production/factory configuration is explicit.

Public test identities (all use password `demo-only`):

| Username | Role | Organization |
| --- | --- | --- |
| demo-admin | admin | A |
| demo-user-a | user | A |
| demo-user-b | user | B |

For `/QA_portal/maintenance`, `demo-admin` can maintain data and save report
definitions, `demo-user-a` is read-only (including export and report preview),
and `demo-user-b` is denied. The page uses the main Flask-Login session; it does
not install the standalone demo identity selector or a second login. This demo-only
admin mapping does not change the company rule that admin entry is not CRUD.

These are mock identities, not production accounts. Do not connect this demo to
production credentials or expose it as the company login system. By default the session key is generated per process; restarting logs users out.
Optional local SQLite state shares leases/job claims/audit across processes on
one host. Stable sessions require the same explicitly provisioned key. Production
configuration refuses demo providers and missing/weak public-default secrets.

The target is Python 3.8.13 with Dash 2.9.1, Flask 2.2.3,
dash-bootstrap-components 1.4.1, Flask-Login 0.6.2, Werkzeug 2.2.3, Plotly
5.13.1 and setuptools 57.5.0 (required by Dash’s pkg_resources import). `requirements-demo.txt` documents that minimal subset of the provided
company versions. For the integrated QSL page, use `requirements-qa-portal.txt`,
which includes that subset and the XLSX adapter. It is not a full transitive lock
or an installation guarantee.
The original `requirements.txt` is historical and has newer versions; do not use
it to upgrade the company environment. In-app notifications use the approved Mantine 0.12.0 pin. No Mantine upgrade is needed. Existing
Mantine 0.12.0 components can later be integrated through adapters if required.

## Preserved known contracts

| URL | Policy |
| --- | --- |
| `/` | Authenticated |
| `/login` | Public; authenticated users redirect to `/` |
| `/logout` | Clears login and redirects to `/login` |
| `/admin`, `/page1`, `/page3` | Admin |
| `/reports` | Admin/user shell; tenant-local view/export/maintain checked for every selected report |
| `/maintenance` | Admin/user; organization-scoped records and owner checks on writes |
| `/QA_portal/etl` | Demo only: org A admin/user view; registered-job manage permission checked for run/schedule/retry/backfill |
| `/QA_portal/maintenance` | Demo only: admin/user **AND** organization A; service-level CRUD checks on each action |
| `/page2` | Role admin/user **AND** organization A |

The removed legacy `auth.py` used OR for conditions within one
`role_permission` decorator and AND for stacked decorators. Active page
policies explicitly preserve each route's known result; the unused legacy helper
and global page modules are no longer part of this package. Header navigation contains a few authorized workspace sections. Reports are
discovered through policy-filtered cards, not a long sidebar. Navigation
visibility is not the authorization boundary. Unknown users in an old session become anonymous safely.

Login IDs and properties remain `username-box.value`, `password-box.value`,
`login-box.n_clicks` → `redirectHome.pathname`, `login-alert.is_open`, triggered
only by the login button. Sidebar IDs/properties remain `btn_sidebar`, `sidebar`,
`page-content`, `side_click` with SHOW/HIDDEN values; popover IDs remain `popover`
and `popover-target`. The report table keeps `table`, native filtering/sorting,
editing, deletion, multi-selection and page size 10. Redirect IDs are retained.
The three original demo accounts are replaced by explicit mock identities.

Report refresh, download and simulation callbacks enforce authorization at both
the Flask callback transport and service boundary. Fixed app-scoped routing is
public so the login page works; each protected layout checks access before creating data.
The original Forbidden view was a rendered message, so restricted page layouts
still render a Forbidden view. Protected API/callback requests return 401/403.

The original report-table CSV exports the original synthetic fixture, not client edits, row deletion or
filters. Table edits are not persisted, matching the absence of a save callback
in the public example. The fixture's new columns are not a company data schema. The separate integrated
QSL page does persist authorized CRUD changes and exports its server-side filtered
QSL data; it does not change that original report-table contract.

## Administration and governed reports

The Admin console configures report name/category/description/availability,
additive user/role/organization permissions, synthetic mail schedules and audit.
Configured reports appear in the existing Report catalog and open `/reports`.
Legacy `/page3` remains admin-only; `/maintenance` remains labeled sample metadata.
Schedule enabled flags are configuration only: the engine stays stopped, and
manual simulations capture only synthetic `example.invalid` recipients.
See [Administration and governed reports](docs/ADMINISTRATION.md) for the complete
offline walkthrough, actual permission semantics, source adapter and migration
boundaries. No accounts, LDAP roles, real SMTP or automatic scheduler are changed.

## Adapter simulations and limitations

`demo_services.py` retains offline fixtures and memory simulations.
`reporting_workspace/providers.py` defines explicit identity/report contracts;
`reporting_workspace/state.py` implements optional durable local leases, job claims and audit.
The adapter marker is not a production certification.
The admin laboratory performs only a manual simulation:

- Default lease: process-local mutex; optional SQLite lease adds atomic
  cross-process claims, owner/token expiry checks and fencing on one local host.
  This is not a production filesystem/distributed lock replacement.
- Job claims: memory default or durable SQLite job/run states. The administration mail simulation starts no thread, cron
  or live scheduler; its failures/uncertain runs are never automatically
  retried. The separately registered ETL workers have their own explicit lifecycle and safe local execution policy. Single claim does not guarantee exactly-once external side effects.
- Mail: in-memory sink accepting only `example.invalid`; never opens SMTP.
- DVC: optional interface stub raising NotImplementedError; no integration claim.

Neither a process-local mutex nor APScheduler max_instances replaces a production
cross-worker lock. Before integrating company scheduling, identify the single
scheduler owner outside Gunicorn workers and preserve IDs, trigger/timezone,
misfire, coalesce, restart and retry behavior. Verify file locks on the real
filesystem and platform. LDAP, Oracle transactions/types, templates/recipients,
company schema, proxy URL prefixes and shared session configuration remain
unverified until the private core and representative fixtures are provided.

## Integrated QSL maintenance and report wizard

`reporting_workspace/ui_pages/qa_maintenance.py` declares the protected main page,
reuses the QSL layout with main-site navigation, and injects the main-session user
resolver into the legacy service callbacks. `web.create_dash_app` registers it
only in demo mode, through the app-owned page/callback registry. Missing or weaker
callback policies fail closed; service authorization still runs on every action.
Production mode does not auto-register this synthetic page.

All user-facing functions are reached through `app.py`; separate demo launchers
are removed by this cleanup. Internal factories and synthetic adapters remain for
isolated tests and offline execution, not as additional website entrypoints.
Data from an older standalone `instance/qa_portal_demo/` installation is not
automatically migrated; review identity and definition ownership before importing it.
[Revision and wizard operations](docs/opus/REVISION_WIZARD_UPGRADE.md) describe the
flows. For the current main entry and exact company-adapter work, use
[Main app integration](docs/opus/MAIN_APP_INTEGRATION.md).

`app.py` starts the main application backed by
`reporting_workspace.application.create_app`. The new package separates validated
configuration, identity/report providers, app-scoped Dash UI and transactional
SQLite state. No scheduler or network client starts from package import. Cleanup
removed the unused global `pages/`, old `app_server.py` / `app_config.py` / `auth.py`,
standalone `my_crud_app.py` / `qa_portal_demo.py`, the `demo_app.py` / `demo_server.py`
wrappers, the DB-writing `test_admin.py` example and obsolete bytecode. The
[current removal inventory and recovery notes](docs/opus/REMOVED_FILES.md) record
42 removed files. No application data was removed or migrated. `demo_services.py`
and `legacy_demo_ui.py` remain required internal synthetic adapters/UI/test
factories; they are not additional launchers. `wsgi.py` remains a factory-import
adapter for an existing WSGI host, not a second executable application.
For source rollback, use Git history or a separate checkout of the previous
commit and the corresponding backed-up state; there is no automatic production
integration feature flag or database downgrade. This repository's `app.py` is not
the company's original Portal entrypoint; do not replace that file wholesale.

## Multi-job ETL dispatch center

Open `/QA_portal/etl` through the same main application. The dispatch center adds
server-registered synthetic pipelines, validated DAG dependencies, explicit local
interval scheduling, per-step status/count/timing, guarded failed-step retries,
and bounded date-range backfills. Existing `/QA_portal/operations` retains its
original single-job monitor, diagnostic ZIP and mock owner notifications.

Only trusted deployment code registers callable adapters. The UI never accepts
SQL, executable code, module paths, credentials or source rows. The new scheduler
uses a separate local SQLite store and fenced engine-owned publication; it does
not provide exactly-once external writes or a distributed task queue. Import and
factory creation do not start workers. Production registration and external
side-effect adapters need explicit integration and acceptance.

See [ETL integration](docs/etl/INTEGRATION.md) and
[ETL validation evidence](docs/etl/QA_EVIDENCE.md). Existing company dependency
pins, original QSL CRUD workflows and single `app.py` entrypoint remain in place.

## Verify

```sh
python -B -m unittest discover -s tests -v
```

The GitHub workflow runs the same suite on Linux Python 3.8 and 3.10 using the
minimal pinned runtime. This is not exact company patch/Windows/full-stack parity.

Tests cover the synthetic authorization matrix, actual Dash routing callbacks
(including query parameters and logout), denied report services and HTTP
callbacks, malformed/Unicode login, invalid session user, anonymous UI controls,
known legacy route semantics, offline report/CSV, multi-process fenced leases,
persistent job claims/uncertain completion, schema validation, backup integrity,
factory/session isolation, fail-closed configuration, safe error boundaries,
mail sink restrictions, local assets and Python 3.8 syntax. Tests use isolated
synthetic providers and temporary state; they never contact company services.

Browser smoke test: sign in as each identity; visit the listed routes; as admin,
refresh/filter the report and export CSV; test light/dark, notifications and
maintenance create/edit/conflict/archive/restore; run the adapter simulation twice and
confirm the second run does not capture another message; sign out and verify
that direct report callbacks cannot return data. Check desktop/mobile widths.

## Architecture and operations

- [Unified main-app and company integration](docs/opus/MAIN_APP_INTEGRATION.md)
- [Chinese QA Portal start guide](START_HERE_QA_PORTAL.md)
- [Removed files and recovery](docs/opus/REMOVED_FILES.md)
- [Architecture and adapter contracts](docs/ARCHITECTURE.md)
- [Registering pages and callbacks](docs/ADDING_PAGES.md)
- [Shared in-app notifications](docs/NOTIFICATIONS.md)
- [Report catalog, color modes and assistant boundary](docs/CATALOG_AND_THEME.md)
- [SQLite CRUD maintenance](docs/MAINTENANCE.md)
- [Administration, report permissions and schedule settings](docs/ADMINISTRATION.md)
- [Configuration, health, backup and rollback](docs/OPERATIONS.md)
- [Company integration acceptance checklist](docs/INTEGRATION_CHECKLIST.md)

Production mode requires explicit non-demo identity/report providers, a stable
external session key, local state path and secure cookies. It is a fail-closed
configuration path, not proof that the company integration is production ready.

Runtime validation evidence and remaining environment limits are recorded in
`VALIDATION.md`. The automated suite also runs on Linux x86_64 Python 3.8.20 and 3.10.21.
Exact Python 3.8.13, Windows 3.10.4, browser appearance/interaction and company
private-system compatibility remain unverified. The cloud browser blocks the
loopback app and exposes no supported preview route, so no browser pass or public
deployment is claimed. See VALIDATION.md for evidence and remaining checks.
