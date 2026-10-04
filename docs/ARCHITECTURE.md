# Reporting workspace foundation

This is a modular, tested starting point for integrating a company reporting
system. It is **not a verified production deployment or a completed migration**.
The public repository does not contain the company's private rules, identity,
database, scheduling or mail implementation.

## Modules and ownership

| Module | Responsibility | Boundary |
| --- | --- | --- |
| `reporting_workspace/config.py` | Immutable validated settings | Only documented environment variables; no dynamic plugin imports |
| `providers.py` | Identity/report interfaces and explicit demo adapters | Successful identities contain only id/role/org; providers are trusted code |
| `application.py` | WSGI factory, session loading, callback/API authorization, request/error boundaries | No network connector or scheduler starts implicitly |
| `registry.py`, `web.py`, `ui_pages/` | App-scoped metadata, card catalog, shell and modular pages | Page-bound server policies; client-only UI callbacks cannot be dispatched by HTTP |
| `notifications.py`, `theme.py` | Safe in-app events and local color preference | No shared message queue or external assistant connection |
| `crud.py`, `definition_domain.py`, `definition_policy.py`, `definition_repository.py` | Request-bound report-definition service, values, centralized policy and tenant-bound SQL | Immutable actor/request scope; owner/tenant/version/lifecycle SQL predicates; atomic audit |
| `state.py` | Optional transactional local SQLite leases, job claims, audit, backup | Same host and trusted local filesystem only |
| `demo_services.py` | Synthetic report fixture, memory simulations, invalid-domain mail sink | No company service integration or real SMTP |
| `app.py` | The sole executable entrypoint: main login, catalog, maintenance, revisions and wizard | Binds loopback and disables debug; directly builds the factory |
| `wsgi.py` | Importable factory entrypoint | Application construction must be explicit |

Factories register page metadata and callbacks on their own Dash instance.
PageSpec is the source for route, title, catalog metadata, navigation and policy;
page callback hooks receive a page-bound registrar. Unknown, replaced and
client-only callback outputs are denied by the server transport. The global Dash Pages
registry is no longer used, avoiding cross-application callback/page leakage.
Known URLs and callback output IDs are retained. Query parameters do not select
providers or override permissions. The obsolete auto-discovered pages and their
`auth.py` helper have been retired; active policies live in PageSpec and services.
The unified QSL page uses the main Flask-Login identity and callback registry.
Synthetic QSL services are registered in offline mode only; production integration
requires reviewed company adapters. See `opus/MAIN_APP_INTEGRATION.md`.

## Identity and report contracts

An identity provider implements `authenticate(username, password)` and
`get_user(user_id)`, returning `None` or a mapping with bounded `id`, `role`, and
`org` strings. The session holds the user ID; current role/org claims are loaded
again for each request. Extra fields, credentials and tokens are discarded.
Provider errors produce opaque 503 responses. Unknown/deleted users lose access.

The report provider implements `rows(user)` and `export(user)` and exposes the
ordered columns `period`, `department`, `revenue`, `cost`, `profit`. The first UI
is built for that contract. A company adapter must map its real schema and units;
this is not a claim that the sample schema matches company data. Both UI and
service boundaries enforce admin access before provider invocation. Exports are
server-owned data, not client-side table edits. A configured adapter must also
honor company row/data-level policy; role checks alone do not establish it.

Production settings require explicit providers marked `is_demo = False`. This
marker is a declaration by trusted adapter code, not a security proof. Simply
relabeling a demo provider is not an acceptable integration. No provider is
loaded from a request, import string or arbitrary command.

## Durable local concurrency

A configured SQLite file is shared by workers on one host. Every operation opens
its own connection and uses an explicit transaction; no inherited connection is
kept across process forks. Busy timeouts are bounded. Version-zero migration adopts only an empty database. Schema version 2 adds
report definitions through a validated, atomic version-1 migration. Future
versions and malformed schemas are refused rather than reset.

Leases have owner, opaque token, expiry and increasing fencing value. Acquire is
atomic; renew/release require the current unexpired owner/token. Release retains
the fencing history. A stale worker cannot release a newer lease. Expiry uses
wall-clock time, so clock discipline matters. A lease does not terminate a stale
worker; a real protected resource must enforce fencing. This is **not** a
cross-host, NFS or distributed-lock solution.

A unique `(job_id, run_key)` is claimed atomically. A job remains `running`,
`succeeded` or `failed`; no failed or unfinished claim is automatically retried.
Only the owner/token of a running claim can finish it. `list_uncertain_jobs()`
includes running jobs, some of which may still be alive. An operator must
establish what happened before recovery. Single claim is **not exactly-once
external delivery**: a crash between a side effect and its status write remains
ambiguous.

The web app starts no scheduler. A later reviewed integration must use one
explicit scheduler owner outside web worker startup, stable job/run keys,
resource fencing, defined timezone/misfire/coalescing/retry behavior, and a
separate deployment lifecycle. APScheduler's max_instances is not a distributed
lock. The included admin action only performs a manual, safe simulation.

## Mail and audit

The included mail sink is memory-only, accepts only `example.invalid` recipients,
and opens no SMTP connection. It is intentionally not a mail delivery/outbox
implementation. With persistent job state, a successful demo claim survives a
restart while captured messages do not: the UI count is process-local. Real
mail needs an explicit adapter, approved recipients/templates, durable outbox
and deduplication/reconciliation semantics. None is silently enabled here.

Audit records are fixed allowlisted events, outcome codes and bounded opaque
identifiers. Credentials, report rows, query values and exception messages are
not stored. Application identity IDs are SHA-256 pseudonyms for correlation;
this does not make low-entropy IDs anonymous. Access to the audit file still
requires protection and a retention policy. Lease/job state and associated audit
writes are atomic. Audit write failures fail the dependent action closed;
logout clears the session even if its audit write fails.

## Remaining production gates

TLS/access gateway, identity-provider quality, data-level permissions, secure
secret lifecycle, audited scheduler ownership, database transactions, outbound
message policy, rate limiting, incident response, capacity testing, real browser
QA, exact company runtime parity and backup restoration drills still need
review. Do not present this foundation as satisfying those gates automatically.

## Complete maintenance vertical slice (2026-10-04)

`/maintenance` now binds one immutable principal and request ID per callback
through `Runtime.definition_request()`. The scope expires at request teardown;
it cannot be reused in another app/request or a copied/re-entered context.
`DEFINITION_ACCESS` is the shared transport/service policy, and
`DefinitionPrincipal` owns the common organization/owner decision used by both
repository writes and UI affordances. Data-return and notification audiences
come from the same scope; shared services hold no current identity or rows.

SQL moved out of `crud.py` into a tenant/actor-bound repository transaction.
Every UPDATE carries organization, owner/admin, expected version and lifecycle
predicates in addition to the centralized policy check. Audit commits with the
mutation; read-only and expired transactions reject writes. There is no schema,
dependency, route or persisted ownership migration. Existing trusted service
callers and exception/helper imports remain compatible.

This is one completed vertical slice, not a claim that all legacy policies have
been replaced. See [architecture/migration notes](architecture/MAINTENANCE_SLICE.md)
and [exact validation evidence](architecture/VALIDATION.md).
