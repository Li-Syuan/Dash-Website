# Bounded QSL XLSX export

The v11 implementation removes the all-row Python list from QSL XLSX export.
It copies an authorized, ID-ordered SQLite snapshot into an auto-deleting
temporary file in batches of 1,024 rows, releases the source transaction/lock,
then feeds the existing openpyxl write-only serializer one row at a time.
The XLSX archive is also generated in an auto-deleting temporary file. The
existing `export_xlsx(user, filters) -> bytes` contract is preserved.

This is a synchronous local export, not a background queue or external service.
The final byte object still occupies memory proportional to the compressed
file size; the Dash download wrapper additionally encodes those bytes as base64.
Do not describe the complete browser/HTTP path as constant-memory or infer
browser rendering/download throughput from direct-service measurements.

## Limits and data contracts

- The application and service default export limit stays **50,000 rows**.
  Oversized results are rejected without returning a truncated workbook.
  The existing trusted service constructor can explicitly select a larger
  `max_export_rows`. The 100,000/200,000-row experiments use that constructor
  setting only; they do not silently enable those sizes in the UI.
- The existing query offset range still bounds supported configured export
  caps at 1,000,000. Every actual result remains bounded by its configured cap.
  No dependency or request-envelope limit changes.
- The worksheet remains `QSL`, with the exact nine `CSV_FIELDS` columns and
  ascending server record-ID order. IDs/versions remain numeric. The seven
  QSL fields remain literal text, including leading zeros, `=`, `+`, `-`, `@`,
  spreadsheet error tokens, Unicode and embedded newlines. CSV behavior is
  unchanged.
- This QSL schema has **no native date/datetime column**. Text such as
  `2026-10-04` and `2026-10-04T12:34:56` stays text; it is not converted to Excel
  serial dates. Native `date`/`datetime` objects and other non-string QSL input
  remain rejected by existing validation. Real company date columns require
  their actual supplied adapter/schema contract; these tests do not certify it.
- Filters still use the same allowlisted, literal substring predicates.
  Target scoping, soft-delete exclusion and authorization are unchanged.

## Authorization, consistency and failure handling

The exporter rechecks the supplied actor against the current server policy
while snapshotting, every 1,024 serialized rows, before archive generation,
after archive generation and before returning bytes. The exact initial actor
ID must still match; switching an actor object cannot receive the original
snapshot. The integrated QSL adapter separately resolves the current identity
provider on service authorization; its integration tests are maintained with
that adapter. No cross-request identity/result cache is introduced.

A snapshot is consistent even if another connection updates/deletes/inserts
records during XLSX serialization. The source SQLite write lock is held only
for bounded snapshot creation, not for the much longer XML serialization.
Snapshotting adds temporary-disk I/O and can still contend with source writers;
it is not a lock-free or distributed snapshot protocol.

Permission revocation, snapshot-disk failure and archive failure do not return
a download. Private snapshot/archive handles close on success or exception.
Pinned openpyxl 3.1.5 does not remove unsaved write-only XML on `Workbook.close`,
so the exporter explicitly closes started worksheet generators and cleans up
their library-owned XML files. Never-started sheets are skipped during cleanup
so revocation before the first row cannot create an orphan worksheet.
Filesystem errors exposed to callers are sanitized as `StorageUnavailable`.
No source record/audit is changed by export failure.

Temporary storage must have enough local capacity and suitable permissions for
the authorized data. The snapshot is local and ephemeral; it is not an export
archive, persistence store, shared cache or remotely retrievable file.

## Reproduce large measurements

Use the approved existing Python environment. No command installs dependencies
or starts an application listener, scheduler, company adapter or external API.
Keep project tests and other heavy workloads paused during timings, and keep
the measured runtime source unchanged. The harness rejects source changes.

```sh
python -B benchmarks/large_xlsx.py --source-root /path/to/immutable-v10 --output benchmarks/evidence/v11-local-before.json --rows 100000 200000 --warmups 1 --samples 3 --label v10-baseline
python -B benchmarks/large_xlsx.py --source-root . --output benchmarks/evidence/v11-local-after.json --rows 100000 200000 --warmups 1 --samples 3 --label v11-candidate
python -B benchmarks/compare_large_xlsx.py benchmarks/evidence/v11-local-before.json benchmarks/evidence/v11-local-after.json --output benchmarks/evidence/v11-local-comparison.json
python -B benchmarks/xlsx_disk_probe.py --source-root . --output benchmarks/evidence/v11-local-disk.json --rows 100000 200000
python -B -m unittest discover -s tests -p test_large_xlsx.py -v
```

Each output filename must be new: evidence is never overwritten. The published
v10 baseline is commit `e4a9681137f4762e4fcdcfee356351b7786a9420`.
All fixtures are deterministic synthetic values, seeded through authorized
500-row stage/import batches. Native process memory measurements use fresh
export children, separate from seed and validation children. Warmup children
warm OS/filesystem caches, not the Python heap of timed children.

Windows measurements use `GetProcessMemoryInfo` native peak working set and
peak commit counters, not only Python allocations or `tracemalloc`. Counters
are process-lifetime high-water marks: imports and service construction are
included and reported before/after each timed export. They do not include the
separate seed/validator processes. On Linux/macOS, the harness records the
platform's native `getrusage` maximum RSS and explicitly labels the method.
Operating-system file cache and other processes are outside this per-process
memory figure; reducing private row buffers trades some work for temporary I/O.

