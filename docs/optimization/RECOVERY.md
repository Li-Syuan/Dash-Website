# ETL interruption and SQLite lock recovery

This v10 increment adds executable recovery evidence for the v9 dispatcher.
It does not change the ETL runtime, schema, automatic retry policy, adapters,
dependencies or scheduling configuration. The dispatcher already preserves the
tested safety properties; no runtime correction was justified by these faults.

## Reproduce

From the project root, using the approved environment:

```sh
python -B -m unittest discover -s tests -p test_etl_recovery_faults.py -v
python -B -m unittest discover -s tests -p 'test_etl*.py' -v
```

The tests construct a temporary synthetic SQLite database and spawn fresh Python
processes. A test-only dispatcher subclass places bounded IPC barriers before
or after real transaction commits. The parent terminates selected workers with
no Python cleanup. Recovery uses another fresh process and the same database.
SQLite transaction/commit/rollback functions are not mocked. Reader and writer
contention use independent real connections and the existing five-second SQLite
timeout. No company source, credentials, network service or notification is used.

The only deliberate wait for recovery follows the stored lease's natural expiry.
Tests neither rewrite leases nor alter the system clock. Every case checks SQLite
integrity and foreign keys after reopening the store. Adapter invocation messages
contain only synthetic step names and business dates.

## Fault boundaries and required outcomes

| Fault | Observed durable outcome and continuation rule |
| --- | --- |
| Kill before the claim commits | Claim, request receipt, lease and steps all roll back. Resubmitting the same request executes once and publishes once. |
| Kill inside the source adapter | The committed claim survives. After expiry the same request becomes `failed/interrupted`, executes no adapter, and cannot be retried. An explicitly new request can run with a higher fence; the interrupted run remains. |
| Kill before a step snapshot commits | The snapshot and digest do not leak from an uncommitted transaction. The attempt becomes interrupted without replay. |
| Kill before the publication commits | Previously successful publication remains byte-for-byte unchanged. The new run has durable completed steps but becomes interrupted; completed snapshots do not authorize a replay. |
| Kill after publication commits but before acknowledgement | Run success and publication remain committed together. Resubmission returns the original run receipt, calls no adapter, and creates no additional run/publication. |
| Kill on day two of a three-day backfill | Day one stays published, day two remains failed/interrupted, and resubmission runs only the previously unclaimed day three. Repeating the range again executes no adapters. |
| Writer lock blocks the initial claim until timeout | A sanitized `StateUnavailable` is returned with no receipt or run. After releasing the lock, the same request can execute once. |
| Transient writer lock blocks the initial claim | No adapter executes before durable claiming. Releasing the lock allows exactly one local run/publication; resubmission only reads the receipt. |
| Writer lock after the final adapter returns | Failed snapshot persistence records `storage_unavailable`, retains the prior successful upstream snapshot and previous publication, and disallows retry/replay. |
| Reader lock blocks publication commit until timeout | Pending publication and run success both roll back. The attempt records `storage_unavailable`; the old publication survives and the request cannot execute again. |

## Validation record

The source checkpoint is v9 commit
`a35898344c752e6c41fab0afdfd81d7d6ebdb617`. The ETL runtime remains identical
to that checkpoint (`etl_dispatch.py` SHA256:
`ec4a41861c3c2a930b9b72614e3a7b338685fae1526ca73d79486e59e24381a9`).
On Windows Python 3.10.22, the first eight fault cases passed
against that runtime in **17.138 seconds**. After adding the two lock cases and
waiting for the coordinated performance measurement window to finish, the final
targeted run passed **10/10 tests in 29.510 seconds** (zero failures/errors/skips).
Python 3.8 grammar validation and `git diff --check` also passed. No failed
baseline was concealed and no runtime change was made to obtain these results.

Do not sum these test cases with checks made inside them as additional tests.
This is subprocess/storage evidence, not a browser result. The full v10 suite
and any separately executed Python 3.8 run are reported by the round's validation
record; this document does not infer them from the Windows targeted run.

## Recovery procedure and limits

1. Keep the durable database, receipts, snapshots and fencing counters. Do not
   clear a request or restore an older database to make it execute again.
2. Allow the current lease to expire after an interrupted process. Reissuing the
   same request or performing an explicit dispatcher tick records the expired
   attempt as interrupted without executing it. Read-only status/detail calls
   do not themselves change expired state. Inspect the resulting durable run;
   an uncertain attempt is not resumed as successful work.
3. For a partial backfill, resubmit the same request/range. Already claimed days
   retain their results, including failed/interrupted days; only unclaimed dates
   execute. Reconcile a failed day before considering a new manual request.
4. Retry only failures the service marks eligible. A storage/lease/interruption
   failure is deliberately ineligible. A new request is a new authorized intent,
   not an automatic retry of an uncertain result.
5. Release an accidental local database lock, inspect the preserved attempt and
   existing publication, then act according to the durable result. Do not infer
   success from a browser timeout or an absent acknowledgement.

These tests establish same-host local SQLite behavior for bounded synthetic
snapshot adapters. They do not cover power loss, disk corruption/full disks,
filesystem loss, NFS/multiple hosts, external Oracle commits or SMTP delivery.
Exactly-once external effects require a separately reviewed idempotency/outbox
contract. Blocking adapter cancellation is still cooperative: a killed process
can be stopped, but the in-process worker cannot safely kill an arbitrary Python
callable. Stable wall clock and deployment lifecycle requirements remain in
[the ETL integration contract](../etl/INTEGRATION.md).

The exact target Linux kernel 3.10.0-1160/Python 3.8.13 and historical Windows
Python 3.10.4 are not established by this Windows 3.10.22 result. No service,
clock, network or security setting was changed for these tests.
