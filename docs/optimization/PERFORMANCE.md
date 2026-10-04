# Synthetic QSL performance evidence

The v10 candidate adds one SQLite partial index:
`legacy_qsl_active_order(target, id) WHERE deleted=0`. The v9 query plan used
the active business-key uniqueness index and a temporary B-tree for `ORDER BY
id`. The new index supplies the existing server-selected target and ID order
directly. The unique business-key index, authorization, literal substring
filtering, pagination, export limits, fields and callback outputs are unchanged.
No dependency, schema column, product entrypoint, public route or user setting
changes.

## Reproduce

Use the approved installed runtime with `requirements-demo.txt` and
`requirements-qa-portal.txt` versions. These commands do not install packages or
open a network listener. Run in a quiet window without other project test,
import or browser workloads. Output files are immutable: choose new filenames
for each run.

```sh
python -B benchmarks/portal_latency.py --output benchmarks/evidence/local-run.json --rows 5000 25000 --samples 15 --warmups 3 --label local-run
python -B benchmarks/portal_latency.py --source-root /path/to/immutable-v9 --output benchmarks/evidence/local-before.json --rows 5000 25000 --samples 15 --warmups 3 --label local-before
python -B benchmarks/compare_latency.py benchmarks/evidence/local-before.json benchmarks/evidence/local-run.json --output benchmarks/evidence/local-comparison.json
python -B -m unittest discover -s tests -p test_legacy_performance_contracts.py -v
```

The baseline source is commit `a35898344c752e6c41fab0afdfd81d7d6ebdb617`.
`--source-root` selects code to measure, not an alternate product launcher.
The harness uses the existing app factory and registered login/CRUD callbacks.
All state is in a new temporary directory, disposed after each dataset. It
does not contact company systems, start scheduler workers, use real identities,
or change clocks/network/security settings.

## Method and environment

- Native Windows 10 / AMD64, Python 3.10.22, SQLite 3.53.4; these are the actual
  measured versions, not the exact requested company OS/Python patch versions.
- Dash 2.9.1, Flask/Werkzeug 2.2.3, Flask-Login 0.6.2, Plotly 5.13.1,
  dash-bootstrap-components 1.4.1, dash-mantine-components 0.12.0,
  setuptools 57.5.0 and openpyxl 3.1.5. Complete metadata and source/harness
  SHA256 values are embedded in each raw evidence file.
- Deterministic 5,000 and 25,000 active rows, 100 supplier groups, four countries,
  32 cities; two original synthetic seed rows are soft-deleted. Fixtures are
  inserted through authorized `stage_rows`/`submit_stage` in 500-row batches,
  retaining the normal normalization, audit and revision paths.
- Page queries return 1,000 rows. The literal `Vendor_Name` filter selects
  `group 007` (50 or 250 rows). Exports contain the entire active dataset.
- Each operation has three warmups and 15 timed samples using
  `time.perf_counter_ns`. p95 uses nearest rank and, with 15 samples, equals the
  highest sample. Raw samples are retained; no retry, sleep, discarded outlier
  or timing assertion is used. These figures are measurements, not an SLA.
- Timed service calls include authorization and materialization. Timed Dash
  calls are real Flask test-client HTTP dispatch, including transport policy,
  callback dispatch, serialization and download base64 encoding. They exclude
  a TCP connection, browser rendering and browser download latency. They are
  **not browser performance measurements**.
- Validation is outside the timed interval. Every sample checks ordered query
  rows, CSV values and callback row/download shape; complete XLSX cell values,
  sheet header and row order are checked for the final service sample. The
  contract tests additionally check formula-like text remains a literal XLSX
  string. Profiling is a separate extra call and does not enter timing samples.
- The comparison CLI rejects different environments, harnesses, sample methods
  and normalized fixture hashes, and reports changed source hashes explicitly.
  Root and the other task agents paused CPU-heavy tests during both timed runs.

## Results

Comparison validation passed: identical interpreter, packages, harness,
fixture hashes, warmups and samples. The only changed runtime source hash is
`reporting_workspace/legacy_crud.py`. Both candidate query plans use
`legacy_qsl_active_order (target=?)` with no temporary ORDER BY B-tree.

All times below are milliseconds. A positive reduction means a lower median;
negative reductions are preserved rather than presented as improvements.

