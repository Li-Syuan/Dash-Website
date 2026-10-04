"""Durable same-host ETL dispatch, using JobMonitor's explicit lifecycle patterns.

Construction/import never starts a worker. The launcher owns start/stop. Leases,
request receipts, step snapshots and local publication share one SQLite file,
so a transaction checks owner/token/fencing/expiry before its protected effects.
Only engine-owned local publication is covered by this fencing contract, never
arbitrary external effects. Adapters are trusted, side-effect-free server code.
An expired or interrupted attempt is never automatically retried. A blocking
adapter cannot be forcibly killed; its late result is fenced, not published.
Storage must be trusted local disk on one host, with a stable wall clock, not
NFS or a distributed cluster. Preserve the database and fencing counters on
backup/restore, and stop all workers while restoring it.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid

from demo_services import AccessDenied
from .crud import Conflict, NotFound, StateUnavailable, ValidationError
from .etl_adapters import ETLRegistry, default_registry
from .job_monitor import LOCAL_ZONE, TIMEZONE, _local, _number
from .providers import validate_identity
from .state import _ddl_tokens, _path

MIN_INTERVAL_SECONDS = 60
MAX_INTERVAL_SECONDS = 86400
DEFAULT_INTERVAL_SECONDS = 300
MAX_BACKFILL_DAYS = 31
MAX_ATTEMPTS = 3
MAX_RUNS = 100
MAX_SNAPSHOT_BYTES = 262144
_REQUEST = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z')
_DATE = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}\Z')
_ERRORS = {
    'adapter_failed': 'A trusted snapshot adapter failed; no source details are exposed.',
    'quality_failed': 'The snapshot did not pass the configured count or quality checks.',
    'snapshot_invalid': 'The adapter output did not satisfy the bounded JSON snapshot contract.',
    'lease_lost': 'The worker lease expired or was replaced; publication was blocked.',
    'configuration_changed': 'Job configuration changed; publication was blocked.',
    'permission_revoked': 'Current job permission is unavailable; execution was blocked.',
    'interrupted': 'An earlier worker stopped before completion; this attempt was not replayed.',
    'upstream_failed': 'An earlier step failed; this step was skipped.',
    'storage_unavailable': 'Local durable ETL storage is unavailable.',
    'worker_failed': 'The local ETL worker encountered a safe internal failure.',
}
_RETRY_ERRORS = frozenset(('adapter_failed', 'quality_failed', 'snapshot_invalid'))
_DDL = (
    '''CREATE TABLE etl_config (
        job_id TEXT PRIMARY KEY NOT NULL, org TEXT NOT NULL, signature TEXT NOT NULL,
        enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
        interval_seconds REAL NOT NULL CHECK(interval_seconds BETWEEN 0.01 AND 86400),
        next_run_at REAL, version INTEGER NOT NULL CHECK(version>0), updated_by TEXT,
        CHECK((enabled=0 AND next_run_at IS NULL) OR
              (enabled=1 AND next_run_at IS NOT NULL AND updated_by IS NOT NULL)))''',
    '''CREATE TABLE etl_leases (
        job_id TEXT PRIMARY KEY NOT NULL, owner TEXT NOT NULL, token TEXT NOT NULL,
        fencing INTEGER NOT NULL CHECK(fencing>0), expires_at REAL NOT NULL,
        FOREIGN KEY(job_id) REFERENCES etl_config(job_id))''',
    '''CREATE TABLE etl_requests (
        request_id TEXT PRIMARY KEY NOT NULL, job_id TEXT NOT NULL, actor_id TEXT NOT NULL,
        action TEXT NOT NULL, payload_hash TEXT NOT NULL, run_id TEXT,
        FOREIGN KEY(job_id) REFERENCES etl_config(job_id))''',
    '''CREATE TABLE etl_runs (
        run_id TEXT PRIMARY KEY NOT NULL, job_id TEXT NOT NULL, business_date TEXT NOT NULL,
        trigger TEXT NOT NULL CHECK(trigger IN ('manual','timer','backfill','retry')),
        dedup_key TEXT NOT NULL, actor_id TEXT NOT NULL, config_version INTEGER NOT NULL,
        signature TEXT NOT NULL, owner TEXT NOT NULL, token TEXT NOT NULL,
        fencing INTEGER NOT NULL CHECK(fencing>0),
        status TEXT NOT NULL CHECK(status IN ('running','succeeded','failed')),
        attempt INTEGER NOT NULL CHECK(attempt BETWEEN 1 AND 3),
        parent_run_id TEXT UNIQUE, root_run_id TEXT NOT NULL,
        started_at REAL NOT NULL, finished_at REAL, duration_seconds REAL,
        row_count INTEGER NOT NULL CHECK(row_count BETWEEN 0 AND 1000), error_code TEXT,
        UNIQUE(job_id,dedup_key), UNIQUE(root_run_id,attempt),
        FOREIGN KEY(job_id) REFERENCES etl_config(job_id),
        FOREIGN KEY(parent_run_id) REFERENCES etl_runs(run_id),
        CHECK((status='running' AND finished_at IS NULL AND error_code IS NULL) OR
              (status='succeeded' AND finished_at IS NOT NULL AND error_code IS NULL) OR
              (status='failed' AND finished_at IS NOT NULL AND error_code IS NOT NULL)))''',
    '''CREATE TABLE etl_steps (
        run_id TEXT NOT NULL, name TEXT NOT NULL, ordinal INTEGER NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('pending','running','succeeded','failed','skipped')),
        started_at REAL, finished_at REAL, duration_seconds REAL,
        row_count INTEGER NOT NULL CHECK(row_count BETWEEN 0 AND 1000), error_code TEXT,
        snapshot TEXT, digest TEXT, checks_json TEXT NOT NULL, reused_from_run_id TEXT,
        PRIMARY KEY(run_id,name), UNIQUE(run_id,ordinal),
        FOREIGN KEY(run_id) REFERENCES etl_runs(run_id))''',
    '''CREATE TABLE etl_publications (
        job_id TEXT NOT NULL, business_date TEXT NOT NULL, run_id TEXT NOT NULL,
        published_at REAL NOT NULL, row_count INTEGER NOT NULL CHECK(row_count BETWEEN 0 AND 1000),
        snapshot TEXT NOT NULL, digest TEXT NOT NULL, fencing INTEGER NOT NULL CHECK(fencing>0),
        PRIMARY KEY(job_id,business_date), FOREIGN KEY(run_id) REFERENCES etl_runs(run_id),
        FOREIGN KEY(job_id) REFERENCES etl_config(job_id))''',
    'CREATE INDEX etl_runs_recent ON etl_runs(job_id,started_at DESC,run_id)',
)


class _Blocked(Exception):
    def __init__(self, code):
        self.code = code


class _Quality(Exception):
    def __init__(self, checks):
        self.checks = checks


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def _digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _business_date(value=None):
    today = datetime.now(LOCAL_ZONE).date()
    if value is None:
        return today.isoformat()
    if not isinstance(value, str) or not _DATE.fullmatch(value):
        raise ValidationError('Use a valid business date in YYYY-MM-DD format.')
    try:
        parsed = datetime.strptime(value, '%Y-%m-%d').date()
    except ValueError:
        raise ValidationError('Use a valid business date in YYYY-MM-DD format.') from None
    if parsed.isoformat() != value or parsed > today:
        raise ValidationError('Business dates must be canonical dates no later than today in Asia/Taipei.')
    return value


def _request_id(value):
    if value is None:
        return uuid.uuid4().hex
    if not isinstance(value, str) or not _REQUEST.fullmatch(value):
        raise ValidationError('Request IDs must contain 1–128 bounded ASCII identifier characters.')
    return value


class ETLDispatch:
    """Multiple registered DAGs, safe retries and bounded backfills, offline only."""
    def __init__(self, database, identities, *, registry=None, min_interval_seconds=60,
                 poll_interval_seconds=1, lease_ttl_seconds=30):
        if not callable(getattr(identities, 'get_user', None)):
            raise ValueError('A current server-owned identity provider is required.')
        registry = default_registry() if registry is None else registry
        if not isinstance(registry, ETLRegistry):
            raise ValueError('A trusted ETLRegistry is required.')
        self.registry = registry.snapshot()
        self.path = str(_path(database))
        self.identities = identities
        self.min_interval_seconds = _number(min_interval_seconds, .01, 60, 'minimum interval')
        self.poll_interval_seconds = _number(poll_interval_seconds, .01, 10, 'poll interval')
        self.lease_ttl_seconds = _number(lease_ttl_seconds, .05, 300, 'lease lifetime')
        self.available = True
        self._owner = 'etl-' + uuid.uuid4().hex
        self._thread = None
        self._thread_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._worker_error_code = None
        try:
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        except OSError:
            raise StateUnavailable(_ERRORS['storage_unavailable']) from None
        else:
            os.close(descriptor)
        with self._transaction(initialize=True) as connection:
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            if version == 0:
                if connection.execute("SELECT 1 FROM sqlite_master WHERE name NOT GLOB 'sqlite_*'").fetchone():
                    raise StateUnavailable('Unversioned ETL storage is not empty.')
                for statement in _DDL:
                    connection.execute(statement)
                connection.execute('PRAGMA user_version=1')
            self._validate_schema(connection)
            stored = {row['job_id']: row for row in connection.execute('SELECT * FROM etl_config')}
            if set(stored) - set(self.registry):
                raise StateUnavailable('The deployed registry differs from durable ETL configuration.')
            for job in self.registry.values():
                if job.job_id in stored:
                    if stored[job.job_id]['signature'] != job.signature or stored[job.job_id]['org'] != job.org:
                        raise StateUnavailable('The deployed registry differs from durable ETL configuration.')
                else:
                    connection.execute('INSERT INTO etl_config VALUES(?,?,?,0,?,NULL,1,NULL)',
                                       (job.job_id, job.org, job.signature, DEFAULT_INTERVAL_SECONDS))

    @staticmethod
    def _validate_schema(connection):
        if connection.execute('PRAGMA user_version').fetchone()[0] != 1:
            raise StateUnavailable('Unsupported ETL storage schema.')
        actual = {_ddl_tokens(row['sql']) for row in connection.execute(
            "SELECT sql FROM sqlite_master WHERE name NOT GLOB 'sqlite_*'")}
        if actual != {_ddl_tokens(statement) for statement in _DDL}:
            raise StateUnavailable('ETL storage schema does not match its contract.')

    @contextmanager
    def _transaction(self, initialize=False):
        connection = None
        try:
            connection = sqlite3.connect(Path(self.path).as_uri()+'?mode=rw', uri=True,
                                         isolation_level=None, timeout=5)
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA foreign_keys=ON')
            connection.execute('PRAGMA synchronous=FULL')
            if connection.execute('PRAGMA journal_mode').fetchone()[0].lower() not in ('delete','truncate','persist'):
                raise StateUnavailable('ETL storage requires local rollback-journal mode.')
            connection.execute('BEGIN IMMEDIATE')
            if not initialize:
                self._validate_schema(connection)
            yield connection
            connection.commit()
        except (sqlite3.Error, OSError):
            if connection is not None:
                connection.rollback()
            raise StateUnavailable(_ERRORS['storage_unavailable']) from None
        except BaseException:
            if connection is not None:
                connection.rollback()
            raise
        finally:
            if connection is not None:
                connection.close()

    def _auth(self, user, job=None, manage=False):
        try:
            supplied = validate_identity(user)
            current = validate_identity(self.identities.get_user(supplied['id']))
        except Exception:
            raise AccessDenied('A current valid identity is required.') from None
        if supplied != current or current['role'] not in ('admin','user'):
            raise AccessDenied('This ETL action is not permitted.')
        if job is not None and not self._allowed(current, job, manage):
            raise AccessDenied('This ETL job action is not permitted.')
        return current

    @staticmethod
    def _allowed(user, job, manage=False):
        return (user['org'] == job.org and user['role'] in (job.manage_roles if manage else job.read_roles)
                and (not job.allowed_user_ids or user['id'] in job.allowed_user_ids))

    def _job(self, job_id):
        if not isinstance(job_id, str) or job_id not in self.registry:
            raise NotFound('The requested ETL job is unavailable.')
        return self.registry[job_id]

    def _config(self, connection, job):
        row = connection.execute('SELECT * FROM etl_config WHERE job_id=?', (job.job_id,)).fetchone()
        if row is None or row['signature'] != job.signature or row['org'] != job.org:
            raise StateUnavailable('The deployed registry differs from durable ETL configuration.')
        return row

    @property
    def worker_running(self):
        return self._thread is not None and self._thread.is_alive()

    def _job_dict(self, user, job, config):
        return dict(job_id=job.job_id, name=job.name, description=job.description, org=job.org,
                    enabled=bool(config['enabled']), interval_seconds=config['interval_seconds'],
                    next_run_at=_local(config['next_run_at']), next_run_at_epoch=config['next_run_at'],
                    version=config['version'], can_manage=self._allowed(user, job, True), timezone=TIMEZONE,
                    steps=[dict(name=s.name, depends_on=list(s.depends_on)) for s in job.steps])

    def jobs(self, user):
        user = self._auth(user)
        with self._transaction() as connection:
            user = self._auth(user)
            return [self._job_dict(user, job, self._config(connection, job))
                    for job in self.registry.values() if self._allowed(user, job)]

    def status(self, user, job_id):
        job = self._job(job_id)
        user = self._auth(user, job)
        with self._transaction() as connection:
            user = self._auth(user, job)
            result = self._job_dict(user, job, self._config(connection, job))
            rows = connection.execute('SELECT * FROM etl_runs WHERE job_id=? ORDER BY started_at DESC,run_id LIMIT ?',
                                      (job_id, MAX_RUNS)).fetchall()
            runs = [self._run_dict(connection, row, job, user) for row in rows]
            publication = connection.execute('SELECT * FROM etl_publications WHERE job_id=? '
                                             'ORDER BY published_at DESC LIMIT 1', (job_id,)).fetchone()
            result.update(worker_running=self.worker_running, scheduler_running=self.worker_running,
                          worker_error_code=self._worker_error_code, runs=runs,
                          last_run=runs[0] if runs else None, publication=self._publication_dict(publication))
            return result

    @staticmethod
    def _publication_dict(row):
        if row is None:
            return None
        return {name: _local(row[name]) if name == 'published_at' else row[name]
                for name in ('run_id','business_date','published_at','row_count','fencing')}

    def configure(self, user, job_id, enabled, interval_seconds, expected_version=None):
        job = self._job(job_id)
        user = self._auth(user, job, True)
        if type(enabled) is not bool:
            raise ValidationError('enabled must be a boolean.')
        interval = _number(interval_seconds, self.min_interval_seconds, MAX_INTERVAL_SECONDS, 'interval_seconds')
        if expected_version is not None and (type(expected_version) is not int or not 1 <= expected_version <= 9223372036854775807):
            raise ValidationError('The configuration version must be a positive integer.')
        with self._transaction() as connection:
            self._auth(user, job, True)
            config = self._config(connection, job)
            if expected_version is not None and config['version'] != expected_version:
                raise Conflict('The ETL configuration changed; reload before saving.')
            if (bool(config['enabled']) != enabled or config['interval_seconds'] != interval or
                    (enabled and config['updated_by'] != user['id'])):
                connection.execute('UPDATE etl_config SET enabled=?,interval_seconds=?,next_run_at=?,version=version+1,updated_by=? WHERE job_id=?',
                                   (int(enabled), interval, time.time()+interval if enabled else None, user['id'], job_id))
            self._auth(user, job, True)
        return self.status(user, job_id)

    @staticmethod
    def _receipt(connection, request_id, user, job, action, payload):
        fingerprint = _digest(_json(payload))
        row = connection.execute('SELECT * FROM etl_requests WHERE request_id=?', (request_id,)).fetchone()
        if row:
            if (row['job_id'] != job.job_id or row['actor_id'] != user['id'] or
                    row['action'] != action or row['payload_hash'] != fingerprint):
                raise Conflict('This request ID was already used for a different action.')
            return row['run_id']
        connection.execute('INSERT INTO etl_requests VALUES(?,?,?,?,?,NULL)',
                           (request_id, job.job_id, user['id'], action, fingerprint))
        return None

    def _recover(self, connection, job_id, now):
        lease = connection.execute('SELECT * FROM etl_leases WHERE job_id=?', (job_id,)).fetchone()
        if lease is None or lease['expires_at'] <= now:
            for row in connection.execute("SELECT run_id FROM etl_runs WHERE job_id=? AND status='running'", (job_id,)).fetchall():
                self._fail_row(connection, row['run_id'], 'interrupted', now)

    @staticmethod
    def _fail_row(connection, run_id, code, now, active_step=None, checks=None):
        connection.execute("UPDATE etl_runs SET status='failed',finished_at=?,duration_seconds=MAX(0,?-started_at),error_code=? WHERE run_id=? AND status='running'",
                           (now, now, code, run_id))
        if active_step:
            connection.execute("UPDATE etl_steps SET status='failed',started_at=COALESCE(started_at,?),finished_at=?,duration_seconds=MAX(0,?-COALESCE(started_at,?)),error_code=?,checks_json=? WHERE run_id=? AND name=? AND status IN ('pending','running')",
                               (now, now, now, now, code, _json(checks or []), run_id, active_step))
        if active_step and code == 'quality_failed' and checks:
            observed = next((check.get('actual') for check in checks if check.get('name') == 'row_count'), 0)
            if type(observed) is int and 0 <= observed <= 1000:
                connection.execute('UPDATE etl_steps SET row_count=? WHERE run_id=? AND name=?',
                                   (observed, run_id, active_step))
        connection.execute("UPDATE etl_steps SET status='failed',finished_at=?,duration_seconds=MAX(0,?-started_at),error_code=? WHERE run_id=? AND status='running'",
                           (now, now, code, run_id))
        connection.execute("UPDATE etl_steps SET status='skipped',finished_at=?,duration_seconds=0,error_code='upstream_failed' WHERE run_id=? AND status='pending'", (now, run_id))

    def _claim(self, user, job, business_date, trigger, dedup_key, request_id=None, parent_run_id=None, due=None):
        user = self._auth(user, job, True)
        with self._transaction() as connection:
            self._auth(user, job, True)
            config = self._config(connection, job)
            self._recover(connection, job.job_id, time.time())
            if request_id:
                payload = dict(business_date=business_date, parent_run_id=parent_run_id)
                existing_id = self._receipt(connection, request_id, user, job, trigger, payload)
                if existing_id:
                    return dict(existing_run_id=existing_id)
            existing = connection.execute('SELECT run_id FROM etl_runs WHERE job_id=? AND dedup_key=?', (job.job_id,dedup_key)).fetchone()
            if existing:
                if request_id:
                    connection.execute('UPDATE etl_requests SET run_id=? WHERE request_id=?', (existing['run_id'],request_id))
                return dict(existing_run_id=existing['run_id'])
            now = time.time()
            if trigger == 'timer' and (not config['enabled'] or config['next_run_at'] > now or
                                      config['updated_by'] != user['id'] or due != (config['version'],config['next_run_at'])):
                return None
            lease = connection.execute('SELECT * FROM etl_leases WHERE job_id=?', (job.job_id,)).fetchone()
            if lease and lease['expires_at'] > now:
                if trigger == 'timer':
                    return None
                raise Conflict('This ETL job is already running; no overlapping attempt was started.')
            self._recover(connection, job.job_id, now)
            attempt, root_run_id, copies = 1, None, {}
            if parent_run_id:
                parent = connection.execute('SELECT * FROM etl_runs WHERE run_id=? AND job_id=?', (parent_run_id,job.job_id)).fetchone()
                if parent is None or parent['business_date'] != business_date or not self._retryable(connection, parent, job):
                    raise Conflict('This attempt cannot be retried safely; its provenance or retry limit is unavailable.')
                copies = self._verified_outputs(connection, parent, job)
                attempt, root_run_id = parent['attempt']+1, parent['root_run_id']
            run_id, token = uuid.uuid4().hex, uuid.uuid4().hex
            fencing = lease['fencing']+1 if lease else 1
            connection.execute('INSERT INTO etl_leases VALUES(?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET owner=excluded.owner,token=excluded.token,fencing=excluded.fencing,expires_at=excluded.expires_at',
                               (job.job_id,self._owner,token,fencing,now+self.lease_ttl_seconds))
            connection.execute('INSERT INTO etl_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,0,NULL)',
                               (run_id,job.job_id,business_date,trigger,dedup_key,user['id'],config['version'],
                                job.signature,self._owner,token,fencing,'running',attempt,parent_run_id,root_run_id or run_id,now))
            for ordinal, step in enumerate(job.steps,1):
                old = copies.get(step.name)
                connection.execute('INSERT INTO etl_steps VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                   (run_id,step.name,ordinal,'succeeded' if old else 'pending',now if old else None,
                                    now if old else None,0 if old else None,old['row_count'] if old else 0,None,
                                    old['snapshot'] if old else None,old['digest'] if old else None,
                                    old['checks_json'] if old else '[]',
                                    (old['reused_from_run_id'] or parent_run_id) if old else None))
            if request_id:
                connection.execute('UPDATE etl_requests SET run_id=? WHERE request_id=?', (run_id,request_id))
            if trigger == 'timer':
                ticks = math.floor((now-config['next_run_at'])/config['interval_seconds'])+1
                connection.execute('UPDATE etl_config SET next_run_at=? WHERE job_id=?',
                                   (config['next_run_at']+ticks*config['interval_seconds'],job.job_id))
            return dict(run_id=run_id,job=job,user=user,token=token,fencing=fencing,
                        config_version=config['version'],business_date=business_date)

    def _guard(self, connection, context):
        self._auth(context['user'], context['job'], True)
        config = self._config(connection, context['job'])
        if config['version'] != context['config_version']:
            raise _Blocked('configuration_changed')
        lease = connection.execute('SELECT * FROM etl_leases WHERE job_id=?', (context['job'].job_id,)).fetchone()
        run = connection.execute('SELECT status,token FROM etl_runs WHERE run_id=?', (context['run_id'],)).fetchone()
        if (lease is None or lease['owner'] != self._owner or lease['token'] != context['token'] or
                lease['fencing'] != context['fencing'] or lease['expires_at'] <= time.time() or
                run is None or run['status'] != 'running' or run['token'] != context['token']):
            raise _Blocked('lease_lost')

    @staticmethod
    def _snapshot(rows, step, upstream):
        if not isinstance(rows, list) or len(rows) > 1000:
            raise _Blocked('snapshot_invalid')
        for row in rows:
            if not isinstance(row, dict) or not 1 <= len(row) <= 64:
                raise _Blocked('snapshot_invalid')
            for key, value in row.items():
                if not isinstance(key, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}',key):
                    raise _Blocked('snapshot_invalid')
                if value is not None and type(value) not in (str,bool,int,float):
                    raise _Blocked('snapshot_invalid')
                if type(value) is str and (len(value) > 256 or any(ord(c)<32 for c in value)):
                    raise _Blocked('snapshot_invalid')
                if type(value) in (int,float):
                    try:
                        valid = math.isfinite(value) and abs(value) <= 10**15
                    except OverflowError:
                        valid = False
                    if not valid:
                        raise _Blocked('snapshot_invalid')
        encoded = _json(rows)
        if len(encoded.encode('utf-8')) > MAX_SNAPSHOT_BYTES:
            raise _Blocked('snapshot_invalid')
        checks = [dict(name='row_count',passed=step.min_rows<=len(rows)<=step.max_rows,
                       actual=len(rows),expected='{}..{}'.format(step.min_rows,step.max_rows))]
        if step.required_fields:
            checks.append(dict(name='required_fields',passed=all(all(field in row and row[field] is not None for field in step.required_fields) for row in rows),actual=None,expected='All required fields present'))
        if step.unique_fields:
            values = [tuple(_json(row.get(field)) for field in step.unique_fields) for row in rows]
            checks.append(dict(name='unique_fields',passed=(len(values)==len(set(values)) and all(all(field in row and row[field] is not None for field in step.unique_fields) for row in rows)),actual=len(set(values)),expected=str(len(rows))))
        if step.nonnegative_fields:
            checks.append(dict(name='nonnegative_fields',passed=all(all(type(row.get(field)) in (int,float) and row[field]>=0 for field in step.nonnegative_fields) for row in rows),actual=None,expected='Finite nonnegative numeric values'))
        if step.count_matches:
            expected = len(upstream[step.count_matches])
            checks.append(dict(name='count_match',passed=len(rows)==expected,actual=len(rows),expected=str(expected)))
        if not all(check['passed'] for check in checks):
            raise _Quality(checks)
        return encoded, _digest(encoded), checks

    def _verified_outputs(self, connection, run, job):
        if run['signature'] != job.signature:
            raise Conflict('Stored snapshot provenance is incompatible.')
        stored = {row['name']:row for row in connection.execute('SELECT * FROM etl_steps WHERE run_id=?',(run['run_id'],))}
        if set(stored) != {step.name for step in job.steps}:
            raise Conflict('Stored snapshot provenance is incomplete.')
        ancestors = set()
        cursor = run
        for _ in range(MAX_ATTEMPTS):
            if not cursor['parent_run_id']:
                if cursor['attempt'] != 1 or cursor['root_run_id'] != cursor['run_id']:
                    raise Conflict('Stored retry lineage is invalid.')
                break
            parent = connection.execute('SELECT * FROM etl_runs WHERE run_id=?', (cursor['parent_run_id'],)).fetchone()
            if (parent is None or parent['run_id'] in ancestors or parent['root_run_id'] != run['root_run_id'] or
                    parent['job_id'] != job.job_id or parent['business_date'] != run['business_date'] or
                    parent['signature'] != job.signature or parent['attempt'] != cursor['attempt']-1 or
                    parent['status'] != 'failed' or parent['error_code'] not in _RETRY_ERRORS or
                    parent['config_version'] != run['config_version']):
                raise Conflict('Stored retry lineage is invalid.')
            ancestors.add(parent['run_id'])
            cursor = parent
        else:
            raise Conflict('Stored retry lineage is invalid.')
        outputs, copies = {}, {}
        for step in job.steps:
            old = stored[step.name]
            if old['status'] != 'succeeded':
                continue
            try:
                rows = json.loads(old['snapshot'])
                encoded, digest, checks = self._snapshot(rows,step,outputs)
                if (encoded != old['snapshot'] or digest != old['digest'] or len(rows)!=old['row_count'] or
                        _json(checks) != old['checks_json'] or any(dep not in outputs for dep in step.depends_on)):
                    raise ValueError()
                if old['reused_from_run_id']:
                    origin = connection.execute('SELECT s.*,r.job_id,r.business_date,r.signature,r.root_run_id,r.attempt FROM etl_steps s JOIN etl_runs r USING(run_id) WHERE s.run_id=? AND s.name=?', (old['reused_from_run_id'],step.name)).fetchone()
                    if (origin is None or origin['status']!='succeeded' or origin['job_id']!=job.job_id or
                            origin['business_date']!=run['business_date'] or origin['signature']!=job.signature or
                            origin['snapshot']!=encoded or origin['digest']!=digest or
                            origin['row_count']!=len(rows) or origin['checks_json']!=_json(checks) or
                            origin['root_run_id']!=run['root_run_id'] or origin['attempt']>=run['attempt'] or
                            origin['reused_from_run_id'] is not None or old['reused_from_run_id'] not in ancestors):
                        raise ValueError()
            except Exception:
                raise Conflict('Stored snapshot provenance is unavailable or invalid.') from None
            outputs[step.name], copies[step.name] = rows, old
        return copies

    def _retryable(self, connection, run, job):
        if (run['status']!='failed' or run['error_code'] not in _RETRY_ERRORS or run['attempt']>=MAX_ATTEMPTS or
                run['config_version']!=self._config(connection,job)['version'] or
                connection.execute('SELECT 1 FROM etl_runs WHERE parent_run_id=?',(run['run_id'],)).fetchone()):
            return False
        try:
            copies = self._verified_outputs(connection,run,job)
            return all(step.adapter.retry_safe for step in job.steps if step.name not in copies)
        except Conflict:
            return False

    def _execute(self, context):
        job, run_id, active, checks = context['job'],context['run_id'],None,None
        try:
            with self._transaction() as connection:
                self._guard(connection,context)
                run = connection.execute('SELECT * FROM etl_runs WHERE run_id=?',(run_id,)).fetchone()
                outputs = {name:json.loads(row['snapshot']) for name,row in self._verified_outputs(connection,run,job).items()}
            for step in job.steps:
                if step.name in outputs:
                    continue
                active, checks = step.name,None
                with self._transaction() as connection:
                    self._guard(connection,context)
                    connection.execute("UPDATE etl_steps SET status='running',started_at=? WHERE run_id=? AND name=? AND status='pending'", (time.time(),run_id,step.name))
                adapter_context = dict(job_id=job.job_id,run_id=run_id,business_date=context['business_date'])
                upstream = {name:json.loads(_json(outputs[name])) for name in step.depends_on}
                # Exceptions from adapter code never become logs, public fields or
                # exception chains. There is deliberately no plugin/code loader.
                try:
                    rows = step.adapter.function(adapter_context,upstream)
                except Exception:
                    raise _Blocked('adapter_failed') from None
                encoded,digest,checks = self._snapshot(rows,step,outputs)
                with self._transaction() as connection:
                    self._guard(connection,context)
                    now = time.time()
                    connection.execute("UPDATE etl_steps SET status='succeeded',finished_at=?,duration_seconds=MAX(0,?-started_at),row_count=?,snapshot=?,digest=?,checks_json=? WHERE run_id=? AND name=? AND status='running'",
                                       (now,now,len(rows),encoded,digest,_json(checks),run_id,step.name))
                    self._guard(connection,context)
                outputs[step.name] = json.loads(encoded)
            with self._transaction() as connection:
                self._guard(connection,context)
                now = time.time()
                published = connection.execute('SELECT * FROM etl_steps WHERE run_id=? AND name=?',(run_id,job.publish_step)).fetchone()
                changed = connection.execute('INSERT INTO etl_publications VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(job_id,business_date) DO UPDATE SET run_id=excluded.run_id,published_at=excluded.published_at,row_count=excluded.row_count,snapshot=excluded.snapshot,digest=excluded.digest,fencing=excluded.fencing WHERE etl_publications.fencing<excluded.fencing',
                                             (job.job_id,context['business_date'],run_id,now,published['row_count'],published['snapshot'],published['digest'],context['fencing'])).rowcount
                if changed != 1:
                    raise _Blocked('lease_lost')
                connection.execute("UPDATE etl_runs SET status='succeeded',finished_at=?,duration_seconds=MAX(0,?-started_at),row_count=? WHERE run_id=? AND status='running'",
                                   (now,now,published['row_count'],run_id))
                # Final expiry/identity check is after publication work but inside
                # its transaction. Run is now succeeded, so check lease directly.
                self._auth(context['user'],job,True)
                if connection.execute('SELECT expires_at FROM etl_leases WHERE job_id=?',(job.job_id,)).fetchone()[0] <= time.time():
                    raise _Blocked('lease_lost')
        except Exception as error:
            code = (error.code if isinstance(error,_Blocked) else 'quality_failed' if isinstance(error,_Quality)
                    else 'permission_revoked' if isinstance(error,AccessDenied)
                    else 'storage_unavailable' if isinstance(error,StateUnavailable) else 'worker_failed')
            with self._transaction() as connection:
                current = connection.execute('SELECT status,token FROM etl_runs WHERE run_id=?',(run_id,)).fetchone()
                if current and current['status']=='running' and current['token']==context['token']:
                    self._fail_row(connection,run_id,code,time.time(),active,error.checks if isinstance(error,_Quality) else checks)
        finally:
            try:
                with self._transaction() as connection:
                    connection.execute('UPDATE etl_leases SET expires_at=0 WHERE job_id=? AND owner=? AND token=?', (job.job_id,self._owner,context['token']))
            except StateUnavailable:
                self._worker_error_code = 'storage_unavailable'
        return self.run_detail(context['user'],run_id)

    def run_now(self, user, job_id, business_date=None, request_id=None):
        job = self._job(job_id)
        user = self._auth(user,job,True)
        date, request_id = _business_date(business_date),_request_id(request_id)
        context = self._claim(user,job,date,'manual','manual:'+request_id,request_id)
        return self.run_detail(user,context['existing_run_id']) if 'existing_run_id' in context else self._execute(context)

    def retry(self, user, run_id, request_id=None):
        detail = self.run_detail(user,run_id)
        job = self._job(detail['job_id'])
        user = self._auth(user,job,True)
        request_id = _request_id(request_id)
        context = self._claim(user,job,detail['business_date'],'retry','retry:'+run_id,request_id,parent_run_id=run_id)
        return self.run_detail(user,context['existing_run_id']) if 'existing_run_id' in context else self._execute(context)

    def backfill(self, user, job_id, start_date, end_date, request_id=None):
        job = self._job(job_id)
        user = self._auth(user,job,True)
        if start_date is None or end_date is None:
            raise ValidationError('Backfill requires explicit start and end dates.')
        start_date,end_date = _business_date(start_date),_business_date(end_date)
        start,end = datetime.strptime(start_date,'%Y-%m-%d').date(),datetime.strptime(end_date,'%Y-%m-%d').date()
        days = (end-start).days+1
        if not 1 <= days <= MAX_BACKFILL_DAYS:
            raise ValidationError('Backfill requires an inclusive range of one to 31 dates.')
        request_id = _request_id(request_id)
        with self._transaction() as connection:
            self._auth(user,job,True)
            self._receipt(connection,request_id,user,job,'backfill',dict(start_date=start_date,end_date=end_date))
        runs = []
        for offset in range(days):
            date = (start+timedelta(days=offset)).isoformat()
            context = self._claim(user,job,date,'backfill','backfill:'+date)
            runs.append(self.run_detail(user,context['existing_run_id']) if 'existing_run_id' in context else self._execute(context))
        return dict(job_id=job_id,start_date=start_date,end_date=end_date,runs=runs)

    def _run_dict(self, connection, row, job, user):
        result = {name:row[name] for name in ('run_id','job_id','business_date','trigger','status','attempt',
                  'parent_run_id','root_run_id','duration_seconds','row_count','error_code')}
        result.update(started_at=_local(row['started_at']),finished_at=_local(row['finished_at']),
                      started_at_epoch=row['started_at'],finished_at_epoch=row['finished_at'],
                      error=_ERRORS.get(row['error_code']),
                      retry_allowed=self._allowed(user,job,True) and self._retryable(connection,row,job))
        specs = {step.name:step for step in job.steps}
        result['steps'] = []
        for step in connection.execute('SELECT * FROM etl_steps WHERE run_id=? ORDER BY ordinal',(row['run_id'],)):
            fields = {name:step[name] for name in ('name','ordinal','status','duration_seconds','row_count','error_code','reused_from_run_id')}
            fields.update(started_at=_local(step['started_at']),finished_at=_local(step['finished_at']),
                          error=_ERRORS.get(step['error_code']),depends_on=list(specs[step['name']].depends_on),
                          checks=json.loads(step['checks_json']))
            result['steps'].append(fields)
        return result

    def run_detail(self, user, run_id):
        user = self._auth(user)
        if not isinstance(run_id,str) or not re.fullmatch('[0-9a-f]{32}',run_id):
            raise NotFound('The requested ETL attempt is unavailable.')
        with self._transaction() as connection:
            row = connection.execute('SELECT * FROM etl_runs WHERE run_id=?',(run_id,)).fetchone()
            if row is None:
                raise NotFound('The requested ETL attempt is unavailable.')
            job = self._job(row['job_id'])
            self._auth(user,job)
            self._config(connection,job)
            return self._run_dict(connection,row,job,user)

    def tick(self):
        """Execute due registered jobs once; missed ticks coalesce, never replay."""
        results = []
        for job in self.registry.values():
            with self._transaction() as connection:
                self._recover(connection,job.job_id,time.time())
                config = dict(self._config(connection,job))
            if not config['enabled'] or config['next_run_at']>time.time():
                continue
            try:
                user = validate_identity(self.identities.get_user(config['updated_by']))
                self._auth(user,job,True)
            except Exception:
                with self._transaction() as connection:
                    connection.execute('UPDATE etl_config SET enabled=0,next_run_at=NULL,version=version+1 WHERE job_id=? AND version=?', (job.job_id,config['version']))
                continue
            due = (config['version'],config['next_run_at'])
            context = self._claim(user,job,_business_date(),'timer','timer:{}:{}'.format(*due),due=due)
            if context is not None and 'existing_run_id' not in context:
                results.append(self._execute(context))
        return results

    _tick = tick

    def _worker(self):
        while not self._stop_event.is_set():
            try:
                self.tick()
                self._worker_error_code = None
            except Exception as error:
                self._worker_error_code = 'storage_unavailable' if isinstance(error,StateUnavailable) else 'worker_failed'
            self._stop_event.wait(self.poll_interval_seconds)

    def start(self):
        with self._thread_lock:
            if self.worker_running:
                return False
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._worker,name='local-etl-dispatch',daemon=True)
            self._thread.start()
            return True

    def stop(self, timeout=10):
        timeout = _number(timeout,0,60,'stop timeout')
        with self._thread_lock:
            self._stop_event.set()
            if self._thread is None:
                return True
            if self._thread is threading.current_thread():
                return False
            self._thread.join(timeout)
            return not self._thread.is_alive()
