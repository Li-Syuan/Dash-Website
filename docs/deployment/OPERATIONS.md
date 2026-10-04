# Offline deployment, backup and rollback checks

`app.py` remains the only product launcher. `tools/deployment_check.py` is an
offline maintenance command: it never serves HTTP, constructs the application,
starts a worker, contacts Oracle/LDAP/SMTP or deploys a release. Run it with the
approved environment and this repository's existing pinned dependencies.

These checks support the bundled same-host local SQLite layout. They do not
certify company adapters, a target OS, NFS, multi-host locking or external effects.
The exact target Linux kernel 3.10.0-1160/Python 3.8.13 and historical Windows
Python 3.10.4 remain separate acceptance targets.

## Configuration preflight

From the project root:

```sh
python -B tools/deployment_check.py preflight --config-json /absolute/path/configuration.json
```

The JSON file is a mapping of the existing `REPORTING_*` environment settings.
For a completely isolated demo configuration, `{}` is valid. Omitting
`--config-json` explicitly checks the current process environment. Output contains
only status, mode, storage class and validation limits, never secret values or
private paths. Exit status is `0` for valid configuration and `2` for rejection.
The command validates existing `Settings.from_env` rules, provider shape and the
configured local path's form/parent. It does not create the database, prove write
permission, test a deployment secret's entropy or call a provider.

Production requires actual provider instances from trusted deployment code.
The CLI cannot import arbitrary provider paths from JSON or environment, and
therefore rejects production configuration without those instances. A deployment
owner can call `configuration_preflight(settings_mapping, identity_provider,
report_provider)` directly using the existing approved instances. This checks
their declared interfaces and non-demo markers without authenticating, querying
or otherwise proving the company's integrations.

The optional report-template flag follows the central Settings contract; no
second environment parser or alternative server is introduced here.

## Liveness and readiness

The existing `/healthz` and `/readyz` routes call the helpers in
`reporting_workspace/deployment.py`.

- Liveness preserves `status` and `mode`. A disposed app returns sanitized 503.
- Readiness validates every required local store for the current app, including
  QSL/report definitions/mock jobs, operations, job-monitor state and its separate
  lease store, and ETL. Lazy maintenance fixture stores are checked if created.
- Connections are read-only, use a 50 ms SQLite busy timeout and check current
  schema/version metadata plus bounded reads. Readiness performs no full data
  scan, backup, schema migration, provider request or worker startup.
- QSL and ReportBuilder retain live SQLite connections. Readiness also acquires
  each resource's existing lock with a 50 ms timeout and executes constant
  `SELECT 1` on that actual connection. Thus a readable file cannot conceal a
  closed active service connection. A busy resource can return temporary 503;
  readiness recovers after the lock is released, while liveness remains separate.
- Missing/incompatible/locked storage, unavailable resources, a worker error or
  a restore-review marker returns only `{"status":"unavailable"}`, HTTP 503.
  A failure does not recreate a missing database.
- Healthy output preserves `status`, `storage` and `scheduler`; the latter is
  `running` when either owned ETL or JobMonitor thread is actually alive, otherwise
  `not-started`. It does not infer that an external supervisor intended workers
  to be running, or that company integrations are healthy.

The local checks do not replace real provider readiness contracts or a hosting
owner's request-drain and worker-lifecycle supervision.

## Capture a complete stopped set

Stop accepting requests, drain active requests, stop **all** writers/workers for
this set, and close long-lived connections. In an app-owning integration, call
`dispose_app(server)` only after draining requests. Check shutdown failures;
`--quiesced` is an explicit operator assertion, not proof that other processes
were stopped. Do not stop a live production service through these example tests.

Use absolute local paths and a destination directory that does not exist:

```sh
python -B tools/deployment_check.py backup --state /absolute/source/workspace.sqlite --destination /absolute/backups/new-release-snapshot --release reviewed-release-id --profile demo --quiesced
```

On Windows, use absolute drive paths in place of the POSIX examples. The release
label is operator-supplied metadata, not a claim that the current Git tree is
clean or that the label names a verified commit. Record the independently checked
commit and any local changes with the release package. Storage-code fingerprints
are recorded automatically.

The `demo` profile requires these eight stores:

| Relative backup member | Purpose |
| --- | --- |
| `workspace.sqlite` | Main state, maintenance, administration, audit, claims and fences |
| `workspace.sqlite.qa/qsl.sqlite` | QSL rows, audit, revisions, stages and previews |
| `workspace.sqlite.qa/jobs.sqlite` | Legacy mock job settings, receipts and audit |
| `workspace.sqlite.qa/report_builder.sqlite` | Saved report definitions |
| `workspace.sqlite.operations/operations.sqlite` | Operations metadata and history |
| `workspace.sqlite.operations/job_monitor.sqlite` | Monitor settings, runs, notifications and publication |
| `workspace.sqlite.operations/job_monitor.sqlite.leases.sqlite` | Monitor's separate lease/fencing store |
| `workspace.sqlite.etl.sqlite` | ETL settings, requests, leases, snapshots and publication |

