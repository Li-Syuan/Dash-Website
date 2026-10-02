# Report workspace — compatible demo v1

An offline first version of the Flask + Dash reporting workspace. It preserves the
known public example's routes, callback IDs and role/organization rules, with a
consistent Bootstrap component shell and local design tokens. All report data is
synthetic. No company identity provider, database, mail server or live scheduler
is connected. This is a compatibility skeleton, **not a completed company-system
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
production credentials or expose it as the company login system. The session key
is generated per process; restarting logs users out. Use one process for this
demo; independent workers have independent sessions, leases and simulation state.

The target is Python 3.8.13 with Dash 2.9.1, Flask 2.2.3,
dash-bootstrap-components 1.4.1, Flask-Login 0.6.2, Werkzeug 2.2.3 and Plotly
5.13.1. `requirements-demo.txt` documents that minimal subset of the provided
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
the Flask callback transport and service boundary. Pages routing is public so
the login page works; each protected layout checks access before creating data.
The original Forbidden view was a rendered message, so restricted page layouts
still render a Forbidden view. Protected API/callback requests return 401/403.

CSV exports the original synthetic fixture, not client edits, row deletion or
filters. Table edits are not persisted, matching the absence of a save callback
in the public example. The fixture's new columns are not a company data schema.

## Adapter simulations and limitations

`demo_services.py` contains replaceable report, lock, scheduler and mail adapters.
The admin laboratory performs only a manual simulation:

- Lease: process-local mutex and expiry clock; owner checks; no actual file lock.
- Scheduler: fixed job/run key executes once per process; no thread, cron or live
  job. Failed runs remain recorded; retry policy is not implemented.
- Mail: in-memory sink accepting only `example.invalid`; never opens SMTP.
- DVC: optional interface stub raising NotImplementedError; no integration claim.

Neither a process-local mutex nor APScheduler max_instances replaces a production
cross-worker lock. Before integrating company scheduling, identify the single
scheduler owner outside Gunicorn workers and preserve IDs, trigger/timezone,
misfire, coalesce, restart and retry behavior. Verify file locks on the real
filesystem and platform. LDAP, Oracle transactions/types, templates/recipients,
company schema, proxy URL prefixes and shared session configuration remain
unverified until the private core and representative fixtures are provided.

`app.py` now starts `demo_app.py` and `demo_server.py`. Original `pages/`,
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

Tests cover the synthetic authorization matrix, denied report services and HTTP
callbacks, login failure, invalid session user, offline report/CSV, lock owner and
expiry, once-only simulated job, mail sink restrictions, local assets and Python
3.8 syntax. They import only the demo entrypoint, never company services.

Browser smoke test: sign in as each identity; visit the listed routes; as admin,
refresh/filter the report and export CSV; run the adapter simulation twice and
confirm the second run does not capture another message; sign out and verify
that direct report callbacks cannot return data. Check desktop/mobile widths.

Runtime validation evidence and remaining environment limits are recorded in
`VALIDATION.md`. Python 3.8 Linux and company private-system compatibility must
still be validated on the target server; passing on a newer Windows interpreter
does not establish that compatibility.
