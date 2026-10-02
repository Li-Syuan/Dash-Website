# Run and operate the foundation

## Safe demo

Use the existing approved package environment. Do not install the historical
`requirements.txt` over it. `requirements-demo.txt` records the minimal approved
runtime subset; it is not the complete company environment or a transitive lock.

```sh
python -B app.py
python -B -m unittest discover -s tests -v
```

Open `http://127.0.0.1:8050/login` on the same machine. Public test identities are
`demo-admin`, `demo-user-a`, `demo-user-b`; their password is `demo-only`.
These are unsuitable for public exposure. No company service is contacted.

The direct demo launcher creates a private Git-ignored `instance/` directory
and uses `instance/workspace.sqlite` if no state path was supplied. Sessions still
use fresh per-process keys unless a stable key is explicitly configured. WSGI
factory callers do not invoke launcher defaults. To select durable state
explicitly, set `REPORTING_STATE_PATH` to a new absolute SQLite filename inside
an existing private local directory; StateStore itself does not create directories. Do not place state in Git, a network
share, synced folder, NFS mount or multi-host deployment. New POSIX files are
created mode 0600; verify equivalent Windows directory/file ACLs yourself.

## Configuration

| Variable | Default in demo | Production requirement |
| --- | --- | --- |
| `REPORTING_MODE` | `demo` | Explicit `production` |
| `REPORTING_SECRET_KEY` | Random per process | Stable externally supplied, non-public key of at least 32 characters |
| `REPORTING_STATE_PATH` | Direct launcher: private instance file; factory: unset | Absolute trusted local SQLite filename |
| `REPORTING_SESSION_COOKIE_SECURE` | `false` | Must be `true`; HTTPS required |
| `REPORTING_MAX_CONTENT_LENGTH` | 1048576 | Positive byte limit appropriate for reviewed report volume |
| `REPORTING_SESSION_LIFETIME_SECONDS` | 3600 | Positive reviewed lifetime |

Settings do not print the secret in repr. Keep keys out of source, command-line
history, logs and shared files. Length checks cannot establish entropy. Generate
and provision secrets through the company's approved process. All workers must
share the intended stable key; changing it revokes existing sessions. Proxy
forwarding headers are not automatically trusted. Configure TLS/host/proxy
boundaries through the deployment owner's reviewed infrastructure.

`create_app(settings, identity_provider=..., report_provider=...)` is the WSGI
factory. Use the already approved WSGI server's factory support; this project
does not add or install a server package. Importing `wsgi` alone starts no app,
thread or job. Configure actual provider instances in a reviewed deployment
entrypoint. Production construction refuses missing/demo providers, missing or
public/short secrets, non-local state paths and insecure cookies. There is no
fallback to demo identity when production configuration fails.

Do not use Flask's development server as the production server. No hosting,
TLS gateway, system service, Docker, Redis, Celery or cloud account is provisioned.

## Health, errors and logs

- `/healthz`: process-level health and mode; does not prove external services
- `/readyz`: local state availability and `scheduler: not-started`; does not
  prove identity/Oracle/SMTP readiness
- Responses carry `X-Request-ID`, no-sniff and same-origin frame/referrer headers
- Non-asset responses use `Cache-Control: no-store`
- Application HTTP logs contain fixed endpoint name, method, status, elapsed time and request
  ID; errors contain a type and request ID, not provider messages or payloads
- The hosting operator must configure INFO log collection, rotation, retention,
  alarms and disk monitoring; default Flask log levels may hide INFO messages

There is deliberately no blanket CSP claiming compatibility with untested Dash
assets. Security header and browser policy review remains a deployment gate.

## Job inspection and recovery

Read `StateStore(path).list_uncertain_jobs()` to inspect running claims and
`get_job(job_id, run_key)` for known state. Never delete a claim or choose a new
run key merely to force a retry: first reconcile whether its external effect
occurred. There is no automatic requeue, retry, takeover or arbitrary-job API.
Keep any real scheduler in one separately supervised owner process. Web worker
startup/reload must never schedule jobs.

## Consistent backup

Use the SQLite backup API, not a blind copy of an open database:

```python
from reporting_workspace.state import StateStore
store = StateStore(absolute_live_path)
store.backup_to(absolute_new_backup_path)
```

Both parent directories must exist and be trusted/local. The destination must
be new; existing files are never overwritten. The method validates schema and
uses a consistent SQLite snapshot. Check retention/access and perform a restore
drill separately. Backups contain audit/state metadata and must remain private.

## Restore and rollback

1. Stop every web worker and scheduler owner that can touch the state file
2. Preserve the current database using the backup API where possible; record the
   running Git commit and schema version, without writing secrets
3. Validate a backup in a separate local location using a compatible
   `StateStore` and SQLite integrity check; do not point live workers at it yet
4. Reconcile running jobs and all possible external effects. Restoring old state
   can revive job keys and roll fencing numbers back. Reconcile downstream
   fencing consumers before any worker resumes
5. Replace/switch the state path only through an operator-approved recovery
   procedure while all users are stopped; preserve the prior file for recovery
6. Resume one owner, verify health/readiness, authentication and permissions,
   then resume workers and monitor errors. Do not automatically retry uncertain
   jobs

Code rollback uses an earlier normal Git commit/checkout. Never force-push or
assume old code can read a newer schema. The current schema is version 2, with an atomic, validated upgrade from
version 1. Back up before upgrading. Older code cannot read version 2, and there
is no automatic downgrade; newer unknown versions fail closed. A code rollback and database restoration are
separate decisions. No automated destructive restore command is supplied.

### Request framing on the pinned Flask/Werkzeug version

Callback POSTs require a declared Content-Length, rejected before JSON parsing
when it exceeds the configured byte limit. Missing-length callback bodies are
rejected. Configure the trusted HTTP gateway/WSGI server to bound and normalize
request framing; raw chunked request handling is not assumed. Review upstream
access logs separately: they may include URLs even though application logs omit
query values.
