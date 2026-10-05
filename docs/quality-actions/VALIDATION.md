# Independent report handoff validation (2026-10-05)

## Scope and source

Fresh clone baseline: `45918b243af32cf8d4db966e82bfc6b634bf3771` from
Li-Syuan/Dash-Website main. This local increment adds a distinct corrective-action
aging report and closes concrete handoff/coverage instructions gaps. The existing
inspection template, product entrypoint, routes and company integration boundaries
are retained. No push, upload, merge to remote or deployment was performed.

## Environment

Dot cloud, Debian 13.6, Linux x86_64 kernel 6.18.44. User authorized isolated
installation of the existing approved requirements and Python 3.8/3.10 runtimes.
Existing uv 0.12.19 installed Astral-managed CPython 3.8.20 and 3.10.21; PyPI
resolved `requirements-qa-portal.txt`. No declared dependency pin was changed.
Complete actual installed versions are in `runtime-python38.json` and
`runtime-python310.json`. Transitive versions differ by interpreter; the existing
minimal requirements remain a subset, not a full transitive lock.
Node 24.19.0 and system Chromium 154.0.8037.57 were already available.

## Passed

- New focused report suite: **37 tests passed, no skips**, on both Python 3.8.20
  and Python 3.10.21. Includes date/late-closure/historical snapshot semantics,
  whole-filter KPI counts, stable sort/pagination, exact public schema, source
  bounds/closing/failure, formula-safe CSV and header-only empty export.
- Authorization includes same-organization sharing, foreign admin own-tenant
  scope, anonymous/guest/forged/changed/revoked identity denial, lazy-iteration
  revocation and revocation while serializing the final CSV cell. No result is
  published after those revocations. These tests are distinct from the sibling
  QSL export repair; that repair is not included in this report-only checkpoint.
- Configuration tests verify default off, strict flag values, per-app isolation,
  original-template coexistence and production rejection even for explicit SPEC.
- Real Flask/Dash router/query/export transport tests passed. These are HTTP
  boundary tests, not browser rendering evidence.
- Final full regression: Python 3.8.20 **1159 tests, 1153 passed, 6 skipped** in
  101.173 seconds; Python 3.10.21 **1159 tests, 1153 passed, 6 skipped** in
  95.065 seconds. No failures/errors. The counts include the 37 new tests.
- Both opt-in reports enabled in entrypoint coverage: **5 tests, 279 matrix
  checks passed**, including the new page, query and export callback identities.
  Matrix subchecks are not additional unittest cases or browser scenarios.
- Python 3.8 AST syntax check and `git diff --check` passed.

Initial pre-new-test Python 3.8 regression is also retained: 1122 tests with
1116 passed and the same 6 Windows-only skips. It is historical initial-run
coverage, not the final report result, and is not added to the final test count.

## Platform skips

All six skips use existing Windows-only guards:

- Directory junction cannot escape artifact/source checks
- Windows Job Object assignment failure never launches the target
- Windows Job Object parent-exit descendant accounting
- Windows output junction cannot authorize external writes
- Native Windows precise-clock guard regression
- Existing junction ancestor rejection before output-directory creation

These cases were not validated on MSI/Windows. Passing Linux cannot replace them.

## Browser execution

The new harness adds 10 named corrective-action scenarios to the existing 32
base/40 template scenarios. Both report flags request **50 scenarios**. Native
browser delivery is required; no static bridge is used.

**BLOCKED, not passed.** Default and approved escalated shell launches failed
before any scenario: Chromium `process_singleton` reported
`socket() Operation not permitted`. Each result is **0 passed, 1 harness-startup
failure, 50 explicitly unrun scenarios**. The supported managed cloud browser
also rejected the isolated loopback fixture with `net::ERR_BLOCKED_BY_CLIENT`.
No proxy/tunnel, alternate host, security/network change or restriction bypass
was attempted; HTTP tests above are not substituted for browser evidence.

Both original failures are retained under
`output/playwright/quality-actions/2026-10-05T02-04-05-296Z/` and
`output/playwright/quality-actions/2026-10-05T02-04-44-466Z/`. Browser JavaScript
syntax checks and fixture startup metadata checks passed, but do not validate
rendering or interactions. A supported environment that permits the existing
harness's local server/browser is needed for the 50 real browser scenarios.

## Reproduction and evidence

Commands are in [README.md](README.md). Private generated evidence remains under
ignored `output/handoff/` and `output/playwright/`; runtime files and summaries
here are sanitized. Logs record actual commands/results; no account credentials,
request bodies or production data are included in committed source. Browser
captures/downloads, SQLite state, runtimes and caches must remain out of Git.

## Unverified and deliberately unchanged

- Exact Python 3.8.13/3.10.4, Linux kernel 3.10.0-1160, MSI/Windows
- Company Oracle, LDAP, SMTP, Flask-RESTX and SQLALCHEMY_BINDS integration
- Production deployment, real data, company quality policies and SLA semantics
- Whole-project acceptance and performance gates; Windows skips and any browser
  blocker prevent interpreting these checks as whole-project certification

No system clock, network, OS security setting, real notification or company
credential was changed or used. There is no new dependency, data migration,
arbitrary report designer, external API or background worker.

## Final harness-only cross-check

After the blocked launches, static review corrected the default snapshot's open
count to 25, closed count to 11 and open-overdue count to 25. Case 33 closes on
2026-10-06, so it belongs to the 2026-10-05 open CSV (25 rows). Eleven explicit
query/date/sort expectation sets were corroborated against the service; all four
independent fixture opt-in combinations and both invalid flag rejections passed
(six configuration checks). These are service/fixture checks, **not browser
passes**. The final helper SHA256 is
`388c3af6baaeeb86ae92bddf783a5280d06070cb3fcbf7c0f68fae30062b4cd2`.
The final corrected browser scenarios remain unrun. Earlier failure evidence
retains its original harness hashes instead of being relabeled as final evidence.
