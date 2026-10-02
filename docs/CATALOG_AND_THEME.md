# Report catalog, color modes and assistant panel

The company-facing discovery pattern is a card catalog, not a sidebar containing
every report. Header navigation is limited to authorized Reports, Maintenance
and Admin sections. The old `sidebar` component ID is retained only for a
collapsible right-side assistant panel; it is not a report-navigation list.

## Dozens of reports

Each report's PageSpec owns title, path, category, description and tags alongside
its policy. Catalog categories, counts, cards and links derive only from the
current identity's authorized subset. Search covers name/title, description and
tags; category and Favorites/Recent filters combine with 12-card pagination.
Favorite and recently opened shortcuts appear first, capped at four each.

Only the existing report and clearly labeled permission fixtures appear in the
sample catalog. Fifty-page synthetic registries exist only in tests to verify
paging/search and authorization boundaries. Adding dozens of fake production
reports would not prove business-system integration.

Favorites and recent entries store only bounded report IDs under an opaque
account/org/role-scoped browser key. They contain no report rows or names.
Malformed or forged IDs are intersected with the currently authorized registry,
never used to discover a hidden route. A local preference is not an access grant.
Shared-browser storage and localStorage restrictions should be considered during
company deployment; storage failure degrades to an in-memory preference.

## Light and dark

The native theme button is keyboard accessible and exposes its current state.
First visit follows the OS color scheme. An explicit light/dark choice persists
under the app-local `workspace.theme` browser key; no server account setting or
operating-system theme is changed. A memory Store synchronizes Mantine 0.12 and
the report's Plotly colors/template while preserving trace data and ranges.
Cards, forms, filters, tables, overlays and notifications use shared tokens.
Theme callbacks are named, local, client-only callbacks and cannot be dispatched
as server data operations. No remote fonts, icons or dependencies are introduced.

## Assistant panel

The right panel can open/close and supports Escape/focus return. At widths up
to 650px it is a modal drawer: keyboard focus stays inside and background
branches are temporarily inert/hidden from assistive technology. Resizing to
desktop releases those restrictions; the desktop panel is nonmodal. Closing or
replacing the shell restores prior attributes without moving focus to a removed
route. The local script performs no network requests. Its input and Send control are disabled, and it
states that no assistant is connected. No report content, prompt or company data
is sent anywhere; no model, DVC or other backend is automatically contacted.
Connecting the intended company assistant requires a separately specified,
approved adapter and data-sharing contract. This placeholder is not a completed
assistant integration.

## Validation boundary

Unit and callback-transport tests verify catalog filtering, local preference
logic, scoped authorization, theme transformations and declared client-only
callbacks. They do not prove browser rendering. A real screenshot of earlier
commit 1825644 showed a collapsed/blank chart area; its figure and correct graph
assets pass server checks, but the browser cause is unresolved. Final desktop/
mobile chart rendering, theme contrast, card interactions, drawer keyboard use,
notification dismissal and CRUD flows must be checked on the final commit.
