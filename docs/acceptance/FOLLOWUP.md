> Historical round record: publication of the cumulative source is now
> explicitly authorized. See [the current publication record](PUBLICATION.md).
> Raw execution evidence and checkpoint inventories referenced below are
> retained in the local delivery archives, outside this public source tree.

# v12 follow-up: ETL diagnosis and calibrated performance gate

This approved follow-up is isolated on local restore checkpoint
`50e6d185c616ef14d331289f894c7e52222fb3fd`. Completed v12 sources and archives
remain untouched. No push, merge, deployment, installation or system setting
change occurred. Product code, dependency pins, app.py, CRUD, ETL lease policy,
report features and the default 50,000-row export cap are unchanged.

## ETL result and repair

The old recovery fixture read the stored wall-clock lease expiry once, slept
for the computed remaining duration, then assumed the lease had expired. A
module-local fake clock reproduced a 0.8-second loss of wall progress: the old
helper returned 0.77 seconds before expiry. A real restarted dispatcher correctly
refused the live lease with the exact historical overlap Conflict. All six
durable tables stayed unchanged and no adapter ran. After simulated natural
expiry, only the unclaimed date ran; the interrupted date stayed nonretryable
and replay made no changes. The diagnostic uses real isolated SQLite and an
owned terminated child, without changing operating-system clocks.

The fixture now rechecks wall expiry after each bounded wait, with a five-second
monotonic deadline and sleeps of at most 50 ms. It does not rewrite durable
expiry, retry an ETL operation or replace cross-process wall leases with
process-relative monotonic values. Eight fake-clock boundary tests cover normal
progress, 0.8-second rollback, stuck wall time, monotonic jumps, expired leases
and exhausted budgets. All durable rows must remain unchanged.

The historical WSL run is still INVALID: it recorded three realtime-minus-
monotonic offset discontinuities, and its one recovery failure is preserved.
All sampled wall deltas were positive. Without per-test/lease timestamps, those
records do not prove which clock jumped or establish this exact historical
cause. The counterfactual establishes a fixture defect, not an ETL safety defect.

Reproduce focused checks with the approved interpreter:

```sh
python -B -m unittest discover -s tests -p test_etl_lease_wait.py -v
python -B -m unittest discover -s tests -p test_etl_recovery_faults.py -v
python -B tools/agent_acceptance.py run --profile unit
```

## Performance rules

The pure gate accepts comparable schema, runtime/dependency/SQLite versions,
harness hashes, data and workload hashes, sampling plans and memory methods.
Its file loader binds them to raw artifacts, fixed-command process receipts,
source inventories and valid clock records. It never executes report commands.
Changed environment or unavailable/expired evidence is INCOMPLETE; tampered or
inconsistent evidence is INVALID. Unique IDs, byte hashes and complete sample
fingerprints reject obvious replay, but are not signed proof of independence.
Equal scalar readings within one run remain legitimate observations.

Minimum evidence is three independent baseline benchmark invocations, each
with at least 15 latency samples or seven XLSX samples after warmups, followed
by one candidate with the same plan. Three runs are an engineering minimum,
not a statistical confidence guarantee. One candidate does not demonstrate
repeatability or certify other workloads.

For each scenario and metric:

```text
reference = median(baseline run medians)
noise percent = 100 * (max(run medians) - min(run medians)) / reference
regression percent = 100 * (candidate median - reference) / reference
```

Noise above half the explicitly approved metric tolerance is INCOMPLETE. It
never raises the limit. With adequate comparable evidence and acceptable noise,
a regression above the limit is FAILED; otherwise that performance assessment
is PASSED. Wall-time and peak-RSS percentages are independently configured.
p95, file sizes and other memory counters remain descriptive. The XLSX producer
retains its historical p95 description mentioning three samples; the gate
checks actual sample counts and nearest-rank arithmetic (also the maximum for
seven observations), rather than inferring the count from that description.

`benchmarks/performance-policy.example.json` is PROVISIONAL, with no approval
timestamp. Candidate limits for review are wall 10% / RSS 5%, or wall 15% /
RSS 10%. Neither is a default or a calibrated recommendation. The only existing
baseline invocation has latency relative MAD 0.79-6.11%; XLSX has just three
samples per size. Those within-run observations do not establish between-run
stability. Candidate results were not used to select tolerances. Missing limits
(including null), missing baselines, excessive noise and unapproved policy all
remain INCOMPLETE.