| Active rows | Scenario | Before median | After median | Median reduction | Before p95 | After p95 |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 5,000 | Service page query | 2.905 | 2.122 | 27.0% | 12.665 | 11.172 |
| 5,000 | Service literal filter | 0.700 | 0.746 | -6.4% | 0.920 | 0.876 |
| 5,000 | Service CSV export | 38.952 | 34.754 | 10.8% | 48.674 | 48.217 |
| 5,000 | Service XLSX export | 501.471 | 494.826 | 1.3% | 528.768 | 506.770 |
| 5,000 | Dash page query | 5.317 | 4.418 | 16.9% | 6.446 | 5.005 |
| 5,000 | Dash CSV export | 47.739 | 42.859 | 10.2% | 57.149 | 55.552 |
| 5,000 | Dash XLSX export | 510.186 | 506.166 | 0.8% | 515.685 | 518.085 |
| 25,000 | Service page query | 5.810 | 2.097 | 63.9% | 16.898 | 10.736 |
| 25,000 | Service literal filter | 5.167 | 5.451 | -5.5% | 5.424 | 5.830 |
| 25,000 | Service CSV export | 222.514 | 187.483 | 15.7% | 226.261 | 190.558 |
| 25,000 | Service XLSX export | 2,532.071 | 2,542.979 | -0.4% | 2,551.407 | 2,577.494 |
| 25,000 | Dash page query | 10.022 | 4.461 | 55.5% | 12.576 | 4.699 |
| 25,000 | Dash CSV export | 246.869 | 210.590 | 14.7% | 250.714 | 222.592 |
| 25,000 | Dash XLSX export | 2,571.086 | 2,568.058 | 0.1% | 2,605.951 | 2,635.771 |

The useful measured gains are bounded page reads and CSV exports. Literal
substring filtering still scans active rows: the 25,000-row filter median
increased by 0.285 ms (5.5%) in this pair. XLSX latency is effectively unchanged;
its small deltas do not establish an export speedup. Sample-level variability
is visible, especially in the service-page p95. No statistical significance or
cross-machine performance claim follows from one pair of sequential runs.

The added index has a real storage/write cost:

| Active rows | Before DB bytes | After DB bytes | Added bytes | Before import setup | After import setup |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 5,000 | 3,928,064 | 4,067,328 | 139,264 (+3.5%) | 270.774 ms | 276.146 ms |
| 25,000 | 19,202,048 | 19,898,368 | 696,320 (+3.6%) | 1,372.395 ms | 1,451.589 ms |

Database size is SQLite page count multiplied by page size, including the
normal audit/revision rows and consumed import receipts. Import figures are
the sum of the one-time 500-row fixture setup batches, not repeated steady-state
write throughput: +2.0% at 5,000 rows and +5.8% at 25,000 in this pair. The
tradeoff is explicit; import-heavy workloads should measure their own mix.

Sanitized evidence contains all samples, top profile frames, source hashes and
environment metadata; no row payloads, accounts, local paths or database files:

- [Baseline v9](../../benchmarks/evidence/baseline-v9.json), SHA256
  `226c2e256402ddc546ed1200f817e57d6ba399d9dac5eadcc8f837c5e9b378e7`.
- [Candidate v10](../../benchmarks/evidence/candidate-v10.json), SHA256
  `ffd518401b34a878451672a4c0af0196752c242fedba596ac8a026b6b467f0ab`.
- [Validated comparison](../../benchmarks/evidence/comparison-v10.json).

The initial successful 1,000-row harness smoke run is retained separately in
local task evidence; it is not included in the deliverable or this comparison.

## XLSX bottleneck and limits

At 25,000 rows, the baseline XLSX service median is 2,532.071 ms and the Dash
XLSX callback median is 2,571.086 ms. The separate profiled call (profiling adds
overhead) took 5.53 seconds: 25,001 row appends accounted for 4.56 seconds,
225,009 cell writes for 2.97 seconds, and XML element/attribute processing
dominated those writes. This runtime uses openpyxl's standard ElementTree
serialization path. Those nested cumulative times must not be added together.

The export already uses a write-only workbook and explicitly types all QSL
fields as literal strings, including text beginning with `=` or spreadsheet
error tokens. Retain this safe output contract. The index removes avoidable
query sorting, but does not eliminate the cost of serializing 225,000 cells.
No alternative XML/XLSX engine or new dependency was added. Do not claim the
query optimization makes large XLSX files fast, and do not infer production
Oracle, browser, network, concurrent throughput or target Linux performance
from these synchronous synthetic measurements.

## Existing stores and regression coverage

The index is added with `CREATE INDEX IF NOT EXISTS` during the existing QSL
service initialization. Existing rows, versions, revisions, uniqueness and
payload-free audits are retained; reopening is idempotent. A previous runtime
can ignore this additional index. No destructive migration or database reset
is needed. Creating the index still takes a SQLite schema write lock; use the
existing stopped-application backup/upgrade procedure for a maintained store.

Five focused tests passed on Python 3.10.22. They cover stable ID pagination
across target/deleted boundaries; literal filters and CSV/XLSX presentation;
upgrade of an existing database without modifying rows/audit/revisions;
exact-cap exports versus overflow rejection; and policy revocation on every
query/export call. They do not assert machine-specific timing thresholds.
The root v10 validation record reports the separate complete regression and
real-browser outcomes.
