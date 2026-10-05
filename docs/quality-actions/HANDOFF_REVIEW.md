# Independent agent handoff review

## Starting observation, before implementation (2026-10-05)

The exercise starts from a fresh GitHub checkout of
`45918b243af32cf8d4db966e82bfc6b634bf3771`. Only repository instructions,
documents and relevant code informed the implementation. Earlier task chats,
private memories and other checkouts were not used as implementation sources.
This round permits local code/tests/commit only; no publication or deployment.

The existing report-template contract is unusually explicit about identity,
row validation, tenant isolation, CSV safety and page registration. It is enough
to implement another report without inventing a company adapter. The gaps below
were recorded before changing runtime code:

1. `docs/ADDING_PAGES.md` explains default/extra page registration, while
   `docs/report-template/README.md` asks for a reviewed opt-in registration but
   does not enumerate `config.Settings`, `Settings.from_env`, validation and
   `application.create_app` as the exact edit points. These must be discovered
   in source. A second report needs its own independent, demo-only switch.
2. The browser guide only describes the existing template's eight hard-coded
   cases. Copying the report tests cannot automatically add browser coverage.
   A new report must have named cases, private fixture opt-in, runtime/source
   evidence and an explicit browser command; the original cases remain intact.
3. `AGENT_HANDOFF.md`, `PROJECT_CONTEXT.md`, `README.md` and `AGENTS.md` preserve
   multiple historical paragraphs called “current” or “local/unpushed”. The
   newer release banner limits them, but an agent must reconcile chronology.
   A concise current-start pointer and an explicit per-task authority reminder
   are safer than interpreting any historical publication approval as new scope.
4. No business schema was specified for this new report. We must choose an
   explicitly synthetic example and document its calculations/access matrix,
   rather than imply the template's inspection quantities are company policy.
5. Existing docs list runtime pins but not an approved environment discovery
   procedure. This cloud has Python 3.12.14, Node and Chromium/Playwright, but no
   project Dash/Flask dependencies or Python 3.8/3.10. Execution is initially
   blocked pending permission for isolated installation of existing pins and
   runtimes; syntax checking alone cannot certify runtime compatibility.

## Bounded choices for this exercise

- A distinct **Corrective action aging** report, with case dates, priority,
  finding text, open/closed state, calendar age and days overdue. It is read-only;
  there is no CAPA workflow, company SLA or write permission invention.
- A user-selected, validated `as_of` date (fixed synthetic default 2026-10-05)
  makes historical examples deterministic. Only cases opened on/before that date
  appear; closure after that date is treated as still open. A due date itself is
  not overdue. Date calculations are calendar-day differences, not workdays.
- Admin/user identities share their own organization's rows; foreign admins see
  only their own organization. Unknown roles, forged claims and revoked sessions
  are denied. Actual company access rules remain unverified.
- Keep the single `app.py`, explicit PageSpec/callback registration, request-fresh
  identities, `/QA_portal/quality-actions`, no workers/import effects, bounded
  server-owned queries/exports and production rejection.
- Implement only the small configuration/factory wiring needed for this opt-in
  report. Do not refactor existing reports into a new framework merely to reduce
  duplication during a handoff exercise.

Validation results and the final changes are recorded separately in
[VALIDATION.md](VALIDATION.md), so these initial observations are not overwritten
with hindsight. MSI/Windows, exact company runtime patches and Oracle/LDAP/SMTP/
Flask-RESTX/SQLALCHEMY_BINDS integration remain unverified.

## Independent repository-only cross-check

A separate read-only review confirmed the first two gaps at the original source
locations: `docs/report-template/README.md:101-105`, `docs/ADDING_PAGES.md:33-35`,
`tests/browser/fixture_server.py:24-35` and `tests/browser/acceptance.cjs:144-179`.
It also identified that the original steps 5-6 appeared to require replacing the
synthetic source with a company adapter. The recipe now explicitly skips those
optional steps for synthetic-only work, retaining the demo guard. This is a
handoff/documentation correction, not a claim of an original authorization flaw.
