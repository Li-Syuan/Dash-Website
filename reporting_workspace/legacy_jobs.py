"""Offline legacy report-job adapter. No SMTP, threads or scheduler are started.

Authorization callback is server-owned: authorize(actor, action, job_id) -> bool.
Actions are admin_edit, run, read. Never derive actor/policy from browser state.
SQLite run-key claims prevent concurrent duplicate mock execution; they do NOT
provide exactly-once delivery when a real SMTP adapter is later integrated.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import re
import sqlite3
import uuid


class JobValidationError(ValueError):
    pass


class JobAccessDenied(PermissionError):
    pass


class JobConflict(RuntimeError):
    pass


_ZONES = {'UTC': timezone.utc, 'Asia/Taipei': timezone(timedelta(hours=8))}
_ID = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,119}\Z')
_MAIL = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9._+-]{0,63}@example\.invalid\Z')
_TIME = re.compile(r'([01][0-9]|2[0-3]):([0-5][0-9])\Z')


def _id(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise JobValidationError('Invalid identifier.')
    return value


def _now():
    return datetime.now(timezone.utc).isoformat()


def validate_settings(settings):
    fields = {'cadence', 'time', 'timezone', 'weekday', 'recipients', 'enabled'}
    if not isinstance(settings, dict) or set(settings) - fields:
        raise JobValidationError('Unknown schedule fields.')
    result = dict(settings)
    if result.get('cadence') not in ('daily', 'weekly', 'quarterly'):
        raise JobValidationError('cadence must be daily, weekly or quarterly.')
    if not isinstance(result.get('time'), str) or not _TIME.fullmatch(result['time']):
        raise JobValidationError('time must be HH:MM.')
    if result.get('timezone') not in _ZONES:
        raise JobValidationError('Supported explicit timezones: UTC, Asia/Taipei.')
    if type(result.get('enabled')) is not bool:
        raise JobValidationError('enabled must be boolean.')
    weekday = result.get('weekday')
    if result['cadence'] == 'weekly':
        if type(weekday) is not int or not 0 <= weekday <= 6:
            raise JobValidationError('weekly weekday must be 0 (Monday) through 6.')
    elif weekday is not None:
        raise JobValidationError('weekday is only used by weekly schedules.')
    recipients = result.get('recipients')
    if not isinstance(recipients, list) or not 1 <= len(recipients) <= 30:
        raise JobValidationError('Provide 1 to 30 synthetic recipients.')
    if any(not isinstance(r, str) or not _MAIL.fullmatch(r) for r in recipients):
        raise JobValidationError('Only synthetic example.invalid recipients are allowed.')
    if len(set(r.lower() for r in recipients)) != len(recipients):
        raise JobValidationError('Duplicate recipients.')
    result['recipients'] = list(recipients)
    result['weekday'] = weekday
    return result


def preview_schedule(settings, after, count=3):
    """Return potential UTC fire times strictly after an aware timestamp.

    Quarterly means Jan/Apr/Jul/Oct 1. Disabled schedules have no fire times.
    This calculates settings only and never registers an actual scheduled job.
    """
    settings = validate_settings(settings)
    if not isinstance(after, datetime) or after.utcoffset() is None:
        raise JobValidationError('after must be timezone-aware.')
    if type(count) is not int or not 1 <= count <= 12:
        raise JobValidationError('count must be 1 through 12.')
    if not settings['enabled']:
        return []
    local = after.astimezone(_ZONES[settings['timezone']])
    hour, minute = map(int, settings['time'].split(':'))
    candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    results = []
    for _ in range(1200):
        cadence = settings['cadence']
        matches = (cadence == 'daily' or
                   cadence == 'weekly' and candidate.weekday() == settings['weekday'] or
                   cadence == 'quarterly' and candidate.day == 1 and candidate.month in (1, 4, 7, 10))
        if candidate > local and matches:
            results.append(candidate.astimezone(timezone.utc).isoformat())
            if len(results) == count:
                return results
        candidate += timedelta(days=1)
    raise JobValidationError('Preview horizon exceeded.')


class LegacyJobAdapter:
    """Durable mock settings, runs and audit log. Caller owns DB file access."""
    def __init__(self, database_path, authorize):
        if not callable(authorize):
            raise TypeError('A server authorization callback is required.')
        if str(database_path) == ':memory:':
            raise JobValidationError('A durable SQLite file is required.')
        self.database_path = str(database_path)
        self.authorize = authorize
        with self._connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS legacy_job_settings (
                    job_id TEXT PRIMARY KEY, settings TEXT NOT NULL,
                    version INTEGER NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS legacy_job_runs (
                    run_id TEXT PRIMARY KEY, job_id TEXT NOT NULL,
                    run_key TEXT NOT NULL, actor TEXT NOT NULL,
                    status TEXT NOT NULL, detail TEXT NOT NULL,
                    settings TEXT NOT NULL, retry_of TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(job_id, run_key));
                CREATE UNIQUE INDEX IF NOT EXISTS legacy_retry_once
                    ON legacy_job_runs(retry_of) WHERE retry_of IS NOT NULL;
                CREATE TABLE IF NOT EXISTS legacy_job_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL,
                    action TEXT NOT NULL, job_id TEXT NOT NULL,
                    run_id TEXT, detail TEXT NOT NULL, created_at TEXT NOT NULL);
            ''')

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _guard(self, actor, action, job_id):
        _id(job_id)
        if not isinstance(actor, str) or not actor.strip() or len(actor) > 200:
            raise JobAccessDenied('A server identity is required.')
        if self.authorize(actor, action, job_id) is not True:
            raise JobAccessDenied('Action not authorized.')

    def _audit(self, db, actor, action, job_id, detail, run_id=None):
        db.execute('INSERT INTO legacy_job_audit(actor,action,job_id,run_id,detail,created_at) VALUES(?,?,?,?,?,?)',
                   (actor, action, job_id, run_id, json.dumps(detail), _now()))

    def save_settings(self, actor, job_id, settings, expected_version=0):
        self._guard(actor, 'admin_edit', job_id)
        settings = validate_settings(settings)
        if type(expected_version) is not int or expected_version < 0:
            raise JobValidationError('expected_version must be nonnegative.')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT version FROM legacy_job_settings WHERE job_id=?', (job_id,)).fetchone()
            version = row['version'] if row else 0
            if expected_version != version:
                raise JobConflict('Settings changed; reload before saving.')
            db.execute('INSERT OR REPLACE INTO legacy_job_settings VALUES(?,?,?,?)',
                       (job_id, json.dumps(settings), version + 1, _now()))
            self._audit(db, actor, 'settings_saved', job_id, {'version': version + 1})
        return {'job_id': job_id, 'version': version + 1, 'settings': settings}

    def get_settings(self, actor, job_id):
        self._guard(actor, 'read', job_id)
        with self._connect() as db:
            row = db.execute('SELECT * FROM legacy_job_settings WHERE job_id=?', (job_id,)).fetchone()
        if row is None:
            raise JobValidationError('Unknown job.')
        result = dict(row)
        result['settings'] = json.loads(result['settings'])
        return result

    def preview(self, actor, job_id, after, count=3):
        return preview_schedule(self.get_settings(actor, job_id)['settings'], after, count)

    def run_mock(self, actor, job_id, run_key, source_ok=True, report_ok=True,
                 delivery='accepted', retry_of=None, acknowledge_duplicate_risk=False):
        """Explicit one-shot simulation. 'accepted' means mock transport acceptance,
        never inbox delivery. An interrupted claimed run remains visible as claimed;
        it is not silently taken over or automatically retried.
        The enabled setting controls schedule preview only. Explicit manual
        run_mock is intentionally permitted when the schedule is disabled.
        """
        self._guard(actor, 'run', job_id)
        _id(run_key)
        if type(source_ok) is not bool or type(report_ok) is not bool:
            raise JobValidationError('Stage outcomes must be boolean.')
        if delivery not in ('accepted', 'partial', 'uncertain', 'failure'):
            raise JobValidationError('Invalid mock delivery result.')
        if type(acknowledge_duplicate_risk) is not bool:
            raise JobValidationError('Duplicate-risk acknowledgement must be boolean.')
        if retry_of is not None:
            _id(retry_of)
        run_id = uuid.uuid4().hex
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            config = db.execute('SELECT settings FROM legacy_job_settings WHERE job_id=?', (job_id,)).fetchone()
            if not config:
                raise JobValidationError('Configure the job first.')
            if retry_of:
                previous = db.execute('SELECT status FROM legacy_job_runs WHERE run_id=? AND job_id=?', (retry_of, job_id)).fetchone()
                if not previous or previous['status'] not in ('source_failed', 'report_failed', 'send_failed', 'partial', 'uncertain'):
                    raise JobConflict('Only completed failed, partial or uncertain runs may be manually retried.')
                if previous['status'] in ('partial', 'uncertain') and not acknowledge_duplicate_risk:
                    raise JobConflict('Retry may duplicate accepted mail; explicitly acknowledge this risk.')
            try:
                db.execute('INSERT INTO legacy_job_runs VALUES(?,?,?,?,?,?,?,?,?,?)',
                           (run_id, job_id, run_key, actor, 'claimed', '{}', config['settings'], retry_of, _now(), _now()))
            except sqlite3.IntegrityError:
                raise JobConflict('Run key or retry already claimed; no execution occurred.') from None
            self._audit(db, actor, 'run_claimed', job_id,
                        {'mock': True, 'retry_of': retry_of, 'duplicate_risk_acknowledged': acknowledge_duplicate_risk}, run_id)
        steps = {'source': 'succeeded' if source_ok else 'failed', 'report': 'blocked', 'send': 'blocked', 'mock': True}
        status = 'source_failed'
        if source_ok:
            steps['report'] = 'succeeded' if report_ok else 'failed'
            status = 'report_failed'
            if report_ok:
                steps['send'] = delivery
                status = 'send_failed' if delivery == 'failure' else delivery
        steps['summary'] = {
            'source_failed': 'Source failed; report and send were blocked.',
            'report_failed': 'Report generation failed; send was blocked.',
            'send_failed': 'Mock transport reported failure; no automatic retry.',
            'accepted': 'Mock transport accepted the message; inbox delivery is not verified.',
            'partial': 'Mock transport accepted only some recipients; whole-run retry can duplicate messages.',
            'uncertain': 'Mock transport outcome is uncertain; no automatic retry.'}[status]
        with self._connect() as db:
            db.execute('UPDATE legacy_job_runs SET status=?,detail=?,updated_at=? WHERE run_id=?',
                       (status, json.dumps(steps), _now(), run_id))
            self._audit(db, actor, 'run_finished', job_id, steps, run_id)
        return {'run_id': run_id, 'job_id': job_id, 'run_key': run_key, 'status': status, 'detail': steps}

    def list_runs(self, actor, job_id, limit=50):
        self._guard(actor, 'read', job_id)
        if type(limit) is not int or not 1 <= limit <= 200:
            raise JobValidationError('limit must be 1 through 200.')
        with self._connect() as db:
            rows = db.execute('SELECT * FROM legacy_job_runs WHERE job_id=? ORDER BY created_at DESC LIMIT ?', (job_id, limit)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item['detail'] = json.loads(item['detail'])
            item['settings'] = json.loads(item['settings'])
            result.append(item)
        return result

    def audit_log(self, actor, job_id, limit=100):
        self._guard(actor, 'read', job_id)
        if type(limit) is not int or not 1 <= limit <= 500:
            raise JobValidationError('limit must be 1 through 500.')
        with self._connect() as db:
            rows = db.execute('SELECT * FROM legacy_job_audit WHERE job_id=? ORDER BY id DESC LIMIT ?', (job_id, limit)).fetchall()
        return [dict(row, detail=json.loads(row['detail'])) for row in rows]
