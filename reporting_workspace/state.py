"""Optional, same-host SQLite state; never use this file on NFS or a cluster.

Callers supply an absolute path on a trusted local filesystem. There are no
background workers or network side effects. Lease expiry uses wall-clock time,
so operators must keep the host clock stable. A lease cannot stop an expired
worker: the protected resource must enforce its fencing value. Restoring an old
backup also restores old fencing values, so fence consumers must be reconciled
before workers resume. Job claims deduplicate attempts, not external effects;
an unfinished claim is uncertain and is never automatically retried.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from typing import List, Optional


SCHEMA_VERSION = 2
BUSY_TIMEOUT_MS = 5000
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}\Z")
_EVENTS = frozenset({
    "lease.acquired", "lease.renewed", "lease.released", "job.claimed",
    "job.succeeded", "job.failed", "access.denied", "runtime.started",
    "runtime.stopped",
    "login.succeeded", "login.failed", "logout.succeeded", "provider.error", "report.export",
    "crud.created", "crud.updated", "crud.deleted", "crud.restored",
})
_OUTCOMES = frozenset({"success", "failure", "denied"})
_FAILURE_CODES = frozenset({"execution_failed", "interrupted", "cancelled", "unknown"})
_AUDIT_CODES = _FAILURE_CODES | frozenset({
    "invalid_credentials", "authentication_required", "permission_denied",
    "provider_unavailable", "provider_failed",
})

# Preserve the exact version 1 contract so only validated databases are migrated.
_SCHEMA_DDL_V1 = {
    ("table", "leases", "leases"): """
CREATE TABLE leases (
        key TEXT PRIMARY KEY NOT NULL,
        owner TEXT NOT NULL,
        token TEXT NOT NULL,
        fencing INTEGER NOT NULL CHECK (fencing > 0),
        expires_at REAL NOT NULL
    )
    """,
    ("table", "job_runs", "job_runs"): """
CREATE TABLE job_runs (
        job_id TEXT NOT NULL,
        run_key TEXT NOT NULL,
        owner TEXT NOT NULL,
        token TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('running', 'succeeded', 'failed')),
        claimed_at REAL NOT NULL,
        finished_at REAL,
        failure_code TEXT,
        PRIMARY KEY (job_id, run_key),
        CHECK ((state = 'running' AND finished_at IS NULL AND failure_code IS NULL)
            OR (state = 'succeeded' AND finished_at IS NOT NULL AND failure_code IS NULL)
            OR (state = 'failed' AND finished_at IS NOT NULL AND failure_code IS NOT NULL))
    )
    """,
    ("table", "audit_events", "audit_events"): """
CREATE TABLE audit_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        occurred_at REAL NOT NULL,
        event TEXT NOT NULL,
        actor_id TEXT,
        resource_id TEXT,
        outcome TEXT NOT NULL CHECK (outcome IN ('success', 'failure', 'denied')),
        request_id TEXT,
        code TEXT
    )
    """,
    ("index", "job_runs_state", "job_runs"): "CREATE INDEX job_runs_state ON job_runs (state, claimed_at)",
}
_SCHEMA_COLUMNS_V1 = {
    "leases": (
        ("key", "TEXT", 1, None, 1), ("owner", "TEXT", 1, None, 0),
        ("token", "TEXT", 1, None, 0), ("fencing", "INTEGER", 1, None, 0),
        ("expires_at", "REAL", 1, None, 0),
    ),
    "job_runs": (
        ("job_id", "TEXT", 1, None, 1), ("run_key", "TEXT", 1, None, 2),
        ("owner", "TEXT", 1, None, 0), ("token", "TEXT", 1, None, 0),
        ("state", "TEXT", 1, None, 0), ("claimed_at", "REAL", 1, None, 0),
        ("finished_at", "REAL", 0, None, 0), ("failure_code", "TEXT", 0, None, 0),
    ),
    "audit_events": (
        ("event_id", "INTEGER", 0, None, 1), ("occurred_at", "REAL", 1, None, 0),
        ("event", "TEXT", 1, None, 0), ("actor_id", "TEXT", 0, None, 0),
        ("resource_id", "TEXT", 0, None, 0), ("outcome", "TEXT", 1, None, 0),
        ("request_id", "TEXT", 0, None, 0), ("code", "TEXT", 0, None, 0),
    ),
}


_REPORT_DEFINITIONS_DDL = {
    ("table", "report_definitions", "report_definitions"): """
