# Real browser acceptance

This harness launches an installed browser with Playwright and exercises the
actual rendered application. It imports `app.py` in a private fixture process,
binds an automatically allocated loopback port, and creates fresh synthetic
SQLite stores. It never calls the product launcher or starts workers/schedulers.
The product entrypoint remains `app.py`.

Prerequisites: the approved project Python environment, Node.js, an existing
Playwright module, and Chrome (or another installed Chromium channel supported by
that module). No dependencies are installed or upgraded by this harness.

```powershell
# Set these to existing approved installations on this machine.
$env:QA_PYTHON = '<approved Python executable>'
$env:QA_PLAYWRIGHT_MODULE = '<existing Playwright package directory>'
$env:QA_STATIC_BRIDGE = '0'
$env:QA_HTTP10 = '0'
node tests/browser/acceptance.cjs
```

If `playwright` resolves through normal Node module resolution, omit
`QA_PLAYWRIGHT_MODULE`. If `python` resolves to the approved environment, omit
`QA_PYTHON`. Optional variables: `QA_BROWSER_CHANNEL` (default `chrome`),
`QA_BROWSER_EXECUTABLE` to use an explicitly selected existing Chromium executable
instead of the channel, `QA_HEADED=1` to show the browser, and `QA_LOGIN_ONLY=1` for a startup diagnostic.
`QA_REVOKE_ONLY=1` runs a focused session-revocation diagnostic and captures the
renderer error stack; it is not a full acceptance pass.
`QA_REPORT_ONLY=1` runs login plus fresh-admin report refresh/export, explicitly
listing the other 30 scenarios as unrun. The fixture request-settle allowance is
90 seconds, retained from transport diagnosis; native requests are not retried.
Action and authorization assertions are unchanged.
The default run executes the full suite.

## Opt-in report-template acceptance

The original 32 scenarios retain their existing interactions and assertions.
Set `QA_REPORT_TEMPLATE=1` to add eight real browser scenarios for the synthetic
read-only quality report. The private fixture then explicitly enables
`REPORTING_ENABLE_REPORT_TEMPLATE=true` before importing the same `app.py`.
Only the literal values `0` and `1` are accepted for the fixture switch; inherited
`REPORTING_*` settings are cleared. The normal product remains opt-in.

```powershell
$env:QA_REPORT_TEMPLATE = '1'
$env:QA_STATIC_BRIDGE = '0'
$env:QA_HTTP10 = '0'
node tests/browser/acceptance.cjs
```

This mode runs 40 scenarios and writes only beneath
`output/playwright/v11/<UTC timestamp>/`, with its own `v11/latest.json` index.
It uses the template's 32 synthetic rows per organization; large-export
benchmarks are separate and do not enlarge the browser fixture.

The additional cases exercise anonymous redirect and the real Quality catalog card,
search and department controls, pagination and stable sorting, a real filtered
CSV download containing all matches beyond one page, same-organization shared
reads, and organization-B user/admin isolation. Negative cases modify actual
browser callback submissions with invalid offset/limit/filter values, attempt
tenant claims through the URL, and alter visible client table text before a
fresh server CSV export. The file must contain only authorized server rows.
Invalid export inputs must produce no download. A separately revoked synthetic
identity clicks both Apply filters and Export; each must receive HTTP 401 and
produce no download. No callback payload, password or session token is saved.

The original final browser-error aggregate runs after these cases with the same
classifications. Results identify opt-in coverage and any unrun template cases;
`source-hashes.json` includes the new runtime modules, and `harness-hashes.json`
records the exact fixture, browser script and instructions used at startup.
No suite is automatically rerun. Coordinate source freeze and an idle benchmark
window before launching the browser on a shared measurement machine.

## Opt-in corrective-action acceptance

Set `QA_QUALITY_ACTIONS=1` independently of `QA_REPORT_TEMPLATE` to exercise the
**Corrective action aging** report. The private fixture clears inherited
`REPORTING_*` variables before enabling `REPORTING_ENABLE_QUALITY_ACTIONS=true`;
only literal `0`/`1` fixture flags are accepted. It adds only synthetic guest and
revoked-user accounts, and exposes no HTTP test-control route.

```sh
QA_PYTHON='<approved Python executable>' \
QA_BROWSER_EXECUTABLE='<existing Chromium executable>' \
QA_STATIC_BRIDGE=0 QA_HTTP10=0 QA_QUALITY_ACTIONS=1 \
node tests/browser/acceptance.cjs
```

The unchanged original 32 scenarios plus ten explicitly named quality-action
scenarios run in this mode (42 total). Set `QA_REPORT_TEMPLATE=1` as well for
50 total scenarios, including the original eight template cases. Neither the
original 32 nor the template-only 40 certify the quality-action page.
The ten additional cases cover:

- Anonymous redirect, allowed catalog discovery and initial 20-of-36 query
- Status, priority, as-of date, case/finding search and historical closure semantics
- Pagination, offset reset and deterministic case-ID ties for descending due dates
- Real filtered server CSV downloads beyond the displayed page, including changed
  controls not yet applied to the table; no client table rows supply the CSV
- Same-organization peer/admin reads and foreign administrator/user row isolation
- Empty results, disabled paging and a real header-only CSV download
- Invalid UI date and tampered query/export controls, with no invalid downloads
- Wrong-role catalog/route rejection plus supplementary browser-context callback
  replay returning 403 (the forbidden route has no report controls)
- Actual Apply filters/Export clicks after server-side identity revocation,
  both returning 401 without a download

