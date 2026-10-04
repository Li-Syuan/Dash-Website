> Historical round record: publication of the cumulative source is now
> explicitly authorized. See [the current publication record](PUBLICATION.md).
> Raw execution evidence and checkpoint inventories referenced below are
> retained in the local delivery archives, outside this public source tree.

# v12 validation — local and unpushed

The executable tool was exercised end to end on unchanged v11 product code. Windows full acceptance returned **INCOMPLETE / 3**. Python 3.8 returned **INVALID / 86**. Neither is whole-project PASSED.

| Evidence | Actual result |
| --- | --- |
| Windows Python 3.10.22 full regression | 1044 cases: 1040 passed, 0 failures, 0 errors, 4 skipped |
| Authorization subset | 97/97 passed; already overlaps full regression |
| HTTP entry coverage | Default 263/263; report template 271/271 checks; separate from browser |
| Native Chrome | 40 passed, 0 failed, 0 unrun; owned server/state cleanup confirmed |
| Windows clock | 0 backward steps, 0 monitor errors |
| Python 3.8.20 WSL regression | 1044 cases: 1037 passed, 1 failures, 0 errors, 6 skipped; gate INVALID |
| WSL clock | 3 backward steps, 0 monitor errors; system configuration unchanged |
| CLI deliberate-failure cases | 28/28 passed after fixing demonstrated clock/evidence issues; also included in full regression |
| Pure producer validators | 23/23 passed; also included in full regression |

Windows regression elapsed 140.765 seconds; browser elapsed 176.969 seconds. Browser version: `154.0.8037.95`.

All native selected phases ran once in this full invocation. Required unverified gates: `regression, latency_compare, xlsx_compare`. The four Windows platform skips and missing performance budgets are retained. No skipped/unrun result is promoted, and unrelated test/check counts are not summed.

## Python 3.8 limitations

The approved cached Python 3.8.20 runtime executed the complete unit profile on an isolated WSL temporary filesystem. No network/runtime download or clock/service modification occurred. A clock-invalid run is not a valid Python 3.8 acceptance certificate. The report preserves the actual unittest result separately; no automatic retry occurred.

Failed method IDs:
- `test_etl_recovery_faults.ETLRecoveryFaultTests.test_partial_backfill_restart_continues_only_unclaimed_dates`

The existing ETL backfill restart test received a `Conflict` stating that its
job was still running after the fixture's expiry wait. Its code and all product
code are unchanged from v11. Backward wall-clock adjustments are a plausible
contributor to lease-expiry behavior, but the exact assertion instant is not
correlated with a recorded clock event, so causality is not proven. This remains
an unresolved observation in an invalid environment; it was not retried or
patched in the acceptance-tool round. All 82 new tool test methods executed:
76 passed and 6 platform cases were skipped on Python 3.8.20.

Browser and performance profiles were not rerun on WSL. Linux native-browser ownership is explicitly unsupported by this version of the runner; Windows supplies the actual UI evidence.

## Before/after measurements

Before/after product hashes are identical; this is an acceptance-tool integration measurement, not a product optimization claim. Portal: 5,000/25,000 rows, 7 scenarios, 15 measured samples and 3 warmups each. XLSX: 100,000/200,000 rows, 3 measured samples and 1 warmup each; all 16 workbooks are semantically validated. The default 50,000-row cap still rejects larger data. No other agent tests ran during the measurement window.

| XLSX rows | Before seconds | After seconds | Before peak RSS MiB | After peak RSS MiB |
| --- | --- | --- | --- | --- |
| 100000 | 12.427 | 12.764 | 44.180 | 44.223 |
| 200000 | 25.648 | 23.675 | 48.254 | 48.344 |

Both comparisons remain INCOMPLETE because no time/RSS acceptance budget was supplied. Detailed medians, p95, raw samples and file sizes are in the machine report. The separate historical v11 temporary-disk probe was not rerun.

## Reproduction and integrity

```sh
python -B tools/agent_acceptance.py run --profile full --baseline ../optimization-v11
python -B tools/agent_acceptance.py verify --report output/agent-acceptance/RUN/report.json --baseline ../optimization-v11
```

Program digest: `c9e5ef814d565845a824dfea3aba3a1d062418b1f0eeb9ef261fe86b68fa6ff4`.
506 original v11 files and 61 product/asset files remain byte-identical. The local restore checkpoint is `1e7983962a19438d37d1d97f173ec0fe643249c9`. Source syntax is Python 3.8 compatible; exact historical Python/OS patch targets are not certified by these runtimes.

Raw evidence stays local. Sanitized delivery copies have an explicit original/delivered hash map in EVIDENCE_INTEGRITY.json. They are derivatives, not new execution receipts; verify the original local run or run a new profile after extracting the archive. The public report digest is not a signature.

No v12 GitHub CI was run because this round is unpushed; historical CI is not v12 evidence. No push, merge, deployment, company service, real notification or external model/API was used. Library was not retried: the known official helper remains unavailable; last verified Library version is 8.
