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
