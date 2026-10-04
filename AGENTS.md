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
- Publish source, necessary tests and sanitized docs only. Exclude databases,
  instance state, credentials, private paths, logs containing payloads, caches,
  downloaded runtimes and large screenshot/export bundles. Preserve complete
  evidence separately when requested, with byte counts and hashes.
- Update the handoff and validation records when behavior or known limits
  change. Record user-provided project facts as confirmed, historical, or
  awaiting verification. Never include unrelated private memories or secrets.