## Executable workflow

Use a frozen, trusted local baseline and an exclusive quiet measurement window.
The following profile creates a new run directory and captures baseline only:

```sh
python -B tools/agent_acceptance.py run --profile performance-baseline --baseline ../frozen-baseline
```

Invoke it three independent times. Latency uses 5,000/25,000 rows, 15 measured
samples and three warmups. XLSX uses 100,000/200,000 rows, seven measured samples
and one warmup per size. Keep all results, including noisy or failed runs.
No automatic reruns occur. The focused profile's overall exit is normally 3
because other project gates are UNRUN; examine each measurement step separately.

Create separate calibration JSON files for each family:

```json
{
  "schema": 1,
  "family": "latency",
  "records": [
    {"report": "run1/report.json", "step": "latency_before"},
    {"report": "run2/report.json", "step": "latency_before"},
    {"report": "run3/report.json", "step": "latency_before"}
  ]
}
```

Paths resolve relative to the manifest. For XLSX use family `xlsx` and
`xlsx_before`. After reviewing baseline-only variability, explicitly approve
numeric limits and record the actual approval time as finite UTC epoch seconds
in `approved_at`, with `status: "approved"`. The gate requires every baseline
report to finish before approval and the candidate report to start afterward.
Do not backdate approval or choose limits from a candidate result.

Then capture the candidate with the seven/15-sample profile:

```sh
python -B tools/agent_acceptance.py run --profile performance-calibrated --baseline ../frozen-baseline
python -B tools/agent_acceptance.py assess-performance --family latency --baseline ../frozen-baseline --policy benchmarks/performance-policy.json --calibration benchmarks/calibration-latency.json --candidate-report output/agent-acceptance/CANDIDATE/report.json --output output/latency-assessment.json
python -B tools/agent_acceptance.py assess-performance --family xlsx --baseline ../frozen-baseline --policy benchmarks/performance-policy.json --calibration benchmarks/calibration-xlsx.json --candidate-report output/agent-acceptance/CANDIDATE/report.json --output output/xlsx-assessment.json
```

Outputs must be NEW paths under this checkout's output directory. Assessment
exit codes are 0 PASSED, 1 FAILED, 3 INCOMPLETE, 86 INVALID; every assessment has
`whole_project_verified: false`. Read-only assessment creates JSON, SHA256 and a
short Markdown result. Default freshness is 24 hours, bounded configurable to
one second through seven days. The general `verify` command handles run reports;
re-run `assess-performance` into a new output to check assessment freshness.
Unsigned local hashes detect changes; they are not independent attestations.

Legacy `--max-wall-regression-percent` and `--max-rss-regression-percent` flags
remain descriptive and cannot make the run gate pass or fail performance.
The full run and the calibrated performance assessment are separate reports;
do not merge their statuses into whole-project success. Existing full/performance
profiles retain three XLSX samples for historical reproducibility; use the new
profiles for sufficient future calibration. No product or database migration is
needed. Tool clients must recognize the new subcommand and INCOMPLETE behavior.

## Remaining evidence and environment limits

The native full regression has 1,091 passes, four platform skips and zero
failures (1,095 total); the unit profile remains INCOMPLETE. See
[FOLLOWUP_VALIDATION.md](FOLLOWUP_VALIDATION.md) for bound results and initial
failures. UI and HTTP behavior were unchanged; prior v12 Chrome 40/40 and HTTP
263/263 plus template 271/271 are preserved historical evidence, not new runs.
New heavy calibration/candidate benchmarks were not run while numerical policy
remains unresolved. Existing records were read once per family and returned
INCOMPLETE because each has only one independent baseline capture.

No usable non-WSL Python 3.8 was found in the bounded local inventory. Docker
was stopped and Hyper-V inventory was denied by current OS account permissions.
No install, service start or WSL repeat occurred. The smallest next action is
separate authorization to obtain a trusted Windows x64 Python 3.8 and create
an isolated workspace environment with existing pins, or an administrator's
read-only confirmation of an already running suitable non-WSL VM. Neither
would by itself certify the exact production Linux/kernel/patch target.