Wall time covers the service export call only. Every warmup and timed workbook
is validated in another process: all row values/order/counts, sheet/header,
numeric IDs/versions, seven literal-string cell types, formula/error strings,
Unicode and date-looking text must match a normalized fixture SHA256.
The default 50,000 cap is separately checked to reject both large datasets.

The disk probe is **untimed** and excluded from memory/latency samples. It
observes only exporter-owned snapshot/archive files and openpyxl's worksheet
cleanup boundary. Its maximum observed sum is a phase-based local-file size,
not an OS-wide disk high-water counter. It also verifies that owned handles
close and openpyxl's temporary-file registry returns to its prior state.

## Evidence and results

The immutable baseline is
[v11-baseline-large-xlsx.json](../../benchmarks/evidence/v11-baseline-large-xlsx.json).
Actual baseline: Windows 10 AMD64, Python 3.10.22, openpyxl 3.1.5 with existing
approved packages. Three descriptive samples per size plus one warmup; nearest
rank p95 equals the maximum of three, not a statistically robust SLA.

The matched-condition comparison passed. All eight workbooks per version
(one warmup plus three samples at each of two sizes) passed full semantic
validation. Default-cap checks rejected both oversized datasets. The candidate
source tree includes other v11 integration/deployment/template changes; the
source manifest records all of them. This harness invokes the direct legacy
QSL service, not the application, HTTP adapter, deployment code or templates.

| Rows | Before median | After median | Before p95 | After p95 | Before median peak RSS | After median peak RSS | RSS reduction |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 100,000 | 11.018 s | 10.829 s | 11.028 s | 10.942 s | 150.047 MiB | 43.551 MiB | 71.0% |
| 200,000 | 21.904 s | 23.679 s | 21.957 s | 24.210 s | 262.719 MiB | 47.703 MiB | 81.8% |

MiB means 1,048,576 bytes. Native peak commit medians also decreased from
136.605 to 29.129 MiB at 100,000 rows and from 250.941 to 33.336 MiB at 200,000.
Peak working set includes shared/native memory; commit and working-set figures
are distinct counters and must not be added together.

The 100,000-row median difference (-1.7%) is descriptive only. At 200,000 rows
the candidate was **8.1% slower** by median; its three samples were 21.606,
24.210 and 23.679 seconds. All samples are retained without retries, exclusions
or tuning after the result. The useful improvement is lower process memory,
with an observed time/I/O tradeoff. Three samples on one workstation do not
establish statistical significance, causality for each time difference or an SLA.

- [Raw baseline](../../benchmarks/evidence/v11-baseline-large-xlsx.json), SHA256
  `6cb5b94c9960a5369bd22d166c429776ec10e8e4a1d99b89fb273978100e022d`.
- [Raw candidate](../../benchmarks/evidence/v11-candidate-large-xlsx.json), SHA256
  `8da6d4a6fd72e21720ed88d78e22c26a2ef89263fbf014d61c57f3108fd094bc`.
- [Validated comparison](../../benchmarks/evidence/v11-large-xlsx-comparison.json).

The separately instrumented disk observations use the same normalized fixtures:

| Rows | Baseline observed temp peak | Candidate observed temp peak | Increase | Candidate snapshot file | Candidate final archive |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 100,000 | 50.386 MiB | 67.330 MiB | 33.6% | 13,452,531 bytes | 4,317,288 bytes |
| 200,000 | 101.936 MiB | 135.951 MiB | 33.4% | 27,016,164 bytes | 8,653,625 bytes |

The uncompressed worksheet XML sizes remain 52,833,357 and 106,887,900 bytes
respectively. The candidate adds the snapshot and file-backed archive to disk;
the baseline builds the archive in RAM. The peak is observed just before the
worksheet XML is removed, when the archive is not yet fully finalized, so the
sum of final file sizes is slightly higher than that phase observation.
These figures exclude the existing source SQLite database, filesystem metadata
and other processes. They measure logical file bytes, not allocated disk blocks.
All four diagnostic runs closed owned handles and restored openpyxl's
temporary-file registry. No worksheet or source payload is stored in the JSON.

- [Baseline disk observations](../../benchmarks/evidence/v11-baseline-xlsx-disk.json).
- [Candidate disk observations](../../benchmarks/evidence/v11-candidate-xlsx-disk.json).

Exact target Python 3.8.13/Linux kernel 3.10.0-1160, company adapters, real
network/browser memory, concurrent large export load and production storage
remain unmeasured by this harness.

## Focused behavior validation

On native Python 3.10.22, 13 tests in `tests/test_large_xlsx.py` pass. They cover
multiple batches, numeric/text/date-looking cells, optional blank-cell reimport,
literal filters and exact/overflow caps, target/deleted boundaries, independent
writer progress with snapshot consistency, actor substitution, revocation at
snapshot/serialization/ZIP/pre-first-row boundaries, sanitized snapshot/archive
disk failures, source immutability and temporary-file cleanup. The existing
30 legacy CRUD and five query/index contract tests also passed. Root owns the
complete regression, current-provider/UI HTTP tests and browser acceptance;
these focused results do not replace them.
