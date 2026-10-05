# Copyable, executable report template

The opt-in quality inspection report is a working read-only example through the
existing `app.py`, factory, login, PageSpec and callback registry. It is not a
second launcher, a self-service SQL tool, or a private company adapter.

From the repository root in the approved environment:

```powershell
$env:REPORTING_ENABLE_REPORT_TEMPLATE = '1'
python -B app.py
```

On a POSIX shell:

```sh
REPORTING_ENABLE_REPORT_TEMPLATE=1 python -B app.py
```

Open the existing `/login`, sign in with a synthetic demo account, then open
`/QA_portal/report-template` or its Quality catalog card. `demo-admin` and
`demo-user-a` see the same organization-A rows. `demo-user-b` sees organization-B
rows. Foreign administrators have no override. This is intentionally a shared
read-only report: there are no owner-specific writes or CRUD controls.

Unset the flag or set it to `0` to leave the normal default routes and catalog
unchanged. The controlled factory option is `Settings(enable_report_template=True)`.
The factory rejects enabling this synthetic example in production mode. Tests
can explicitly use `create_app(..., extra_pages=(SPEC,))`; this uses the existing
registry and does not start a web server. Explicitly adding this synthetic SPEC
in production is also rejected during callback registration.

## Files to read and copy

| File | Responsibility |
| --- | --- |
| `reporting_workspace/report_templates/contract.py` | One shared access policy, immutable bounded Query, strict row/date/quantity schema, safe CSV text |
| `reporting_workspace/report_templates/repository.py` | Explicit synthetic tenant-scoped data source; no database/network state |
| `reporting_workspace/report_templates/service.py` | Fresh authoritative identity before/after data access and before publication, limits, filtering/order and export |
| `reporting_workspace/ui_pages/report_template.py` | Protected PageSpec, read-only table, controls, paging, shared notifications and download |
| `tests/test_report_template.py` | Service, adapter-failure and actual Flask/Dash callback regressions |

Services retain their provider and repository configuration only. They do not
cache actors or rows across requests. The UI uses `runtime.identity()`; browser
parameters never supply an actor, role, organization, SQL, connection, provider,
module name, projection or export filename. The fresh provider lookup must match
the exact supplied actor ID and claims. Revocation or a changed role/organization
during a lazy fetch blocks the whole result.

## Report contract

The sample source generates 32 inspections for each of organizations A and B.
Sample IDs deliberately repeat between tenants. Rows are shared by authorized
members of that organization; the ID is not an authorization token.

| Field | Contract |
| --- | --- |
| Internal `org` | Must equal the current authoritative tenant; removed from public rows and CSV |
| `sample_id` | `sample-` plus four ASCII digits, unique within one result |
| `inspection_date` | Real calendar date in exact `YYYY-MM-DD` form, 2000-01-01 through 2100-12-31 |
| `department` | Assembly or Laboratory |
| `product` | Nonempty text, at most 120 characters, no control/surrogate characters |
| `inspected`, `rejected` | Actual integers, excluding booleans, between 0 and 1,000,000,000; rejected cannot exceed inspected |

The query accepts only `search` (up to 80 characters), `department`, `order_by`
(`sample_id`, `inspection_date`, `rejected`), `direction` (`asc`, `desc`), `limit`
(integer 1–100) and `offset` (integer 0–10000). Search is literal, case-insensitive
text over sample ID, product and date. Sort ties use ascending sample ID. The
table defaults to 20 rows; applying filters resets its offset. Previous/Next
use server-validated bounds. An empty filter match is a normal empty result.

The source must yield at most 1000 rows per tenant. The service validates the
whole bounded result before returning a page; it rejects an overflowing stream,
duplicate IDs, invalid types or any foreign row instead of silently truncating
or publishing partial data. Lazy iterators are closed. A real adapter must also
apply a parameterized tenant predicate and a bound at the database; this small
in-memory example is not a scalable unrestricted scan pattern.

CSV uses the current controls and re-queries server data. It includes all
matching rows, not just the visible page or client-edited table contents, and
is bounded to 1000 rows and 1 MiB UTF-8. The fixed filename is
`synthetic-quality-inspections.csv`; ordered columns match the six public fields
above. Dates remain ISO text; quantities remain numbers before serialization.
Strings beginning with `=`, `+`, `-` or `@` after leading whitespace receive an
apostrophe so spreadsheets treat them as text. This escaping changes CSV text
only, not UI values. CSV quoting preserves commas, quotes and Unicode.

## Copy and adapt one report

1. Copy `reporting_workspace/report_templates/` to a sibling package such as
   `reporting_workspace/incoming_quality/`, preserving `__init__.py`. Relative
   imports remain at the same depth. Copy the UI module to
   `reporting_workspace/ui_pages/incoming_quality.py` and the tests to a distinct
   `tests/test_incoming_quality.py`.
2. In the copied UI, import `..incoming_quality`; replace every callback ID
   starting `report_template.` with `incoming_quality.`, set
   `PAGE_ID = 'incoming_quality'`, set `PREFIX = 'incoming-quality'`, and change
   the PageSpec path to `/QA_portal/incoming-quality`. Update title, catalog
   description/tags and fixed CSV filename. Keep IDs unique, policies explicit,
   and registration app-local. Update test IDs/imports to match.
3. First run the copied synthetic tests unchanged in meaning. In a controlled
   factory test, explicitly pass the copied `SPEC` through `extra_pages`.
   The main application's reviewed opt-in registration must import this exact
   SPEC from trusted Python code; it must not load a browser/environment-supplied
   import string. Add a distinct default-false boolean to `Settings`, parse its
   dedicated environment flag in `Settings.from_env`, reject production mode,
   and append the SPEC in `application.create_app` only when enabled. Do not add
   a demo-only copy to `default_pages()`. Continue launching only `python -B app.py`.
   Test default-off, enabled, production-denied and original-plus-copy coexistence.
   [Corrective action aging](../quality-actions/README.md) is a complete second
   synthetic report showing these exact wiring and acceptance changes.
4. Agree on the new report's columns, units, allowed filters/order, output limits
   and actual business access matrix. Update the copied contract, fixture,
   service projection, UI and tests together. Do not assume this sample schema
   or the general admin role expresses private company rules.
   For a synthetic-only task, document the demonstration access matrix, keep
   synthetic data and the production guard, then proceed directly to step 7.
   Steps 5–6 are an optional, separately authorized company integration.
5. Replace only the copied repository with an explicit reviewed adapter using
   the actual existing bind and parameterized tenant predicate. Supply the
   private schema/bind names and representative sanitized fixtures first. Keep
   current-identity checks around adapter work, strict row validation, bounds,
   safe failure messages, export controls and no automatic side effects.
6. The shipped UI intentionally refuses production. Remove/replace that guard
   only as part of separately verified company-adapter integration, with the
   correct provider declarations and acceptance evidence. Merely changing
   `is_demo` or enabling a flag does not implement or certify Oracle, RESTX,
   LDAP or SQLALCHEMY_BINDS compatibility.
7. Run the targeted suite, complete regression and real-browser checklist in
   [ACCEPTANCE.md](ACCEPTANCE.md). Record actual runtime/source/results and obtain
   authorization for that report's publication. The v11 local-work approval is
   not a main push or deployment approval.

No Flask-RESTX Api/Namespace, Oracle query, SQLAlchemy bind, company grant or
real export destination is invented by this template. Those details remain
the integration boundaries documented in `docs/PROJECT_CONTEXT.md`.
