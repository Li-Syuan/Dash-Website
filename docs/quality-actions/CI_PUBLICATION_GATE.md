# Browser acceptance publication gate (2026-10-05)

The owner explicitly approved uploading a dedicated validation branch and now
requests the completed release on GitHub. Main remains unchanged until the
exact candidate passes full Python regression and browser acceptance. This
approval covers this release only. No deployment is part of this workflow.

## Proposed/approved execution shape

The existing reporting workflow keeps its Python 3.8/3.10 regression matrix,
with the existing read-only clock guard rejecting invalid-time invocations.
A push to `validation/dash-handoff-*` also runs the full native browser harness
on the official GitHub-hosted Ubuntu 24.04 runner using its preinstalled Chrome.
The test driver is fixed to Playwright 1.62.1, matching the inspected local
installed package. It is installed from npm only in the runner temporary area,
with package scripts disabled. Product Python pins and project dependencies do
not change. No browser or OS/network/security settings are modified.

Both report switches are enabled. The harness uses the same `app.py`, synthetic
identities, loopback fixture and owned temporary state; no company adapters,
credentials or production data are used. The CI workflow itself does not push,
merge, deploy or change repository permissions. Its token is contents-read only.
The job has a 30-minute limit and performs no automatic retry.

`node tests/browser/verify_acceptance.cjs` requires the exact 50 scenario names,
50 passed / zero failed / zero unrun, both enabled reports, actual browser
version, native transport, stopped fixture, removed owned state, fresh output,
and complete current source/harness hashes. A diagnostic-only run, a bridge run,
missing/duplicate case, changed source or startup failure cannot pass. Evidence
is parsed as inert JSON; it never supplies commands or import paths.

Only the allowlisted passing CI receipt, source/harness/runtime hashes and
synthetic screenshots are kept as a seven-day GitHub Actions artifact. Raw
`results.json` is excluded because failures can contain browser launch logs and
host paths. Raw server logs, request bodies, SQLite state, credentials, caches
and runtimes are also excluded. The artifact is evidence
for this repository's synthetic acceptance, not company-system certification.

## Local configuration validation

- Workflow YAML parses; existing Python matrix and read-only token are retained.
- Node syntax checks pass.
- Gate self-test accepts one synthetic complete fixture and rejects 20 partial,
  blocked, stale, bridged, duplicate-case or otherwise invalid fixtures.
- The actual retained blocked browser output is rejected with exit code 1.
- These checks validate the gate configuration only. They are not a browser pass.

After branch acceptance, re-read remote main. If it advanced outside the tested
candidate's ancestry, integrate/review it and repeat applicable acceptance on the
new exact commit before a normal fast-forward main push. Verify the resulting
remote SHA and the main push's Python/browser CI. Never force-push, merge a
failed candidate or label a blocked/partial run passed.

The existing six Windows-only skips and unverified exact runtime/company
integrations stay separate. The 15% time / 10% RSS policy remains unapproved
and disabled; no performance acceptance is inferred from test durations.

## Official references

- https://playwright.dev/docs/ci
- https://playwright.dev/docs/browsers
- https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2404-Readme.md
- https://github.com/actions/upload-artifact/tree/ea165f8d65b6e75b540449e92b4886f43607fa02

Runner/browser versions are recorded by each actual run; the hosted image is
maintained upstream and is not claimed to be an immutable production runtime.
