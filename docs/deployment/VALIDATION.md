# Deployment checks: v11 evidence

The published base is `e4a9681137f4762e4fcdcfee356351b7786a9420`.
This deployment slice is a local v11 change, not a deployment or a new push.
All data was generated in temporary directories; tests used native Windows
Python **3.10.22**, existing approved packages and owned child processes only.
No company service, real notification, host/guest clock or OS configuration was
used or changed.

## Commands and outcomes

From the repository root, in the approved Python environment:

```sh
python -B -m unittest discover -s tests -p test_deployment.py -v
```

| Run | Result | Explanation |
| --- | --- | --- |
| Initial targeted run | **17 passed, 1 error; 5.461 s** | The lifecycle test exposed that `JobMonitor` has an owned `_thread`, not ETL's `worker_running` property. The initial helper therefore could under-report that worker. |
| After correction | **18 passed; 5.398 s** | The helper inspects the actual threads of both worker types; all backup/restore, readiness and CLI tests passed. |
| Final expanded lifecycle case | **1 passed; 0.250 s** | Explicitly started/stopped each owned synthetic JobMonitor and ETL worker with jobs disabled, checked `running`/`not-started`, and checked sanitized disposed state. This is a rerun of one of the 18 cases, not an additional test count. |
| Reviewer finding reproduced | **2 failed; 0.282 s** | Closing the actual QSL or ReportBuilder persistent connection still returned readiness 200 because a fresh connection could read the valid file. Both new regression tests failed before the fix. |
| Persistent-connection fix | **4 passed; 0.662 s** | Both closed connections now return sanitized 503. An owned thread holding either resource lock causes bounded 503, followed by recovery after release. The existing worker/disposed lifecycle case also passes. |

The final targeted lifecycle invocation, with `tests` on `PYTHONPATH`, was:

```sh
python -B -m unittest test_deployment.DeploymentTests.test_liveness_and_readiness_preserve_healthy_contract_without_starting_workers -v
```

The four-case persistent-connection verification used the following tests in one
`python -B -m unittest ... -v` invocation, with `tests` on `PYTHONPATH`:

```text
test_deployment.DeploymentTests.test_readiness_rejects_closed_persistent_qsl_connection
test_deployment.DeploymentTests.test_readiness_rejects_closed_persistent_builder_connection
test_deployment.DeploymentTests.test_persistent_connection_probe_is_bounded_and_recovers_after_lock_release
test_deployment.DeploymentTests.test_liveness_and_readiness_preserve_healthy_contract_without_starting_workers
```

The initial failed result is preserved here rather than relabeled as success.
No tests remain known failing in this targeted slice. The root v11 validation
record owns the complete regression and actual Python 3.8/browser results; they
are not inferred from these targeted Windows runs. The deployment module now
contains 21 cases (the original 18 plus three persistent-connection cases); reruns
in the table are not additional distinct test cases.

## Exercised boundaries

- Configuration and provider-interface validation makes no provider calls and
  prints no supplied secret or filesystem path. Missing production providers
  are rejected rather than dynamically imported.
- Current-state readiness detects a missing sibling without recreating it,
  incompatible state and an actual exclusive SQLite lock. The lock check fails
  within the test's two-second outer bound and recovers after release. Healthy
  responses retain the established fields; worker state is observed directly.
- Persistent QSL and ReportBuilder connections are probed under their existing
  locks with constant `SELECT 1`, without reading report rows or contacting a
  provider. Actual closed connections fail readiness. A separate thread holding
  either resource lock is rejected within the test's one-second outer bound;
  releasing it restores readiness. No service implementation file was modified.
- A complete capture/restore includes the eight required stores and all three
  lazy fixture stores after explicit synthetic access initializes them. Logical
  content for every store is equal before/after, including a running uncertain
  claim, durable fence and an enabled ETL configuration.
- The restored running claim still rejects duplicate claiming. No worker starts
  on restore, and the review marker blocks factory construction before restored
  state or providers can be initialized.
- Missing stores, unknown sibling stores, WAL and a removed QSL history-protection
  trigger fail closed. Validation checks the actual schema contract, not merely
  whether the file can be opened as SQLite.
- An owned child process holding a real writer lock blocks capture with bounded
  failure. Already acquired locks are released; the same source can subsequently
  be backed up to a new destination after the blocker exits.
- Every backup copy is observed while independent write connections attempt
  `BEGIN IMMEDIATE` on **all** eight stores: all are rejected until capture ends.
  This proves simultaneous writer exclusion rather than independent live copies.
- Fault injection after the first copied member leaves a partial backup/restore
  explicitly incomplete. No final manifest is emitted; partial restoration is
  factory-blocked. This simulated disk error tests cleanup semantics, not actual
  disk-full or power-loss durability.
- Corrupt bytes, incompatible code fingerprints, wrong declared release,
  omitted required members and path traversal in a manifest prevent restore.
  Existing destinations and backup-overlapping destinations are never overwritten.
- The rollback drill changes only a synthetic source's schema version to 999,
  rejects backing up that unsupported source, then restores its earlier valid
  snapshot elsewhere. The original remains at 999; the restored copy is the
  exact known before-image, at the supported schema version.
- Real CLI child processes execute preflight, backup and restore successfully.
  Their stdout contains sanitized results; every owned process/worker is stopped.

## Deliberate limits

The tests do not prove that an operator's quiescence assertion covers external
processes or non-SQLite systems, nor do they validate company Oracle/LDAP/SMTP,
exact target OS/Python patches, arbitrary release downgrades, unreviewed bind
layouts, network filesystems, disk exhaustion, corruption recovery or power loss.
There is no migration, worker activation, claim reset, automatic retry or data
cutover. Source/data compatibility and operational reconciliation remain explicit
requirements in [the operations guide](OPERATIONS.md).