Quality-action runs write to `output/playwright/quality-actions/<UTC timestamp>/`
with a separate `quality-actions/latest.json`. The summary lists the actual
named scenarios, enabled flags, planned count and unrun cases. Each run saves
runtime versions, `runtime-hashes.json` (Python/Node executable and approved
requirement files, plus an explicit browser entrypoint when selected), runtime
source hashes and exact harness hashes, including the quality-action helper.
A browser entrypoint can be a vendor launcher script; its hash alone is not a
hash of every browser library. The recorded browser version identifies the
running browser. All evidence remains ignored by Git.

The new browser suite exercises only deterministic synthetic fixtures. It does
not certify company adapters, exact target OS/Python patch versions, production
connections, deployment, or performance. Failed runs remain separate; fix the
demonstrated cause before starting a new invocation. No automatic replay is used.

Each run writes `output/playwright/<UTC timestamp>/` with screenshots, readable
DOM text, sanitized server logs, downloaded synthetic exports, and `results.json`.
`output/playwright/latest.json` points to the latest run and labels its mode.
`source-hashes.json` identifies the runtime and asset bytes used at startup;
`results.json` records actual browser, Node, Python, and pinned package versions.
Each scenario reports
its own pass/fail result; failures do not imply that dependent scenarios passed.
Failure of the login prerequisite stops dependent tests. Browser-context request
replays are explicitly labeled supplementary; real clicks, filling, selection,
file uploads, navigation, and downloads provide the UI evidence.

Coverage includes button-only login, invalid credentials, maintenance pagination,
owner CRUD, double click, same-organization shared reads/read-only peer behavior,
version conflict, archive/restore, new-form reset, tenant isolation, tampered IDs,
identity revocation, QSL create/update/delete/upload modal cancel and reopen,
QSL CRUD, query-proof ID mismatch, history, upload, wizard next/back, exports,
actual Plotly SVG bar rendering, ETL/operations rendering and tenant rejection, browser back/forward, mobile
maintenance, logout/replay rejection, and browser error/network observation.

The fixture's test-control channel is parent-process stdin/stdout. It adds no
test-control HTTP routes, reuses synthetic public identities, and adds only
synthetic test accounts. Tokens/passwords/request payloads are never saved by the
harness. Static-byte hashes and synthetic visible DOM/export data are evidence.
The fixture process ID is recorded and only that owned process is stopped. Its
synthetic state directory is removed after confirmed process shutdown. The
normal port 8050 and any already running application are left alone.

## Native transport diagnosis and verification

`diagnose_native.cjs` starts a minimal isolated Werkzeug server and compares
Python urllib, Node HTTP, and native Chrome response byte counts, SHA-256,
headers, and errors for small, 4 MiB, Dash table, and Plotly files. It has no
browser request routes or static bridge. Optional `QA_DIAGNOSTIC_HTTP=HTTP/1.0`
and `QA_DIAGNOSTIC_FINISH_DELAY=0.1` are diagnostic controls, not product settings.

The recorded comparisons isolated incomplete large single-chunk responses
around immediate connection finish. HTTP/1.0 did not fix them. Identical bytes
split into 16 KiB chunks completed for all three clients; a diagnostic-only
100 ms finish delay also completed. Server iterators finished without a
`connection_dropped` event. This establishes a send/close-timing interaction in
the tested runtime, not a packet-level kernel diagnosis.

The product now bounds static GET/HEAD WSGI chunks to 16 KiB without changing
response status, headers, contents, or authentication. `native_preflight.cjs`
loads the real report graph, requires visible SVG bars, and performs 20 separate
uncached native Chrome Plotly GETs against the product server. Each response
must match the installed original file's length and SHA-256. It records all
network failures, including requests canceled during login navigation, and
retains its aggregate status honestly. The full acceptance additionally checks
rendered bars after report refresh. Its existing network aggregate rejects
failures other than browser navigation cancellation (`net::ERR_ABORTED`).

```powershell
node tests/browser/diagnose_native.cjs
node tests/browser/native_preflight.cjs
```

Evidence is retained under `output/playwright/native-diagnostic-*` and
`native-preflight-*`; `NATIVE_TRANSPORT_DIAGNOSIS.json` indexes the comparison.

## Historical static bridge

The installed Chrome 154 and pinned Werkzeug 2.2.3 environment produced
`ERR_CONNECTION_RESET` while receiving some large local Dash JavaScript files.
The CLI installation attempt also failed certificate verification; TLS validation
was not disabled. The existing Playwright package was used directly instead.

Earlier evidence used the following optional diagnostic settings to
deliver **the same static bytes**, fetched from the same owned loopback HTTP URL,
to the browser. All dynamic Dash callbacks remain native browser HTTP POSTs.

```powershell
$env:QA_HTTP10 = '1'                 # fixture-only HTTP framing
$env:QA_STATIC_BRIDGE = '1'          # explicitly enable diagnostic static bridge
$env:QA_STATIC_READER = 'powershell'
node tests/browser/acceptance.cjs
```

`QA_STATIC_READER=powershell` uses Windows PowerShell `Invoke-WebRequest` and its
binary `-OutFile` response; it does not modify source files or JavaScript. Set
`QA_POWERSHELL` to an approved PowerShell executable if needed. The default reader
for this optional bridge uses Python urllib. `static-delivery.json` records each
static URL path, byte length, SHA-256, and attempt count. Only these read-only
static GETs may retry, at most three times. The bridge accepts only `/assets/` and
`/_dash-component-suites/` paths on the test server. It is a test-only transport
workaround, not production configuration or a pass for native static delivery.
Use an unset/zero `QA_STATIC_BRIDGE` for native acceptance. The bridge is not
needed for the product static-transport fix and is not used by final native runs.

Passing synthetic acceptance does not certify Oracle, LDAP, SMTP, real
notifications, production data, exact company runtime patch versions, or a
production deployment. This harness never connects those systems.
