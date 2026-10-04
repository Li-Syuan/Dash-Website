# Concurrent maintenance writes and recovery

This v10 increment validates `/maintenance` report definitions against real local
SQLite connections and independent spawned processes. It extends the existing
two-writer update/create-key tests; it does not change the owner/organization
policy, database schema, dependency pins or CRUD UI. The tests use temporary
synthetic state and providers, without company services or external effects.

Run from the repository root in the approved environment:

```sh
python -B -m unittest discover -s tests -p test_concurrency_consistency.py -v
python -B -m unittest discover -s tests -v
git diff --check
```

## Scenarios and acceptance criteria

Each writer owns its own `StateStore`, identity provider, application service and
SQLite connections. A bounded multiprocessing barrier releases writers only
after they have read the record. Separate process IDs are asserted; the test
does not serialize writers with an application mutex or retry failed writes.

| Scenario | Required result |
| --- | --- |
| Six owner/admin writers read version 1 and submit different names | Exactly one version-2 success and five `Conflict` results; exactly one matching success audit; failed writers only succeed after an explicit fresh read and new-version submission |
| Update and archive race, followed by competing restores | One winner for each expected version; lifecycle and audit event match the winner; all losers leave state untouched |
| Owner/admin compete alongside a same-org peer and foreign-org owner/admin | One authorized winner; peer retains reads but gets `AccessDenied` for writes; foreign IDs remain `NotFound`, including identical actor IDs in another organization |
| A separate connection holds `BEGIN IMMEDIATE` through the normal busy timeout | `StateUnavailable` with the fixed safe message; no changed row or success audit; explicit retry works only after the lock is released |
| Writer pauses after staging the row and audit but before commit | An independent reader sees the complete old snapshot; after commit it sees the new row and matching audit together |
| SQLite rejects the audit INSERT with a real CHECK-constraint error after row mutation | The underlying exception is confirmed as `IntegrityError`; the service returns safe `StateUnavailable`; the row/version and audit roll back together; the same expected version remains available |
| Writer process is terminated after staging row and audit, before commit | Reopening the same database preserves the old row and audit, passes `integrity_check`, releases the lock, and permits an explicit new attempt |
| Provider revokes the actor after the writer attempts `BEGIN IMMEDIATE` behind a held lock | Fresh identity validation rejects the write after the wait; no mutation or success audit is committed |

Audit checks match resource ID, hashed actor ID, request ID, event type and
outcome to the winning mutation. Server-owned organization/owner/creation fields
and unrelated editable fields must stay unchanged. Losing and denied requests
must not add success events. Readers inspect the record and audit in one
independent SQLite read transaction to avoid conflating separate snapshots.

The constraint-failure and pre-commit pause hooks are test-worker fixtures only.
They do not add product fault switches, patch transaction code, change schema,
weaken authentication, or shorten the application's 5000 ms busy timeout.

## Why the current transaction contract is retained

`StateStore._transaction(write=True)` obtains `BEGIN IMMEDIATE` before the
repository reads and evaluates the version. Concurrent writers therefore
observe the previous committed winner after acquiring the SQLite write lock.
`ReportDefinitions._transaction()` revalidates the current provider identity
after the lock wait and before commit. The repository repeats organization,
owner/admin, version and lifecycle conditions in the SQL write predicate.
The row and payload-free audit share one connection and transaction; exceptions
roll back both and the connection is always closed.

The expected client action after a conflict is to reload and review the current
record. Automatic retries with a new version could discard another person's
work and are not introduced. A lock timeout is an unavailable operation, not a
successful save. Process termination before commit is recoverable local state;
termination after commit but before the caller receives the response is an
uncertain response and is not evidence that the transaction failed.

## Validation record and limits

On 2026-10-04, the targeted command above passed **8/8 tests in 7.457 seconds**
on Windows (`win32`), Python **3.10.22**, SQLite **3.53.4**. There were no failures
or skips. The run started after the separate performance measurement window
ended. Python 3.8 grammar parsing and whitespace checks also passed. Grammar
parsing is not execution on Python 3.8. The combined full-regression result is
recorded separately by the integration owner.

No maintenance product defect was demonstrated, so this increment adds the
regressions and this contract without changing `definition_repository.py`.

These are repository/service tests, not browser or RESTX endpoint tests. Existing
HTTP and browser evidence remains separate. The fixtures exercise `/maintenance`
metadata, not Oracle transactions, legacy QSL storage, multi-host coordination,
NFS, power-loss durability, or exactly-once external mail/database effects.
Local process termination is not a disk-controller or operating-system crash.
The target Python 3.8.13/company Linux kernel and historical Windows 3.10.4 must
not be inferred from a different tested interpreter or platform.
