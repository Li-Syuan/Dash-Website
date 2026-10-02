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
