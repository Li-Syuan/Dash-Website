# Current modal workflow verification

558 tests pass on Linux Python 3.8.20 and 3.10.21. Sixteen new main-app
HTTP/SQLite modal-flow cases supplement the 542-test baseline.
Read [original workflow parity](ORIGINAL_CRUD_PARITY.md) and the latest
section of [root validation](../../VALIDATION.md).
No browser / Windows / company-service pass is claimed.

## Historical validation checkpoints (superseded counts)

# Latest single-entry verification

542 tests pass on Linux Python 3.8.20 and 3.10.21 after main app.py integration.
Fresh ZIP extraction launches app.py; seven HTTP endpoints return 200.
See [full current verification](../../VALIDATION.md#2026-10-02-single-entry-apppy-integration-and-cleanup)
and [main integration guide](MAIN_APP_INTEGRATION.md).
Browser, Windows, Oracle, LDAP and SMTP remain unverified.

## Prior increment record

# Validation — initial integration checkpoint (443 tests)

This file and the adjacent test-results-python*.txt preserve the earlier integration checkpoint. The final revision/wizard release has 520 tests per runtime; see ../../VALIDATION.md and REVISION_WIZARD_UPGRADE.md. MANIFEST.json below is refreshed for the current repository payload, excluding itself.

## Passed

- Full unittest suite: **443 tests, Python 3.8.20**.
- Full unittest suite: **443 tests, Python 3.10.21**.
- Runtime pins: Dash2.9.1, Flask2.2.3, DMC0.12.0, DBC1.4.1; new XLSX adapter openpyxl3.1.5.
- New tests: 6 exact legacy-policy tests, 30 secure QSL service tests, 10 mock job tests, 8 actual Flask/Dash HTTP callback tests (54 new tests; existing baseline389 preserved).
- Packaged CSV and XLSX synthetic fixtures independently imported: two rows each.
- Python3.8 syntax and git diff --check.

Specific coverage includes anonymous/reader/admin restrictions; current server policy revocation; OR/org-prefix/empty-policy semantics; server operation flags; locked/unknown fields; duplicate five-key rows; versions and concurrent races; recoverable deletion; literal filters; formula-safe exports; bounded XLSX XML/ZIP; atomic rollback/data+audit counters; partial commits; dry-run token rollback; cross-account/target/expiry/replay rejection; source/report failure gates; accepted/partial/uncertain status; explicit retries; 16 concurrent run-key claims; actual callback JSON serialization and output shapes; login CSRF/origin/body limits; CSV/XLSX preview/import/export; edit form ID-load.

## Not run / not claimed

- Real browser visual/mobile/interrupted-flow validation. Prior cloud localhost browser access was policy-blocked; it was not bypassed. HTTP callback tests are not visual QA.
- Exact Python3.8.13 or Windows company runtime.
- SQLAlchemy1.4.29/1.4.48 or APScheduler3.9.1/3.10.1 matrix. New stdlib service deliberately does not depend on them.
- Oracle/LDAP/SMTP, company rows, company app startup, real scheduling, migrations, production authorization changes or deployment.
- Full company generic CRUD widget/date parsing parity: supplied service is explicitly QSL-scoped. Company date/LOB/event/CSV semantics need a separate typed adapter and tests.

## State and persistence

The dedicated demonstration allocates temporary synthetic databases and resets on restart. The service constructors accept a local database path for controlled persistence tests/integration. The existing main demo retains its own documented local state behavior.
Demo identities are publicly selectable by design, loopback-only, not a secure production identity provider. Remove the selector and bind trusted company identity before any deployment.

## Handoff baseline

Baseline commit: 5af4ea14becf9321b5df7429cd233e5bf8e1d3f7.
Baseline tracked files were not modified. New modules, tests, fixtures, launcher and handoff docs are additive. No git push or deployment occurred.

## 修改預覽、歷史還原與精靈增量

2026-10-02 最終快照：Linux Python 3.8.20 與 3.10.21 各 **520 tests / OK / exit 0**。原 443 案例保留（直接更新測試改為預覽後確認），新增 77 項，包含 20 項真實 Dash HTTP callback 流程測試。詳細命令、範圍與限制見根目錄 `VALIDATION.md` 的最新章節。

新畫面尚未完成真實瀏覽器／Windows 視覺驗證；未測公司 Oracle、LDAP、SMTP。啟動入口及持久化／升級注意事項見 `REVISION_WIZARD_UPGRADE.md`。

## 2026-10-03 多 pipeline ETL 調度中心

Final frozen full regression: **779 tests passed on each of Linux x86_64 Python 3.8.20 and 3.10.21**. Command: `python -B -m unittest discover -s tests -q`. Measured durations: 33.874 seconds (3.8), 30.068 seconds (3.10); exit 0. This retains the 669-test v4 baseline and adds 110 checks, including the additive production-page isolation assertion.

- 41 backend cases: registered DAG validation, two jobs, real interval lifecycle, safe quality/count gates, durable requests, date bounds, capped retry provenance, stale lease fencing, current permissions, strict SQLite/registry validation.
- 45 independent acceptance cases: process crash/restart and same-DB exclusion, late-worker fencing, real timers, malformed backfill dates, corrupted origin counts/checks and unrelated-run provenance, HTTP quality failure then safe retry, current tenant/action denial, repeat/cancel/stale inputs.
- 19 UI/lifecycle cases plus 4 executable read-only SQLite integration-example cases. Existing navigation expectations add the new route without dropping old pages. The production factory does not register the synthetic ETL page/service/callbacks.
- Fresh candidate ZIP extraction started via actual `python -B app.py` using Python 3.8.20. All eight HTTP endpoints returned 200; anonymous ETL callback was denied with 401; authenticated ETL page and manual execution succeeded. Repeating the same manual request returned the same run ID. A public-minimum **60-second timer** then completed source → validate → summary with step row counts 4, 4, 1. The process exited and port 8050 closed. Runtime Python sources match the tested artifact. Machine-readable evidence: `docs/etl/LAUNCHER_EVIDENCE.json`.
- Python 3.8 grammar and whitespace checks passed. No packages were installed/upgraded. Existing implicit temporary-directory ResourceWarnings remain, with no failed assertions.

The dispatcher is an offline, same-host/local-SQLite foundation. All jobs default disabled; interval schedules are not cron or company production schedules. No Oracle/LDAP/SMTP/company ETL adapter, external side effect, distributed queue or deployment was enabled. Explicit retries preserve verified successful snapshots and are limited to three attempts per lineage; uncertain/interrupted work is not replayed. Callable timeouts are cooperative: a lease can fence a late result but cannot forcibly kill arbitrary Python code. Stored provenance/receipts are retained; last-100 UI is not a retention cap.

Browser rendering/screenshots, Windows, exact company patch versions, full company dependency compatibility and live-company integration remain unverified. Prior cloud-browser localhost blocking was respected without an alternate-route bypass. Read `docs/etl/INTEGRATION.md` and `docs/etl/QA_EVIDENCE.md` for the integration and operational boundaries. No Git commit, push or deployment was performed for this increment.
