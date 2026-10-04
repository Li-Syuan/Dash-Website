"""Provider-backed maintenance adapters reject substituted and stale actors."""
import os
import tempfile
import unittest
from unittest.mock import Mock

from reporting_workspace.legacy_crud import PermissionDenied
from reporting_workspace.maintenance_registry import (
    MaintenanceDefinition, MaintenancePolicy, MaintenanceRegistry,
    SQLiteDatabase, SQLiteMaintenanceAdapter,
)
from reporting_workspace.providers import DemoIdentityProvider
from test_maintenance_registry import CONFIG, model, user


class RegistryIdentityBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.identities = DemoIdentityProvider({
            'writer': dict(password='synthetic-only', role='admin', org='A'),
            'peer': dict(password='synthetic-only', role='user', org='A'),
        })
        database = SQLiteDatabase(os.path.join(self.directory.name, 'registry.sqlite'), 'local')
        self.adapter = SQLiteMaintenanceAdapter(database)
        self.registry = MaintenanceRegistry(self.identities)
        self.registry.register(MaintenanceDefinition(
            'fixture', 'Synthetic table', model(), database, 'local', CONFIG,
            lambda current: MaintenancePolicy(), self.adapter))
        self.addCleanup(self.registry.close)
        self.writer = user()

    def test_wrong_provider_id_is_denied_before_adapter_io(self):
        self.identities.get_user = Mock(return_value=user(name='other-actor'))
        self.adapter.create = Mock(side_effect=AssertionError('must not reach adapter'))
        with self.assertRaises(PermissionDenied):
            self.registry.create(self.writer, 'fixture', {'code': 'DENIED'})
        self.adapter.create.assert_not_called()

    def test_changed_role_or_organization_requires_a_fresh_actor(self):
        for field, value in (('role', 'user'), ('org', 'B')):
            self.identities.user_db['writer'] = dict(password='synthetic-only', role='admin', org='A')
            self.identities.user_db['writer'][field] = value
            with self.subTest(field=field), self.assertRaises(PermissionDenied):
                self.registry.catalog(self.writer)
            self.assertEqual(self.registry.catalog(self.identities.get_user('writer'))['total'], 1)

    def test_forged_low_privilege_snapshot_cannot_be_silently_upgraded(self):
        self.adapter.create = Mock(side_effect=AssertionError('must not reach adapter'))
        with self.assertRaises(PermissionDenied):
            self.registry.create(user(role='user'), 'fixture', {'code': 'DENIED'})
        self.adapter.create.assert_not_called()

    def test_revoked_actor_is_denied_without_database_creation(self):
        del self.identities.user_db['writer']
        with self.assertRaises(PermissionDenied):
            self.registry.query(self.writer, 'fixture')
        self.assertFalse(os.path.exists(self.adapter.init_db.path))

    def test_adapter_read_cannot_release_rows_after_identity_change(self):
        for method in ('query', 'get'):
            self.identities.user_db['writer']['org'] = 'A'

            def change_tenant(*args):
                self.identities.user_db['writer']['org'] = 'B'
                return {'private': 'original tenant sentinel'}

            setattr(self.adapter, method, Mock(side_effect=change_tenant))
            with self.subTest(method=method), self.assertRaises(PermissionDenied):
                if method == 'query':
                    self.registry.query(self.writer, 'fixture')
                else:
                    self.registry.get(self.writer, 'fixture', 1)

    def test_adapter_read_rechecks_current_table_policy(self):
        from dataclasses import replace
        policy = [MaintenancePolicy()]
        registry = MaintenanceRegistry(self.identities)
        registry.register(replace(self.registry.definitions[0], policy_resolver=lambda current: policy[0]))

        def revoke_policy(*args):
            policy[0] = MaintenancePolicy(read_roles=('auditor',), write_roles=('auditor',))
            return {'private': 'original tenant sentinel'}

        self.adapter.query = Mock(side_effect=revoke_policy)
        with self.assertRaises(PermissionDenied):
            registry.query(self.writer, 'fixture')


if __name__ == '__main__':
    unittest.main()
