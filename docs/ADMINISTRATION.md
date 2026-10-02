# Tenant-local administration and governed reports

`/admin` now combines report settings, explicit report access, mail schedule
configuration, mock run history and a tenant-scoped audit trail. Its legacy
adapter laboratory remains available below the console. `/maintenance` remains
an explicitly labeled sample-metadata CRUD page; its records do not define
access or trigger jobs. Company policy details are still awaiting confirmation.

## Try the complete offline flow

1. Start `python -B app.py` in the approved environment and sign in as
   `demo-admin` with `demo-only`. The launcher provides local SQLite storage
2. In **Admin**, create a report with a name, category, description, enabled
   state and **Synthetic monthly performance** source
3. Add a `user`-type grant for `demo-user-a`, or a `role`-type grant with subject
   `user`, selecting View and optionally Export/Maintain
4. The persisted report appears in the existing **Report catalog**. Sign in as
   `demo-user-a` and open its card. View/export/metadata actions are checked again
   against the current server identity. `demo-user-b` cannot see organization A
5. As admin, create a schedule with a synthetic address such as
   `tester@example.invalid`, a local time, explicit timezone and enabled state
6. **Simulate once** records one local mock capture per schedule version. Repeated
   clicks or another worker do not repeat that version. The log records the
   durable outcome; no SMTP connection or automatic scheduled execution occurs
7. Edit, disable, archive and restore the report to check version conflicts and
   recovery. Audit lists who changed which field names, when, and old/new versions

The public demo identities and data are not suitable for public deployment.
No real company login, account creation, role modification, LDAP, SMTP or
production scheduler has been added.

## Access semantics (demo policy, not an invented company policy)

Identity claims are supplied and reloaded by the existing identity provider.
All queries, mutations, log views and exports bind that exact organization.
There is no superadmin role or cross-organization administration capability.

- `admin` can manage report settings, permissions, schedules and audit in its own
  organization. Admin grants cannot create or change identity-provider roles
- Other supported users are denied by default. Positive user, role and
  organization grants add together. Revoking one rule does not override another
  matching rule; the console explicitly explains this
- Roles are the existing `admin` / `user`. A user grant requires an existing
  identity with the exact requested ID and same organization. Revoking a former
  user's grant remains possible after their account is removed or moved
- Organization grants must name the administrator's own exact organization
- Export and maintain require view in each saved grant. The evaluator also
  enforces view along with the requested action
- Maintain permits editing only name, category and description. Availability,
  source, archive/restore, grants, schedules and identity management cannot be
  changed by a nonadmin maintainer
- Disabled or archived reports cannot return rows, CSV or mock delivery, even to
  admins. Authorized metadata maintenance can remain available while disabled
- Every registered callback has a transport policy, and every service method
  independently checks current tenant/action. Disabled controls, query strings,
  selected table rows and browser Stores are never authority

Legacy `/page3` and `/api/reports/export.csv` remain admin-only and use their
original provider. Governed `/reports?report=<server-issued-id>` and
`/api/managed-reports/<id>/export.csv` use the new service; a grant does not weaken
legacy route policy. Unknown/cross-tenant IDs cannot disclose another tenant's
records. Report CSV uses server-owned rows and escapes formula-like text cells.

## One coherent catalog and explicit source adapters

Admin-maintained metadata is projected into the existing search/category/
favorites/recent card catalog. Persisted cards do not mutate the frozen PageSpec
registry. They use immutable projections and server-generated internal links;
URLs cannot select SQL, files, Python imports or arbitrary adapter methods.
The `/reports` page is one explicitly registered route with governed selection.

Demo mode uses an independent synthetic provider. It never fabricates an admin
identity to bypass the old report provider. Production does not fall back to
synthetic data or legacy `rows()` for governed reports. A trusted, explicitly
supplied report adapter must declare bounded `managed_sources` and implement
`managed_rows(user, source_key)` with the documented five-column report schema.
Unsupported adapters expose no governed sources and return no governed data.
This interface is not company integration certification. Review actual source,
per-user/per-org queries and payload semantics before enabling a real adapter.

