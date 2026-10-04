"""Local SQLite repository boundary tests, not browser or company-adapter QA."""
import ast
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from reporting_workspace.crud import (
    AccessDenied, Conflict, NotFound, ReportDefinitions, StateUnavailable,
)
from reporting_workspace.definition_policy import DefinitionPrincipal
from reporting_workspace.definition_repository import ReportDefinitionRepository
from reporting_workspace.state import StateStore


class DefinitionRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = StateStore(Path(self.directory.name) / 'state.sqlite')
        self.service = ReportDefinitions(self.store)
        self.repository = self.service.repository
        self.owner = DefinitionPrincipal.from_user(dict(id='owner', org='A', role='user'))
        self.peer = DefinitionPrincipal.from_user(dict(id='peer', org='A', role='user'))
        self.admin = DefinitionPrincipal.from_user(dict(id='admin', org='A', role='admin'))
        self.foreign = DefinitionPrincipal.from_user(dict(id='owner', org='B', role='admin'))
        self.row = self.service.create(self.owner, {'name': 'A-only definition'}, request_key='a' * 32)

    def test_repository_cannot_accept_an_unvalidated_mapping(self):
        for user in (None, {}, {'id': 'owner', 'org': 'A', 'role': 'admin'}):
            with self.subTest(user=user), self.assertRaises(TypeError):
                with self.repository.transaction(user):
                    self.fail('Unvalidated identity reached storage')

    def test_detached_transaction_cannot_be_reused(self):
        with self.repository.transaction(self.owner) as unit:
            self.assertEqual(unit.get(self.row['id']), self.row)
        for operation in (lambda: unit.get(self.row['id']),
                          lambda: unit.list('', 20, 0, True),
                          lambda: unit.created_with('a' * 32),
                          lambda: unit.update(self.row['id'], 1, {'name': 'no'}, 1, None)):
            with self.assertRaises(StateUnavailable):
                operation()

    def test_failed_transaction_is_also_closed(self):
        with self.assertRaises(RuntimeError):
            with self.repository.transaction(self.owner) as unit:
                raise RuntimeError('synthetic transaction interruption')
        with self.assertRaises(StateUnavailable):
            unit.get(self.row['id'])

    def test_read_only_unit_cannot_mutate_or_audit(self):
        before_audit = self.store.list_audit()
        with self.repository.transaction(self.owner) as unit:
            operations = (
                lambda: unit.update(self.row['id'], 1, {'name': 'forbidden'}, 1, 'a' * 24),
                lambda: unit.set_deleted(self.row['id'], 1, True, 1, 'a' * 24),
                lambda: unit.insert('b' * 32, 'b' * 32, self.row, 1, 'a' * 24),
            )
            for operation in operations:
                with self.assertRaises(StateUnavailable):
                    operation()
        self.assertEqual(self.service.get(self.owner, self.row['id']), self.row)
        self.assertEqual(self.store.list_audit(), before_audit)

    def test_all_repository_reads_remain_tenant_and_actor_scoped(self):
        with self.repository.transaction(self.foreign) as unit:
            self.assertEqual(unit.list('', 20, 0, True)['items'], [])
            self.assertIsNone(unit.created_with('a' * 32))
            with self.assertRaises(NotFound):
                unit.get(self.row['id'])
        with self.repository.transaction(self.peer) as unit:
            self.assertEqual(unit.get(self.row['id']), self.row)
            self.assertIsNone(unit.created_with('a' * 32))
        with self.repository.transaction(self.owner) as unit:
            self.assertEqual(unit.created_with('a' * 32), self.row)

    def test_repository_enforces_ownership_before_version_and_lifecycle(self):
        with self.repository.transaction(self.peer, write=True) as unit:
            with self.assertRaises(AccessDenied):
                unit.update(self.row['id'], 999, {'name': 'forbidden'}, 1, None)
            with self.assertRaises(AccessDenied):
                unit.set_deleted(self.row['id'], 999, False, 1, None)
        self.assertEqual(self.service.get(self.owner, self.row['id']), self.row)

    def test_sql_owner_predicate_remains_when_policy_check_is_accidentally_omitted(self):
        # A regression in the Python policy must not broaden the SQL write.
        with patch.object(DefinitionPrincipal, 'require_change', return_value=None):
            with self.repository.transaction(self.peer, write=True) as unit:
                with self.assertRaises(Conflict):
                    unit.update(self.row['id'], 1, {'name': 'forbidden'}, 1, None)
                with self.assertRaises(Conflict):
                    unit.set_deleted(self.row['id'], 1, True, 1, None)
        self.assertEqual(self.service.get(self.owner, self.row['id']), self.row)
        self.assertEqual(len(self.store.list_audit()), 1)

    def test_sql_version_and_lifecycle_predicates_are_independent_guards(self):
        with patch.object(DefinitionPrincipal, 'require_change', return_value=None):
            with self.repository.transaction(self.owner, write=True) as unit:
                with self.assertRaises(Conflict):
                    unit.update(self.row['id'], 2, {'name': 'stale'}, 1, None)
                with self.assertRaises(Conflict):
                    unit.set_deleted(self.row['id'], 1, False, 1, None)
        deleted = self.service.soft_delete(self.owner, self.row['id'], 1)
        with patch.object(DefinitionPrincipal, 'require_change', return_value=None):
            with self.repository.transaction(self.owner, write=True) as unit:
                with self.assertRaises(Conflict):
                    unit.update(self.row['id'], 2, {'name': 'archived'}, 1, None)
                with self.assertRaises(Conflict):
                    unit.set_deleted(self.row['id'], 2, True, 1, None)
        self.assertEqual(self.service.get(self.owner, self.row['id']), deleted)

    def test_admin_write_preserves_owner_and_uses_bound_actor_in_audit(self):
        with self.repository.transaction(self.admin, write=True) as unit:
            result = unit.update(self.row['id'], 1, {'name': 'admin edit'}, 123, 'c' * 24)
        self.assertEqual(result['owner_id'], self.owner.actor_id)
        audit = [event for event in self.store.list_audit() if event.event == 'crud.updated'][0]
        self.assertEqual(audit.actor_id, self.admin.actor_id)
        self.assertEqual(audit.request_id, 'c' * 24)

    def test_audit_failure_rolls_back_repository_mutation_and_hides_backend_text(self):
        with patch.object(self.store, '_audit', side_effect=sqlite3.OperationalError('private backend detail')):
            with self.assertRaisesRegex(StateUnavailable, '^Durable state is unavailable.$'):
                with self.repository.transaction(self.owner, write=True) as unit:
                    unit.update(self.row['id'], 1, {'name': 'must roll back'}, 1, 'd' * 24)
        self.assertEqual(self.service.get(self.owner, self.row['id']), self.row)
        self.assertEqual(len(self.store.list_audit()), 1)
        with self.assertRaises(StateUnavailable):
            unit.get(self.row['id'])

    def test_service_and_callback_layers_have_no_sql_execution(self):
        root = Path(__file__).resolve().parents[1] / 'reporting_workspace'
        for relative in ('crud.py', 'ui_pages/maintenance.py', 'definition_policy.py'):
            tree = ast.parse((root / relative).read_text(encoding='utf-8'))
            calls = [node.func.attr for node in ast.walk(tree)
                     if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                     and node.func.attr in ('execute', 'executemany', 'executescript', 'cursor')]
            self.assertEqual(calls, [], relative)
        # Importing service/domain/repository does not require an HTTP context;
        # this keeps trusted background adapters independently testable.
        self.assertIsInstance(self.repository, ReportDefinitionRepository)


if __name__ == '__main__':
    unittest.main()
