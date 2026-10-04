> Historical round record: publication of the cumulative source is now
> explicitly authorized. See [the current publication record](PUBLICATION.md).
> Raw execution evidence and checkpoint inventories referenced below are
> retained in the local delivery archives, outside this public source tree.

# One-page local acceptance report

`render-report` creates an offline HTML page from existing acceptance evidence.
It displays the current gate, PASS/FAIL/SKIP counts, failure causes, historical
INVALID runs, descriptive performance differences, fixed reproduction commands,
source hashes, execution/generation times and unverified scope. It does not
start app.py, open a port, run a test/benchmark, or execute report metadata.
Product routes, authorization, CRUD, ETL and dependency pins are unchanged.

## Generate and view

First run the desired fixed acceptance profile with the approved interpreter.
Use its actual report path in the following command, replacing placeholders:

```sh
python -B tools/agent_acceptance.py run --profile unit
python -B tools/agent_acceptance.py render-report --report output/agent-acceptance/RUN/report.json --output output/acceptance-review-NEW.html
```

For a report whose original run used a baseline, also provide the same explicitly
trusted `--baseline` source directory. Current evidence goes through the existing
`verify` contract, including source/tool/runtime identity, freshness, artifact
hashes, fixed entrypoints, counts and clock validity. A changed source makes an
older primary report INVALID; generate new evidence rather than altering hashes.
The HTML renderer's own source and browser harness are part of that fingerprint.

To display preserved earlier runs separately, repeat `--history-report`:

```sh
python -B tools/agent_acceptance.py render-report --report output/agent-acceptance/CURRENT/report.json --history-report /trusted/local/native/report.json --history-report /trusted/local/invalid-clock/report.json --output output/acceptance-review-NEW.html
```

Open the emitted `.html` directly in an installed browser. There is no server,
JavaScript, installation, login, remote font or external resource. Anchor links
and native details/summary controls work offline and with the keyboard. The
page adapts to desktop/mobile widths; wide metric tables scroll within their
own region. Copy the reviewed fixed commands manually and replace placeholder
paths. No command button or arbitrary report-provided argv is rendered.

## Interpretation and status

Generation returns the CURRENT gate's exit code: 0 PASSED, 1 FAILED, 2 BLOCKED,
3 INCOMPLETE, 86 INVALID, 124 TIMED_OUT. A successfully written page can therefore
return a nonzero code. The CLI JSON includes `generated`, `report`, `sha256`,
`whole_project_verified` and `commands_executed:false`. Failed generation returns
`generated:false`. Do not treat a created HTML file as a passing acceptance run.

Missing primary evidence creates an INCOMPLETE page. Malformed, unsealed,
tampered, stale or source-mismatched evidence creates an INVALID page. Missing
or invalid evidence never supplies green success counts. A structurally valid
report with an invalid clock also suppresses its current passing counts.
The hero counts describe only the current regression; historical, authorization,
HTTP and browser subchecks are not added to them. SKIP remains unverified.

Historical reports undergo raw seal/artifact and clock-record consistency checks,
but are clearly labeled as historical and are not revalidated against current
source/runtime. Their recorded statuses cannot promote the current gate.
Missing or inconsistent historical inputs remain visible and invalid/incomplete.
They do not change the independently verified current gate. Source/run identifiers,
SHA256 and UTC timestamps allow matching the page to original local evidence.
These unsigned hashes are not independent attestations of honest execution.

Performance data are displayed only from bound before/after/comparison artifacts
whose hashes and strict producer schemas, source maps, workloads, environments
and numerical aggregates agree. The table shows actual medians, sample counts
and relative differences; positive differences mean increased time or memory.
No missing number becomes zero. Invalid metrics are not displayed. The 15% time /
10% RSS limits are NOT approved, and this viewer never enables them or certifies
performance. Existing single-invocation measurements remain descriptive; the
separate calibrated gate still requires adequate independent baselines and
explicit approval. See [FOLLOWUP.md](FOLLOWUP.md).

## Output and privacy boundaries

Only a NEW `.html` below the selected checkout's `output/` is accepted. Existing
files, traversal, observed links/reparse ancestors and nonregular files are
refused, as are Windows device names and alternate data stream output paths.
Exclusive file creation prevents overwriting a concurrent writer.
These checks are not a sandbox against a hostile filesystem administrator.
At most eight historical inputs and 8 MiB per parsed JSON are accepted.

All displayed evidence strings are escaped and common credential/authorization/
cookie/URL/private-home forms are masked. The page excludes arbitrary JSON
fields, environment variables, raw argv and complete logs. Failure diagnostics
show test IDs, structured reasons, allowlisted exception types, and one exact
reviewed ETL overlap-message explanation; arbitrary raw exception messages are
not embedded. Unknown secrets cannot be inferred from arbitrary text, so review
custom structured reason strings before sharing. CSP blocks scripts, connections,
images, base URLs and forms; the only style is bundled CSS.

## Tests and local scope

```sh
python -B -m unittest discover -s tests -p test_acceptance_report.py -v
python -B tools/agent_acceptance.py run --profile unit
node tests/browser/acceptance_report.cjs output/acceptance-review-NEW.html
```

The browser command uses the existing approved Playwright module and Chrome.
See [report browser instructions](../../tests/browser/REPORT_README.md) for
`QA_PLAYWRIGHT_MODULE` and evidence paths. This checks actual report rendering,
desktop/mobile layout, keyboard disclosures, CSP, inert content and absence of
external requests. It does not rerun or certify the underlying portal UI.

No database migration is required. Changed CLI clients only need the new
subcommand and its nonzero-but-generated result contract. Existing profiles and
product entrypoints remain intact. Read [REPORT_VALIDATION.md](REPORT_VALIDATION.md)
for this round's observed results, initial failures and publication file scope.
No push, upload, merge, deployment, system change or Python installation is
authorized by this local report round.
