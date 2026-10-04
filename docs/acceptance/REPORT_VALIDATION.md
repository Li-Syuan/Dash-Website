> Historical round record: publication of the cumulative source is now
> explicitly authorized. See [the current publication record](PUBLICATION.md).
> Raw execution evidence and checkpoint inventories referenced below are
> retained in the local delivery archives, outside this public source tree.

# Local one-page report validation

Native Python 3.10.22 full regression: **1,118 PASS, 4 SKIP, 0 FAIL**
(1,122 total), 122.178 seconds in unittest (122.203 seconds for the worker). The unit acceptance profile and
independent `verify` both return **INCOMPLETE (3)**; evidence is valid and
whole-project verification is false. Clock and owned-process cleanup are checked
in the canonical run report. The 27 report boundary tests are included in the
1,122 and must not be added again to that total.

Actual current run: `20261004T181154Z-e2b59cb99472`. The new HTML was generated
from that run, plus explicitly historical native-v12 and WSL-v12 run reports.
The renderer returned `generated:true`, `commands_executed:false`, INCOMPLETE/3.
Report HTML SHA256:
`8028895ce3390590174c8c0703ee02ad92ef107de8fa7b18fc5ef0d687dfbc4b`.

## Boundary verification and preserved initial results

The focused suite initially failed to import the new renderer as a package;
`acceptance_binding` used only a bare sibling import. Adding a package-relative
import with the existing script fallback fixed that demonstrated issue.
The second invocation had 24 passes and one formatting assertion failure:
the API correctly returned FAILED while the visible badge intentionally used
FAIL. The assertion was aligned with the documented display label without
changing the API gate. The third invocation passed all 25 focused tests.
Two additional cases cover clock-invalid success counts and a nested ETL overlap
exception with a private payload canary. Both pass in the final full regression. All initial logs remain under local report-round-evidence.

The tests cover escaped HTML/script payloads, restrictive CSP, common secret
forms, omitted raw exception messages, missing/invalid/seal/artifact evidence,
output containment and no overwrite, fixed inert commands, current/history
separation, source/time details and raw-hash plus semantic performance checks.
An intentionally stale primary report was used for the initial HTML preview:
it returned INVALID/86 and did not present old counts as current success.

The historical WSL overlap failure remains visible as INVALID and is not rerun.
The exact reviewed overlap exception maps to a safe explanation of the active
lease; arbitrary log text is not embedded. Existing before/after XLSX and latency
numbers are displayed only as historical descriptive measurements. The 15%/10%
policy remains unapproved; no performance criterion was activated and no new
benchmark was run.

## Rendering and scope

Actual Chrome report-rendering results and screenshots are recorded separately
under `output/playwright/acceptance-report-*`; the final review manifest records
the exact run and counts. This is a static-report browser check, not new portal
CRUD/login/ETL browser evidence. The application was not started. The earlier
portal browser and HTTP coverage results remain historical.

The four native regression skips remain FIFO/POSIX link/fork or host symlink
privilege limitations. Python 3.8 syntax parsing is checked separately; a new
clean Python 3.8 runtime was not available and was not installed. No WSL suite,
new CI, company Oracle/LDAP/SMTP, real notification or external API was used.

## Pending publication scope

This round adds the HTML renderer, report boundary tests, a static-report browser
harness and browser instructions, report operating/validation documents, and
updates the CLI/toolchain inventory, test fixture copies, package import support,
agent instructions and handoff links. Product code, app.py, /QA_portal/ routes,
authorization, CRUD, ETL and dependencies were not changed by this report round.

The pre-existing uncommitted ETL/performance follow-up and earlier v12 work remain
pending as before. A 705-file snapshot was preserved before report edits. The
review manifest distinguishes this round's added/modified files from the full
dirty worktree and records their hashes. It does not authorize publication of
the combined changes. Original D drive files and prior delivery ZIPs remain
protected. No files were staged or committed and no push, upload, merge or
deployment occurred. Destination/authorization confirmation stays with the
parent conversation.

## Final visual evidence

Actual native Chrome **12 PASS, 0 FAIL, 0 UNRUN** for the final HTML, at desktop
1440 px and mobile 390 px. Eight disclosures work with Tab/Enter/Space; no
horizontal document overflow, external request, page error or console error was
observed. Script-free content and CSP checks passed; owned Chrome was closed.
Node v24.21.0, Playwright 1.62.1, Chrome 154.0.8037.95. This is report-view evidence only.
Final browser run: `acceptance-report-2026-10-04T18-14-39-744Z-t3ZiWt`. All screenshot/artifact hashes match.

The first full report run had 1,117 PASS / 4 SKIP / 0 FAIL and its first browser
check had 12 PASS. Screenshot review then found that a nested historical ETL
Conflict was summarized only as AssertionError. A fixed safe message mapping and
payload-canary regression were added, explaining the still-active lease without
embedding raw error contents. The final full regression and browser run above
include that changed source plus the final Windows device/alternate-stream
output refusal. Its output boundary case is included in the existing parameterized
test. Earlier successful native and browser runs remain preserved; final checks
followed source changes, not blind retries of failures.

Python 3.8 grammar parsing passed for 128 program files. This does not
replace a Python 3.8 runtime test. Source digest: `f7e6798ae9853f28197e8f2ee4340712aa75ae9b7b38958768f84279415fd1ed`.
