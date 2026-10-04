"""Offline deployment checks and coherent, explicitly quiesced SQLite snapshots.

No provider calls, app construction, worker startup, SQL migration or external
effects occur here. Backups/restores only create new directories. The supported
layout is this repository's main store and its explicitly known demo siblings.
"""
from contextlib import ExitStack, closing
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time

from .config import Settings
from .providers import validate_providers
from .state import SCHEMA_VERSION, StateStore, _ddl_tokens


FORMAT_VERSION = 1
RESTORE_MARKER = 'RESTORE_REVIEW_REQUIRED.json'
_MAIN = 'workspace.sqlite'
_REQUIRED = {
    _MAIN: 'state',
    _MAIN + '.qa/qsl.sqlite': 'qsl',
    _MAIN + '.qa/jobs.sqlite': 'jobs',
    _MAIN + '.qa/report_builder.sqlite': 'builder',
    _MAIN + '.operations/operations.sqlite': 'operations',
    _MAIN + '.operations/job_monitor.sqlite': 'monitor',
    _MAIN + '.operations/job_monitor.sqlite.leases.sqlite': 'state',
    _MAIN + '.etl.sqlite': 'etl',
}
_OPTIONAL = {_MAIN + '.operations/fixture-sqlite-' + letter + '.sqlite': 'fixture-' + letter
             for letter in 'abc'}
_CONTRACT_FILES = ('deployment.py', 'state.py', 'admin_schema.py',
                   'legacy_crud.py', 'legacy_jobs.py', 'report_builder.py',
                   'operations.py', 'job_monitor.py', 'etl_dispatch.py',
                   'etl_adapters.py', 'maintenance_registry.py')

# Legacy services have no strict shared schema validator. These exact reviewed
# DDL contracts make missing constraints/indexes/triggers fail closed as well.
_LEGACY_DDL = {
    'qsl': '''
CREATE TABLE legacy_qsl_records (
 id INTEGER PRIMARY KEY AUTOINCREMENT, target TEXT NOT NULL,
 Material_Type TEXT NOT NULL, Vendor_Code TEXT NOT NULL, Vendor_Name TEXT NOT NULL,
 Country TEXT NOT NULL, City TEXT NOT NULL, Rev TEXT NOT NULL, Supplier_Level TEXT NOT NULL,
 version INTEGER NOT NULL DEFAULT 1, deleted INTEGER NOT NULL DEFAULT 0);
CREATE UNIQUE INDEX legacy_qsl_unique_active ON legacy_qsl_records
 (target, Material_Type, Vendor_Code, Vendor_Name, Country, City) WHERE deleted=0;
CREATE INDEX legacy_qsl_active_order ON legacy_qsl_records(target, id) WHERE deleted=0;
CREATE TABLE legacy_qsl_audit (id INTEGER PRIMARY KEY AUTOINCREMENT, target TEXT NOT NULL,
 actor TEXT NOT NULL, action TEXT NOT NULL, record_id INTEGER NOT NULL,
 version INTEGER NOT NULL, changed_fields TEXT NOT NULL, at REAL NOT NULL);
CREATE TABLE legacy_qsl_stages (token_hash TEXT PRIMARY KEY, owner TEXT NOT NULL,
 target TEXT NOT NULL, expires REAL NOT NULL, consumed INTEGER NOT NULL DEFAULT 0,
 payload TEXT NOT NULL);
CREATE TABLE legacy_qsl_revisions (target TEXT NOT NULL, record_id INTEGER NOT NULL,
 version INTEGER NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, at REAL NOT NULL,
 changed_fields TEXT NOT NULL, source_version INTEGER, snapshot TEXT NOT NULL,
 PRIMARY KEY(target,record_id,version));
CREATE TRIGGER legacy_qsl_revisions_no_update BEFORE UPDATE ON legacy_qsl_revisions BEGIN
 SELECT RAISE(ABORT, 'Revision history is immutable.'); END;
CREATE TRIGGER legacy_qsl_revisions_no_delete BEFORE DELETE ON legacy_qsl_revisions BEGIN
 SELECT RAISE(ABORT, 'Revision history is immutable.'); END;
CREATE TABLE legacy_qsl_previews (token_hash TEXT PRIMARY KEY, owner TEXT NOT NULL,
 target TEXT NOT NULL, action TEXT NOT NULL, record_id INTEGER NOT NULL,
 expected_version INTEGER NOT NULL, source_version INTEGER, expires REAL NOT NULL,
 consumed INTEGER NOT NULL DEFAULT 0, payload TEXT NOT NULL);
''',
    'jobs': '''
CREATE TABLE legacy_job_settings (job_id TEXT PRIMARY KEY, settings TEXT NOT NULL,
 version INTEGER NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE legacy_job_runs (run_id TEXT PRIMARY KEY, job_id TEXT NOT NULL,
 run_key TEXT NOT NULL, actor TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL,
 settings TEXT NOT NULL, retry_of TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(job_id, run_key));
CREATE UNIQUE INDEX legacy_retry_once ON legacy_job_runs(retry_of) WHERE retry_of IS NOT NULL;
CREATE TABLE legacy_job_audit (id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL,
 action TEXT NOT NULL, job_id TEXT NOT NULL, run_id TEXT, detail TEXT NOT NULL, created_at TEXT NOT NULL);
''',
    'builder': '''
CREATE TABLE report_builder_schema (singleton INTEGER PRIMARY KEY CHECK(singleton=1), version INTEGER NOT NULL);
CREATE TABLE report_builder_definitions (id INTEGER PRIMARY KEY AUTOINCREMENT,
 owner_id TEXT NOT NULL, owner_org TEXT NOT NULL, name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 120),
 version INTEGER NOT NULL CHECK(version>=1), definition TEXT NOT NULL, UNIQUE(owner_id,owner_org,name));
''',
}