The service bounds one organization to 200 report records, including archives,
64 active grant principals per report and 50 schedule records per report. The
catalog retrieves the whole bounded authorized set, so counts/search are not
silently truncated. There is no purge control; capacity changes need a reviewed
retention/data-model decision.

## Schedules are configuration; the engine is stopped

The UI always distinguishes enabled configuration from **ENGINE STOPPED**.
No thread, cron, background scheduler, SMTP connection or external mail starts
from import, startup, save or enable. The read-only next occurrence is only a
calendar preview, not a promise of execution.

Supported cadence is daily or weekly, with an explicit local `HH:MM` and weekday.
Initial supported zones are `UTC` and `Asia/Taipei` (fixed UTC+08:00 for these
future schedules). No daylight-saving zone is accepted. Expanding the zone set
requires a reviewed Python-3.8-compatible timezone source and DST gap/fold rules.
Recipients must be 1–20 distinct synthetic `@example.invalid` addresses; real
addresses, display-name/header syntax, line breaks and external domains fail
validation. No arbitrary cron, code, SQL, template or mail-server setting exists.

A manual simulation:

1. Checks admin/tenant, enabled report/schedule and expected schedule version
2. Commits a unique `(schedule ID, configuration version)` claim plus audit in one
   SQLite write transaction before any effect
3. Reloads current identity and rechecks settings, calls the authorized synthetic
   provider, then checks again before a fixed in-memory sink capture
4. Commits terminal state and audit together. A failed completion write leaves the
   prior running claim uncertain; it is not relabeled or automatically retried

Claims survive process restarts and deduplicate across workers sharing the same
local file. `running` can mean currently active or interrupted/uncertain.
Errors use fixed codes (`provider_unavailable`, `configuration_changed`,
`mock_failed`), never exception text or report payloads. History comes from
SQLite, not process-local message counts. Editing a schedule produces a new
version that can be manually simulated; it is not an automatic retry mechanism.

A real scheduler still needs a separately supervised single owner, actual due-slot
keys rather than mock version keys, IANA/DST rules, restart/misfire/coalescing
policy, recipient reauthorization, durable outbox, retry/reconciliation rules
and end-to-end idempotency/fencing. This mock does not guarantee exactly-once
external delivery. Never run one scheduler inside every web worker.

## Concurrency, audit and schema upgrade

Report metadata, grants, schedule settings and lifecycle changes use optimistic
versions. ACL changes advance the report version too. No-op saves do not advance
a version or duplicate audit. Creation uses bounded per-draft idempotency keys;
replayed changed payloads conflict. Archive is reversible; schedules can be
disabled and re-enabled. Source is immutable after report creation.

Schema **3** adds five tables to the existing StateStore. Exact validated v1 and
v2 databases migrate in one transaction, preserving legacy records, leases,
fences, claims and audit sequence. Unknown/malformed/newer schemas fail closed.
Composite foreign keys and every SQL tenant predicate preserve tenant/report
relationships; foreign-key enforcement is enabled on each connection.

Audit shares the mutation transaction, storing organization, report/resource ID,
current actor ID, fixed action and actual changed field names, before/after
versions, UTC timestamp and optional request ID. It contains no field values,
recipient payloads, passwords, tokens, report rows or backend exception strings.
Audit access is tenant-admin only. It is an operational audit, not an immutable
regulatory log; a database operator can modify the underlying file.

Back up with `StateStore.backup_to` before upgrade. It includes all admin tables.
There is no schema downgrade or destructive restore command. Old code cannot
use schema 3. Restore/code rollback are separate operator decisions; reconcile
uncertain claims before resuming. SQLite remains a trusted-local-filesystem,
single-host solution, not NFS/cluster storage.

## Verification limits

Automated tests use synthetic identities/data, temporary databases, true
Flask/Dash callback transport, multiple local processes and approved package
pins. They cannot certify the company's LDAP/Oracle/SMTP, target Windows setup,
real scheduler or browser appearance. Cloud localhost access is blocked; no
alternate browser route or restriction bypass has been used. Browser visual,
keyboard, resize, Back/Forward, toast, form and download acceptance must be run
on an explicitly permitted test host using the released commit.
