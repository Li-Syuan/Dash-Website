# ETL dispatch center: independent acceptance evidence

## Scope and boundaries

This increment builds on the v4 reporting workspace. Validation uses isolated,
local SQLite databases, synthetic identities and synthetic adapter inputs. No
company credentials, Oracle connection, SMTP delivery or external service is
used. No Git publish, deploy or production migration is part of this work.

Tests exercise Python services, real Flask/Dash HTTP callback dispatch and
server-generated layout. They do not constitute browser rendering or visual QA.
The previously denied loopback browser route (`ERR_BLOCKED_BY_CLIENT`) was not
retried through another route. Windows and exact company Python patch versions
remain unverified.

## Starting baseline, 2026-10-02 UTC

Run from the repository root in the existing approved isolated environments:

```sh
python -B -m unittest discover -s tests -q
```

| Interpreter | Platform | Result |
| --- | --- | --- |
| Python 3.8.20 | Linux x86_64 | 669 tests; OK; 25.499 seconds |
| Python 3.10.21 | Linux x86_64 | 669 tests; OK; 23.222 seconds |

No packages were installed or upgraded. Existing temporary-directory cleanup
ResourceWarnings were present without failed assertions.

## Independent acceptance, after fixes

```sh
PYTHONPATH=tests python -B -m unittest test_etl_dispatch_acceptance -v
```

| Interpreter | Result |
| --- | --- |
| Python 3.8.20 | 45 tests; OK; 4.649 seconds |
| Python 3.10.21 | 45 tests; OK; 4.112 seconds |

`tests/test_etl_dispatch_acceptance.py` verifies:

- Two distinct jobs, disabled defaults, trusted DAG topology and rejection of
  cycles, unknown/self dependencies, duplicate registrations and an unchecked
  publication branch. Row and registry bounds reject invalid types and sizes.
- Actual timer-thread execution, disabled-job isolation, stop behavior and
  factory/lifecycle helpers. Factory creation and schedule configuration leave
  workers stopped; explicit launcher helpers start and stop real workers.
- Durable repeat-request receipts through service/factory restart, payload/date
  binding, inclusive date backfills, date-level deduplication, retained receipts
  beyond the 100-run display cap, and a maximum of three retry attempts.
- Source failure and count-loss failure, dependent-step skipping, malformed,
  non-finite, overflowing, duplicate, missing and oversized row rejection;
  unsuccessful attempts preserve the prior committed publication.
- Retry creates a new linked run and reuses only verified successful upstream
  snapshots. Corrupt row counts, digests, snapshots, checks, origin pointers,
  incompatible registry signatures and unrelated retry ancestors fail closed.
- Independent service instances cannot overlap the same job. A spawned process
  holding an active lease blocks another process; deliberately terminating it
  leaves an interrupted attempt that is not automatically replayed. An expired
  blocked worker cannot overwrite the successor's successful publication.
- Current-identity reload, read-only mutation denial, forged claims, missing
  users, provider outage, organization and job allowlist isolation, revocation
  during work, and configuration change before publication.
- Real login, main navigation, callback transport and server layout. HTTP tests
  cover repeated manual actions across restart, stable request tokens, refresh
  without overwriting selected job or unsaved schedule fields, canceled/stale
  backfill confirmations, changed-job proofs, repeated confirmations, cross-job
  run selection, malformed/future dates, forged tokens, cross-origin requests,
  session revocation, and quality failure followed by a successful safe retry.

Fault injection and deliberate local SQLite corruption are confined to fresh
synthetic test stores. The HTTP retry test injects a one-time row-count mismatch
at the quality boundary, then uses real callback dispatch and durable execution
for the retry; it does not mock the retry or authorization implementation.

## Issues found and verified fixed

1. A missing backfill endpoint previously defaulted to the current business
   date and could create unintended runs. Both endpoints now must be explicit
   canonical dates; rejected input leaves the database unchanged.
2. Reused snapshot origin metadata was not fully checked. Corrupting the
   original source count or pointing reuse at an unrelated same-date run could
   leave retry enabled. Origin count/check evidence and actual retry-chain
   ancestry are now verified before reuse.
3. UI form loading needed a bounded response for stale/unknown job selection.
   The form now clears its version proof without exposing a raw service error.

## Final combined regression

After the implementation and tests were frozen, the full regression command
was rerun in both existing approved environments:

```sh
python -B -m unittest discover -s tests -q
```

| Interpreter | Final full result | Captured summary |
| --- | --- | --- |
| Python 3.8.20 | 779 tests; OK; 33.874 seconds | [Python 3.8 summary](test-results-python38.txt) |
| Python 3.10.21 | 779 tests; OK; 30.068 seconds | [Python 3.10 summary](test-results-python310.txt) |

All assertions passed, including the independent acceptance tests above and
existing QSL, administration, operations, routing and authorization regressions.
Existing implicit temporary-directory cleanup ResourceWarnings remain visible;
they are not being represented as assertion failures or a warning-free run.

The separate fresh-archive, direct `app.py` launch and real 60-second timer
smoke is recorded in `LAUNCHER_EVIDENCE.json` by the integration owner. Its result
must be read separately; unit/HTTP test success alone does not prove a live
launcher or browser pass.
