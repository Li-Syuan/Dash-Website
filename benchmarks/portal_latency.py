"""Offline, deterministic QSL service + real Flask/Dash dispatch benchmark.

Run from the repository root; --source-root can select an immutable baseline.
All SQLite state is temporary and created through authorized synthetic imports.
No network listener, external provider, scheduler, or browser is started.
"""
import argparse
import base64
import cProfile
import csv
import hashlib
import importlib.metadata
import io
import json
import math
import platform
import pstats
from pathlib import Path
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False).encode('utf-8')).hexdigest()


def callback_body(app, prefix, trigger, values=None):
    values = values or {}
    matches = [(key, spec) for key, spec in app.callback_map.items()
               if prefix in key and any(item['id'] == trigger for item in spec['inputs'])]
    if len(matches) != 1:
        raise AssertionError('Expected one registered callback')
    key, spec = matches[0]
    outputs = spec['output']
    many = isinstance(outputs, (tuple, list))
    outputs = outputs if many else [outputs]
    output_specs = [{'id': item.component_id, 'property': item.component_property}
                    for item in outputs]
    trigger_prop = next(item['property'] for item in spec['inputs'] if item['id'] == trigger)

    def value(item, default=None):
        return values.get(item['id'] + '.' + item['property'], values.get(item['id'], default))

    return {'output': key, 'outputs': output_specs if many else output_specs[0],
            'inputs': [dict(item, value=value(item, 1 if item['id'] == trigger else None))
                       for item in spec['inputs']],
            'state': [dict(item, value=value(item)) for item in spec['state']],
            'changedPropIds': [trigger + '.' + trigger_prop]}


def fixture_row(index):
    return dict(Material_Type='SYNTHETIC', Vendor_Code='BENCH-{:06d}'.format(index),
                Vendor_Name='Synthetic group {:03d}'.format(index % 100),
                Country=('TW', 'JP', 'DE', 'US')[index % 4],
                City='Synthetic city {:02d}'.format(index % 32), Rev='A',
                Supplier_Level='LEVEL 1')


def measure(function, validate, samples, warmups):
    for _ in range(warmups):
        validate(function())
    durations = []
    result = None
    for _ in range(samples):
        start = time.perf_counter_ns()
        result = function()
        durations.append((time.perf_counter_ns() - start) / 1000000)
        validate(result)
    profile = cProfile.Profile()
    profile.enable()
    result = function()
    profile.disable()
    validate(result)
    stats = pstats.Stats(profile)
    top = []
    for (filename, line, name), (primitive, calls, own, cumulative, _) in sorted(
            stats.stats.items(), key=lambda item: item[1][3], reverse=True)[:16]:
        top.append({'file': Path(filename).name, 'line': line, 'function': name,
                    'calls': calls, 'self_ms': round(own * 1000, 4),
                    'cumulative_ms': round(cumulative * 1000, 4)})
    return {'samples_ms': durations, 'median_ms': statistics.median(durations),
            'p95_ms': sorted(durations)[math.ceil(len(durations) * .95) - 1],
            'min_ms': min(durations), 'max_ms': max(durations), 'profile': top}, result