class DeploymentError(RuntimeError):
    """Safe diagnostic code only; never include paths, database rows or secrets."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _local_path(value, exists=False):
    path = Path(value)
    if (not path.is_absolute() or str(path).startswith(('//', '\\\\')) or
            any(part == '..' for part in path.parts)):
        raise DeploymentError('absolute_local_path_required')
    # Do not follow a symlink into a different source/destination tree.
    if any(item.is_symlink() for item in (path,) + tuple(path.parents)):
        raise DeploymentError('symlink_not_supported')
    path = path.resolve()
    if exists and not path.is_file():
        raise DeploymentError('required_store_missing')
    return path


def assert_restore_reviewed(state_path):
    if state_path is None:
        return
    try:
        blocked = False
        for name in (RESTORE_MARKER, 'INCOMPLETE.json'):
            try:
                (Path(state_path).parent / name).lstat()
                blocked = True
            except FileNotFoundError:
                pass
    except (OSError, TypeError, ValueError):
        blocked = True
    if blocked:
        raise ValueError('Restored state requires offline reconciliation before application startup.')


def configuration_preflight(environ, identity_provider=None, report_provider=None):
    """Validate explicit configuration/provider shape without opening any stores.

    Production providers must be passed as actual instances by trusted caller
    code. This never authenticates or queries a provider and is not certification.
    """
    try:
        settings = Settings.from_env(environ)
        assert_restore_reviewed(settings.state_path)
        validate_providers(settings, identity_provider, report_provider)
        if settings.state_path:
            path = _local_path(settings.state_path)
            if not path.parent.is_dir():
                raise DeploymentError('state_parent_missing')
            if path.exists() and not path.is_file():
                raise DeploymentError('state_file_invalid')
    except Exception:
        return dict(status='invalid', code='configuration_or_provider_contract_invalid')
    return dict(status='valid', mode=settings.mode,
                storage='sqlite-local' if settings.state_path else 'process-memory',
                external_integrations='not-probed', exact_target_platform='not-certified')


def _objects(connection):
    return {(row['type'], row['name']): _ddl_tokens(row['sql']) for row in connection.execute(
        "SELECT type,name,sql FROM sqlite_master WHERE name NOT GLOB 'sqlite_*'")}


@lru_cache(maxsize=128)
def _reference_objects(script):
    with closing(sqlite3.connect(':memory:')) as connection:
        connection.row_factory = sqlite3.Row
        connection.executescript(script)
        return _objects(connection)


def _fixture_ddl(connection, kind):
    names = [row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT GLOB 'sqlite_*'")]
    tables = sorted(name for name in names if name != 'maintenance_audit')
    valid = {'synthetic_maintenance_{:03d}'.format(number)
             for number in range(1, 76) if 'abc'[(number - 1) % 3] == kind[-1]}
    if not tables or not set(tables) <= valid or 'maintenance_audit' not in names:
        raise DeploymentError('incompatible_fixture_schema')
    script = '''CREATE TABLE maintenance_audit (
id INTEGER PRIMARY KEY AUTOINCREMENT, definition_key TEXT NOT NULL,
org TEXT NOT NULL, actor_hash TEXT NOT NULL, action TEXT NOT NULL,
record_id INTEGER NOT NULL, version INTEGER NOT NULL, changed_fields TEXT NOT NULL, at REAL NOT NULL);'''
    for table in tables:
        index = 'maintenance_unique_' + hashlib.sha256((table + ':code').encode('ascii')).hexdigest()[:24]
        script += '''CREATE TABLE "{}" ("id" INTEGER PRIMARY KEY AUTOINCREMENT,
"code" TEXT NOT NULL,"description" TEXT,"enabled" INTEGER,org TEXT NOT NULL,
version INTEGER NOT NULL DEFAULT 1,deleted INTEGER NOT NULL DEFAULT 0 CHECK(deleted IN (0,1)));
CREATE UNIQUE INDEX "{}" ON "{}"(org,"code") WHERE deleted=0;'''.format(table, index, table)
    return script


def _validate(connection, kind, full=False):
    version = connection.execute('PRAGMA user_version').fetchone()[0]
    if connection.execute('PRAGMA journal_mode').fetchone()[0].lower() not in ('delete', 'truncate', 'persist'):
        raise DeploymentError('unsupported_journal_mode')
    if kind == 'state':
        if version != SCHEMA_VERSION:
            raise DeploymentError('incompatible_state_version')
        StateStore._validate_schema(connection)
    elif kind == 'operations':
        from .operations import OperationsService
        OperationsService._validate_schema(connection)
    elif kind == 'monitor':
        from .job_monitor import JobMonitor
        JobMonitor._validate_schema(connection)
    elif kind == 'etl':
        from .etl_dispatch import ETLDispatch
        from .etl_adapters import default_registry
        ETLDispatch._validate_schema(connection)
        expected = {job.job_id: (job.org, job.signature) for job in default_registry().snapshot().values()}
        actual = {row['job_id']: (row['org'], row['signature']) for row in connection.execute('SELECT job_id,org,signature FROM etl_config')}
        if actual != expected:
            raise DeploymentError('incompatible_etl_registry')
    else:
        if version != 0:
            raise DeploymentError('incompatible_legacy_version')
        script = _fixture_ddl(connection, kind) if kind.startswith('fixture-') else _LEGACY_DDL[kind]
        if _objects(connection) != _reference_objects(script):
            raise DeploymentError('incompatible_legacy_schema')
        if kind == 'builder' and [tuple(row) for row in connection.execute(
                'SELECT singleton,version FROM report_builder_schema')] != [(1, 1)]:
            raise DeploymentError('incompatible_builder_version')
    if full:
        if [row[0] for row in connection.execute('PRAGMA integrity_check')] != ['ok']:
            raise DeploymentError('sqlite_integrity_failed')
        if connection.execute('PRAGMA foreign_key_check').fetchone() is not None:
            raise DeploymentError('sqlite_foreign_key_failed')
    # A bounded metadata read also forces a missing/locked database to fail.
    connection.execute('SELECT name FROM sqlite_master LIMIT 1').fetchone()
    return dict(user_version=version, schema_sha256=_digest_json(
        sorted((kind, name, tokens) for (kind, name), tokens in _objects(connection).items())))


def _open(path, write=False, timeout=.2):
    path = _local_path(path, exists=True)
    connection = sqlite3.connect(path.as_uri() + ('?mode=rw' if write else '?mode=ro'),
                                 uri=True, isolation_level=None, timeout=timeout)
    connection.row_factory = sqlite3.Row
    if not write:
        connection.execute('PRAGMA query_only=ON')
    return connection


def _layout(state_path, profile):
    if profile not in ('demo', 'production'):
        raise DeploymentError('unsupported_layout_profile')
    main = _local_path(state_path, exists=True)
    expected = _REQUIRED if profile == 'demo' else {_MAIN: 'state'}
    available = dict(expected)
    paths = {name: main.parent / (main.name + name[len(_MAIN):]) for name in dict(_REQUIRED, **_OPTIONAL)}
    for name in expected:
        _local_path(paths[name], exists=True)
    if profile == 'production' and any(path.exists() for name, path in paths.items() if name != _MAIN):
        raise DeploymentError('unexpected_demo_siblings')
    if profile == 'demo':
        for name, kind in _OPTIONAL.items():
            if paths[name].exists():
                _local_path(paths[name], exists=True)
                available[name] = kind
        allowed = {str(path) for path in paths.values()}
        for directory in (Path(str(main) + '.qa'), Path(str(main) + '.operations')):
            _local_path(directory)
            for entry in directory.iterdir():
                if entry.is_symlink() or entry.is_dir():
                    raise DeploymentError('unexpected_sibling_entry')
                if str(entry) not in allowed and not any(
                        str(entry) == base + suffix for base in allowed for suffix in ('-journal', '-wal', '-shm')):
                    raise DeploymentError('unexpected_sibling_entry')
    return {name: (paths[name], kind) for name, kind in sorted(available.items())}


def _runtime_stores(server):
    extensions = server.extensions
    runtime = extensions['workspace']
    if runtime.settings.state_path:
        return _layout(runtime.settings.state_path, 'demo' if runtime.is_demo else 'production')
    stores = {}
    if runtime.is_demo:
        directory = Path(extensions['qa_demo_directory'])
        for name, kind in (('qsl.sqlite', 'qsl'), ('jobs.sqlite', 'jobs'), ('report_builder.sqlite', 'builder')):
            stores['qa/' + name] = (directory / name, kind)
        operations = Path(extensions['operations_service'].path)
        stores['operations'] = (operations, 'operations')
        monitor = extensions['job_monitor']
        stores['monitor'] = (Path(monitor.path), 'monitor')
        stores['monitor-leases'] = (Path(monitor.lease_path), 'state')
        stores['etl'] = (Path(extensions['etl_dispatch'].path), 'etl')
        for letter in 'abc':
            path = operations.parent / ('fixture-sqlite-' + letter + '.sqlite')
            if path.exists():
                stores['fixture-' + letter] = (path, 'fixture-' + letter)
    return stores


def liveness_status(server):
    if server.extensions.get('_workspace_disposed'):
        return dict(status='unavailable'), 503
    runtime = server.extensions['workspace']
    return dict(status='ok', mode='offline-demo' if runtime.is_demo else 'configured'), 200


def _persistent_connection_ready(resource):
    # A fresh connection can read the file even when the active service's own
    # connection was closed. Probe that connection under its normal lock, with
    # a bounded wait and a constant SELECT that neither touches rows nor writes.
    lock = resource._lock
    if not lock.acquire(timeout=.05):
        raise DeploymentError('resource_busy')
    try:
        resource._db.execute('SELECT 1').fetchone()
    finally:
        lock.release()


def readiness_status(server):
    try:
        extensions = server.extensions
        runtime = extensions['workspace']
        assert_restore_reviewed(runtime.settings.state_path)
        if extensions.get('_workspace_disposed'):
            raise DeploymentError('disposed')
        if runtime.is_demo:
            for key in ('qa_demo_crud', 'qa_demo_builder', 'qa_demo_jobs',
                        'operations_service', 'job_monitor', 'etl_dispatch', 'maintenance_registry'):
                resource = extensions[key]
                if getattr(resource, 'available', True) is False or getattr(resource, '_worker_error_code', None):
                    raise DeploymentError('resource_unavailable')
            for key in ('qa_demo_crud', 'qa_demo_builder'):
                _persistent_connection_ready(extensions[key])
        for path, kind in _runtime_stores(server).values():
            with closing(_open(path, timeout=.05)) as connection:
                _validate(connection, kind)
        threads = [getattr(extensions.get(key), '_thread', None)
                   for key in ('job_monitor', 'etl_dispatch')]
        running = any(thread is not None and thread.is_alive() for thread in threads)
        return dict(status='ready', storage='sqlite-local' if runtime.state else 'process-memory',
                    scheduler='running' if running else 'not-started'), 200
    except Exception:
        return dict(status='unavailable'), 503


def _digest_json(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _contracts():
    directory = Path(__file__).parent
    return {name: hashlib.sha256((directory / name).read_text(encoding='utf-8').encode('utf-8')).hexdigest()
            for name in _CONTRACT_FILES}


def _write_json(path, value):
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def _new_directory(destination):
    destination = _local_path(destination)
    if not destination.parent.is_dir():
        raise DeploymentError('destination_parent_missing')
    try:
        destination.mkdir(mode=0o700)
    except FileExistsError:
        raise DeploymentError('destination_exists') from None
    _write_json(destination / 'INCOMPLETE.json', dict(status='incomplete', format_version=FORMAT_VERSION))
    return destination


def _backup_one(source, destination, deadline):
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(str(destination), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    def progress(status, remaining, total):
        if time.monotonic() > deadline:
            raise DeploymentError('backup_deadline_exceeded')
    with closing(_open(source)) as reader, closing(sqlite3.connect(str(destination))) as writer:
        reader.backup(writer, pages=128, progress=progress, sleep=.01)


def _version_label(value):
    if not isinstance(value, str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9._-]{0,79}', value):
        raise DeploymentError('release_label_invalid')
    return value


def backup_set(state_path, destination, release, profile='demo', quiesced=False, timeout=60):
    """Backup a stopped set under all writer locks; a complete manifest is last.

    The explicit assertion covers writers outside this process. Locks prevent
    database writes during capture but cannot prove the operator stopped every
    process or coordinated arbitrary external effects.
    """
    if quiesced is not True:
        raise DeploymentError('explicit_quiescence_required')
    release = _version_label(release)
    if type(timeout) not in (int, float) or not 1 <= timeout <= 300:
        raise DeploymentError('timeout_invalid')
    destination = None if destination is None else _local_path(destination)
    try:
        stores = _layout(state_path, profile)
        main = stores[_MAIN][0]
        for directory in (Path(str(main) + '.qa'), Path(str(main) + '.operations')):
            if destination == directory or directory in destination.parents:
                raise DeploymentError('destination_overlaps_source')
        target = _new_directory(destination)
        deadline = time.monotonic() + timeout
        members = []
        with ExitStack() as stack:
            locks = {}
            for name, (path, kind) in stores.items():
                connection = stack.enter_context(closing(_open(path, write=True)))
                if time.monotonic() > deadline:
                    raise DeploymentError('backup_deadline_exceeded')
                connection.execute('BEGIN IMMEDIATE')
                locks[name] = connection
            for name, (path, kind) in stores.items():
                metadata = _validate(locks[name], kind, full=True)
                output = target / name
                _backup_one(path, output, deadline)
                with closing(_open(output)) as copy:
                    if _validate(copy, kind, full=True) != metadata:
                        raise DeploymentError('backup_validation_mismatch')
                members.append(dict(name=name, kind=kind, size_bytes=output.stat().st_size,
                                    sha256=_hash(output), **metadata))
            if _layout(state_path, profile) != stores:
                raise DeploymentError('layout_changed_during_backup')
            manifest = dict(format_version=FORMAT_VERSION, status='complete', profile=profile,
                            declared_release=release, storage_contracts=_contracts(), members=members,
                            absent_optional=sorted(set(_OPTIONAL) - set(stores)) if profile == 'demo' else [],
                            claims_and_fences='preserved-no-replay', quiescence='operator-asserted-and-writer-locked',
                            external_effects='not-certified')
            _write_json(target / 'manifest.json', manifest)
        # Only an owned marker is removed; no source or user tree is deleted.
        (target / 'INCOMPLETE.json').unlink()
        return manifest
    except DeploymentError:
        raise
    except Exception:
        raise DeploymentError('backup_unavailable_or_incompatible') from None


def _read_manifest(directory):
    directory = _local_path(directory)
    path = directory / 'manifest.json'
    if ((directory / 'INCOMPLETE.json').exists() or not path.is_file() or
            path.is_symlink() or path.stat().st_size > 256 * 1024):
        raise DeploymentError('incomplete_or_missing_manifest')
    with path.open(encoding='utf-8') as stream:
        manifest = json.load(stream)
    if (manifest.get('format_version') != FORMAT_VERSION or manifest.get('status') != 'complete' or
            manifest.get('profile') not in ('demo', 'production') or
            manifest.get('storage_contracts') != _contracts()):
        raise DeploymentError('incompatible_backup_contract')
    _version_label(manifest.get('declared_release'))
    required = _REQUIRED if manifest['profile'] == 'demo' else {_MAIN: 'state'}
    possible = dict(_REQUIRED, **_OPTIONAL) if manifest['profile'] == 'demo' else required
    members = manifest.get('members')
    if not isinstance(members, list) or not len(required) <= len(members) <= len(possible):
        raise DeploymentError('backup_member_set_invalid')
    names = [member.get('name') for member in members if isinstance(member, dict)]
    if len(names) != len(members) or len(set(names)) != len(names) or not set(required) <= set(names) <= set(possible):
        raise DeploymentError('backup_member_set_invalid')
    expected_absent = sorted(set(_OPTIONAL) - set(names)) if manifest['profile'] == 'demo' else []
    if manifest.get('absent_optional') != expected_absent:
        raise DeploymentError('backup_member_set_invalid')
    for member in members:
        if member.get('kind') != possible[member['name']]:
            raise DeploymentError('backup_member_kind_invalid')
        path = _local_path(directory / member['name'], exists=True)
        if (type(member.get('size_bytes')) is not int or member['size_bytes'] != path.stat().st_size or
                not isinstance(member.get('sha256'), str) or _hash(path) != member['sha256']):
            raise DeploymentError('backup_checksum_failed')
        with closing(_open(path)) as connection:
            metadata = _validate(connection, member['kind'], full=True)
        if any(member.get(key) != value for key, value in metadata.items()):
            raise DeploymentError('backup_schema_metadata_invalid')
    return manifest


def restore_set(backup_directory, destination, expected_release):
    """Restore verified members to a NEW quarantined directory; never migrate.

    Exact storage-code fingerprints are intentionally conservative. A different
    release/schema needs reviewed compatibility/migration, not a force option.
    """
    try:
        manifest = _read_manifest(backup_directory)
        if manifest['declared_release'] != _version_label(expected_release):
            raise DeploymentError('backup_release_mismatch')
        backup_directory = _local_path(backup_directory)
        destination = _local_path(destination)
        if destination == backup_directory or backup_directory in destination.parents:
            raise DeploymentError('destination_overlaps_backup')
        with ExitStack() as stack:
            for member in manifest['members']:
                source = backup_directory / member['name']
                guard = stack.enter_context(closing(_open(source, write=True)))
                guard.execute('BEGIN IMMEDIATE')
                if _hash(source) != member['sha256']:
                    raise DeploymentError('backup_changed_during_restore')
            target = _new_directory(destination)
            deadline = time.monotonic() + 300
            for member in manifest['members']:
                source = backup_directory / member['name']
                output = target / member['name']
                _backup_one(source, output, deadline)
                with closing(_open(output)) as connection:
                    metadata = _validate(connection, member['kind'], full=True)
                if any(member.get(key) != value for key, value in metadata.items()):
                    raise DeploymentError('restore_schema_mismatch')
                # SQLite backup preserves logical pages; verify logical content
                # without logging rows or resetting claims.
                with closing(_open(source)) as before, closing(_open(output)) as after:
                    if _logical_digest(before) != _logical_digest(after):
                        raise DeploymentError('restore_content_mismatch')
        _write_json(target / RESTORE_MARKER, dict(status='offline-review-required',
                    declared_release=manifest['declared_release'], schedules='preserved-not-started',
                    claims_and_fences='preserved-reconcile-before-resume', external_effects='not-certified'))
        _write_json(target / 'restore-manifest.json', manifest)
        (target / 'INCOMPLETE.json').unlink()
        return dict(status='restored-offline-review-required', declared_release=manifest['declared_release'],
                    members=len(manifest['members']), state_file=_MAIN)
    except DeploymentError:
        raise
    except Exception:
        raise DeploymentError('restore_unavailable_or_incompatible') from None


def _logical_digest(connection):
    digest = hashlib.sha256()
    for statement in connection.iterdump():
        digest.update(statement.encode('utf-8'))
        digest.update(b'\n')
    return digest.hexdigest()
