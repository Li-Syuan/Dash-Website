# SQLite report-definition maintenance

`/maintenance` is a real persisted CRUD page, not an editable table pretending to
save. It maintains a bounded sample entity: report definitions with `name`,
`description`, `cadence` and `enabled`. Cadence/enabled are metadata only; saving
does not create a schedule, run SQL, execute a report or send mail. This is not a
generic database admin console or a mapping of all company tables.

## Access and fields

Authenticated `admin` and `user` roles can enter the page. Every service query is
scoped to the identity provider's current organization. Both roles can read
records in that organization. Users can change only records they own; admins
can change any record in their own organization. Admins do not bypass this
maintenance tenant boundary. Other page policies remain unchanged.

The server assigns record ID, organization, owner pseudonym, timestamps and
version. Payload attempts to set these or unknown fields are rejected. Outside-
organization and malformed/missing IDs share a not-found result. Browser rows,
selection indexes, disabled buttons and hidden Stores are not trusted authority.

- Name: nonempty trimmed text, at most 120 characters
- Description: at most 1000 characters; safe multiline text
- Cadence: manual, daily, weekly or monthly
- Enabled: boolean, descriptive only

Search treats SQL wildcard characters as literal text. Queries, page sizes and
offsets are bounded; SQL values are parameterized and sort order is fixed.
There are no supplied SQL expressions, executable paths or dynamic table names.

## Editing workflow

Search/filter the list, select a row, then edit the form and save. Selection and
Reload selected fetch the record again from the service. Save, archive and
restore require the exact selected version; a concurrent save returns a conflict
without replacing unsaved form values. Reload before retrying. New definition
clears the form. A successful mutation refreshes the list and selected record.
Choosing another row or reloading replaces unsaved form edits, as stated in UI.

Archive is a soft deletion; Include archived shows those rows for inspection
and restore. Archived records cannot be edited until restored. Every mutation
increments its version. There is no purge or permanent-delete endpoint. Each new form draft has an idempotency key. Repeated saves of the same initial
payload create one record and one audit event, including concurrent requests.
Changed replay payloads or edited/archived records conflict; New definition
creates a fresh draft. This is not business-name uniqueness: separate drafts can
create separate records with the same name until the company defines a rule.

## Storage, audit and migration

The direct demo launcher `python -B app.py` creates `instance/` and uses its local
`workspace.sqlite` when no state path was supplied. The directory is Git-ignored;
new POSIX directory/file modes are private. Verify Windows ACLs in deployment.
An explicit `REPORTING_STATE_PATH` is honored, and production never receives a
demo default. Factory callers without state can view the page's configuration
notice, but writes fail closed rather than falling back to memory.

Schema version 2 adds the definitions table/indexes. Opening a genuine validated
version-1 workspace database migrates it atomically, retaining leases, job
claims, audit records and sequence. Invalid or newer schemas are refused.
Successful mutations and their payload-free audit event commit in one
transaction; audit failure rolls the change back. Audit does not contain names,
descriptions or other editable payloads. There is no claim that all rejected
business attempts are retained as mutation events.

Back up using `StateStore.backup_to` before an upgrade. There is no automatic
schema downgrade. Old version-1 code cannot use a version-2 file; follow the
separate backup/restore and code rollback procedure in OPERATIONS.md. SQLite is
restricted to a trusted local filesystem on one host, not NFS, a cluster or a
high-write-throughput service.

## HTTP and operational boundaries

CRUD actions are explicit registered callbacks, guarded at transport and service
boundaries. Callback POSTs reject foreign/null origins, cross-site fetches and
missing/oversized declared bodies. Same-origin browser requests and authenticated
Origin-less non-browser clients are supported. Review trusted proxy/host/TLS
configuration before deployment; this is not an XSS defense or a blanket CSRF
security certification. Logout remains an intentional session-revocation route, but explicit
foreign-origin/cross-site requests are rejected before clearing a session. No CORS grant or external connection is added.

Tests exercise tenant/owner rules, forged payloads, conflicts, two-process update
contention, persistence, archive/restore, migration rollback and real callback
transports. UI interaction still needs a real browser pass on the final commit.

### Inline field feedback

Name, description and cadence show fixed field-specific feedback on editing or
Save, using DBC 1.4.1 `invalid` and `FormFeedback`. New/selected forms start clean;
read-only forms do not show editable errors. The feedback callback uses the same
field validators as the CRUD service, but never authorizes or writes data.
Save still independently validates all fields, identity, draft key and version;
conflicts/backend failures retain the existing sanitized notification behavior.

## Request-scoped architecture

The complete maintenance workflow now follows a shared policy → immutable
request scope → application service → tenant-bound repository transaction path.
See [the architecture and migration guide](architecture/MAINTENANCE_SLICE.md).
The database schema, rows and public trusted-service API remain compatible.
Multiple write-button triggers in one callback request now produce no mutation,
rather than letting browser-provided trigger order choose an action.
