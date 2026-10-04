# Architecture-slice validation — 2026-10-04

## Results

- Baseline: **779 tests passed** on Linux x86_64, Python **3.8.20** before edits.
- Final full suite: **818 tests passed** on Python **3.8.20** (26.384 seconds).
- Final full suite: **818 tests passed** on Python **3.10.21** (23.009 seconds).
- No failing or skipped tests in either final aggregate run.
- Both runtimes emit non-failing `ResourceWarning` messages for existing synthetic
  QSL factory temporary-directory cleanup and a static-asset test file handle.
  The same 220 warnings were present in the pre-edit baseline. They are retained in the
  log; private interpreter paths and generated temporary names are redacted.
  This increment does not change that existing fixture lifecycle.
- **39 new independent tests**: 28 policy/request/HTTP-isolation cases and 11
  repository/boundary cases. Existing test classes were not inherited to inflate
  totals, and no pre-existing test assertion was weakened or deleted.
- All **81 Python source/test files** parsed with the actual Python 3.8.20
  interpreter. Existing JavaScript assets passed `node --check`.
- `git diff --check` and separate whitespace checks for all newly added Python
  modules/tests passed.
- Independent review identified two implementation defects during this change:
  copied Flask contexts could reactivate a retained scope, and pattern-ID trigger
  values could be unhashable. Both were corrected and regression-tested. Final
  focused review found no remaining material issue.

Commands, run from repository root in each approved environment:

```sh
python -B -m unittest discover -s tests -v
```

Full logs: [Python 3.8](test-results-python38.txt),
[Python 3.10](test-results-python310.txt).

## Tested behavior

- Shared transport/service policy and identical owner/admin UI affordances
- Invalid identities, unchanged exact IDs/org strings, immutable claim copies
- Complete bound list/get/create/update/archive/restore lifecycle
- No caller override of a bound actor or request ID
- Cross-tenant same-ID access, same-org ownership and admin limits
- Concurrent thread calls to one shared service, scoped duplicate draft keys,
  and overlapping real Flask/Dash callback requests with separate clients
- Per-user notification audience and actor/request-ID audit correlation
- Role revocation, changed organization and removed identity on the next request
- No retained scope reuse outside its original request/app, with copied/reentered
  Flask contexts or a reused request ID; teardown invalidation is explicit
- Read-only and expired transaction rejection; SQL owner, tenant, version and
  lifecycle predicates; atomic mutation/audit rollback and sanitized failures
- Ambiguous multi-action, unknown property and forged pattern-ID mutation
  triggers produce no write
- Existing two-process create/update contention, schema migration/backup,
  application, QSL/modals/revision/wizard, administration, operations and ETL
  regression suites remain included in both 818-test runs

## Actual launcher HTTP check

The original `python -B app.py` was started with a temporary synthetic SQLite
store and private, newly generated local session key. An HTTP client connected
only to loopback. Health, readiness, login, Dash layout/dependencies and local CSS
all returned 200. The same authenticated client completed create → select →
update → archive → active-list exclusion → restore → list reload. SQLite was
checked for one persisted row at version 4. The temporary process and store were
removed afterward. This tested the real launcher/server transport and persistence,
not a mocked callback invocation and not browser rendering.

Machine-readable summary: [HTTP_SMOKE.json](HTTP_SMOKE.json).

## Dependencies and migration

No package was added or upgraded. Verified pins include Dash 2.9.1, DBC 1.4.1,
DMC 0.12.0, Flask 2.2.3, Flask-Login 0.6.2, Werkzeug 2.2.3, Plotly 5.13.1 and
setuptools 57.5.0. No schema or StateStore changes. The previously delivered
ETL/QSL runtime files, dependency files, existing tests, and `app.py` were checked
against the preserved pre-edit worktree hashes; none were removed or overwritten.

The worktree had pre-existing staged and unstaged changes. A complete source
snapshot and hash manifest were taken first; no reset, clean, staging, commit,
push or deployment was performed as part of this increment. Release file hashes
are in the existing package manifest.

## Not verified / not claimed

- Real desktop/mobile browser rendering, focus, keyboard, client-side timing,
  downloaded file handling or click-flow visual acceptance. The previously
  recorded cloud-browser access restriction was respected; no alternate preview
  or route was used to bypass it. HTTP and component tests are not browser QA.
- Exact company Python 3.8.13/Linux or Windows Python 3.10.4 and full company
  dependency inventory. Matching minor versions on Linux does not prove these.
- Production identity, Oracle/LDAP/SMTP, external services, production exposure,
  realistic load or distributed/multi-host storage semantics.
- Full-site policy migration. This release completes the `/maintenance` slice;
  other legacy authorization systems keep their existing scope and semantics.

This remains a runnable, synthetic integration foundation, not a verified
production deployment. No company service or remote hosting endpoint was used.
