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
