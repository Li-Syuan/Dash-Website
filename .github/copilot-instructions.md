# Dash reporting workspace rules

This repository is a Flask/Dash reporting foundation with synthetic offline
fixtures. Read `README.md`, `VALIDATION.md` and the relevant `docs/` contract
before changing behavior. It is not a certified company-system migration.

## Runtime and dependencies

- Keep runtime code compatible with Python 3.8.13 on Linux x86_64 and Python
  3.10.4 on Windows. Recorded Linux checks on 3.8.20/3.10.21 do not establish
  exact patch-version or Windows compatibility.
- Preserve the company pins in `requirements-demo.txt`. Do not upgrade packages
  or add dependencies without the owner's approval. The historical
  `requirements.txt` is not permission to upgrade; the demo subset is not a full
  transitive lock or installation guarantee.

## Routing, pages and authorization

- Preserve existing URLs, login callback IDs/properties and button-only login
  triggering. The retired legacy auto-discovery helpers are not active code.
  `app.py` is the only executable entrypoint; do not restore duplicate launchers.
- Use the app-scoped `PageSpec` registry in `reporting_workspace/registry.py`
  and modular pages in `reporting_workspace/ui_pages/`. Each new page must
  explicitly declare its route, access policy and navigation in one spec;
  register each callback with an explicit policy. Do not introduce global
  `dash.register_page` registration or page auto-discovery.
- Keep app instances, callbacks and sessions isolated. Enforce access at the
  server callback/route boundary and the service/provider boundary. Hiding a
  navigation item or UI control is not authorization. Protected layouts must
  check access before obtaining data.
- Preserve the known policy matrix: `/login` is public; `/` requires login;
  `/admin`, `/page1`, `/page3` require admin; `/page2` requires admin/user AND
  organization A. `/logout` clears the session.

## Integrations, state and privacy

- Keep tests and public fixtures synthetic. Do not contact company systems,
  SMTP, Oracle or LDAP, or start real scheduler jobs from tests or imports.
  Do not run root `test_admin.py`: it is a historical DB-writing example.
- Never publish secrets, private filesystem paths, company data or request/
  report payloads. Keep errors and audit logs sanitized; do not log passwords,
  tokens, request bodies or report rows.
- Treat provider `is_demo=False` as a declaration, not integration certification.
  Real adapters require the acceptance checks in `docs/INTEGRATION_CHECKLIST.md`.
- Optional SQLite state is for a local filesystem on one host, not NFS or a
  cluster. Preserve lease owner/token/expiry checks and fencing. Preserve
  uncertain job outcomes without automatic retry; claiming a job once does not
  guarantee exactly-once external effects.

## Confirmed application modules

- Report discovery uses authorized PageSpec card metadata, search/category,
  favorites/recent and bounded pagination. Do not put dozens of report links
  back in a left sidebar. The right assistant panel is unconnected and must not
  send company data or make model requests without an approved adapter.
- Use the common notification catalog with DMC 0.12.0. Do not emit raw exception
  text, row data or cross-user events. Keep local theme/favorite preferences
  nonsensitive; light/dark starts from OS preference until the user chooses.
- Maintenance is a sample report-definition entity, not arbitrary SQL/admin
  access. Users edit their own records in their organization; admins manage only
  their organization's maintenance records. Server-owned fields are not editable.
  Preserve optimistic versions, soft-delete/restore, atomic audit and validated
  schema migration/backup. Never add an irreversible purge as an implied feature.

## Confirmed administration increment

- `/admin` owns tenant-local managed report metadata, role/user/org grants,
  synthetic mail schedule settings, run history and actor/changed-field audit.
  `/reports` is a separately governed report route; legacy `/page3` stays admin.
- Managed metadata drives cards in the existing catalog through projections,
  never mutable PageSpecs or arbitrary SQL/provider paths. The existing
  `/maintenance` records remain labeled legacy sample metadata.
- Existing identity-provider admin/user claims are authoritative. There is no
  account/role creation, superadmin or cross-org management. Nonadmin maintain
  edits name/category/description only. Positive rules are additive; export and
  maintain require view. Availability/source/grants/schedules are admin controls.
- Keep mail synthetic-only and the actual scheduler stopped. Persist mock claims
  before effects, deduplicate schedule-version attempts, preserve uncertain
  completion and never automatically replay. Real SMTP/scheduling integration
  is pending the company's lifecycle and authorization contracts.
- Schema 3 upgrades exact v1/v2 state atomically, preserves legacy rows/claims,
  enables composite foreign keys, and includes administration in normal backups.
  Audit is tenant-admin-only and stores field names, not arbitrary payloads.

## Validation and Git history

- Run `python -B -m unittest discover -s tests -v` and `git diff --check` for
  changes. Use the approved runtime environment and keep tests offline.
- Update validation documentation when behavior or evidence changes. Report
  the actual interpreter/platform, commands and outcomes; distinguish passed,
  failed and unrun checks. Do not claim browser or mobile QA without rendering
  and exercising the application in a real browser.
- Preserve `.github/workflows/` and existing Git history. Never force-push.
  Publishing to `main` requires the owner's authorization for the current work.

## Pending company-specific rules

Additional custom business rules are awaiting the owner's specification. Do not
invent company policies, schemas, integrations or acceptance criteria, or treat
the synthetic examples as those rules. Add confirmed requirements here when
provided and keep unresolved assumptions explicit.

## Confirmed ETL dispatch increment

- `/QA_portal/etl` adds a demo-only multi-job dispatcher while retaining the
  original `job_monitor.py` monitor and all existing QSL modal/CRUD contracts.
- Registered callables are trusted deployment code, never uploaded code, SQL,
  module paths or browser-provided connections. Snapshot adapters are bounded
  and side-effect-free; only the engine's fenced SQLite transaction publishes.
- Automatic interval execution is explicit in direct `app.py` lifecycle, never
  import/factory construction. Jobs default disabled; timers use real local
  time. The administration mail scheduler remains a manual mock simulation.
- Read and manage permissions are tenant/job/action scoped and rechecked
  against the current identity, including worker execution and publication.
- Retry only approved failed steps with preserved successful upstream
  snapshots/provenance; never silently replay interrupted or uncertain work.
  Date backfills are bounded and deduplicated. External writes/SMTP need a
  separately reviewed idempotency/outbox integration and remain unconnected.
- Maintain Python 3.8 compatibility and existing dependency pins. This is
  single-host trusted-local SQLite, not NFS, multi-host or distributed delivery.

## Confirmed request-isolated maintenance slice

- `/maintenance` uses `definition_policy.DEFINITION_ACCESS` for transport and
  service entry, and `DefinitionPrincipal` for the common owner/org write rule.
  Do not duplicate or broaden those rules in callbacks.
- HTTP callbacks acquire `runtime.definition_request()` once. Never retain its
  actor, bound service, results or transaction across requests or in a global
  cache. The scope expires at teardown, including copied Flask contexts.
- Keep SQL in `definition_repository.py`; writes must retain tenant, owner/admin,
  version and lifecycle predicates plus atomic payload-free audit. Preserve
  `crud.py` compatibility exports used by administration and operations.
- Multiple write-button triggers in one mutation callback fail without writing.
  Existing single-action CRUD, QSL modals and ETL behavior remain intact.
