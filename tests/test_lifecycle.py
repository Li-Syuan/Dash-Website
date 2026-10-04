"""Explicit shutdown releases SQLite handles without crossing app ownership."""

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from reporting_workspace.application import create_app
from reporting_workspace.config import Settings
from reporting_workspace.lifecycle import dispose_app


class AppLifecycleTests(unittest.TestCase):
    def test_workers_finish_before_services_close_and_temporary_cleanup(self):
        events = []

        def service(name, worker=False):
            resource = SimpleNamespace(close=lambda: events.append(name + '.close'))
            if worker:
                resource.stop = lambda: events.append(name + '.stop') or True
            return resource

        jobs = service('jobs', worker=True)
        server = SimpleNamespace(extensions={
            'etl_dispatch': service('etl', worker=True),
            'job_monitor': service('monitor', worker=True),
            'qa_demo_jobs': jobs,
            'qa_demo_builder': service('builder'),
            'qa_demo_crud': service('crud'),
            'maintenance_registry': service('registry'),
            'operations_service': jobs,
            'qa_demo_temporary': SimpleNamespace(cleanup=lambda: events.append('qa.cleanup')),
            'operations_temporary': SimpleNamespace(cleanup=lambda: events.append('operations.cleanup')),
            'etl_dispatch_temporary': SimpleNamespace(cleanup=lambda: events.append('etl.cleanup')),
            'unrelated_resource': Mock(),
        })
        dispose_app(server)
        self.assertEqual(events, [
            'etl.stop', 'monitor.stop', 'jobs.stop', 'builder.close',
            'crud.close', 'jobs.close', 'registry.close', 'monitor.close',
            'etl.close', 'qa.cleanup', 'operations.cleanup', 'etl.cleanup',
        ])
        completed = list(events)
        dispose_app(server)
        self.assertEqual(events, completed)
        server.extensions['unrelated_resource'].assert_not_called()
        self.assertEqual(server.extensions['unrelated_resource'].mock_calls, [])

    def test_unfinished_worker_retains_connections_and_temporary_files(self):
        worker = Mock()
        worker.stop.return_value = False
        service, temporary = Mock(), Mock()
        server = SimpleNamespace(extensions={
            'etl_dispatch': worker, 'qa_demo_crud': service,
            'qa_demo_temporary': temporary,
        })
        with self.assertRaisesRegex(RuntimeError, 'worker did not stop'):
            dispose_app(server)
        service.close.assert_not_called()
        temporary.cleanup.assert_not_called()
        self.assertNotIn('_workspace_disposed', server.extensions)
        worker.stop.return_value = True
        dispose_app(server)
        service.close.assert_called_once_with()
        temporary.cleanup.assert_called_once_with()

    def test_close_failure_is_visible_and_does_not_delete_resources(self):
        resource, temporary = Mock(), Mock()
        resource.close.side_effect = RuntimeError('synthetic close failure')
        server = SimpleNamespace(extensions={
            'qa_demo_builder': resource, 'qa_demo_temporary': temporary,
        })
        with self.assertRaisesRegex(RuntimeError, 'synthetic close failure'):
            dispose_app(server)
        temporary.cleanup.assert_not_called()
        self.assertNotIn('_workspace_disposed', server.extensions)

    def test_durable_files_survive_shutdown_and_can_be_renamed_and_deleted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / 'workspace.sqlite'
            unrelated = root / 'unrelated.txt'
            unrelated.write_text('owned by another app', encoding='utf-8')
            server = create_app(Settings(state_path=str(state)))
            try:
                self.assertEqual(server.test_client().get('/readyz').status_code, 200)
                databases = list(root.rglob('*.sqlite'))
                self.assertIn(state, databases)
                self.assertIn(root / 'workspace.sqlite.qa' / 'qsl.sqlite', databases)
            finally:
                dispose_app(server)
            for database in databases:
                self.assertTrue(database.is_file())
                renamed = database.with_name(database.name + '.released')
                database.rename(renamed)
                renamed.unlink()
            self.assertEqual(unrelated.read_text(encoding='utf-8'), 'owned by another app')

    def test_disposing_one_app_preserves_other_apps_and_request_lifetime(self):
        first = create_app(Settings())
        self.addCleanup(dispose_app, first)
        second = create_app(Settings())
        self.addCleanup(dispose_app, second)
        first_directory = Path(first.extensions['qa_demo_directory'])
        second_directory = Path(second.extensions['qa_demo_directory'])
        self.assertNotEqual(first_directory, second_directory)
        dispose_app(first)
        self.assertFalse(first_directory.exists())
        self.assertTrue(second_directory.exists())
        client = second.test_client()
        actor = SimpleNamespace(id='demo-admin', orgcode='ORG_QA01',
                                is_authenticated=True, is_dev=True, is_admin=False)
        for _ in range(2):
            self.assertEqual(client.get('/readyz').status_code, 200)
            self.assertGreater(len(second.extensions['qa_demo_crud'].query(actor)), 0)
        dispose_app(second)
        self.assertFalse(second_directory.exists())


if __name__ == '__main__':
    unittest.main()
