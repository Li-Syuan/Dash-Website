# Report-template acceptance

Run in the approved existing environment from the repository root. Do not
install or upgrade packages, contact company services, or use a production DB.

```sh
python -B -m unittest discover -s tests -p test_report_template.py -v
python -B -m unittest discover -s tests -v
git diff --check
```

The integration owner also verifies the strict opt-in flag and production
rejection in `tests/test_v11_integration.py`. Default-off catalog behavior must
remain unchanged.

## Service and HTTP checks

- Same-organization user/peer/admin read equality; foreign users/admins only
  receive their own organization, even when sample IDs are identical.
- Anonymous, unsupported role, stale/revoked claims, caller-forged org/role,
  provider identity substitution and mid-fetch revocation fail closed.
- Reject extra authority/query fields, noninteger/bool/oversized paging,
  unknown sort/direction/department and malformed or excessively long text.
- Preserve literal filtering, stable tie ordering, numeric ordering, empty
  results and bounded paging.
- Reject foreign adapter rows, duplicates, invalid calendar dates, booleans as
  numbers, NaN, overflows, control characters and extra payload columns.
- Stop and close an overflowing lazy source; never silently truncate it.
- Export fresh server rows with fixed columns/name, matching filters/order,
  ISO dates, quantities, CSV quoting/Unicode and formula-leading text neutralized.
- Actual registered router/callback requests enforce login/role/tenant scope;
  URL parameters and client table contents cannot supply an actor or data source.
- Prior results or callback payloads do not authorize a query/export after
  revocation. Errors never reveal adapter exception text or foreign rows.

## Real browser checks

With the opt-in flag enabled in an isolated synthetic app, use native Chrome to
sign in, open the page, inspect the table, apply search/department/order/limit,
move Next/Previous, clear to an empty match and recover, then download the CSV
and inspect its actual bytes. Compare peer and foreign-tenant sessions, exercise
unauthorized callbacks and revocation, and inspect console/network errors.

The callback test client does not establish visual rendering or successful
browser download. Browser results, screenshots and download hashes are recorded
separately by the browser owner, with the exact tested source.

## Status

On 2026-10-04, the targeted command passed **19/19 tests in 1.059 seconds** on
Windows Python **3.10.22**, with no failures or skips. Execution began after
the exclusive large-XLSX baseline measurement window ended. Python 3.8 grammar
parsing passed for the six new Python files; whitespace and `git diff --check`
also passed. These are service and real HTTP callback tests, not browser tests.

Full regression, strict flag tests and native-browser outcomes are recorded
separately by the integration owner. Python 3.8 grammar acceptance alone does
not certify execution on the target Python 3.8.13 or company Linux kernel.
No company adapter or production deployment is certified.

## Acceptance for a copied report

Replace the targeted test filename with the new report's suite. The original
40-case browser run and `--report-template` transport matrix certify only the
original report. The browser fixture clears inherited `REPORTING_*` values;
add a dedicated validated `QA_*` opt-in, reinstate the corresponding setting
before importing `app.py`, and implement named cases using the new route, card,
component IDs, CSV filename/schema and semantics. Preserve the old cases.
Extend entrypoint coverage to register the new SPEC and verify its page and
query/export callback IDs appear in the emitted evidence. See
[quality-actions/README.md](../quality-actions/README.md) for runnable commands.
