"""Bounded synthetic quickstart drill; never launches app.py or a network listener.

All state is freshly generated in a NEW destination. No input database, company
provider, environment-derived settings, marker removal or activation is supported.
Existing deployment backup/restore contracts remain the authority.
"""
import argparse
from contextlib import closing
import csv
import io
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

RELEASE = 'quickstart-synthetic-v1'


def _require(condition, code):
    if not condition:
        raise DrillError(code)


class DrillError(RuntimeError):
    """Fixed diagnostic codes, never raw exception messages or private paths."""


class _SafeParser(argparse.ArgumentParser):
    def error(self, message):
        raise DrillError('arguments_invalid')


def _write_json(path, value):
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write('\n')


def _snapshot(state_path):
    from reporting_workspace.deployment import _layout, _logical_digest, _open
    result = {}
    for name, (path, kind) in _layout(state_path, 'demo').items():
        with closing(_open(path)) as connection:
            result[name] = _logical_digest(connection)
    return result


def _login_and_route(server):
    """Real Flask in-process transport; no socket or browser rendering claimed."""
    client = server.test_client()
    _require(client.get('/healthz').status_code == 200, 'liveness_failed')
    ready = client.get('/readyz')
    _require(ready.status_code == 200 and ready.get_json()['scheduler'] == 'not-started',
             'readiness_failed')
    _require(client.get('/login').status_code == 200, 'login_page_failed')
    response = client.post('/_dash-update-component', json={
        'output': '..redirectHome.pathname...login-alert.is_open..',
        'outputs': [{'id': 'redirectHome', 'property': 'pathname'},
                    {'id': 'login-alert', 'property': 'is_open'}],
        'inputs': [{'id': 'username-box', 'property': 'value', 'value': 'demo-admin'},
                   {'id': 'password-box', 'property': 'value', 'value': 'demo-only'},
                   {'id': 'login-box', 'property': 'n_clicks', 'value': 1}],
        'state': [], 'changedPropIds': ['login-box.n_clicks'],
    })
    _require(response.status_code == 200, 'login_callback_failed')
    payload = response.get_json()['response']
    _require(payload['redirectHome']['pathname'] == '/' and
             payload['login-alert']['is_open'] is False, 'login_rejected')
    response = client.post('/_dash-update-component', json={
        'output': '.._pages_content.children..._pages_store.data..',
        'outputs': [{'id': '_pages_content', 'property': 'children'},
                    {'id': '_pages_store', 'property': 'data'}],
        'inputs': [{'id': '_pages_location', 'property': 'pathname',
                    'value': '/QA_portal/quality-actions'},
                   {'id': '_pages_location', 'property': 'search', 'value': ''}],
        'state': [], 'changedPropIds': ['_pages_location.pathname'],
    })
    _require(response.status_code == 200 and
             'quality-actions-table' in response.get_data(as_text=True), 'report_route_failed')


