> Historical round record: publication of the cumulative source is now
> explicitly authorized. See [the current publication record](PUBLICATION.md).
> Raw execution evidence and checkpoint inventories referenced below are
> retained in the local delivery archives, outside this public source tree.

# v12 follow-up validation

Native Python 3.10.22 complete regression: **1,091 PASS, 4 SKIP, 0 FAIL**
(1,095 total), 137.459 seconds in unittest. The unit profile and independent
verification both return **INCOMPLETE (3)**. Clock: zero offset discontinuities
and monitor errors; cleanup confirmed. Source digest:
`05b5988e2307b3806de1e46b3978ee6a85ba1b04b18b5b4a99839958d449557f`.

The eight new ETL wait tests and ten existing recovery tests passed in focused
runs and again in full discovery. The final 31 tolerance tests and 12 provenance/
actual-CLI tests all pass in the full suite; these counts overlap the 1,095.
No double-counting of tests or HTTP subchecks is intended.

The first tolerance recheck had one failure (30 pass / 1 fail): after the missing
threshold behavior was corrected to INCOMPLETE, an older test still expected
INVALID. That log is preserved. Its assertion was corrected to the explicit
requirement; malformed/negative/nonfinite limits still remain INVALID. The
complete regression above is the next invocation and contains the corrected
case. There were no retries of the clock-invalid WSL suite.

The unchanged dispatcher and original fixture were exercised in an isolated
native fake-clock counterfactual. Lost wall progress reproduced the exact
overlap Conflict without any durable write or adapter call. This demonstrates
the fixture's assumption defect, not a product safety defect. The WSL trace
does not establish the exact historical cause and remains INVALID.

The final read-only CLI assessed preserved actual native latency and XLSX data:
both return INCOMPLETE because each has one baseline invocation. The XLSX data
also have only three samples; the policy is provisional. No new benchmarks
were run and no real performance PASSED claim is made. Manufactured test data
validate robust-median noise handling, clear regression failure, missing values,
approval chronology, replay, environment/source/hash changes and no command
execution. They are unit fixtures, not calibration measurements.

Python 3.8 syntax: 126 program files accepted. This is not a runtime
test. Clean Python 3.8 remains blocked by runtime availability; no installation,
WSL repeat or system setting change occurred. Prior v12 native Chrome 40/40 and
HTTP coverage 263/263 plus template 271/271 remain historical. This follow-up
changed only tests/tools/docs/configuration; the browser and HTTP profiles were
not rerun. No CI, push, merge, deployment, company service or external API ran.

All 649 protected original-v12 files and all 13 prior delivery archives were
hash-verified unchanged, as were the recorded original checkout files.
Product code, benchmark producers and dependency pins are unchanged.

See FOLLOWUP.md for commands and limitations. Follow-up evidence under
`followup/evidence/` is sanitized delivery material. EVIDENCE_INTEGRITY.json maps
original bytes to derivatives. Sanitized receipts retain provenance and must
not be resealed or presented as new executions. Verify original local reports
in their recorded environment, or run fresh acceptance after extraction.