Any created `fixture-sqlite-a.sqlite`, `fixture-sqlite-b.sqlite` and
`fixture-sqlite-c.sqlite` under the operations directory are also captured.
Their legitimate absence is explicit in the manifest. The source main filename
can differ; the backup normalizes it to `workspace.sqlite` and preserves the
known sibling suffixes. Unknown sibling files/directories and missing required
stores are rejected rather than silently omitted. The `production` profile is
main-state-only, matching this repository's current non-demo factory; it refuses
existing demo siblings. Company-specific binds/stores require a reviewed layout
extension before this tool can claim to back them up.

The tool simultaneously holds SQLite `BEGIN IMMEDIATE` writer locks on all
members. Separate read connections use SQLite's backup API while those locks
remain held. It never copies live database/WAL files as ordinary filesystem
files. Lock acquisition has a 200 ms SQLite busy timeout per store; lock failures
release already acquired connections. Only rollback-journal modes are accepted.
WAL requires a separately reviewed quiesced/checkpoint procedure and is rejected
here; the tool does not switch the source's journal mode.

Every member receives exact current schema/constraint validation, integrity and
foreign-key checks, byte count and SHA256. The manifest also records schema
digests, current storage-source fingerprints, the profile and absent optional
stores. It contains no source filesystem paths, credentials or database rows.
The backup files themselves contain the complete source data and must remain in
the owner's protected local storage; they are not repository source artifacts.

`StateStore.backup_to()` remains available for its single database. It cannot
by itself capture the dependent multi-file set and is not used as a substitute
for this coordinated backup.

## Restore only into a new isolated directory

```sh
python -B tools/deployment_check.py restore --backup /absolute/backups/new-release-snapshot --destination /absolute/isolated/restore-drill --expected-release reviewed-release-id
```

The destination must not exist or overlap the backup directory. Restore validates
the complete member set, safe known relative names, checksums, schemas and exact
storage-source compatibility. It locks the backup databases against SQLite
writers and rechecks their hashes under those locks before copying with SQLite's
backup API. Restored schema and logical content are checked against the backup;
claims, receipt tokens, snapshots, audit, lease fences and enabled schedules are
preserved. Restore does not clear an uncertain run, reset a fence, send a message
or start a worker.

A complete restored set includes `RESTORE_REVIEW_REQUIRED.json`. Application
construction checks that marker **before** providers/state initialization and
refuses startup. The preflight and readiness helpers also reject it. Presence
alone is enough, including invalid marker content; only the immediate state-file
parent is checked. Partial backup/restore destinations retain `INCOMPLETE.json`
and no completion manifest. An incomplete restore is also blocked from startup.
Failed destinations are left for inspection and cannot be overwritten by retrying
the same command. Select a new path after understanding the failure.

No automated activation or marker-removal command exists. The owner must first
review unknown/in-flight runs and any external outcome, reconcile lease/fencing
consumers, review preserved enabled schedules, and validate a compatible release
against this isolated set. Only after that explicit reconciliation may the owner
remove the review marker from the selected restored directory. This task does
not authorize that action on a real deployment. The marker is an operational
guard, not an authorization boundary against someone who can edit local files.

## Safe release rollback

1. Before an upgrade, retain its known-good code/environment and a coherent
   stopped-state snapshot. Record source identity separately from the declared
   manifest label. Keep the old set unchanged.
2. If the candidate fails, drain/stop it. Preserve its current state for diagnosis;
   do not overwrite it with the old database or discard newer uncertain effects.
3. Restore the **pre-upgrade** snapshot into a new isolated directory, using a
   recovery tool whose storage-source fingerprints match that snapshot.
4. Verify the compatible code/data pair offline, reconcile claims/fences and any
   writes that occurred after the snapshot, then follow the owner's separately
   approved cutover procedure. No push, deployment or cutover is performed here.

The tool deliberately rejects a different storage-code fingerprint, a wrong
release label and an unsupported schema. There is no `--force`, schema downgrade
or in-place overwrite option. A reviewed compatibility/migration extension is
required even when a code difference may seem harmless. Restoring an older fence
does not make an external consumer forget newer work; exactly-once Oracle or
SMTP effects are not established by SQLite recovery.

The automated rollback drill restores a known before-image after the original
synthetic store is deliberately marked with a future schema version. It confirms
that the future-version source remains unchanged and is not downgraded in place.
It does not certify arbitrary older application releases or real company data.

See [deployment validation](VALIDATION.md) and the existing
[ETL recovery contract](../optimization/RECOVERY.md).
