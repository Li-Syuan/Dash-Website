# Agent change-impact hints (local only)

This offline CLI answers: changed file → candidate pages, HTTP APIs, Dash
callbacks and synthetic schedule boundaries → suggested tests. It is a planning
hint, never proof of runtime reachability, complete test coverage or acceptance.
It does not import the application, start workers, access company services or
execute the tests/commands it prints. Python 3.8-compatible standard library only.
No new package, UI, model call, push, upload or deployment is involved.

## Run from the checkout root

Use an existing approved Python environment:

```sh
python -B tools/change_impact.py --changed reporting_workspace/quality_actions/service.py
python -B tools/change_impact.py --changed reporting_workspace/etl_dispatch.py reporting_workspace/etl_adapters.py --format json
python -B tools/change_impact.py --base HEAD~1 --format json
python -B tools/change_impact.py --format json > output/change-impact.json
```

Create the ignored `output/` directory before using shell redirection. The tool
writes only stdout/stderr; it never creates or overwrites a report file itself.
`--repo` may identify another existing local Git repository root, but reviewed
bindings are specific to this Dash repository and missing anchors force full
regression. Do not use it as a generic framework analyzer.

- No selection means working-tree changes against HEAD, including staged,
  unstaged and nonignored untracked paths. `--base` compares the local commit to
  the current working tree (not only committed changes); no fetch is performed.
- `--changed` limits the report to explicitly named current paths. It retains
  their real Git status; tracked unchanged files can be named for a what-if
  analysis. This is not a complete worktree report when other changes exist.
- Renames are represented as deletion + addition. New/deleted/unknown files
  require full regression: the current AST cannot reconstruct historical edges.
- Paths must be canonical relative paths using letters, digits, `_`, `-`, `.`,
  `/`. Absolute paths, traversal, backslashes, shell/control characters, spaces
  and symlink paths are rejected rather than interpreted or read outside the
  source tree. An invalid input returns 2 with no success report.
- Exit 0 means only that a report was generated. Read `status` and
  `full_regression_required_now`; exit 0 never means tests passed. Git reads are
  fixed argv, no shell, external diff/textconv or fsmonitor helper execution.

## Interpretation and evidence

The CLI parses Python 3.8 AST without importing any project module. Each impact
has a real declaration path/line and a shortest deterministic module dependency
chain. Each import edge includes its source line and reason. Relative imports,
static aliases, re-exports, package initialization, nested/conditional imports
and cycles are handled conservatively. Module-level analysis intentionally
includes conditional/dead-code imports, so some suggested tests are broad.
A changed dependency propagates to consumers; unrelated siblings are not
included just because the application imports both optional features.

It reads actual nested `PageSpec`, HTTP decorators, callback registrations and
`JobSpec` declarations. QSL's generated callback IDs are labeled as a group,
not fabricated final wire keys. The retired standalone demo-login is excluded
from the integrated product surface. HTTP APIs and Dash callback transport are
listed separately. Reviewed service-injection bridges are explicit in
`reviewed_bindings()` with checked AST anchors. Update and test them when wiring
changes; missing anchors mean `FULL_REGRESSION_REQUIRED`.

Examples in this repository:

- `quality_actions/service.py`: `/QA_portal/quality-actions`,
  `quality_actions.query`, `quality_actions.export`; suggested tests include
  `test_quality_actions.py`. Report-template page/callbacks are not inferred as
  consumers merely because the factory can enable both.
- `etl_dispatch.py`: `/QA_portal/etl` and the two default synthetic jobs
  `synthetic-sales-daily`, `synthetic-inventory-health`.
- `governance.py`: governed report API, admin/managed-report controls and the
  manual managed-report mail simulation boundary.
- `job_monitor.py`: the operations surface and `example-report-summary` worker.

Default-off and demo-only declarations remain in the candidate set. A listed
schedule is not evidence that it runs. Only the explicit launcher owns the
synthetic ETL/job-monitor workers; schedules default disabled. Administration
and QSL mail entries are manual mock simulation/configuration, not active SMTP
or timers. `/maintenance` cadence/enabled fields remain metadata only.

## Conservative full-regression rule

Full regression is required immediately for added/deleted/renamed/untracked
files, unknown or non-Python changes, unreadable/oversize/invalid Python sources,
unresolved runtime imports or registrations, mapping drift, dynamic product
imports/code, and changes with no defensible surface/test edge. Core
permissions/provider/configuration/state/lifecycle, package initializers,
shared assets, dependency pins, CI, UI registration/policy modules and report
contract files also require the full suite. Runtime dynamic imports can hide
consumers of any module, so their presence forces full regression globally;
existing tool/test dynamic loaders are considered in the affected region.

`FULL_REGRESSION_REQUIRED` means every page/API/callback/schedule is potentially
affected. The displayed static candidates are not an exhaustive list. Deleted
surfaces may be absent from that list; never interpret that as no effect.
`FOCUSED_HINTS_ONLY` merely allows focused tests as the next development step.
Even `NO_CHANGES` is not a previously verified release.

## Required before publication

A reduced suggestion set never waives full regression. Review commands and use
trusted source/approved runtimes. Do not automatically execute argv read from a
report JSON, which is inert, unsigned input that someone could replace.

```sh
python -B -m unittest discover -s tests -v
python -B tests/test_entrypoint_coverage.py --report-template --quality-actions
git diff --check
```

For affected UI/callback/export changes, in a permitted environment with the
existing browser dependencies:

```sh
QA_REPORT_TEMPLATE=1 QA_QUALITY_ACTIONS=1 node tests/browser/acceptance.cjs
```

Both optional reports must be in the matrix; older acceptance profiles that
only enable report-template are insufficient for quality-actions. HTTP/service
checks cannot replace browser evidence. Retain skipped, failed and unrun checks
and use the existing clock guard for run provenance. The current known browser
socket/access blocker is not permission to bypass it or modify security/network
settings. This tool neither tests nor retries that blocked browser path.

The JSON source fingerprint hashes the parsed-source input bytes (including
files that failed parsing); it is deterministic, not a signed attestation or
complete repository/content digest. Private company integrations, exact target
OS/Python patch versions, reflection and arbitrary external injection remain
unverified. Current local validation: [VALIDATION.md](VALIDATION.md).
