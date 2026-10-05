# Change-impact CLI validation (local only)

2026-10-05. Independent checkout based on
`a0b367f4fc4a471da0ffd35c36d05e3327a21704`, Linux 6.18.44 x86_64,
existing approved Python 3.8.20 and 3.10.21 environments. No dependencies,
shared runtime, product behavior, company integrations or system settings changed.
Only CLI/tests/docs were added. No push, upload or deployment.

## Final results

| Check | Python 3.8.20 | Python 3.10.21 |
| --- | --- | --- |
| Impact CLI focused tests | 30 passed, 0 skipped; 10.130 s | 30 passed, 0 skipped; 8.852 s |
| Full unittest discover | 1192 total: 1186 passed, 6 skipped; 106.335 s | 1192 total: 1186 passed, 6 skipped; 101.552 s |
| Full-run clock guard | Valid, exit 0, no backsteps/errors | Valid, exit 0, no backsteps/errors |

No failures or errors in completed runs. The 30 new tests are included in the
1192 full-suite total; do not count them twice. The six skips are existing
Windows-only junction (three), Job Object/process ownership (two) and native
precise-clock backend (one) checks. `git diff --check` passed.
Runtime code and test file SHA256 values, log hashes and counts are recorded in
[EVIDENCE.json](EVIDENCE.json). Elapsed test times are not performance benchmarks.

The first Python 3.10 full invocation is **INCOMPLETE**, not passed: its execution
session was interrupted/expired, the log stopped during the ETL process-exit
case, and neither unittest nor clock guard had a completion footer. The exact
termination cause is unavailable; no Guardian denial was received. That log and
its clock-start record were retained unchanged. A separate authorized invocation
on the same source hashes completed with the result above, including that ETL
case. The new run does not retroactively certify the interrupted one.

## Tests added

- Real repository mappings for quality-actions, report-template, ETL default
  jobs, managed-report API and mock mail scheduling; shared authorization full
  fallback; standalone demo-login excluded.
- Quality-actions/report-template isolation counterexamples, static aliases,
  nested PageSpec declarations, relative imports, re-exports, deterministic
  shortest chains, dependency direction and cycles.
- New/deleted/renamed/untracked, unknown/unmapped, syntax failure, unresolved
  package members, dynamic imports and missing callback metadata fallback.
- Path traversal, shell/option injection, symlink boundaries, staged/unstaged/
  untracked Git input, local base comparisons and deterministic JSON/text.
- Sentinel product/test code is parsed without execution; only fixed read-only
  Git commands run, and all suggested test commands remain inert argv data.

A read-only independent mapping review found two uncertainty gaps (missing
callback metadata and unresolved members of known packages). Both were fixed
before final focused/full validation, with regression tests. There are no known
remaining blocking review findings. Static analysis is still not complete
runtime reachability or coverage evidence.

## Reproduction

Use each existing approved environment's Python from the repository root:

```sh
python -B -m unittest discover -s tests -p test_change_impact.py -v
python -B tests/probes/clock_guard.py --output output/change-impact/clock.jsonl -- python -B -m unittest discover -s tests -v
python -B tools/change_impact.py --changed reporting_workspace/quality_actions/service.py --format json
git diff --check
```

The representative quality-actions command identifies its page and two
query/export callbacks with dependency evidence, plus 22 test-file candidates.
The report always requires full regression before publication. JSON is unsigned
inert input, not an executable plan or a release certificate.

## Unverified and intentionally unrun

No browser work was needed for this CLI-only change, and the previously blocked
browser/socket route was not retried. No new browser pass is claimed. Optional
combined entrypoint/browser commands printed by the analyzer are recommendations,
not commands it executed. Exact target Linux/Python 3.8.13, Windows/Python
3.10.4, private RESTX/Oracle/LDAP/SMTP integrations and deployment remain unverified.
No 15%/10% performance policy was enabled. All publication requires separate
current authorization and full applicable acceptance evidence.
