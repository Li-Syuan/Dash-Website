"""Explicit local interval worker for one safe, synthetic three-step report job.

Importing this module or constructing JobMonitor never starts a worker. The direct
launcher owns start()/stop(); WSGI/factory users must explicitly arrange a worker.
Only fixed synthetic source -> clean/validate -> atomic SQLite publication runs.
No submitted SQL, Python, company database, filesystem export, SMTP or URL runs.

Storage must be a trusted same-host local filesystem, never NFS or a cluster.
StateStore's exact schema is kept in a private lease sidecar; its durable lease is
checked inside every protected monitor transaction using SQLite ATTACH. This
fences an expired/stale worker at publication, rather than assuming lease expiry
stops it. SQLite rollback-journal mode permits atomic attached-db transactions.
Interrupted attempts are failed visibly and never replayed. Missed interval ticks
coalesce into one new attempt; a failed attempt waits for the next interval.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import math
import os
import re
from pathlib import Path
import sqlite3
import threading
import time
import uuid

from demo_services import AccessDenied, MailSink
from .crud import Conflict, NotFound, StateUnavailable, ValidationError
from .providers import validate_identity
from .state import StateError, StateStore, _ddl_tokens, _path


JOB_ID = 'example-report-summary'
JOB_NAME = 'Synthetic report summary / 合成報表摘要'
TIMEZONE = 'Asia/Taipei'
LOCAL_ZONE = timezone(timedelta(hours=8), name=TIMEZONE)
DEFAULT_INTERVAL_SECONDS = 300
MIN_INTERVAL_SECONDS = 60
MAX_INTERVAL_SECONDS = 86400
STEPS = ('source_snapshot', 'clean_validate', 'atomic_publish')
MAX_RUNS = 100
MAX_LOGS = 200
_LEASE_KEY = 'jobmonitor:A:example-report-summary'
_MONITOR_LINK = '/QA_portal/operations'
_OWNER_EMAIL = re.compile(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,63}@example\.invalid\Z')
_ERROR_MESSAGES = {
    'source_failed': 'Synthetic source snapshot failed.',
    'validation_failed': 'Synthetic report validation failed.',
    'publish_failed': 'Atomic local publication failed.',
    'lease_lost': 'Worker lease expired or was replaced; publication was blocked.',
    'configuration_changed': 'Configuration changed during this attempt; publication was blocked.',
    'permission_revoked': 'Current administrator permission is unavailable; execution was blocked.',
    'provider_unavailable': 'Current identity provider is unavailable; execution was blocked.',
    'interrupted': 'An earlier worker stopped before finishing; this attempt was not replayed.',
    'upstream_failed': 'An upstream step failed; this step was skipped.',
    'storage_unavailable': 'Local durable job storage is unavailable.',
    'worker_failed': 'The local worker encountered a safe internal failure.',
}
_LOG_MESSAGES = {
    'schedule.enabled': 'Automatic synthetic report execution enabled.',
    'schedule.disabled': 'Automatic synthetic report execution disabled.',
    'run.started': 'Synthetic report attempt started.',
    'run.succeeded': 'Three synthetic ETL steps completed and the summary was published locally.',
    'run.failed': 'Synthetic report attempt failed; the last successful publication was retained.',
    'run.interrupted': 'An interrupted attempt was marked failed without replaying it.',
    'schedule.permission_revoked': 'Automatic execution disabled after administrator access changed.',
    'worker.error': 'The worker could not safely execute a pending attempt.',
}
_DDL = (
    '''CREATE TABLE monitor_config (
        job_id TEXT PRIMARY KEY NOT NULL CHECK(job_id='example-report-summary'),
        org TEXT NOT NULL CHECK(org='A'), enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
        interval_seconds REAL NOT NULL CHECK(interval_seconds BETWEEN 0.01 AND 86400),
        next_run_at REAL, version INTEGER NOT NULL CHECK(version>0),
        updated_by TEXT, updated_at REAL NOT NULL,
        CHECK((enabled=0 AND next_run_at IS NULL) OR
              (enabled=1 AND next_run_at IS NOT NULL AND updated_by IS NOT NULL)))''',
    '''CREATE TABLE monitor_runs (
        run_id TEXT PRIMARY KEY NOT NULL CHECK(length(run_id)=32),
        job_id TEXT NOT NULL, org TEXT NOT NULL CHECK(org='A'),
        trigger TEXT NOT NULL CHECK(trigger IN ('manual','timer')),
        scheduled_for REAL, config_version INTEGER NOT NULL CHECK(config_version>0),
        lease_owner TEXT NOT NULL, lease_token TEXT NOT NULL, fencing INTEGER NOT NULL CHECK(fencing>0),
        status TEXT NOT NULL CHECK(status IN ('running','succeeded','failed')),
        started_at REAL NOT NULL, finished_at REAL,
        duration_seconds REAL CHECK(duration_seconds>=0),
        row_count INTEGER NOT NULL CHECK(row_count BETWEEN 0 AND 100),
        error_code TEXT CHECK(error_code IN ('source_failed','validation_failed','publish_failed',
            'lease_lost','configuration_changed','permission_revoked','provider_unavailable','interrupted')),
        FOREIGN KEY(job_id) REFERENCES monitor_config(job_id),
        CHECK((status='running' AND finished_at IS NULL AND duration_seconds IS NULL AND error_code IS NULL) OR
              (status='succeeded' AND finished_at IS NOT NULL AND duration_seconds IS NOT NULL AND error_code IS NULL) OR
              (status='failed' AND finished_at IS NOT NULL AND duration_seconds IS NOT NULL AND error_code IS NOT NULL)))''',
    '''CREATE TABLE monitor_steps (
        run_id TEXT NOT NULL, name TEXT NOT NULL CHECK(name IN ('source_snapshot','clean_validate','atomic_publish')),
        ordinal INTEGER NOT NULL CHECK(ordinal BETWEEN 1 AND 3),
        status TEXT NOT NULL CHECK(status IN ('pending','running','succeeded','failed','skipped')),
        started_at REAL, finished_at REAL, duration_seconds REAL CHECK(duration_seconds>=0),
        row_count INTEGER NOT NULL CHECK(row_count BETWEEN 0 AND 100),
        error_code TEXT CHECK(error_code IN ('source_failed','validation_failed','publish_failed','lease_lost',
            'configuration_changed','permission_revoked','provider_unavailable','interrupted','upstream_failed')),
        PRIMARY KEY(run_id,name), UNIQUE(run_id,ordinal),
        FOREIGN KEY(run_id) REFERENCES monitor_runs(run_id) ON DELETE CASCADE)''',
    '''CREATE TABLE monitor_publication (
        job_id TEXT PRIMARY KEY NOT NULL, run_id TEXT NOT NULL,
        published_at REAL NOT NULL, row_count INTEGER NOT NULL CHECK(row_count BETWEEN 1 AND 100),
        revenue INTEGER NOT NULL CHECK(revenue>=0), cost INTEGER NOT NULL CHECK(cost>=0),
        profit INTEGER NOT NULL, fencing INTEGER NOT NULL CHECK(fencing>0),
        FOREIGN KEY(job_id) REFERENCES monitor_config(job_id))''',
    '''CREATE TABLE monitor_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at REAL NOT NULL,
        code TEXT NOT NULL CHECK(code IN ('schedule.enabled','schedule.disabled','run.started','run.succeeded',
            'run.failed','run.interrupted','schedule.permission_revoked','worker.error')),
        run_id TEXT, error_code TEXT CHECK(error_code IN ('source_failed','validation_failed','publish_failed',
            'lease_lost','configuration_changed','permission_revoked','provider_unavailable','interrupted',
            'storage_unavailable','worker_failed')))''',
    'CREATE INDEX monitor_runs_started ON monitor_runs(started_at DESC,run_id)',
)


_DDL_V1 = _DDL
_NOTIFICATION_DDL = (
    """CREATE TABLE monitor_notification_config (
        job_id TEXT PRIMARY KEY NOT NULL, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
        owner_email TEXT CHECK(owner_email IS NULL OR length(owner_email) BETWEEN 1 AND 80),
        failure_open INTEGER NOT NULL CHECK(failure_open IN (0,1)), episode_run_id TEXT,
        version INTEGER NOT NULL CHECK(version>0), updated_at REAL NOT NULL,
        FOREIGN KEY(job_id) REFERENCES monitor_config(job_id),
        CHECK(enabled=0 OR owner_email IS NOT NULL))""",
    """CREATE TABLE monitor_notifications (
        notification_id TEXT PRIMARY KEY NOT NULL CHECK(length(notification_id)=32),
        run_id TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL CHECK(status IN ('pending','simulated','coalesced','failed','disabled','blocked')),
        error_code TEXT CHECK(error_code IN ('mock_failed','permission_revoked','interrupted')),
        created_at REAL NOT NULL, finished_at REAL,
        FOREIGN KEY(run_id) REFERENCES monitor_runs(run_id) ON DELETE CASCADE)""",
)
_DDL = _DDL_V1 + _NOTIFICATION_DDL
_NOTIFICATION_MESSAGES = {
    'pending': 'Local mock notification was claimed; delivery has not been recorded.',
    'simulated': 'The local mock sink accepted this notification; no email was sent.',
    'coalesced': 'Consecutive failure notification coalesced until the next successful publication.',
    'failed': 'Local mock notification failed; the ETL attempt was not rerun.',
    'disabled': 'Owner failure notifications are disabled.',
    'blocked': 'Current administrator permission is unavailable; notification was blocked.',
}


class _LeaseLost(Exception):
    pass


class _ConfigurationChanged(Exception):
    pass


def _number(value, minimum, maximum, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError('{} must be a finite number within the documented bounds.'.format(field))
    try:
        number = float(value)
    except (OverflowError, ValueError):
        raise ValidationError('{} must be a finite number within the documented bounds.'.format(field)) from None
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValidationError('{} must be between {} and {}.'.format(field, minimum, maximum))
    return number


def _local(epoch):
    return None if epoch is None else datetime.fromtimestamp(epoch, LOCAL_ZONE).isoformat(timespec='milliseconds')


class JobMonitor:
    """One durable org-A job with current-identity control and bounded safe output.

    ``database`` is a separate absolute local SQLite file, not a StateStore file.
    Tests may explicitly lower ``min_interval_seconds`` (minimum .01), poll time
    and lease TTL. Those are trusted constructor parameters, never UI settings.
    """
    def __init__(self, database, identities, *, min_interval_seconds=MIN_INTERVAL_SECONDS,
                 poll_interval_seconds=1.0, lease_ttl_seconds=30.0):
        if not callable(getattr(identities, 'get_user', None)):
            raise ValueError('A current server-owned identity provider is required.')
        self.path = str(_path(database))
        self.identities = identities
        self.mail = MailSink()
        self._notification_error_code = None
        self.min_interval_seconds = _number(min_interval_seconds, .01, MIN_INTERVAL_SECONDS, 'minimum interval')
        self.poll_interval_seconds = _number(poll_interval_seconds, .01, 10, 'poll interval')
        self.lease_ttl_seconds = _number(lease_ttl_seconds, .05, 300, 'lease lifetime')
        self.available = True
        self._owner = 'jobmonitor-' + uuid.uuid4().hex
        self._thread = None
        self._thread_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._worker_error_code = None
        # StateStore owns/validates this separate schema, and stores no report data.
        self.lease_path = str(_path(self.path + '.leases.sqlite'))
        try:
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        try:
            self._leases = StateStore(self.lease_path)
        except (StateError, sqlite3.Error, OSError):
            raise StateUnavailable('Local durable job leases are unavailable.') from None
        with self._transaction(initialize=True) as connection:
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            if version == 0:
                if connection.execute("SELECT 1 FROM sqlite_master WHERE name NOT GLOB 'sqlite_*'").fetchone():
                    raise StateUnavailable('Unversioned job monitor storage is not empty.')
                for statement in _DDL:
                    connection.execute(statement)
                connection.execute('PRAGMA user_version=2')
                connection.execute('INSERT INTO monitor_config VALUES(?,?,0,?,NULL,1,NULL,?)',
                                   (JOB_ID, 'A', DEFAULT_INTERVAL_SECONDS, time.time()))
                connection.execute('INSERT INTO monitor_notification_config VALUES(?,0,NULL,0,NULL,1,?)',
                                   (JOB_ID, time.time()))
            elif version == 1:
                self._validate_schema(connection, version=1)
                for statement in _NOTIFICATION_DDL:
                    connection.execute(statement)
                connection.execute('INSERT INTO monitor_notification_config VALUES(?,0,NULL,0,NULL,1,?)',
                                   (JOB_ID, time.time()))
                connection.execute('PRAGMA user_version=2')
            self._validate_schema(connection)

    def _auth(self, user, admin=False):
        try:
            supplied = validate_identity(user)
            current = validate_identity(self.identities.get_user(supplied['id']))
        except Exception:
            raise AccessDenied('A current valid identity is required.') from None
        if (supplied != current or current['org'] != 'A' or
                current['role'] not in (('admin',) if admin else ('admin', 'user'))):
            raise AccessDenied('This job monitor action is not permitted.')
        return current

    @staticmethod
    def _validate_schema(connection, version=2):
        if version not in (1, 2) or connection.execute('PRAGMA user_version').fetchone()[0] != version:
            raise StateUnavailable('Unsupported job monitor storage schema.')
        actual = {_ddl_tokens(row['sql']) for row in connection.execute(
            "SELECT sql FROM sqlite_master WHERE name NOT GLOB 'sqlite_*'")}
        if actual != {_ddl_tokens(statement) for statement in (_DDL_V1 if version == 1 else _DDL)}:
            raise StateUnavailable('Job monitor storage schema does not match its contract.')

    @contextmanager
    def _transaction(self, initialize=False, write=False, fenced=False):
        connection = None
        try:
            connection = sqlite3.connect(Path(self.path).as_uri() + '?mode=rw', uri=True,
                                         isolation_level=None, timeout=5)
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA foreign_keys=ON')
            connection.execute('PRAGMA synchronous=FULL')
            # WAL/NFS cannot supply the attached-file atomicity required here.
            if connection.execute('PRAGMA journal_mode').fetchone()[0].lower() not in ('delete', 'truncate', 'persist'):
                raise StateUnavailable('Job monitor requires local SQLite rollback-journal storage.')
            if fenced:
                connection.execute('ATTACH DATABASE ? AS lease_store',
                                   (Path(self.lease_path).as_uri() + '?mode=rw',))
                if connection.execute('PRAGMA lease_store.journal_mode').fetchone()[0].lower() not in ('delete', 'truncate', 'persist'):
                    raise StateUnavailable('Job leases require local SQLite rollback-journal storage.')
                connection.execute('PRAGMA lease_store.synchronous=FULL')
            connection.execute('BEGIN IMMEDIATE' if write or initialize else 'BEGIN')
            if not initialize:
                self._validate_schema(connection)
            yield connection
            connection.commit()
        except (sqlite3.Error, OSError, StateError):
            if connection is not None:
                connection.rollback()
            raise StateUnavailable('Local durable job storage is unavailable.') from None
        except BaseException:
            if connection is not None:
                connection.rollback()
            raise
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _config(connection):
        config = connection.execute('SELECT * FROM monitor_config WHERE job_id=?', (JOB_ID,)).fetchone()
        if config is None:
            raise StateUnavailable('Local job configuration is unavailable.')
        return config

    @staticmethod
    def _log(connection, code, now, run_id=None, error_code=None):
        # All messages are reconstructed from server-owned codes, never exception text.
        connection.execute('INSERT INTO monitor_logs(occurred_at,code,run_id,error_code) VALUES(?,?,?,?)',
                           (now, code, run_id, error_code))
        connection.execute('DELETE FROM monitor_logs WHERE id NOT IN '
                           '(SELECT id FROM monitor_logs ORDER BY id DESC LIMIT ?)', (MAX_LOGS,))

    @staticmethod
    def _fence(connection, lease):
        if connection.execute('PRAGMA lease_store.user_version').fetchone()[0] != 3:
            raise StateUnavailable('Local job lease schema changed.')
        row = connection.execute('SELECT owner,token,fencing,expires_at FROM lease_store.leases WHERE key=?',
                                 (_LEASE_KEY,)).fetchone()
        if (row is None or row['owner'] != lease.owner or row['token'] != lease.token or
                row['fencing'] != lease.fencing or row['expires_at'] <= time.time()):
            raise _LeaseLost()

    def _guard_run(self, connection, context):
        self._fence(connection, context['lease'])
        config = self._config(connection)
        if config['version'] != context['config_version']:
            raise _ConfigurationChanged()
        row = connection.execute('SELECT status,lease_token FROM monitor_runs WHERE run_id=?',
                                 (context['run_id'],)).fetchone()
        if row is None or row['status'] != 'running' or row['lease_token'] != context['lease'].token:
            raise _LeaseLost()

    def configure(self, user, enabled, interval_seconds):
        user = self._auth(user, admin=True)
        if type(enabled) is not bool:
            raise ValidationError('enabled must be a boolean.')
        interval = _number(interval_seconds, self.min_interval_seconds, MAX_INTERVAL_SECONDS, 'interval_seconds')
        with self._transaction(write=True) as connection:
            config, now = self._config(connection), time.time()
            if bool(config['enabled']) != enabled or config['interval_seconds'] != interval:
                connection.execute('UPDATE monitor_config SET enabled=?,interval_seconds=?,next_run_at=?, '
                                   'version=version+1,updated_by=?,updated_at=? WHERE job_id=?',
                                   (int(enabled), interval, now + interval if enabled else None, user['id'], now, JOB_ID))
                self._log(connection, 'schedule.enabled' if enabled else 'schedule.disabled', now)
            elif enabled and config['updated_by'] != user['id']:
                # Explicitly renewing ownership is also a configuration change.
                connection.execute('UPDATE monitor_config SET version=version+1,updated_by=?,updated_at=? WHERE job_id=?',
                                   (user['id'], now, JOB_ID))
        return self.status(user)

    def configure_notification(self, user, owner_email, enabled):
        """Configure one local mock failure recipient; real domains are rejected."""
        self._auth(user, admin=True)
        if type(enabled) is not bool:
            raise ValidationError('enabled must be a boolean.')
        if owner_email in (None, '') and not enabled:
            owner_email = None
        elif not isinstance(owner_email, str) or not _OWNER_EMAIL.fullmatch(owner_email):
            raise ValidationError('Use one bounded synthetic owner@example.invalid recipient.')
        with self._transaction(write=True) as connection:
            config = connection.execute('SELECT * FROM monitor_notification_config WHERE job_id=?', (JOB_ID,)).fetchone()
            if config['enabled'] != int(enabled) or config['owner_email'] != owner_email:
                connection.execute('UPDATE monitor_notification_config SET enabled=?,owner_email=?,version=version+1,updated_at=? WHERE job_id=?',
                                   (int(enabled), owner_email, time.time(), JOB_ID))
        return self.status(user)

    def _notify_failure(self, context):
        """Durably claim once, then call only the in-memory mock sink, never SMTP."""
        run_id = context['run_id']
        try:
            self._auth(context['user'], admin=True)
            allowed = True
        except AccessDenied:
            allowed = False
        with self._transaction(write=True) as connection:
            if connection.execute('SELECT 1 FROM monitor_notifications WHERE run_id=?', (run_id,)).fetchone():
                return
            run = connection.execute("SELECT * FROM monitor_runs WHERE run_id=? AND status='failed'", (run_id,)).fetchone()
            if run is None:
                return
            config = connection.execute('SELECT * FROM monitor_notification_config WHERE job_id=?', (JOB_ID,)).fetchone()
            status, error_code = ('disabled', None)
            if config['enabled']:
                if not allowed:
                    status, error_code = 'blocked', 'permission_revoked'
                elif config['failure_open']:
                    status = 'coalesced'
                else:
                    status = 'pending'
                    connection.execute('UPDATE monitor_notification_config SET failure_open=1,episode_run_id=? WHERE job_id=?',
                                       (run_id, JOB_ID))
            now, notification_id = time.time(), uuid.uuid4().hex
            connection.execute('INSERT INTO monitor_notifications VALUES(?,?,?,?,?,?)',
                               (notification_id, run_id, status, error_code, now, None if status == 'pending' else now))
            if status != 'pending':
                return
            owner = config['owner_email']
            # Fixed values only: no exception text, settings, identity or source rows.
            failed_step = connection.execute("SELECT name FROM monitor_steps WHERE run_id=? AND status='failed' ORDER BY ordinal LIMIT 1",
                                             (run_id,)).fetchone()
            body = 'Job: {}\nFailed step: {}\nTime: {}\nCode: {}\nMonitor: {}'.format(
                JOB_NAME, failed_step['name'] if failed_step else STEPS[0],
                _local(run['finished_at']), run['error_code'], _MONITOR_LINK)
        try:
            self._auth(context['user'], admin=True)
            self.mail.send('[MOCK] {} failed'.format(JOB_NAME), body, [owner])
            # This sink is intentionally bounded and has no network transport.
            if isinstance(self.mail.messages, list):
                del self.mail.messages[:-MAX_LOGS]
            status, error_code = 'simulated', None
        except AccessDenied:
            status, error_code = 'blocked', 'permission_revoked'
        except Exception:
            status, error_code = 'failed', 'mock_failed'
        with self._transaction(write=True) as connection:
            connection.execute("UPDATE monitor_notifications SET status=?,error_code=?,finished_at=? "
                               "WHERE notification_id=? AND status='pending'", (status, error_code, time.time(), notification_id))
        self._notification_error_code = None

    @staticmethod
    def _recover(connection, now):
        # Called only with a newly acquired, checked lease. Old workers can no
        # longer publish, even if they eventually wake up and try to finish.
        connection.execute("UPDATE monitor_notifications SET status='failed',error_code='interrupted',finished_at=? WHERE status='pending'", (now,))
        abandoned = connection.execute("SELECT run_id,started_at FROM monitor_runs WHERE status='running'").fetchall()
        for row in abandoned:
            connection.execute("UPDATE monitor_runs SET status='failed',finished_at=?,duration_seconds=?,error_code='interrupted' "
                               "WHERE run_id=? AND status='running'", (now, max(0, now-row['started_at']), row['run_id']))
            connection.execute("UPDATE monitor_steps SET status='failed',finished_at=?,duration_seconds=MAX(0,?-started_at),"
                               "error_code='interrupted' WHERE run_id=? AND status='running'", (now, now, row['run_id']))
            connection.execute("UPDATE monitor_steps SET status='skipped',finished_at=?,duration_seconds=0,"
                               "error_code='upstream_failed' WHERE run_id=? AND status='pending'", (now, row['run_id']))
            JobMonitor._log(connection, 'run.interrupted', now, row['run_id'], 'interrupted')

    def _claim(self, user, trigger):
        user = self._auth(user, admin=True)
        try:
            lease = self._leases.acquire(_LEASE_KEY, self._owner, self.lease_ttl_seconds)
        except (StateError, sqlite3.Error, OSError):
            raise StateUnavailable('Local durable job leases are unavailable.') from None
        if lease is None:
            if trigger == 'manual':
                raise Conflict('The synthetic job is already running; no overlapping attempt was started.')
            return None
        context = None
        try:
            with self._transaction(write=True, fenced=True) as connection:
                self._fence(connection, lease)
                config, now = self._config(connection), time.time()
                if trigger == 'timer' and (not config['enabled'] or config['next_run_at'] > now or
                                          config['updated_by'] != user['id']):
                    return None
                self._recover(connection, now)
                run_id = uuid.uuid4().hex
                connection.execute('INSERT INTO monitor_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,0,NULL)',
                                   (run_id, JOB_ID, 'A', trigger, config['next_run_at'] if trigger == 'timer' else None,
                                    config['version'], lease.owner, lease.token, lease.fencing, 'running', now))
                for ordinal, name in enumerate(STEPS, 1):
                    connection.execute("INSERT INTO monitor_steps VALUES(?,?,?,'pending',NULL,NULL,NULL,0,NULL)",
                                       (run_id, name, ordinal))
                if trigger == 'timer':
                    # Keep cadence anchored while avoiding a restart catch-up storm.
                    ticks = math.floor((now - config['next_run_at']) / config['interval_seconds']) + 1
                    connection.execute('UPDATE monitor_config SET next_run_at=? WHERE job_id=?',
                                       (config['next_run_at'] + ticks * config['interval_seconds'], JOB_ID))
                self._log(connection, 'run.started', now, run_id)
                context = dict(run_id=run_id, lease=lease, config_version=config['version'], user=user)
                return context
        finally:
            if context is None:
                self._release(lease)

    def _release(self, lease):
        try:
            self._leases.release(_LEASE_KEY, lease.owner, lease.token)
        except (StateError, sqlite3.Error, OSError):
            # Expiry still fences a failed release. Do not expose raw exceptions.
            self._worker_error_code = 'storage_unavailable'

    def _start_step(self, context, name):
        self._auth(context['user'], admin=True)
        with self._transaction(write=True, fenced=True) as connection:
            self._guard_run(connection, context)
            connection.execute("UPDATE monitor_steps SET status='running',started_at=? WHERE run_id=? AND name=? AND status='pending'",
                               (time.time(), context['run_id'], name))

    def _finish_step(self, context, name, row_count):
        with self._transaction(write=True, fenced=True) as connection:
            self._guard_run(connection, context)
            now = time.time()
            connection.execute("UPDATE monitor_steps SET status='succeeded',finished_at=?,duration_seconds=MAX(0,?-started_at),row_count=? "
                               "WHERE run_id=? AND name=? AND status='running'", (now, now, row_count, context['run_id'], name))

    @staticmethod
    def _source_snapshot():
        # In-memory, fixed synthetic source. No source adapter/configuration is selected.
        return [dict(record_id='demo-row-{:03d}'.format(index), department=department,
                     revenue=str(revenue), cost=str(cost))
                for index, (department, revenue, cost) in enumerate(
                    (('Demo Sales', 110000, 65000), ('Demo Operations', 90000, 55000),
                     ('Demo Sales', 125000, 70000), ('Demo Operations', 95000, 57000)), 1)]

    @staticmethod
    def _clean_validate(snapshot):
        if not isinstance(snapshot, list) or not 1 <= len(snapshot) <= 100:
            raise ValueError('Invalid fixed synthetic snapshot.')
        clean, identifiers = [], set()
        for raw in snapshot:
            if (not isinstance(raw, dict) or set(raw) != {'record_id','department','revenue','cost'} or
                    raw['record_id'] not in {'demo-row-001','demo-row-002','demo-row-003','demo-row-004'} or
                    raw['record_id'] in identifiers or raw['department'] not in ('Demo Sales', 'Demo Operations')):
                raise ValueError('Invalid fixed synthetic row.')
            identifiers.add(raw['record_id'])
            values = []
            for field in ('revenue', 'cost'):
                value = raw[field]
                if not isinstance(value, str) or not value.isascii() or not value.isdecimal() or len(value) > 9:
                    raise ValueError('Invalid fixed synthetic metric.')
                values.append(int(value))
            clean.append(dict(record_id=raw['record_id'], department=raw['department'],
                              revenue=values[0], cost=values[1], profit=values[0]-values[1]))
        return clean

    @staticmethod
    def _publish_summary(connection, context, rows, now):
        revenue, cost = sum(row['revenue'] for row in rows), sum(row['cost'] for row in rows)
        # This method and all success bookkeeping share one fenced transaction.
        changed = connection.execute('INSERT INTO monitor_publication VALUES(?,?,?,?,?,?,?,?) '
                           'ON CONFLICT(job_id) DO UPDATE SET run_id=excluded.run_id,published_at=excluded.published_at, '
                           'row_count=excluded.row_count,revenue=excluded.revenue,cost=excluded.cost,profit=excluded.profit,fencing=excluded.fencing '
                           'WHERE monitor_publication.fencing<excluded.fencing',
                           (JOB_ID, context['run_id'], now, len(rows), revenue, cost, revenue-cost, context['lease'].fencing)).rowcount
        if changed != 1:
            # A restored lease backup can have an older durable fencing counter.
            # Never claim success or overwrite a newer protected publication.
            raise _LeaseLost()

    @staticmethod
    def _trim_runs(connection):
        connection.execute("DELETE FROM monitor_runs WHERE status!='running' AND run_id NOT IN "
                           "(SELECT run_id FROM monitor_runs ORDER BY started_at DESC,run_id LIMIT ?) AND run_id NOT IN "
                           '(SELECT run_id FROM monitor_publication)', (MAX_RUNS,))

    def _publish(self, context, rows):
        self._auth(context['user'], admin=True)
        with self._transaction(write=True, fenced=True) as connection:
            self._guard_run(connection, context)
            now = time.time()
            self._publish_summary(connection, context, rows, now)
            # Check once more after work and immediately before the atomic commit.
            self._guard_run(connection, context)
            connection.execute("UPDATE monitor_steps SET status='succeeded',finished_at=?,duration_seconds=MAX(0,?-started_at),row_count=? "
                               "WHERE run_id=? AND name='atomic_publish' AND status='running'", (now, now, len(rows), context['run_id']))
            connection.execute("UPDATE monitor_runs SET status='succeeded',finished_at=?,duration_seconds=MAX(0,?-started_at),row_count=? "
                               "WHERE run_id=? AND status='running'", (now, now, len(rows), context['run_id']))
            connection.execute('UPDATE monitor_notification_config SET failure_open=0,episode_run_id=NULL WHERE job_id=?', (JOB_ID,))
            self._log(connection, 'run.succeeded', now, context['run_id'])
            self._trim_runs(connection)

    def _fail(self, context, active_step, code):
        with self._transaction(write=True) as connection:
            now = time.time()
            changed = connection.execute("UPDATE monitor_runs SET status='failed',finished_at=?,duration_seconds=MAX(0,?-started_at),error_code=? "
                                         "WHERE run_id=? AND lease_token=? AND status='running'",
                                         (now, now, code, context['run_id'], context['lease'].token)).rowcount
            if not changed:
                return False
            connection.execute("UPDATE monitor_steps SET status='failed',started_at=COALESCE(started_at,?),finished_at=?,"
                               "duration_seconds=MAX(0,?-COALESCE(started_at,?)),error_code=? "
                               "WHERE run_id=? AND name=? AND status IN ('pending','running')",
                               (now, now, now, now, code, context['run_id'], active_step))
            connection.execute("UPDATE monitor_steps SET status='skipped',finished_at=?,duration_seconds=0,error_code='upstream_failed' "
                               "WHERE run_id=? AND status='pending'", (now, context['run_id']))
            self._log(connection, 'run.failed', now, context['run_id'], code)
            self._trim_runs(connection)
            return True

    def _execute(self, context):
        active_step = STEPS[0]
        try:
            self._start_step(context, active_step)
            snapshot = self._source_snapshot()
            self._finish_step(context, active_step, len(snapshot))
            active_step = STEPS[1]
            self._start_step(context, active_step)
            rows = self._clean_validate(snapshot)
            self._finish_step(context, active_step, len(rows))
            active_step = STEPS[2]
            self._start_step(context, active_step)
            self._publish(context, rows)
        except Exception as error:
            if isinstance(error, _LeaseLost):
                code = 'lease_lost'
            elif isinstance(error, _ConfigurationChanged):
                code = 'configuration_changed'
            elif isinstance(error, AccessDenied):
                code = 'permission_revoked'
            else:
                code = dict(zip(STEPS, ('source_failed','validation_failed','publish_failed')))[active_step]
            if self._fail(context, active_step, code):
                try:
                    self._notify_failure(context)
                except Exception:
                    # Notification storage failure must never change/rerun ETL.
                    self._notification_error_code = 'storage_unavailable'
        finally:
            self._release(context['lease'])
        with self._transaction() as connection:
            row = connection.execute('SELECT * FROM monitor_runs WHERE run_id=?', (context['run_id'],)).fetchone()
            return self._run(connection, row)

    def run_now(self, user):
        """Synchronous explicit attempt, including when automatic execution is off."""
        return self._execute(self._claim(user, 'manual'))

    def _tick(self):
        with self._transaction() as connection:
            config = dict(self._config(connection))
        if not config['enabled'] or config['next_run_at'] > time.time():
            return
        try:
            user = validate_identity(self.identities.get_user(config['updated_by']))
        except Exception:
            # Missing/malformed/currently inaccessible identity is always fail closed.
            user = None
        if user is None or user != dict(id=config['updated_by'], org='A', role='admin'):
            with self._transaction(write=True) as connection:
                if self._config(connection)['version'] == config['version']:
                    connection.execute('UPDATE monitor_config SET enabled=0,next_run_at=NULL,version=version+1,updated_at=? WHERE job_id=?',
                                       (time.time(), JOB_ID))
                    self._log(connection, 'schedule.permission_revoked', time.time(), error_code='permission_revoked')
            return
        context = self._claim(user, 'timer')
        if context is not None:
            self._execute(context)

    def _worker(self):
        while not self._stop_event.is_set():
            try:
                self._tick()
                self._worker_error_code = None
            except Exception as error:
                self._worker_error_code = 'storage_unavailable' if isinstance(error, StateUnavailable) else 'worker_failed'
                try:
                    with self._transaction(write=True) as connection:
                        self._log(connection, 'worker.error', time.time(), error_code=self._worker_error_code)
                except Exception:
                    pass
            self._stop_event.wait(self.poll_interval_seconds)

    def start(self):
        """Explicitly start one local interval thread; repeated calls are idempotent."""
        with self._thread_lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._worker, name='synthetic-report-worker', daemon=True)
            self._thread.start()
            return True

    def stop(self, timeout=10):
        """Wake and join the worker. False means an active attempt has not finished."""
        timeout = _number(timeout, 0, 60, 'stop timeout')
        with self._thread_lock:
            thread = self._thread
            self._stop_event.set()
            if thread is None:
                return True
            if thread is threading.current_thread():
                return False
            # Holding this lock prevents start() from replacing the event/thread
            # while a concurrent stop() is joining this one.
            thread.join(timeout)
            return not thread.is_alive()

    @staticmethod
    def _run(connection, row):
        if row is None:
            return None
        steps = connection.execute('SELECT * FROM monitor_steps WHERE run_id=? ORDER BY ordinal', (row['run_id'],)).fetchall()
        return dict(run_id=row['run_id'], trigger=row['trigger'], status=row['status'], error_code=row['error_code'],
                    error=_ERROR_MESSAGES.get(row['error_code']), scheduled_for=_local(row['scheduled_for']),
                    started_at=_local(row['started_at']), finished_at=_local(row['finished_at']),
                    started_at_epoch=row['started_at'], finished_at_epoch=row['finished_at'],
                    duration_seconds=row['duration_seconds'], row_count=row['row_count'],
                    steps=[dict(name=step['name'], ordinal=step['ordinal'], status=step['status'],
                                started_at=_local(step['started_at']), finished_at=_local(step['finished_at']),
                                duration_seconds=step['duration_seconds'], row_count=step['row_count'],
                                error_code=step['error_code'], error=_ERROR_MESSAGES.get(step['error_code'])) for step in steps])

    def export_diagnostics(self, user, run_id):
        """Read-only selected-attempt diagnostic metadata for current org-A readers.

        This explicit allowlist omits raw configuration, user identity, paths,
        environment variables, tracebacks, credentials, row payloads and tokens.
        Only the selected attempt's version number is configuration evidence.
        """
        self._auth(user)
        if (not isinstance(run_id, str) or len(run_id) != 32 or
                any(character not in '0123456789abcdef' for character in run_id)):
            raise ValidationError('Select a valid job attempt ID.')
        with self._transaction() as connection:
            row = connection.execute('SELECT * FROM monitor_runs WHERE org=? AND job_id=? AND run_id=?',
                                     ('A', JOB_ID, run_id)).fetchone()
            if row is None:
                raise NotFound('The selected job attempt is unavailable.')
            run = self._run(connection, row)
            run['config_version'] = row['config_version']
            return dict(format_version=1, job_id=JOB_ID, timezone=TIMEZONE, synthetic=True,
                        run=run, related_report_refs=[dict(report_id=JOB_ID, kind='local-synthetic-summary', synthetic=True)])

    def status(self, user, limit=20):
        self._auth(user)
        if type(limit) is not int or not 1 <= limit <= MAX_RUNS:
            raise ValidationError('limit must be an integer between 1 and 100.')
        with self._transaction() as connection:
            config = self._config(connection)
            rows = connection.execute('SELECT * FROM monitor_runs ORDER BY started_at DESC,run_id LIMIT ?', (limit,)).fetchall()
            runs = [self._run(connection, row) for row in rows]
            publication = connection.execute('SELECT * FROM monitor_publication WHERE job_id=?', (JOB_ID,)).fetchone()
            logs = connection.execute('SELECT * FROM monitor_logs ORDER BY id DESC LIMIT 50').fetchall()
            notification_config = connection.execute('SELECT * FROM monitor_notification_config WHERE job_id=?', (JOB_ID,)).fetchone()
            notifications = connection.execute('SELECT * FROM monitor_notifications ORDER BY created_at DESC,notification_id LIMIT 20').fetchall()
            published = (None if publication is None else
                         dict(run_id=publication['run_id'], published_at=_local(publication['published_at']),
                              row_count=publication['row_count'], revenue=publication['revenue'],
                              cost=publication['cost'], profit=publication['profit'], synthetic=True))
            last_success_at = None if publication is None else publication['published_at']
            return dict(job_id=JOB_ID, name=JOB_NAME, org='A', enabled=bool(config['enabled']),
                        interval_seconds=config['interval_seconds'], minimum_interval_seconds=self.min_interval_seconds,
                        maximum_interval_seconds=MAX_INTERVAL_SECONDS, timezone=TIMEZONE,
                        next_run=_local(config['next_run_at']), next_run_at=config['next_run_at'],
                        scheduler_running=self._thread is not None and self._thread.is_alive(),
                        worker_error_code=self._worker_error_code, last_run=runs[0] if runs else None, runs=runs,
                        last_success=_local(last_success_at), last_success_at=last_success_at,
                        data_freshness_seconds=None if last_success_at is None else max(0, time.time()-last_success_at),
                        published_summary=published, synthetic=True, real_delivery=False,
                        notification_config=dict(enabled=bool(notification_config['enabled']), owner_email=notification_config['owner_email']),
                        notification_error_code=self._notification_error_code,
                        notification_logs=[dict(notification_id=row['notification_id'], run_id=row['run_id'],
                                                status=row['status'], error_code=row['error_code'],
                                                created_at=_local(row['created_at']), finished_at=_local(row['finished_at']),
                                                message=_NOTIFICATION_MESSAGES[row['status']], real_delivery=False,
                                                monitor_link=_MONITOR_LINK) for row in notifications],
                        logs=[dict(id=row['id'], occurred_at=_local(row['occurred_at']), code=row['code'],
                                   run_id=row['run_id'], message=_LOG_MESSAGES[row['code']],
                                   error_code=row['error_code'], error=_ERROR_MESSAGES.get(row['error_code'])) for row in logs])
