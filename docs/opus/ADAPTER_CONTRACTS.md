# Adapter contracts

## Identity and policy

`legacy_policy.Policy(orgcode, user_ids, user_roles, crud_user_ids, crud_roles)` is immutable.
The trusted identity exposes `is_authenticated`, `id`, `orgcode`, and boolean `is_<role>` properties.
`scope(user, policy)` returns None/read/crud. `authorize(user, policy, action)` returns a boolean.
`guard(identity_resolver, policy_resolver, action)` checks every callback invocation; it raises PermissionError rather than returning an incorrectly shaped Dash output.
Production policy factories must retrieve current server configuration. Never reconstruct a policy from dcc.Store.

## QSL data service

`LegacyCrudService(database, policy_resolver, target=..., only_update=False, allow_upload=False, ...)`
uses a synthetic schema. Each separate target is a distinct data boundary. To enforce org/row access in production, select target and row predicates on the server; do not expose arbitrary target selection to the browser.

- `query(user, filters=None, limit=1000, offset=0)` uses allowlisted field names and literal substring matching.
- `create(user, payload, dry_run=False, refresh=None)` returns MutationResult.
- `update(user, record_id, version, payload, ...)` requires optimistic version matching.
- `delete` is recoverable soft-delete; `restore` restores subject to uniqueness and version checks.
- `export_csv(user, filters)` exports the server-authorized filtered result; spreadsheet formulas are escaped for presentation.
- `stage_csv(user, bytes)` / `stage_rows(user, rows)` create a server-owned one-use upload token.
- `stage_preview(user, token)` returns at most ten rows and server count.
- `submit_stage(user, token, atomic=True, dry_run=False, refresh=None)` reauthorizes and commits.
- `discard_stage(user, token)` consumes the owner's stage without importing.
- `audit(user)` is CRUD-authorized and contains changed field names, not full data payloads.

Do not feed raw dcc.Store rows straight into SQLAlchemy. Even the small upload path stages and revalidates.
Atomic failure reports zero committed created/updated; partial mode reports only successful row commits.
Dry-run rolls back data, audit, and token consumption; `would_create`/`would_update` are explicit.
`committed=True, refresh_failed=True` means saving succeeded, so retry only query—not the write.

The SQLite uniqueness constraint enforces five-key active-row uniqueness. Oracle schema migration is an explicit separate integration task.
The demo soft-delete is an intentional safety addition, not evidence company delete triggers have equivalent behavior.

## Schedule/mock execution

`LegacyJobAdapter(database_path, authorize)` requires trusted `authorize(actor, action, job_id)`.
Actions: admin_edit, run, read. The actor must originate from the authenticated server session.

- `save_settings(actor, job_id, settings, expected_version=0)` uses optimistic versions.
- `get_settings`, `preview`, `list_runs`, `audit_log` are authorization-checked.
- `run_mock(actor, job_id, run_key, source_ok=True, report_ok=True, delivery='accepted', ...)` is explicit/manual.
- Delivery values: accepted, partial, uncertain, failure. No SMTP is opened.
- Disabled schedule excludes fire-time previews; explicit manual mock execution remains possible.
- Claimed run keys cannot be executed twice, including concurrent calls. Claimed-but-interrupted work is visible and not silently retried.
- Retry of partial/uncertain work requires explicit duplicate-risk acknowledgement. Production should additionally offer recipient-level reconciliation.

A real scheduler may invoke a production pipeline after authorization/config validation, but this package never starts one.
A claim before SMTP cannot prove exactly-once external delivery. Preserve accepted/partial/uncertain rather than translating them into a generic success flag.

### XLSX and retention

`stage_xlsx(user, bytes)` and `export_xlsx(user, filters)` use optional pinned openpyxl3.1.5.
Uploads accept a single bounded worksheet with literal cell values, reject formulas/error cells, duplicate/unknown headers, far-away cells, oversized ZIP expansion, encrypted entries and DTD/entity XML (including NUL-encoded XML).
QSL fields are strings; unsupported typed cells are rejected rather than silently changing company schema. CSV is UTF-8 with optional BOM; CP950/Big5 legacy import would need an explicit tested adapter.
`cleanup_expired_stages(user)` is server-authorized maintenance for expired staging payloads; it does not delete QSL records.
