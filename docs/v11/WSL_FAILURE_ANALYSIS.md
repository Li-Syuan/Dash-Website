> Historical v11 record. See [current publication scope](../acceptance/PUBLICATION.md).
> Full raw evidence and local checkpoint inventories are preserved separately.
> Only the sanitized browser result JSON needed by regression tests is retained here.

# Python 3.8 WSL failure: preserve, do not retry

One final run used the approved cached Python 3.8.20 runtime and Node v22.14.0,
on WSL's native temporary filesystem. Unittest ran 962 cases: **961 passed,
1 failed, 0 skipped**, exit 1. The independent read-only clock guard observed
**3 backwards wall-clock steps**, no monitor errors, and returned **exit 86**.
The environment is invalid for a clean Python 3.8 acceptance gate.

The sole failed case was
`ETLRecoveryFaultTests.test_kill_before_snapshot_commit_keeps_no_partial_snapshot`.
After terminating the owned synthetic worker before snapshot commit, the test
confirmed a running source step with no snapshot or digest. It then waited for
the stored lease and requested the same run. Its assertion expected
`error_code == 'interrupted'`; the actual value was `None`.

## Evidence and inference

Both affected files are byte-identical to the v10 main baseline:

| File | SHA256 |
| --- | --- |
| `tests/test_etl_recovery_faults.py` | `ddc768bde960e47437ac170877c54e1692ee42ee9d58377a728c4446dd945e73` |
| `reporting_workspace/etl_dispatch.py` | `ec4a41861c3c2a930b9b72614e3a7b338685fae1526ca73d79486e59e24381a9` |

The test's `_expire_naturally` reads durable `expires_at`, computes
`remaining = expires - time.time()` once, and sleeps `remaining + 0.03`.
ETL `_recover` changes running rows to interrupted only when the current wall
time has reached the durable lease deadline. `_claim` returns the existing
receipt before creating another attempt. A wall-clock backstep during or after
that sleep can therefore leave the lease unexpired and the existing running
result's `error_code` at `None`, consistent with this assertion failure.

This is an evidence-backed explanation, **not proof of the exact cause**:
the failed assertion did not log its current wall time or the lease deadline.
The clock guard recorded approximately 0.6–0.75-second backsteps during the
suite, but its samples do not timestamp individual unittest assertions.
The failing case passed in the final Windows run; prior v10 CI also passed its
unchanged case. Neither result replaces this failed v11 WSL case.

## Handling and remaining gate

No WSL full-suite or focused retry was performed. No host/guest clock, time
service, network, security setting, session signer, cookie, lease value or
test wait was modified. The time-service state was identical before and after.
The result remains a failure and the overall environment remains invalid.
There were no unraisable exceptions or post-summary shutdown tracebacks.

The complete raw unittest log, structured result and clock JSONL are in
`evidence/regression/`. Native Windows and actual Chrome results are reported
separately. A clean Python 3.8 gate remains outstanding; no v11 GitHub CI was
triggered because v11 push is not authorized. Do not relabel the old v10 CI as
verification of this unpushed candidate.