CREATE TABLE report_definitions (
        id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 32 AND id NOT GLOB '*[^0-9a-f]*'),
        org TEXT NOT NULL CHECK (length(org) BETWEEN 1 AND 128),
        owner_id TEXT NOT NULL CHECK (length(owner_id) = 64 AND owner_id NOT GLOB '*[^0-9a-f]*'),
        create_key TEXT NOT NULL CHECK (length(create_key) = 32 AND create_key NOT GLOB '*[^0-9a-f]*'),
        name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
        description TEXT NOT NULL CHECK (length(description) <= 1000),
        cadence TEXT NOT NULL CHECK (cadence IN ('manual', 'daily', 'weekly', 'monthly')),
        enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
        version INTEGER NOT NULL CHECK (typeof(version) = 'integer' AND version > 0),
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        deleted_at REAL,
        UNIQUE (org, owner_id, create_key)
    )
    """,
    ("index", "report_definitions_org_updated", "report_definitions"):
        "CREATE INDEX report_definitions_org_updated ON report_definitions (org, updated_at DESC, id)",
}
_SCHEMA_DDL = dict(_SCHEMA_DDL_V1)
_SCHEMA_DDL.update(_REPORT_DEFINITIONS_DDL)
_SCHEMA_COLUMNS = dict(_SCHEMA_COLUMNS_V1)
_SCHEMA_COLUMNS["report_definitions"] = (
    ("id", "TEXT", 1, None, 1), ("org", "TEXT", 1, None, 0),
    ("owner_id", "TEXT", 1, None, 0), ("create_key", "TEXT", 1, None, 0),
    ("name", "TEXT", 1, None, 0),
    ("description", "TEXT", 1, None, 0), ("cadence", "TEXT", 1, None, 0),
    ("enabled", "INTEGER", 1, None, 0), ("version", "INTEGER", 1, None, 0),
    ("created_at", "REAL", 1, None, 0), ("updated_at", "REAL", 1, None, 0),
    ("deleted_at", "REAL", 0, None, 0),
)


def _ddl_tokens(sql):
    # Ignore formatting, but preserve literal values and all other SQL tokens.
    return tuple(re.findall(r"'(?:''|[^'])*'|[A-Za-z_][A-Za-z_0-9]*|[0-9]+|[^\s]", sql))


class StateError(RuntimeError):
    """The state database is incompatible or cannot safely perform an action."""


class UnsupportedSchemaVersion(StateError):
    """The database requires a newer implementation; no migration was made."""


@dataclass(frozen=True)
class Lease:
    key: str
    owner: str
    token: str
    fencing: int
    expires_at: float


@dataclass(frozen=True)
class JobRun:
    job_id: str
    run_key: str
    owner: str
    state: str
    claimed_at: float
    finished_at: Optional[float]
    failure_code: Optional[str]


@dataclass(frozen=True)
class AuditEvent:
    event_id: int
    occurred_at: float
    event: str
    actor_id: Optional[str]
    resource_id: Optional[str]
    outcome: str
    request_id: Optional[str]
    code: Optional[str]


def _identifier(value, name):
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError("{} must be a 1-128 character opaque identifier".format(name))
    return value


def _ttl(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("ttl must be a positive finite number")
    try:
        value = float(value)
    except OverflowError:
        raise ValueError("ttl must be a positive finite number") from None
    if not math.isfinite(value) or value <= 0:
        raise ValueError("ttl must be a positive finite number")
    return value


def _path(value):
    value = os.fspath(value)
    if (not isinstance(value, str) or not os.path.isabs(value)
            or value.startswith(("//", "\\\\")) or "\x00" in value):
        raise ValueError("state requires an absolute local filesystem path")
    path = Path(value).resolve()
    if not path.parent.is_dir() or path.is_dir():
        raise ValueError("state requires a file in an existing local directory")
    if path.exists() and not path.is_file():
        raise ValueError("state requires a regular local file")
    return path


class StateStore:
    """Durable local state with per-operation connections safe across threads/fork.

    Schema zero migrates only if empty; exact version one migrates atomically.
    Existing files are never silently reset.
    Identifiers must be opaque: do not pass emails, credentials or report data.
    SQLite errors propagate so callers cannot mistake a failed write for success.
    """

    def __init__(self, path):
        self.path = str(_path(path))
        try:
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        self._initialize()

    def _connect(self):
        # mode=rw prevents recreating a missing database after initialization.
        connection = sqlite3.connect(
            Path(self.path).as_uri() + "?mode=rw", uri=True,
            timeout=BUSY_TIMEOUT_MS / 1000.0, isolation_level=None,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = {}".format(BUSY_TIMEOUT_MS))
            connection.execute("PRAGMA synchronous = FULL")
            return connection
        except BaseException:
            connection.close()
            raise

    @staticmethod
    def _version(connection):
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise UnsupportedSchemaVersion("state schema is newer than supported version 2")
        return version

    def _initialize(self):
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            version = self._version(connection)
            if version == 0:
                objects = connection.execute(
                    "SELECT name FROM sqlite_master WHERE name NOT GLOB 'sqlite_*'"
                ).fetchall()
                if objects:
                    raise StateError("unversioned nonempty state database is not supported")
                # Individual statements keep DDL and user_version in one transaction.
                for statement in _SCHEMA_DDL.values():
                    connection.execute(statement)
                connection.execute("PRAGMA user_version = 2")
            elif version == 1:
                # Validate before touching the old database. DDL, version and
                # final validation share the same transaction and roll back.
                self._validate_schema(connection, version=1)
                for statement in _REPORT_DEFINITIONS_DDL.values():
                    connection.execute(statement)
                connection.execute("PRAGMA user_version = 2")
            elif version != SCHEMA_VERSION:
                raise StateError("unsupported state schema version")
            self._validate_schema(connection)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _validate_schema(connection, version=SCHEMA_VERSION):
        # table_info verifies ordering, affinity declarations, defaults and keys;
        # exact DDL tokens additionally verify CHECK/AUTOINCREMENT constraints.
        if version not in (1, SCHEMA_VERSION):
            raise StateError("unsupported schema validation version")
        schema_columns = _SCHEMA_COLUMNS_V1 if version == 1 else _SCHEMA_COLUMNS
        schema_ddl = _SCHEMA_DDL_V1 if version == 1 else _SCHEMA_DDL
        for table, columns in schema_columns.items():
            actual = tuple(tuple(row) for row in connection.execute(
                "PRAGMA table_info({})".format(table)))
            expected = tuple((index,) + column for index, column in enumerate(columns))
            if actual != expected:
                raise StateError("state schema does not match version {}".format(version))
        actual_ddl = {
            (row[0], row[1], row[2]): _ddl_tokens(row[3])
            for row in connection.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE name NOT GLOB 'sqlite_*'"
            )
        }
        expected_ddl = {key: _ddl_tokens(sql) for key, sql in schema_ddl.items()}
        if actual_ddl != expected_ddl:
            raise StateError("state constraints or indexes do not match version {}".format(version))

    @contextmanager
    def _transaction(self, write=False):
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            if self._version(connection) != SCHEMA_VERSION:
                raise StateError("state schema changed; reopen with a compatible implementation")
            self._validate_schema(connection)
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _audit(connection, event, actor_id, resource_id, outcome, now, request_id=None, code=None):
        return connection.execute(
            "INSERT INTO audit_events (occurred_at, event, actor_id, resource_id, outcome, request_id, code) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)", (now, event, actor_id, resource_id, outcome, request_id, code),
        ).lastrowid

    def acquire(self, key, owner, ttl=30) -> Optional[Lease]:
        """Atomically acquire an absent/expired lease, incrementing its durable fence."""
        _identifier(key, "key")
        _identifier(owner, "owner")
        ttl = _ttl(ttl)
        with self._transaction(write=True) as connection:
            now = time.time()
            expires_at = now + ttl
            if not math.isfinite(expires_at):
                raise ValueError("lease expiry must be finite")
            previous = connection.execute("SELECT * FROM leases WHERE key = ?", (key,)).fetchone()
            if previous is not None and previous["expires_at"] > now:
                return None
            fencing = 1 if previous is None else previous["fencing"] + 1
            if fencing > 9223372036854775807:
                raise StateError("lease fencing sequence is exhausted")
            lease = Lease(key, owner, secrets.token_urlsafe(32), fencing, expires_at)
            if previous is None:
                connection.execute("INSERT INTO leases (key, owner, token, fencing, expires_at) VALUES (?, ?, ?, ?, ?)",
                                   (key, owner, lease.token, fencing, expires_at))
            else:
                connection.execute(
                    "UPDATE leases SET owner = ?, token = ?, fencing = ?, expires_at = ? WHERE key = ?",
                    (owner, lease.token, fencing, expires_at, key),
                )
            self._audit(connection, "lease.acquired", owner, key, "success", now)
            return lease

    def renew(self, key, owner, token, ttl=30) -> Optional[Lease]:
        """Renew only the current, unexpired token and owner; keep its fencing value."""
        _identifier(key, "key")
        _identifier(owner, "owner")
        ttl = _ttl(ttl)
        if not isinstance(token, str):
            raise ValueError("token must be a string")
        with self._transaction(write=True) as connection:
            now = time.time()
            expires_at = now + ttl
            if not math.isfinite(expires_at):
                raise ValueError("lease expiry must be finite")
            changed = connection.execute(
                "UPDATE leases SET expires_at = ? "
                "WHERE key = ? AND owner = ? AND token = ? AND expires_at > ?",
                (expires_at, key, owner, token, now),
            ).rowcount
            if not changed:
                return None
            row = connection.execute("SELECT * FROM leases WHERE key = ?", (key,)).fetchone()
            self._audit(connection, "lease.renewed", owner, key, "success", now)
            return Lease(**dict(row))

    def release(self, key, owner, token) -> bool:
        """Release only the current, unexpired token; retain its fencing history."""
        _identifier(key, "key")
        _identifier(owner, "owner")
        if not isinstance(token, str):
            raise ValueError("token must be a string")
        with self._transaction(write=True) as connection:
            now = time.time()
            changed = connection.execute(
                "UPDATE leases SET expires_at = 0 "
                "WHERE key = ? AND owner = ? AND token = ? AND expires_at > ?",
                (key, owner, token, now),
            ).rowcount
            if changed:
                self._audit(connection, "lease.released", owner, key, "success", now)
            return bool(changed)

    def claim_job(self, job_id, run_key, owner) -> Optional[str]:
        """Claim a unique job/run once; running and terminal claims are never retried."""
        _identifier(job_id, "job_id")
        _identifier(run_key, "run_key")
        _identifier(owner, "owner")
        with self._transaction(write=True) as connection:
            previous = connection.execute(
                "SELECT 1 FROM job_runs WHERE job_id = ? AND run_key = ?", (job_id, run_key),
            ).fetchone()
            if previous is not None:
                return None
            token = secrets.token_urlsafe(32)
            now = time.time()
            connection.execute(
                "INSERT INTO job_runs (job_id, run_key, owner, token, state, claimed_at) "
                "VALUES (?, ?, ?, ?, 'running', ?)", (job_id, run_key, owner, token, now),
            )
            self._audit(connection, "job.claimed", owner, job_id, "success", now)
            return token

    def finish_job(self, job_id, run_key, owner, token, success, failure_code=None) -> bool:
        """Finish the exact running claim; retain failures as fixed, non-sensitive codes."""
        _identifier(job_id, "job_id")
        _identifier(run_key, "run_key")
        _identifier(owner, "owner")
        if not isinstance(token, str) or not isinstance(success, bool):
            raise ValueError("token must be a string and success must be a boolean")
        if success:
            if failure_code is not None:
                raise ValueError("successful jobs cannot have a failure code")
        else:
            failure_code = "execution_failed" if failure_code is None else failure_code
            if not isinstance(failure_code, str) or failure_code not in _FAILURE_CODES:
                raise ValueError("failure_code must be an allowed fixed code, never error text")
        with self._transaction(write=True) as connection:
            now = time.time()
            state = "succeeded" if success else "failed"
            changed = connection.execute(
                "UPDATE job_runs SET state = ?, finished_at = ?, failure_code = ? "
                "WHERE job_id = ? AND run_key = ? AND owner = ? AND token = ? AND state = 'running'",
                (state, now, failure_code, job_id, run_key, owner, token),
            ).rowcount
            if changed:
                self._audit(connection, "job." + state, owner, job_id,
                            "success" if success else "failure", now, code=failure_code)
            return bool(changed)

    @staticmethod
    def _job(row):
        if row is None:
            return None
        fields = dict(row)
        fields.pop("token")
        return JobRun(**fields)

    def get_job(self, job_id, run_key) -> Optional[JobRun]:
        """Return durable status without exposing its claim token."""
        _identifier(job_id, "job_id")
        _identifier(run_key, "run_key")
        with self._transaction() as connection:
            return self._job(connection.execute(
                "SELECT * FROM job_runs WHERE job_id = ? AND run_key = ?", (job_id, run_key),
            ).fetchone())

    def list_uncertain_jobs(self, limit=100) -> List[JobRun]:
        """List running claims requiring inspection, including potentially live workers.

        A process restart cannot distinguish a live worker from a crashed worker
        or an external effect committed before the status write. No retry occurs.
        """
        self._limit(limit)
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM job_runs WHERE state = 'running' ORDER BY claimed_at, job_id, run_key LIMIT ?",
                (limit,),
            ).fetchall()
            return [self._job(row) for row in rows]

    def record_audit(self, event, *, actor_id=None, resource_id=None, request_id=None,
                     outcome="success", code=None) -> int:
        """Append allowlisted metadata only; arbitrary fields, text and payloads are rejected."""
        if not isinstance(event, str) or event not in _EVENTS:
            raise ValueError("event is not an allowed audit event")
        if not isinstance(outcome, str) or outcome not in _OUTCOMES:
            raise ValueError("outcome is not an allowed audit outcome")
        if code is not None and (not isinstance(code, str) or code not in _AUDIT_CODES):
            raise ValueError("code must be an allowed fixed audit code")
        for name, value in (("actor_id", actor_id), ("resource_id", resource_id),
                            ("request_id", request_id)):
            if value is not None:
                _identifier(value, name)
        with self._transaction(write=True) as connection:
            return self._audit(connection, event, actor_id, resource_id, outcome,
                               time.time(), request_id=request_id, code=code)

    @staticmethod
    def _limit(limit):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer between 1 and 1000")

    def list_audit(self, after_id=0, limit=100) -> List[AuditEvent]:
        """Read a bounded page of metadata ordered by its durable sequence number."""
        self._limit(limit)
        if isinstance(after_id, bool) or not isinstance(after_id, int) or after_id < 0:
            raise ValueError("after_id must be a nonnegative integer")
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM audit_events WHERE event_id > ? ORDER BY event_id LIMIT ?",
                (after_id, limit),
            ).fetchall()
            return [AuditEvent(**dict(row)) for row in rows]

    def backup_to(self, destination) -> str:
        """Make a consistent SQLite backup to a new absolute local file; never overwrite."""
        destination = _path(destination)
        if str(destination) == self.path:
            raise ValueError("backup destination must differ from the state database")
        with self._transaction() as source:
            descriptor = os.open(str(destination), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
            target = None
            try:
                target = sqlite3.connect(str(destination))
                source.backup(target, pages=128, sleep=0.01)
                if self._version(target) != SCHEMA_VERSION:
                    raise StateError("backup schema does not match version 2")
                self._validate_schema(target)
            except BaseException:
                if target is not None:
                    target.close()
                    target = None
                destination.unlink()
                raise
            finally:
                if target is not None:
                    target.close()
        return str(destination)
