# Corrective action aging: independent synthetic report

A read-only quality-management example distinct from the inspection template:
case backlog, historical open/closed status, calendar age and late closure.
It reuses the existing login, catalog, app-scoped PageSpec/callback registry and
single `app.py`. No company schema, SLA, workflow or data adapter is implied.

## Launch and access

In an existing approved runtime, from the repository root:

```sh
REPORTING_ENABLE_QUALITY_ACTIONS=1 python -B app.py
```

On PowerShell use `$env:REPORTING_ENABLE_QUALITY_ACTIONS = '1'`, then
`python -B app.py`. Open `/login`, use one of the public synthetic accounts
(password `demo-only`), and select **Corrective action aging** in the Quality
catalog, or open `/QA_portal/quality-actions`. `demo-admin` and `demo-user-a`
share organization A; `demo-user-b` receives B only. A foreign administrator
never reads A through an ID, URL or browser-state claim. Guests/anonymous actors
and revoked identities cannot query or export. This is read-only for every role.

The flag defaults to false; strict booleans match the existing settings contract.
It is independent of `REPORTING_ENABLE_REPORT_TEMPLATE`; both may coexist.
`Settings(enable_quality_actions=True)` is the equivalent factory option.
Production rejects the flag, and explicit `extra_pages=(SPEC,)` still rejects
production during registration. Disabled apps have no report page or callbacks.
Imports/factories start no workers; the existing launcher lifecycle is unchanged.

## Schema and semantics

`SyntheticActionRepository` produces 36 deterministic cases per organization,
with repeating case IDs across tenants. No database or network state is involved.
The source fields are `org`, `case_id`, `opened_date`, `due_date`, `closed_date`,
`priority` and `finding`. Exact field sets are required; org is internal only.

- Case IDs are `action-` plus four ASCII digits and unique within one result.
- Dates are real ISO dates between 2000-01-01 and 2100-12-31. Due/closed dates
  cannot precede opened dates; an empty closed date means no closure recorded.
- Priority is Low, Medium or High. Finding is nonblank text at most 200
  characters; control and surrogate characters are rejected.
- `as_of` defaults to the fixed synthetic date 2026-10-05. Future-opened cases
  are omitted. A closure after `as_of` is hidden and the case is treated as Open.
- `age_days` is calendar days from opened date to effective closure or `as_of`.
  `overdue_days` is max(0, end date minus due date). Due today is not overdue.
  Closed cases retain their late-closure days; this is not a workday/SLA metric.
- Summary counts cover every filtered row, not just the visible page: open,
  closed and **open overdue**. Late-closed cases are not open overdue.

The query accepts only `search` (literal case-insensitive case ID/finding, max
80 chars), `status` (empty/Open/Closed), `priority` (empty/Low/Medium/High),
`as_of`, `order_by` (due_date/case_id/overdue_days/age_days), `direction`
(asc/desc), `limit` (actual integer 1–100) and `offset` (integer 0–10000).
Ties use ascending case ID even for descending order. Apply resets pagination.
An empty match is successful, has zero summaries and permits header-only export.

CSV uses current controls and fresh server rows, ignoring client table edits and
pagination. The fixed filename is `synthetic-corrective-actions.csv`; columns are
`case_id,opened_date,due_date,closed_date,priority,finding,status,age_days,overdue_days`.
Choose/record the as-of date when using a downloaded snapshot; the filename is
not a timestamp or proof of freshness. Formula-leading strings are apostrophe
escaped after leading whitespace; commas, quotes and Unicode are CSV-quoted.
Numbers remain numeric until serialization. All results are bounded to 1000
source rows and CSV to 1 MiB UTF-8. Oversized/malformed/duplicate/foreign rows
invalidate the whole result; no partial data is returned. Iterators are closed.

Identity is authoritative and rechecked before/after adapter work and before
publication. Runtime services retain providers only, not actors or rows between
requests. Query parameters cannot provide an actor, tenant, SQL, bind, projection,
provider path or filename. Provider errors use fixed safe messages.

## Exact integration points

- `quality_actions/contract.py`: row/query/date/access contract
- `quality_actions/repository.py`: explicit isolated synthetic source
- `quality_actions/service.py`: fresh authorization, bounds, filtering, KPI/CSV
- `ui_pages/quality_actions.py`: trusted PageSpec and unique component/callback IDs
- `config.py`: default-false field, strict environment parsing, production guard
- `application.py`: explicit opt-in SPEC, never automatic page discovery

No additional framework, product server, dependency or persistent state is needed.
Replacing the source with a company adapter is a separate, authorized integration
requiring actual approved binds/schema/policies and representative safe fixtures.
Do not remove the demo guard to imply that integration is complete.

## Reproduce acceptance

Use the approved Python executable rather than assuming a system interpreter:

```sh
python -B -m unittest discover -s tests -p 'test_quality_actions.py' -v
python -B -m unittest discover -s tests -v
python -B tests/test_entrypoint_coverage.py --report-template --quality-actions --write-coverage output/quality-actions-entrypoints.json
git diff --check
```

For real browser acceptance, see `tests/browser/README.md`; enable
`QA_QUALITY_ACTIONS=1`. Its fixture imports the same `app.py`, isolates state and
uses public synthetic identities. The new report cases are separate from the
original inspection template. Run both flags to check coexistence.

See [initial handoff review](HANDOFF_REVIEW.md) and [validation](VALIDATION.md).
Linux cloud results never certify MSI/Windows, Python 3.8.13/3.10.4, kernel
3.10.0-1160, or real Oracle/LDAP/SMTP/Flask-RESTX/SQLALCHEMY_BINDS behavior.
