# Add a page through one explicit declaration

`reporting_workspace/registry.py` owns app-scoped PageSpec and callback metadata.
The UI modules live in `reporting_workspace/ui_pages/`. The unused legacy top-level
`pages/` directory has been removed; page discovery remains explicit. This is not Dash's
global `register_page` API and not a dynamic plugin sandbox.

## Importable page fixture

`examples/registry_fixture.py` is an importable PageSpec fixture, not a separate
website launcher. To exercise it in a local test of the main factory, import its
`SPEC` and pass `extra_pages=(SPEC,)` to `create_app`; the existing registry tests
use isolated app instances. To adopt a real page, register its SPEC in the main
app as described below and continue to launch only `python -B app.py`.

When this fixture is explicitly registered, demo-admin and demo-user-a can open
`/examples/quality`; demo-user-b is denied because the policy requires
organization A. Normal `python -B app.py` does not add this opt-in fixture. No
report, SQL, command or provider is selected by URL parameters.

The complete working example is `examples/registry_fixture.py`. Its one SPEC
contains:

- `page_id`: unique stable identifier
- `path`: unique canonical URL
- `title`: title consumed by that page's heading and route metadata
- `layout(runtime)`: builds components only after page authorization
- `policy`: explicit AccessPolicy, never omitted or inferred
- `nav_label` and `nav_order`: the small workspace header is generated from these plus the policy
- `catalog_category`, `catalog_description`, `catalog_tags`: optional report-card metadata, filtered by the same policy
- `register_callbacks(callbacks, runtime)`: optional explicit callback hook

For a normal built-in page, put its module under `ui_pages/`, import its SPEC in
`ui_pages/__init__.py`, and add it to `default_pages()`. For an explicitly
constructed app, pass `extra_pages=(SPEC,)` to `create_app`. Synthetic report copies must remain default-off. Do not add them to
`default_pages()`; use a distinct strict boolean in `config.Settings` and
`Settings.from_env()`, reject it in production, and append its trusted SPEC in
`application.create_app()` only when enabled. See the complete second-report
example in [quality-actions/README.md](quality-actions/README.md). Test default-off,
enabled, production rejection and coexistence with existing opt-in pages.
There is no separate
sidebar list or ADMIN_CALLBACKS collection to edit. New metadata is frozen before
requests are served. Duplicate page IDs/paths and infrastructure-path conflicts
fail application construction.

## Policy semantics

`AccessPolicy.public()` is intentionally public.
`AccessPolicy.require()` requires authentication.
`AccessPolicy.require(roles=('admin', 'user'), org='A')` means authentication AND
one of those roles AND organization A. This is not an implicit super-admin
bypass. The removed legacy helper used within-call OR and stacked AND semantics;
when porting company code that still uses those decorators, preserve the known
route result explicitly rather than treating the policies as interchangeable.

Navigation and catalog visibility follow exactly the page policy. Header links
are workspace sections; individual reports live in the catalog. Admin-A can see
the Page 2 permission-fixture card; user-B cannot see its metadata.
Neither hidden navigation nor layout authorization protects a data service by
itself. Service methods must also enforce access and any row-level scope.

## Register callbacks explicitly

Page callback hooks receive a page-bound registrar, so omitting page_id cannot
escape the page policy. Use its `callbacks.callback(...)`, with a unique
`callback_id`, explicit `policy` and `page_id` for page-owned callbacks. Reuse the
page's policy constant. A page-bound callback may be stricter than the page,
never weaker. Named client-only presentation callbacks must explicitly declare public policy;
that is never server authorization, and direct HTTP dispatch is denied.
The registry captures the actual output key produced by the pinned
Dash version, including grouped/dictionary outputs. Do not hand-build permission
strings or use global `dash.callback`.

Duplicate output targets, duplicate callback IDs and undeclared direct
`app.callback` registrations fail registry freeze. Requests to unknown or replaced
callbacks fail closed even for an admin. Page and callback registries belong to
one app; trusted Python code is still trusted code, not an isolated extension
sandbox. Do not mutate registrations after startup.

If a callback publishes an in-app notification, give it its own event Store via
`notification_store_id(callback_id)` and declare that Store as an additional
output. The shell's sole notification renderer handles the event. Existing
component IDs/properties stay stable, but grouped Dash transport keys may change;
tests must resolve the actual callback-map output schema rather than assuming a
single-output key.

## Required tests and review

Test anonymous, allowed, wrong-role and wrong-organization requests through the
actual router and callback transport. Test service authorization independently.
Test duplicate/missing policy definitions, cross-app isolation and any row-level
rules. Validate dynamic component IDs and layout behavior in a real browser;
metadata validation cannot inspect every possible dynamically generated DOM ID.
Use a fresh local fixture, never a company service. Add the page's behavior and
remaining integration limits to the documentation and run the complete suite.