def run_drill(destination, as_of='2026-10-05'):
    # Lazy imports keep --help harmless; CLI catches missing dependencies safely.
    from reporting_workspace.application import create_app
    from reporting_workspace.config import Settings
    from reporting_workspace.deployment import (RESTORE_MARKER, _local_path,
                                                backup_set, restore_set)
    from reporting_workspace.lifecycle import dispose_app
    from reporting_workspace.quality_actions import (ActionReport, Query,
                                                     SyntheticActionRepository)
    from reporting_workspace.state import StateStore

    query = Query.parse({'as_of': as_of, 'limit': 100})  # Validate before writing.
    destination = _local_path(destination)
    _require(destination.parent.is_dir(), 'destination_parent_missing')
    _require(not destination.exists(), 'destination_exists')
    try:
        destination.mkdir(mode=0o700)
    except FileExistsError:
        raise DrillError('destination_exists') from None
    _write_json(destination / 'INCOMPLETE.json', {'status': 'incomplete-synthetic-drill'})
    _write_json(destination / 'SYNTHETIC_DRILL_DO_NOT_DEPLOY.json', {
        'synthetic_only': True, 'activation': 'forbidden',
        'source_after_drill': 'intentionally-incompatible-for-rollback-demonstration',
        'restored_state': 'quarantined-review-marker-retained',
    })
    source = destination / 'source'
    source.mkdir(mode=0o700)
    state_path = source / 'workspace.sqlite'
    server = None
    try:
        server = create_app(Settings(mode='demo', state_path=str(state_path),
                                     enable_quality_actions=True))
        _require(all(server.extensions[name]._thread is None
                     for name in ('job_monitor', 'etl_dispatch')), 'worker_started')
        _login_and_route(server)
        runtime = server.extensions['workspace']
        actor = runtime.identities.get_user('demo-admin')
        report = ActionReport(runtime.identities, SyntheticActionRepository())
        result = report.query(actor, {'as_of': query.as_of, 'limit': 100})
        content = report.export_csv(actor, {'as_of': query.as_of})
        rows = list(csv.DictReader(io.StringIO(content)))
        _require(len(rows) == result['total'], 'csv_row_count_mismatch')
        _require(all(row['finding'].startswith('A synthetic') for row in rows),
                 'synthetic_tenant_mismatch')
        with (destination / 'synthetic-corrective-actions.csv').open(
                'x', encoding='utf-8', newline='') as stream:
            stream.write(content)
        runtime.state.claim_job('quickstart-before-snapshot', 'synthetic', 'synthetic-owner')
        lease = runtime.state.acquire('quickstart-fence', 'synthetic-owner', ttl=60)
        server.extensions['etl_dispatch'].configure(actor, 'synthetic-sales-daily', True, 60)
    finally:
        if server is not None:
            dispose_app(server)
    # All resources owned by this drill are now disposed; no external writers exist.
    before = _snapshot(state_path)
    backup = destination / 'backup'
    manifest = backup_set(state_path, backup, RELEASE, profile='demo', quiesced=True)
    _require(len(manifest['members']) == 8, 'unexpected_store_count')
    # Deliberately preserve a later receipt and incompatible schema in source.
    # Restore must leave this source untouched and use a new destination.
    StateStore(state_path).claim_job('quickstart-after-snapshot', 'synthetic', 'synthetic-owner')
    with closing(sqlite3.connect(str(state_path))) as connection:
        connection.execute('PRAGMA user_version=999')
        connection.commit()
    changed = _snapshot(state_path)
    _require(changed['workspace.sqlite'] != before['workspace.sqlite'], 'source_mutation_missing')
    restored = destination / 'restored'
    restore_set(backup, restored, RELEASE)
    after = _snapshot(restored / 'workspace.sqlite')
    _require(after == before, 'restored_content_mismatch')
    _require(_snapshot(state_path) == changed, 'source_changed_by_restore')
    with closing(sqlite3.connect(str(state_path))) as connection:
        _require(connection.execute('PRAGMA user_version').fetchone()[0] == 999,
                 'source_schema_was_downgraded')
        _require(connection.execute('SELECT COUNT(*) FROM job_runs').fetchone()[0] == 2,
                 'post_snapshot_receipt_lost')
    with closing(sqlite3.connect(str(restored / 'workspace.sqlite'))) as connection:
        _require(connection.execute('SELECT COUNT(*) FROM job_runs').fetchone()[0] == 1,
                 'restored_receipt_mismatch')
        _require(connection.execute('SELECT fencing FROM leases WHERE key=?',
                                    ('quickstart-fence',)).fetchone()[0] == lease.fencing,
                 'restored_fence_mismatch')
    with closing(sqlite3.connect(str(restored / 'workspace.sqlite.etl.sqlite'))) as connection:
        _require(connection.execute("SELECT enabled FROM etl_config WHERE job_id='synthetic-sales-daily'")
                 .fetchone()[0] == 1, 'restored_schedule_mismatch')
    _require((restored / RESTORE_MARKER).is_file(), 'restore_review_marker_missing')
    try:
        unexpected_server = create_app(Settings(state_path=str(restored / 'workspace.sqlite')))
    except ValueError as error:
        _require('offline reconciliation' in str(error), 'restore_startup_guard_unexpected_error')
    else:
        dispose_app(unexpected_server)
        raise DrillError('restore_startup_guard_failed')
    _require(_snapshot(restored / 'workspace.sqlite') == before, 'blocked_start_changed_state')
    evidence = dict(
        status='passed-synthetic-offline-drill', as_of=query.as_of,
        report_rows=len(rows), report_summary=result['summary'],
        transport='flask-test-client-only', listener='not-started', browser='not-run',
        windows='not-certified', external_integrations='not-contacted',
        python='.'.join(str(value) for value in sys.version_info[:3]),
        members=len(after), all_restored_logical_digests_equal=True,
        logical_sha256=after, source_after_snapshot_receipt_preserved=True,
        source_future_schema_preserved=True, restored_claim_fence_schedule_preserved=True,
        restore_startup_guard='blocked-as-required', restore_marker='retained',
        release=RELEASE,
    )
    _write_json(destination / 'evidence.json', evidence)
    (destination / 'INCOMPLETE.json').unlink()
    return evidence


def main(argv=None):
    parser = _SafeParser(description=__doc__)
    parser.add_argument('--destination', required=True, help='NEW absolute local directory; existing paths are refused.')
    parser.add_argument('--as-of', default='2026-10-05', help='Explicit report date YYYY-MM-DD; fixture default 2026-10-05.')
    try:
        args = parser.parse_args(argv)
        result = run_drill(args.destination, args.as_of)
    except DrillError as error:
        result, code = {'status': 'failed', 'code': str(error)}, 2
    except Exception:
        result, code = {'status': 'failed', 'code': 'synthetic_drill_failed'}, 2
    else:
        code = 0
    print(json.dumps(result, sort_keys=True))
    return code


if __name__ == '__main__':
    sys.exit(main())
