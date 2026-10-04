"""The documented source-adapter example is executable and stays read-only."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from examples.etl_registry import build_registry, create_fixture
from reporting_workspace.etl_dispatch import ETLDispatch
from reporting_workspace.providers import DemoIdentityProvider


class ETLIntegrationExampleTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.source = create_fixture(self.root / 'source.sqlite')
        self.identities = DemoIdentityProvider()
        self.user = self.identities.get_user('demo-admin')
        self.engine = ETLDispatch(self.root / 'dispatch.sqlite', self.identities, registry=build_registry(self.source))
        self.addCleanup(self.engine.stop)

    def test_documented_source_pipeline_is_readonly_and_counts_real_rows(self):
        before = self.source.read_bytes()
        result = self.engine.run_now(self.user, 'approved-orders-fixture', '2026-10-01', 'documented-fixture-run')
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual([step['row_count'] for step in result['steps']], [2, 2, 1])
        self.assertEqual(self.source.read_bytes(), before)
        self.assertNotIn('fixture-order-1', str(result))
        self.assertNotIn(str(self.source), str(result))

    def test_empty_partition_is_a_quality_failure_without_publication(self):
        result = self.engine.run_now(self.user, 'approved-orders-fixture', '2026-09-30', 'empty-fixture-run')
        self.assertEqual(result['status'], 'failed')
        self.assertIsNone(self.engine.status(self.user, 'approved-orders-fixture')['publication'])

    def test_more_than_bound_is_rejected_instead_of_silently_truncated(self):
        with closing(sqlite3.connect(str(self.source))) as connection, connection:
            connection.executemany('INSERT INTO fixture_orders VALUES (?,?,?)',
                [('extra-{}'.format(n), '2026-10-01', 1) for n in range(100)])
        result = self.engine.run_now(self.user, 'approved-orders-fixture', '2026-10-01', 'large-fixture-run')
        self.assertEqual(result['status'], 'failed')
        self.assertIsNone(self.engine.status(self.user, 'approved-orders-fixture')['publication'])

    def test_fixture_creation_will_not_overwrite_existing_file(self):
        before = self.source.read_bytes()
        with self.assertRaises(ValueError):
            create_fixture(self.source)
        self.assertEqual(self.source.read_bytes(), before)
