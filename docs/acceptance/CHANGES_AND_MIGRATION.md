# v12 changes and migration

This round adds local acceptance tooling on top of the complete local v11
checkpoint `1e7983962a19438d37d1d97f173ec0fe643249c9`. The original v11 worktree
and its uncommitted changes are preserved. The checkpoint is an unpushed restore
point; the v12 changes are not staged, committed, pushed, merged or deployed.

## What changed

- `tools/agent_acceptance.py` connects fixed regression, authorization, HTTP
  entry coverage, actual browser and performance profiles. It creates new
  evidence, structured decisions and human summaries on every invocation.
- `acceptance_worker.py` records actual unittest outcomes and method IDs,
  skipped/expected-failure cases and unraisable/thread errors. The controller
  also checks shutdown-error markers in the child's raw log.
- `acceptance_core.py` and `_acceptance_process.py` hash the source/artifacts,
  reject path traversal and observed filesystem links, enforce deadlines and
  clean up owned processes. Windows assignment occurs before the command starts.
- `acceptance_binding.py` binds decisions to the fixed command registry,
  process receipts, complete producer artifacts, environment and clock events.
  `acceptance_evidence.py` checks the exact HTTP/browser catalogs and recomputes
  performance statistics from raw samples, so empty or contradictory evidence
  cannot become a successful gate.
- The read-only Windows clock probe now pairs precise FILETIME with QPC. The
  old coarse clock combination produced a reproducible false backstep during
  initial CLI testing. The one-millisecond detection threshold and the POSIX
  clock path remain; host/guest time configuration was not changed.
- Focused tests exercise deliberate failure, skipped/empty suites, missing
  tools, timeouts, child cleanup, source/evidence tampering, forged completion,
  stale/future timestamps and metadata that must never execute.

## Initial defects and preserved evidence

The first 21-case CLI run had 13 failures due to coarse Windows clock readings;
the raw run and a retained minimal diagnostic are preserved. An initial core
test exposed a real Windows Job Object cleanup race: zero active process count
did not yet guarantee signaled process handles and closed inherited log handles.
The fix waits on retained owned process handles as well. A separate first core
failure was a mistaken same-length corruption fixture and was corrected without
changing the gate. Pure validator testing also corrected a fixture helper that
treated an explicit `None` as an omitted argument. These preliminary attempts
are distinct from final integrated evidence.

## Migration and rollback

There is no product database/schema migration, new dependency or changed
deployment configuration. Keep the approved Python environment and configure
the existing local browser module only when running the browser profile.
`app.py`, CRUD modals, ETL, authorization semantics, report template and the
50,000-row default export cap are unchanged. Baseline/candidate performance
results measure the same product implementation and must not be advertised as
a product speedup.

Use a separate checkout or worktree for this round. To abandon v12, stop only
its owned test processes and return to the preserved v11 worktree/checkpoint;
do not reset or clean the original dirty checkout. Generated evidence lives
under `output/` and can be archived independently. Do not reuse its report for
changed source or a new environment. No command in the tool pushes, merges,
deploys, installs packages or contacts an LLM/company service.

See [the tool contract](README.md) for commands and limitations, and
[validation](VALIDATION.md) for actual pass/fail/skip/unrun counts and artifacts.
