# Static acceptance-report browser verification

`acceptance_report.cjs` opens one existing, reviewed acceptance HTML report in
installed Chrome using Playwright and `file://`. It starts no application server,
installs nothing, executes no report commands, and changes no product or report
file. Coordinate the final report path and source freeze before running it.

```powershell
$env:QA_PLAYWRIGHT_MODULE = '<existing approved Playwright package directory>'
node tests/browser/acceptance_report.cjs output/<report-directory>/report.html
```

The module variable is optional when `playwright` already resolves normally.
`QA_BROWSER_CHANNEL` defaults to `chrome`; only use an existing approved channel.
The only positional argument is a regular `.html`/`.htm` file beneath this
checkout's `output/`. The tool checks every existing ancestor, canonical path,
and observed symbolic links. On Windows, a read-only PowerShell attribute query
also rejects observed reparse points; paths are passed as data, never shell code.
If that inspection cannot complete, the run fails. These checks do not claim to
defeat concurrent adversarial filesystem replacement.

Each invocation creates a unique `output/playwright/acceptance-report-*` folder.
It contains desktop (1440 px) and mobile (390 px) full-page screenshots, visible
DOM text, expanded-disclosure screenshots, `results.json`, and artifact sizes
and SHA256 hashes. Results bind the input report bytes and harness hash, record
actual Node/Playwright/Chrome versions, and confirm owned browser shutdown.
Invalid paths are rejected before output creation or browser launch. Failed
runs remain available and are never automatically retried.

The reviewed HTML contract is:

- `lang="zh-Hant"`, one visible H1, document title and responsive viewport meta.
- Visible section headings/content at `overview`, `findings`, `performance`,
  `reproduce`, `sources`, and `limits`; failure and unverified scope stay visible.
- `status-legend` visibly explains PASS, FAIL, SKIP, INVALID and INCOMPLETE.
- Reproduction commands appear as inert `pre`/`code` text. No scripts, inline
  event handlers, executable links, forms, buttons, input controls, embedded
  frames/objects, refresh redirects or editable report content are permitted.
- A CSP meta defaults to `default-src 'none'`, blocks scripts/connections/objects/
  frames (explicitly or through that default), and sets `base-uri 'none'` and
  `form-action 'none'`. Inline report styling is permitted by its explicit policy.
- Native `details`/`summary` disclosures are reachable with Tab and open/close
  using Enter/Space. Their original open state is restored after testing.
- Neither default nor expanded content creates horizontal document overflow.
  Inner table/code scrolling is allowed when it does not widen the document.

The browser context disables page scripts and blocks every resource request
except the exact local report file. The DOM/CSP assertions independently reject
executable content; disabling scripts is not used to classify such content as
passing. Any attempted external/extra resource load, failed request, console
error or page error fails the check. The harness never clicks source links or
executes displayed reproduction commands. It closes only its owned browser.

Exit 0 means these **static report rendering checks** passed. It does not mean
the application, report evidence, performance gate, target company runtime or
whole project passed, and does not refresh historical evidence. Exit 1 records
a failed check or unavailable prerequisite. Read the report's own PASS/FAIL/
SKIP/INVALID/INCOMPLETE distinctions separately.