def run_dataset(size, samples, warmups):
    from reporting_workspace.application import create_app
    from reporting_workspace.config import Settings
    from reporting_workspace.lifecycle import dispose_app
    from reporting_workspace.legacy_crud import CSV_FIELDS
    import openpyxl

    actor = SimpleNamespace(id='demo-admin', orgcode='ORG_QA01',
                            is_authenticated=True, is_dev=True, is_admin=False)
    results = {}
    with tempfile.TemporaryDirectory(prefix='portal-benchmark-') as directory:
        server = create_app(Settings(secret_key='synthetic-benchmark-not-for-deployment',
                           state_path=str(Path(directory) / 'state.sqlite')))
        server.config['TESTING'] = True
        try:
            service = server.extensions['qa_demo_crud']
            # Preserve production creation/normalization/audit contracts in setup.
            for row in service.query(actor):
                service.delete(actor, row['id'], row['version'])
            fixture = [fixture_row(index) for index in range(size)]
            import_samples = []
            for start in range(0, size, 500):
                import_start = time.perf_counter_ns()
                token = service.stage_rows(actor, fixture[start:start + 500])
                imported = service.submit_stage(actor, token)
                import_samples.append((time.perf_counter_ns() - import_start) / 1000000)
                assert imported.committed and imported.failed == 0
            all_rows = service.query(actor, limit=service.max_export_rows)
            assert len(all_rows) == size
            expected = all_rows[:1000]
            filtered = [row for row in all_rows if 'group 007' in row['Vendor_Name']]
            app = server.extensions['dash_app']
            client = server.test_client()
            assert client.get('/').status_code == 200
            login = client.post('/_dash-update-component', json=callback_body(
                app, 'redirectHome.pathname', 'login-box',
                {'username-box': 'demo-admin', 'password-box': 'demo-only'}))
            assert login.status_code == 200
            assert login.get_json()['response']['login-alert']['is_open'] is False

            def rows_validator(wanted):
                def validate(rows):
                    assert rows == wanted
                return validate

            def csv_validator(content):
                rows = list(csv.DictReader(io.StringIO(content.decode('utf-8-sig'))))
                assert rows == [{key: str(row[key]) for key in CSV_FIELDS} for row in all_rows]

            def xlsx_validator(content):
                # Full semantic XLSX validation is done below, outside timings;
                # each timed sample still has to be a complete ZIP workbook.
                assert isinstance(content, bytes) and content.startswith(b'PK')

            definitions = [
                ('service_query_page', lambda: service.query(actor), rows_validator(expected)),
                ('service_query_filter', lambda: service.query(actor, {'Vendor_Name': 'group 007'}),
                 rows_validator(filtered)),
                ('service_export_csv', lambda: service.export_csv(actor), csv_validator),
                ('service_export_xlsx', lambda: service.export_xlsx(actor), xlsx_validator),
            ]
            for name, function, validate in definitions:
                result, value = measure(function, validate, samples, warmups)
                if isinstance(value, bytes):
                    result['response_bytes'] = len(value)
                if name.endswith('xlsx'):
                    workbook = openpyxl.load_workbook(io.BytesIO(value), read_only=True,
                                                      data_only=False, keep_links=False)
                    try:
                        rows = list(workbook.active.iter_rows(values_only=True))
                        assert rows[0] == CSV_FIELDS
                        assert rows[1:] == [tuple(row[key] for key in CSV_FIELDS) for row in all_rows]
                    finally:
                        workbook.close()
                results[name] = result

            for trigger, name in [('qa-read', 'callback_query_page'),
                                  ('qa-export', 'callback_export_csv'),
                                  ('qa-export-xlsx', 'callback_export_xlsx')]:
                body = callback_body(app, 'qa-status.children', trigger, {'qa-filter': ''})

                def dispatch():
                    return client.post('/_dash-update-component', json=body)

                def validate(response):
                    assert response.status_code == 200
                    payload = response.get_json()['response']
                    assert payload['qa-table']['data'] == expected
                    if trigger == 'qa-export':
                        csv_validator(base64.b64decode(payload['qa-download']['data']['content']))
                    elif trigger == 'qa-export-xlsx':
                        xlsx_validator(base64.b64decode(payload['qa-download']['data']['content']))

                result, value = measure(dispatch, validate, samples, warmups)
                result['response_bytes'] = len(value.data)
                results[name] = result

            # Explain the real SQL predicates/order; do not force an index.
            sql = ('SELECT id,version,' + ','.join(CSV_FIELDS[2:]) +
                   ' FROM legacy_qsl_records WHERE target=? AND deleted=0' +
                   ' AND instr(Vendor_Name,?)>0 ORDER BY id LIMIT ? OFFSET ?')
            plan = [row[3] for row in service._db.execute('EXPLAIN QUERY PLAN ' + sql,
                    [service.target, 'group 007', 1000, 0]).fetchall()]
            database_bytes = service._db.execute('PRAGMA page_count').fetchone()[0] * service._db.execute('PRAGMA page_size').fetchone()[0]
            return {'active_rows': size, 'deleted_seed_rows': 2, 'fixture_sha256': digest(fixture),
                    'normalized_rows_sha256': digest(all_rows), 'filtered_rows': len(filtered),
                    'database_bytes': database_bytes,
                    'setup_import': {'batch_rows': 500, 'samples_ms': import_samples,
                                     'total_ms': sum(import_samples),
                                     'median_batch_ms': statistics.median(import_samples),
                                     'scope': 'one growing dataset setup, not steady-state write throughput'},
                    'query_plan': plan, 'measurements': results}
        finally:
            dispose_app(server)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--rows', type=int, nargs='+', default=[5000, 25000])
    parser.add_argument('--samples', type=int, default=15)
    parser.add_argument('--warmups', type=int, default=3)
    parser.add_argument('--label', default='candidate')
    args = parser.parse_args()
    if args.samples < 3 or args.warmups < 1 or any(size < 1000 or size > 50000 for size in args.rows):
        parser.error('Use at least 3 samples, 1 warmup, and 1000..50000 rows')
    source = args.source_root.resolve()
    sys.path.insert(0, str(source))
    packages = ['dash', 'Flask', 'Werkzeug', 'Flask-Login', 'plotly', 'openpyxl',
                'dash-bootstrap-components', 'dash-mantine-components', 'setuptools']
    result = {'schema': 1, 'label': args.label, 'python': platform.python_version(),
              'platform': platform.system() + ' ' + platform.release(),
              'machine': platform.machine(), 'sqlite': sqlite3.sqlite_version,
              'packages': {name: importlib.metadata.version(name) for name in packages},
              'samples': args.samples, 'warmups': args.warmups,
              'timer': 'time.perf_counter_ns; monotonic elapsed, milliseconds',
              'p95_method': 'nearest rank; no interpolation',
              'measurement_scope': 'synchronous service calls and real Flask test-client Dash HTTP dispatch; not browser/network latency',
              'seed_method': 'authorized synthetic stage_rows + submit_stage; setup outside timings',
              'harness_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'source_hashes': {path.relative_to(source).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                                for path in sorted((source / 'reporting_workspace').rglob('*.py'))},
              'datasets': []}
    try:
        result['git_head'] = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'],
                                                     text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        result['git_head'] = None
    for size in args.rows:
        result['datasets'].append(run_dataset(size, args.samples, args.warmups))
        print(json.dumps({'completed_rows': size, 'label': args.label}), flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        raise SystemExit('Refusing to overwrite existing evidence')
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': 'complete', 'output': args.output.name}), flush=True)


if __name__ == '__main__':
    main()
