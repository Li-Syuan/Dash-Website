"""Real SQLite concurrency/restart tests; no company services are imported."""

import ast
from concurrent.futures import ThreadPoolExecutor
import multiprocessing
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from reporting_workspace.state import (
    AuditEvent, BUSY_TIMEOUT_MS, JobRun, Lease, StateError, StateStore,
    UnsupportedSchemaVersion,
)


def _contend(path, operation, owner, start, results):
    try:
        store = StateStore(path)
        if not start.wait(15):
            raise RuntimeError("contention test did not start")
        if operation == "lease":
            value = store.acquire("shared-resource", owner, ttl=60)
        else:
            value = store.claim_job("daily-report", "2026-10-02", owner)
        results.put(("ok", value))
    except BaseException as error:
        results.put(("error", type(error).__name__ + ": " + str(error)))


def _claim_and_exit(path, results):
    token = StateStore(path).claim_job("daily-report", "abandoned", "departed-worker")
    results.put(token)


class StateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "state.sqlite3"
        self.store = StateStore(self.path)

    def test_absolute_file_only_and_private_new_file(self):
        for value in (":memory:", "relative.sqlite", "file:/tmp/state.sqlite", "//server/state", self.directory.name):
            with self.subTest(value=value), self.assertRaises(ValueError):
                StateStore(value)
        with self.assertRaises(ValueError):
            StateStore(Path(self.directory.name) / "missing" / "state.sqlite")
        if os.name == "posix":
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_empty_schema_migrates_once_and_survives_reopen(self):
        with sqlite3.connect(str(self.path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        lease = self.store.acquire("report", "worker")
        reopened = StateStore(self.path)
        self.assertIsNone(reopened.acquire("report", "other"))
        self.assertTrue(reopened.release("report", "worker", lease.token))
        self.assertEqual(reopened.acquire("report", "other").fencing, lease.fencing + 1)

    def test_nonempty_unversioned_database_is_not_adopted(self):
        path = Path(self.directory.name) / "unknown.sqlite"
        with sqlite3.connect(str(path)) as connection:
            connection.execute("CREATE TABLE private_data (value TEXT)")
            connection.execute("INSERT INTO private_data VALUES ('untouched')")
        with self.assertRaises(StateError):
            StateStore(path)
        with sqlite3.connect(str(path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT value FROM private_data").fetchone()[0], "untouched")

    def test_future_schema_fails_closed_on_open_and_every_operation(self):
        lease = self.store.acquire("report", "worker")
        token = self.store.claim_job("report", "run", "worker")
        with sqlite3.connect(str(self.path)) as connection:
            before = connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
            connection.execute("PRAGMA user_version = 2")
        backup = Path(self.directory.name) / "backup.sqlite"
        operations = [
            lambda: StateStore(self.path),
            lambda: self.store.acquire("other", "worker"),
            lambda: self.store.renew("report", "worker", lease.token),
            lambda: self.store.release("report", "worker", lease.token),
            lambda: self.store.claim_job("other", "run", "worker"),
            lambda: self.store.finish_job("report", "run", "worker", token, True),
            lambda: self.store.get_job("report", "run"),
            lambda: self.store.list_uncertain_jobs(),
            lambda: self.store.record_audit("runtime.started"),
            lambda: self.store.list_audit(),
            lambda: self.store.backup_to(backup),
        ]
        for operation in operations:
            with self.assertRaises(UnsupportedSchemaVersion):
                operation()
        self.assertFalse(backup.exists())
        with sqlite3.connect(str(self.path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0], before)
            self.assertEqual(connection.execute("SELECT state FROM job_runs").fetchone()[0], "running")

    def test_invalid_version_one_schema_is_rejected(self):
        path = Path(self.directory.name) / "invalid.sqlite"
        with sqlite3.connect(str(path)) as connection:
            connection.execute("PRAGMA user_version = 1")
        with self.assertRaises(StateError):
            StateStore(path)

    def test_version_one_requires_column_order_types_keys_and_constraints(self):
        with sqlite3.connect(str(self.path)) as connection:
            ddl = dict(connection.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'table' AND name NOT GLOB 'sqlite_*'"
            ))
        malformed = [
            ("leases-reordered", "leases", ddl["leases"].replace(
                "key TEXT PRIMARY KEY NOT NULL,\n        owner TEXT NOT NULL,",
                "owner TEXT NOT NULL,\n        key TEXT PRIMARY KEY NOT NULL,")),
            ("leases-no-primary-key", "leases", ddl["leases"].replace("PRIMARY KEY ", "")),
            ("leases-no-required-owner", "leases", ddl["leases"].replace("owner TEXT NOT NULL", "owner TEXT")),
            ("leases-no-check", "leases", ddl["leases"].replace(" CHECK (fencing > 0)", "")),
            ("leases-wrong-type", "leases", ddl["leases"].replace("fencing INTEGER", "fencing REAL")),
            ("leases-wrong-check", "leases", ddl["leases"].replace("fencing > 0", "fencing >= 0")),
            ("jobs-no-primary-key", "job_runs", ddl["job_runs"].replace("PRIMARY KEY (job_id, run_key),", "")),
            ("jobs-no-state-check", "job_runs", ddl["job_runs"].replace(
                " CHECK (state IN ('running', 'succeeded', 'failed'))", "")),
            ("audit-no-autoincrement", "audit_events", ddl["audit_events"].replace(" AUTOINCREMENT", "")),
            ("audit-no-check", "audit_events", ddl["audit_events"].replace(
                " CHECK (outcome IN ('success', 'failure', 'denied'))", "")),
            ("audit-nullable-event", "audit_events", ddl["audit_events"].replace("event TEXT NOT NULL", "event TEXT")),
        ]
        for name, table, statement in malformed:
            with self.subTest(name=name):
                self.assertNotEqual(statement, ddl[table], "regression fixture must alter the schema")
                path = Path(self.directory.name) / (name + ".sqlite")
                self.store.backup_to(path)
                with sqlite3.connect(str(path)) as connection:
                    connection.execute("DROP TABLE " + table)
                    connection.execute(statement)
                    if table == "job_runs":
                        connection.execute("CREATE INDEX job_runs_state ON job_runs (state, claimed_at)")
                    before = connection.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()
                with self.assertRaises(StateError):
                    StateStore(path)
                with sqlite3.connect(str(path)) as connection:
                    self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
                    self.assertEqual(connection.execute(
                        "SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall(), before)

    def test_version_one_requires_exact_indexes_and_rejects_triggers(self):
        alterations = [
            ["DROP INDEX job_runs_state"],
            ["DROP INDEX job_runs_state", "CREATE INDEX job_runs_state ON job_runs (owner)"],
            ["CREATE TRIGGER mutate_lease AFTER INSERT ON leases BEGIN DELETE FROM leases; END"],
            ["CREATE TABLE unexpected (value TEXT)"],
        ]
        for index, statements in enumerate(alterations):
            with self.subTest(statements=statements):
                path = Path(self.directory.name) / ("schema-object-{}.sqlite".format(index))
                self.store.backup_to(path)
                with sqlite3.connect(str(path)) as connection:
                    for statement in statements:
                        connection.execute(statement)
                with self.assertRaises(StateError):
                    StateStore(path)

    def test_schema_is_revalidated_on_live_operations_and_backup(self):
        with sqlite3.connect(str(self.path)) as connection:
            connection.execute("DROP TABLE leases")
            connection.execute("CREATE TABLE leases (owner TEXT, key TEXT, token TEXT, fencing INTEGER, expires_at REAL)")
        backup = Path(self.directory.name) / "invalid-backup.sqlite"
        for operation in (
                lambda: self.store.acquire("report", "worker"),
                lambda: self.store.claim_job("report", "run", "worker"),
                lambda: self.store.list_audit(), lambda: self.store.backup_to(backup)):
            with self.assertRaises(StateError):
                operation()
        self.assertFalse(backup.exists())
        with sqlite3.connect(str(self.path)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM leases").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM job_runs").fetchone()[0], 0)

    def test_deleted_database_is_not_recreated_during_operations(self):
        self.path.unlink()
        with self.assertRaises(sqlite3.OperationalError):
            self.store.acquire("report", "worker")
        self.assertFalse(self.path.exists())

    def test_lease_expiry_owner_token_and_fence(self):
        with patch("reporting_workspace.state.time.time", return_value=100):
            lease = self.store.acquire("report", "worker", 10)
        self.assertIsInstance(lease, Lease)
        self.assertEqual(lease.expires_at, 110)
        with patch("reporting_workspace.state.time.time", return_value=109):
            self.assertIsNone(self.store.acquire("report", "worker"))
            self.assertFalse(self.store.release("report", "other", lease.token))
            self.assertFalse(self.store.release("report", "worker", "wrong-token"))
            self.assertIsNone(self.store.renew("report", "other", lease.token))
            self.assertIsNone(self.store.renew("report", "worker", "wrong-token"))
        with patch("reporting_workspace.state.time.time", return_value=110):
            self.assertFalse(self.store.release("report", "worker", lease.token))
            self.assertIsNone(self.store.renew("report", "worker", lease.token))
            replacement = StateStore(self.path).acquire("report", "worker", 10)
            self.assertGreater(replacement.fencing, lease.fencing)
            self.assertNotEqual(replacement.token, lease.token)
            self.assertFalse(self.store.release("report", "worker", lease.token))
            self.assertIsNone(self.store.renew("report", "worker", lease.token))
            self.assertTrue(self.store.release("report", "worker", replacement.token))
            newest = self.store.acquire("report", "worker", 10)
            self.assertEqual(newest.fencing, replacement.fencing + 1)

    def test_renewal_preserves_token_and_fencing(self):
        with patch("reporting_workspace.state.time.time", return_value=100):
            lease = self.store.acquire("report", "worker", 10)
        with patch("reporting_workspace.state.time.time", return_value=105):
            renewed = self.store.renew("report", "worker", lease.token, 20)
        self.assertEqual(renewed.token, lease.token)
        self.assertEqual(renewed.fencing, lease.fencing)
        self.assertEqual(renewed.expires_at, 125)
        with patch("reporting_workspace.state.time.time", return_value=124):
            self.assertIsNone(self.store.acquire("report", "other"))
        with patch("reporting_workspace.state.time.time", return_value=125):
            self.assertIsNotNone(self.store.acquire("report", "other"))

    def test_invalid_ttls_and_identifiers_are_not_written(self):
        for ttl in (0, -1, float("nan"), float("inf"), True, "10", None, 10 ** 1000):
            with self.subTest(ttl=str(ttl)[:30]), self.assertRaises(ValueError):
                self.store.acquire("report", "worker", ttl)
            with self.assertRaises(ValueError):
                self.store.renew("report", "worker", "wrong", ttl)
        for key in ("", "has spaces", "x" * 129, "email@example.com", "line\nbreak", None):
            with self.assertRaises(ValueError):
                self.store.acquire(key, "worker")
        self.assertEqual(self.store.list_audit(), [])

    def test_success_requires_exact_running_claim_and_is_durable(self):
        token = self.store.claim_job("report", "run", "worker")
        self.assertIsInstance(token, str)
        self.assertIsNone(self.store.claim_job("report", "run", "other"))
        self.assertFalse(self.store.finish_job("report", "run", "other", token, True))
        self.assertFalse(self.store.finish_job("report", "run", "worker", "wrong", True))
        self.assertTrue(self.store.finish_job("report", "run", "worker", token, True))
        self.assertFalse(self.store.finish_job("report", "run", "worker", token, False))
        store = StateStore(self.path)
        job = store.get_job("report", "run")
        self.assertIsInstance(job, JobRun)
        self.assertEqual(job.state, "succeeded")
        self.assertIsNotNone(job.finished_at)
        self.assertIsNone(job.failure_code)
        self.assertFalse(hasattr(job, "token"))
        self.assertIsNone(store.claim_job("report", "run", "worker"))
        self.assertIsNotNone(store.claim_job("report", "next-run", "worker"))
        self.assertIsNone(store.get_job("missing", "run"))

    def test_failed_claim_retained_without_retry_or_error_payload(self):
        token = self.store.claim_job("report", "run", "worker")
        with self.assertRaises(ValueError):
            self.store.finish_job("report", "run", "worker", token, False,
                                  failure_code="password=secret report contents")
        self.assertTrue(self.store.finish_job("report", "run", "worker", token, False))
        store = StateStore(self.path)
        self.assertEqual(store.get_job("report", "run").state, "failed")
        self.assertEqual(store.get_job("report", "run").failure_code, "execution_failed")
        self.assertIsNone(store.claim_job("report", "run", "other"))
        self.assertEqual(store.list_uncertain_jobs(), [])
        self.assertEqual(store.list_audit()[-1].event, "job.failed")

    def test_exited_worker_leaves_uncertain_running_claim(self):
        context = multiprocessing.get_context("spawn")
        results = context.Queue()
        process = context.Process(target=_claim_and_exit, args=(str(self.path), results))
        process.start()
        try:
            token = results.get(timeout=20)
            process.join(20)
            self.assertEqual(process.exitcode, 0)
        finally:
            if process.is_alive():
                process.terminate()
                process.join(5)
            results.close()
            results.join_thread()
        store = StateStore(self.path)
        uncertain = store.list_uncertain_jobs()
        self.assertEqual([(job.job_id, job.run_key, job.state) for job in uncertain],
                         [("daily-report", "abandoned", "running")])
        self.assertIsNone(store.claim_job("daily-report", "abandoned", "replacement"))
        self.assertTrue(store.finish_job("daily-report", "abandoned", "departed-worker",
                                         token, False, "interrupted"))

    def _competition(self, operation, method="spawn"):
        context = multiprocessing.get_context(method)
        start, results = context.Event(), context.Queue()
        path = str(Path(self.directory.name) / (operation + "-" + method + ".sqlite"))
        # Processes also compete to initialize the same new database.
        processes = [context.Process(target=_contend, args=(path, operation, "worker-" + str(i), start, results))
                     for i in range(6)]
        for process in processes:
            process.start()
        try:
            start.set()
            values = [results.get(timeout=30) for _ in processes]
            for process in processes:
                process.join(20)
                self.assertEqual(process.exitcode, 0)
            self.assertEqual([value for status, value in values if status != "ok"], [])
            winners = [value for status, value in values if status == "ok" and value is not None]
            self.assertEqual(len(winners), 1, values)
            return path, winners[0]
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join(5)
            results.close()
            results.join_thread()

    def test_multiprocess_competing_leases(self):
        path, winner = self._competition("lease")
        self.assertEqual(winner.fencing, 1)
        self.assertTrue(StateStore(path).release(winner.key, winner.owner, winner.token))
        self.assertEqual(StateStore(path).acquire(winner.key, "next-worker").fencing, 2)

    def test_multiprocess_competing_jobs(self):
        path, token = self._competition("job")
        store = StateStore(path)
        job = store.get_job("daily-report", "2026-10-02")
        self.assertTrue(store.finish_job(job.job_id, job.run_key, job.owner, token, True))
        self.assertIsNone(StateStore(path).claim_job(job.job_id, job.run_key, "next-worker"))

    @unittest.skipUnless("fork" in multiprocessing.get_all_start_methods(), "fork unavailable")
    def test_fork_competing_leases(self):
        self._competition("lease", method="fork")

    def test_shared_store_is_thread_safe_without_shared_connections(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            values = list(pool.map(lambda i: self.store.acquire("threaded", "worker-" + str(i)), range(8)))
        self.assertEqual(sum(value is not None for value in values), 1)
        with self.store._transaction() as connection:
            self.assertEqual(connection.execute("PRAGMA busy_timeout").fetchone()[0], BUSY_TIMEOUT_MS)
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")
        self.assertFalse(any(isinstance(value, sqlite3.Connection) for value in vars(self.store).values()))

    def test_audit_allowlist_and_bounded_paging(self):
        event_id = self.store.record_audit("access.denied", actor_id="user-123", resource_id="report",
                                           request_id="request-123", outcome="denied", code="permission_denied")
        with self.assertRaises(TypeError):
            self.store.record_audit("access.denied", payload={"password": "secret"})
        for fields in ({"event": "arbitrary payload"}, {"event": "access.denied", "outcome": "secret"},
                       {"event": "access.denied", "actor_id": "person@example.com"},
                       {"event": "access.denied", "code": "secret-payload"}):
            with self.assertRaises(ValueError):
                self.store.record_audit(**fields)
        second = self.store.record_audit("runtime.started")
        events = StateStore(self.path).list_audit(limit=1)
        self.assertEqual(events, [AuditEvent(event_id, events[0].occurred_at, "access.denied", "user-123", "report", "denied",
                                            "request-123", "permission_denied")])
        self.assertEqual(self.store.list_audit(after_id=event_id)[0].event_id, second)
        for limit in (0, 1001, True, 1.5):
            with self.assertRaises(ValueError):
                self.store.list_audit(limit=limit)
            with self.assertRaises(ValueError):
                self.store.list_uncertain_jobs(limit=limit)

    def test_runtime_audit_contract(self):
        for event in ("login.succeeded", "login.failed", "logout.succeeded",
                      "provider.error", "report.export", "access.denied"):
            self.store.record_audit(event, actor_id="opaque-user-123", request_id="request-123",
                                    outcome="failure", code="provider_unavailable")
        self.assertEqual(len(self.store.list_audit()), 6)

    def test_state_and_audit_write_roll_back_together(self):
        with patch.object(self.store, "_audit", side_effect=sqlite3.OperationalError("write failure")):
            with self.assertRaises(sqlite3.OperationalError):
                self.store.acquire("report", "worker")
            with self.assertRaises(sqlite3.OperationalError):
                self.store.claim_job("report", "run", "worker")
        self.assertEqual(self.store.list_audit(), [])
        self.assertEqual(self.store.acquire("report", "worker").fencing, 1)
        self.assertIsNotNone(self.store.claim_job("report", "run", "worker"))

    def test_backup_preserves_state_audit_schema_and_source_independence(self):
        lease = self.store.acquire("report", "worker")
        token = self.store.claim_job("report", "failed", "worker")
        self.store.finish_job("report", "failed", "worker", token, False)
        self.store.claim_job("report", "uncertain", "worker")
        destination = Path(self.directory.name) / "backup.sqlite"
        self.assertEqual(self.store.backup_to(destination), str(destination))
        backup = StateStore(destination)
        self.assertEqual(backup.list_audit(), self.store.list_audit())
        self.assertEqual(backup.get_job("report", "failed").failure_code, "execution_failed")
        self.assertEqual(len(backup.list_uncertain_jobs()), 1)
        self.assertIsNone(backup.acquire("report", "other"))
        self.assertTrue(backup.release("report", "worker", lease.token))
        self.assertEqual(backup.acquire("report", "other").fencing, 2)
        self.assertIsNone(self.store.acquire("report", "other"))
        with sqlite3.connect(str(destination)) as connection:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
        with self.assertRaises(FileExistsError):
            self.store.backup_to(destination)
        with self.assertRaises(ValueError):
            self.store.backup_to(self.path)
        with self.assertRaises(ValueError):
            self.store.backup_to("relative.sqlite")

    def test_python38_syntax(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("reporting_workspace/state.py", "tests/test_state.py"):
            ast.parse((root / name).read_text(encoding="utf-8"), feature_version=(3, 8))


if __name__ == "__main__":
    unittest.main()
