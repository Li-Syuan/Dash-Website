> Historical round record: publication of the cumulative source is now
> explicitly authorized. See [the current publication record](PUBLICATION.md).
> Raw execution evidence and checkpoint inventories referenced below are
> retained in the local delivery archives, outside this public source tree.

# Local agent acceptance tool

Current ETL/performance follow-up: [contract, entrypoints and limitations](FOLLOWUP.md).
The prior v12 [validation](VALIDATION.md) describes that preserved historical run.

Use [`render-report`](REPORT.md) for a script-free, offline one-page HTML view
of current results, explicitly historical evidence, failure causes, safe
reproduction commands, measured differences and remaining verification gaps.

This is a fixed local test runner for reviewed repository code. It does not
generate or execute instructions from an LLM, API, report, prompt or config file.
The product still starts only through `app.py`. Python 3.8 is supported; use the
already approved runtime and dependencies. Do not run the historical root
`test_admin.py` database-writing example.

## One-command runs

From the isolated candidate checkout, with the approved Python on PATH:

```sh
python -B tools/agent_acceptance.py run --profile full --baseline ../optimization-v11
```

The baseline is an explicitly trusted local source directory. Its fingerprint
must remain unchanged throughout the run. It needs the existing Git metadata
used by the large-XLSX benchmark. Before/after measurements run sequentially;
keep the machine quiet and stop other test/benchmark activity. No baseline
measurement or old evidence is reused.

For the Windows native-browser profile, configure the already installed local
Playwright module and Chrome before the same command:

```powershell
$env:QA_PLAYWRIGHT_MODULE = 'C:/path/to/approved/node_modules/playwright'
python -B tools/agent_acceptance.py run --profile browser
```

Do not install or download tools automatically. Missing prerequisites produce
a nonzero result. The current browser process cleanup contract is supported on
Windows; non-Windows browser runs are explicitly blocked until detached browser
process ownership is implemented and verified. HTTP coverage is separate from
actual browser operation. The harness uses fresh synthetic accounts and stores,
loopback HTTP and the existing `app.py` fixture; external origins are denied.

Useful focused commands:

```sh
python -B tools/agent_acceptance.py run --profile self
python -B tools/agent_acceptance.py run --profile unit
python -B tools/agent_acceptance.py run --profile authorization
python -B tools/agent_acceptance.py run --profile performance-smoke --baseline ../optimization-v11
python -B tools/agent_acceptance.py run --profile performance --baseline ../optimization-v11
python -B tools/agent_acceptance.py verify --report output/agent-acceptance/RUN/report.json --baseline ../optimization-v11
```

`self` runs the tool's failure/timeout/evidence tests. `unit` discovers only
`tests/test_*.py`. `authorization` runs the fixed isolation suites and both HTTP
entry coverage matrices. `browser` runs the existing 40-case native harness with
the report template enabled. `performance` measures portal latency and large
XLSX time, RSS and export/database sizes before/after. `performance-smoke` is a smaller
latency experiment; it does not replace the large benchmark. `full` runs all
required product checks in order and includes tool tests in regression.
The standalone `self` row remains UNRUN in `full` because those same tool tests
already ran inside regression; it is not a second required invocation.

## Decisions and exit codes

| Code | Status | Meaning |
| --- | --- | --- |
| 0 | PASSED | Every required full-profile check passed with valid evidence |
| 1 | FAILED | A test, browser case, runtime error or explicit budget failed |
| 2 | BLOCKED | A prerequisite or confirmed process cleanup is unavailable |
| 3 | INCOMPLETE | A required check is unrun, skipped, expected-failure, empty, or lacks a budget |
| 86 | INVALID | Source/runtime changed, a clock stepped back, or evidence is inconsistent/stale |
| 124 | TIMED_OUT | An owned step exceeded its deadline and cleanup was confirmed |

A focused profile normally exits 3 even when its selected checks pass because
the other required checks remain UNRUN. Consult `selected_checks_passed` and
the individual outcomes; do not convert exit 3 into whole-project success.
Platform skips remain visible and incomplete. Failure takes precedence over
incomplete; invalid evidence takes precedence over other outcomes.

No performance tolerance was approved for v12. Measurements and comparisons
are still recorded, but comparisons remain INCOMPLETE. The legacy
`--max-wall-regression-percent` and `--max-rss-regression-percent` flags now
produce descriptive comparisons only; they cannot establish a calibrated pass
or fail. Use `assess-performance` with the explicitly approved policy and
independent evidence described in [FOLLOWUP.md](FOLLOWUP.md).
The portal limit applies to scenario medians; the XLSX limits apply to median
wall time and peak RSS. p95 and file sizes remain descriptive evidence. The
separate historical v11 temporary-disk probe is not rerun by these profiles.

`--timeout-seconds` is a bounded per-step deadline (default 1200 seconds).
There are no automatic retries. Windows execution assigns an owned process
tree to a Job Object before starting the reviewed command and verifies process
termination. POSIX core execution owns a process group for trusted children
that do not detach; it is not a hostile-code sandbox. Killing the outer runner
forcibly can prevent final report creation; absence of a complete report is
never success.

## Evidence and freshness

Each invocation creates a unique directory below the candidate's `output/`.
It contains `report.json`, `report.sha256`, `SUMMARY.md`, raw logs, immutable
process receipts, clock events and producer JSON/browser artifacts. Commands
are recorded as argv arrays. Verification reads them as data and never replays
them. Receipts must match the fixed command registry and actual producer
counts, case catalogs, raw samples, aggregate statistics and artifact hashes.
The report includes environment, program hash, baseline changes and before/after
performance and memory. Keep failed attempts alongside later corrected runs.

The program fingerprint covers root Python files, `reporting_workspace/`,
`assets/`, `tests/`, `tools/`, benchmark Python scripts, requirements and CI
workflows, including additions/deletions. Runtime state, output, evidence,
databases and caches are excluded. Documentation is separately reviewable in
Git and does not alter a program fingerprint. Observed symlink/reparse paths
are rejected before output creation or artifact traversal.

`verify` defaults to a 24-hour maximum age, rejects future timestamps, and
requires the current source/tool hashes, recorded Python/package versions,
all required raw artifacts and the explicitly supplied current baseline.
The CLI permits a deliberate maximum age of 1 second to 7 days. Browser
Node/Playwright/Chrome information describes the runtime used during the run;
verification does not certify that the installed browser binary is unchanged.
Python environment snapshots are compared before, between and after steps.
Clock monitoring only reads wall/monotonic clocks; Windows uses precise FILETIME
and QPC, while POSIX uses realtime/monotonic clock_gettime. No time settings,
networking, security controls or session verification are modified.

SHA256 detects changes and binds local evidence; it is not a signature or
independent attestation. A local owner able to rewrite every artifact and the
tool can fabricate a complete bundle. Only run reviewed local test code;
untrusted repositories are not safe inputs. The tool has no company adapter,
Oracle/LDAP/SMTP/real-notification, external LLM/API, push, merge or deployment
action. Synthetic success does not certify company integrations or production.

## v12 review boundary

The candidate is based on the local v11 restore checkpoint
`1e7983962a19438d37d1d97f173ec0fe643249c9`, preserving the original dirty v11
checkout. The checkpoint is local and unpushed. This round adds acceptance
tooling, its tests and agent guidance. Product runtime behavior, dependency
pins, CRUD modals, ETL, report template and the default export cap are unchanged.
No push, merge or deployment is authorized in this round.
