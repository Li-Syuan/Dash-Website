# Report workspace — modular reporting foundation

A modular Flask + Dash reporting foundation with an offline demo entrypoint. It preserves the
known public example's routes, callback IDs and role/organization rules, with a
consistent Bootstrap component shell and local design tokens. Default report data is
synthetic. No company identity provider, database, mail server or live scheduler
is connected. This is a tested integration foundation, **not a completed company-system
migration or a production deployment**.

## Run in the existing company environment

```sh
python -B app.py
```

Open http://127.0.0.1:8050/login. The default binds only to loopback and disables
debug mode. There are no CDN stylesheets, remote fonts or network CSV downloads.
The local CSS supplies the Bootstrap classes needed by this first version.

Public test identities (all use password `demo-only`):

| Username | Role | Organization |
| --- | --- | --- |
| demo-admin | admin | A |
| demo-user-a | user | A |
| demo-user-b | user | B |

These are mock identities, not production accounts. Do not connect this demo to
production credentials or expose it as the company login system. By default the session key is generated per process; restarting logs users out.
Optional local SQLite state shares leases/job claims/audit across processes on
one host. Stable sessions require the same explicitly provisioned key. Production
configuration refuses demo providers and missing/weak public-default secrets.

The target is Python 3.8.13 with Dash 2.9.1, Flask 2.2.3,
dash-bootstrap-components 1.4.1, Flask-Login 0.6.2, Werkzeug 2.2.3, Plotly
5.13.1 and setuptools 57.5.0 (required by Dash’s pkg_resources import). `requirements-demo.txt` documents that minimal subset of the provided
company versions. It is not a full transitive lock or an installation guarantee.
The original `requirements.txt` is historical and has newer versions; do not use
it to upgrade the company environment. No Mantine upgrade is needed. Existing
Mantine 0.12.0 components can later be integrated through adapters if required.

## Preserved known contracts

| URL | Policy |
| --- | --- |
| `/` | Authenticated |
| `/login` | Public; authenticated users redirect to `/` |
| `/logout` | Clears login and redirects to `/login` |
| `/admin`, `/page1`, `/page3` | Admin |
| `/page2` | Role admin/user **AND** organization A |

The legacy `auth.py` is retained unchanged: conditions within one
`role_permission` decorator are OR; stacked decorators are AND. New page
policies explicitly implement each route's known result. Admin navigation keeps
the admin pages; user navigation keeps page2. Navigation visibility is not the
authorization boundary. Unknown users in an old session become anonymous safely.

Login IDs and properties remain `username-box.value`, `password-box.value`,
`login-box.n_clicks` → `redirectHome.pathname`, `login-alert.is_open`, triggered
only by the login button. Sidebar IDs/properties remain `btn_sidebar`, `sidebar`,
`page-content`, `side_click` with SHOW/HIDDEN values; popover IDs remain `popover`
and `popover-target`. The report table keeps `table`, native filtering/sorting,
editing, deletion, multi-selection and page size 10. Redirect IDs are retained.
The three original demo accounts are replaced by explicit mock identities.

Report refresh, download and simulation callbacks enforce authorization at both
the Flask callback transport and service boundary. Fixed app-scoped routing is
public so the login page works; each protected layout checks access before creating data.
The original Forbidden view was a rendered message, so restricted page layouts
still render a Forbidden view. Protected API/callback requests return 401/403.

CSV exports the original synthetic fixture, not client edits, row deletion or
filters. Table edits are not persisted, matching the absence of a save callback
in the public example. The fixture's new columns are not a company data schema.

## Adapter simulations and limitations

`demo_services.py` retains offline fixtures and memory simulations.
`reporting_workspace/providers.py` defines explicit identity/report contracts;
`reporting_workspace/state.py` implements optional durable local leases, job claims and audit.
The adapter marker is not a production certification.
The admin laboratory performs only a manual simulation:

- Default lease: process-local mutex; optional SQLite lease adds atomic
  cross-process claims, owner/token expiry checks and fencing on one local host.
  This is not a production filesystem/distributed lock replacement.
- Job claims: memory default or durable SQLite job/run states. No thread, cron
  or live scheduler is started; failures/uncertain runs are never automatically
  retried. Single claim does not guarantee exactly-once external side effects.
- Mail: in-memory sink accepting only `example.invalid`; never opens SMTP.
- DVC: optional interface stub raising NotImplementedError; no integration claim.

Neither a process-local mutex nor APScheduler max_instances replaces a production
cross-worker lock. Before integrating company scheduling, identify the single
scheduler owner outside Gunicorn workers and preserve IDs, trigger/timezone,
misfire, coalesce, restart and retry behavior. Verify file locks on the real
filesystem and platform. LDAP, Oracle transactions/types, templates/recipients,
company schema, proxy URL prefixes and shared session configuration remain
unverified until the private core and representative fixtures are provided.

`app.py` starts compatibility wrappers backed by
`reporting_workspace.application.create_app`. The new package separates validated
configuration, identity/report providers, app-scoped Dash UI and transactional
SQLite state. No scheduler or network client starts from package import. Original `pages/`,
`app_server.py`, `app_config.py`, `auth.py`, `my_crud_app.py` and `test_admin.py`
remain historical reference files; the new entrypoint does not import or execute
them. In particular it does not execute legacy CSV retrieval or database setup.
Do not run `test_admin.py` as a test suite: it is a separate DB-writing example.
For source rollback, use Git history or a separate checkout of the previous
commit; there is no automatic production integration feature flag yet.

## Verify

```sh
python -B -m unittest discover -s tests -v
```

The GitHub workflow runs the same suite on Linux Python 3.8 and 3.10 using the
minimal pinned runtime. This is not exact company patch/Windows/full-stack parity.

Tests cover the synthetic authorization matrix, actual Dash routing callbacks
(including query parameters and logout), denied report services and HTTP
callbacks, malformed/Unicode login, invalid session user, anonymous UI controls,
legacy OR/AND decorators, offline report/CSV, multi-process fenced leases,
persistent job claims/uncertain completion, schema validation, backup integrity,
factory/session isolation, fail-closed configuration, safe error boundaries,
mail sink restrictions, local assets and Python 3.8 syntax. Tests use isolated
synthetic providers and temporary state; they never contact company services.

Browser smoke test: sign in as each identity; visit the listed routes; as admin,
refresh/filter the report and export CSV; run the adapter simulation twice and
confirm the second run does not capture another message; sign out and verify
that direct report callbacks cannot return data. Check desktop/mobile widths.

## Architecture and operations

- [Architecture and adapter contracts](docs/ARCHITECTURE.md)
- [Configuration, health, backup and rollback](docs/OPERATIONS.md)
- [Company integration acceptance checklist](docs/INTEGRATION_CHECKLIST.md)

Production mode requires explicit non-demo identity/report providers, a stable
external session key, local state path and secure cookies. It is a fail-closed
configuration path, not proof that the company integration is production ready.

Runtime validation evidence and remaining environment limits are recorded in
`VALIDATION.md`. The automated suite also runs on Linux x86_64 Python 3.8.20 and 3.10.21.
Exact Python 3.8.13, Windows 3.10.4, browser appearance/interaction and company
private-system compatibility remain unverified. The cloud browser blocks the
loopback app and exposes no supported preview route, so no browser pass or public
deployment is claimed. See VALIDATION.md for evidence and remaining checks.
