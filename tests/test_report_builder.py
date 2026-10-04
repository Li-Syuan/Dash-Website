"""Offline report-builder contracts using only synthetic, temporary QSL data."""
import os
import sqlite3
from contextlib import closing
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from reporting_workspace.legacy_crud import (
    LegacyCrudService, InvalidInput, PermissionDenied, RecordConflict,
    RecordNotFound, StorageUnavailable, QSL_FIELDS,
)
from reporting_workspace.legacy_policy import Policy
from reporting_workspace.report_builder import (
    ReportBuilderService, SOURCE_ID, MAX_PREVIEW_ROWS, MAX_FILTERS,
)


def actor(name='writer', organization='ORG_QA01', dev=True, **changes):
    attributes = dict(id=name, orgcode=organization, is_dev=dev,
                      is_authenticated=True, is_admin=False)
    attributes.update(changes)
    return SimpleNamespace(**attributes)


def definition(**changes):
    result = {'schema_version': 1, 'source': SOURCE_ID,
              'columns': ['Vendor_Code', 'Country']}
    result.update(changes)
    return result


class ReportBuilderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.qsl_path = os.path.join(self.tmp.name, 'qsl.sqlite')
        self.builder_path = os.path.join(self.tmp.name, 'builder.sqlite')
        self.policy = Policy(orgcode=['ORG_QA'], crud_roles=['dev'])
        self.qsl_policy = self.policy
        self.writer = actor()
        self.reader = actor('reader', dev=False)
        self.qsl = LegacyCrudService(self.qsl_path, lambda user: self.qsl_policy)
        self.builder = ReportBuilderService(self.builder_path, self.qsl, lambda user: self.policy)
        self.services = [self.builder]
        for code, country, city in [('SYN-001', 'TW', 'Taipei'), ('SYN-002', 'TW', 'Tainan'),
                                     ('SYN-003', 'JP', 'Tokyo'), ('SYN-004', 'TWX', 'Taipei')]:
            self.qsl.create(self.writer, dict(Material_Type='Synthetic', Vendor_Code=code,
                Vendor_Name='Synthetic vendor', Country=country, City=city,
                Rev='A', Supplier_Level='LEVEL 1'))

    def tearDown(self):
        for service in self.services:
            service.close()
        self.qsl.close()
        self.tmp.cleanup()

    def other(self):
        service = ReportBuilderService(self.builder_path, self.qsl, lambda user: self.policy)
        self.services.append(service)
        return service

    def test_source_allowlist_and_normalized_definition_are_detached(self):
        source = self.builder.sources(self.reader)[0]
        self.assertEqual(source['id'], SOURCE_ID)
        self.assertEqual(source['columns'], list(QSL_FIELDS))
        self.assertEqual(source['filter_ops'], ['eq', 'contains'])
        self.assertEqual(source['max_rows'], MAX_PREVIEW_ROWS)
        source['columns'].append('sql')
        supplied = definition()
        result = self.builder.validate(self.reader, supplied)
        self.assertEqual(result['limit'], 100)
        self.assertEqual(result['filters'], [])
        self.assertEqual(result['group_by'], [])
        self.assertIsNone(result['aggregate'])
        result['columns'].append('City')
        self.assertEqual(supplied['columns'], ['Vendor_Code', 'Country'])
        self.assertNotIn('sql', self.builder.sources(self.reader)[0]['columns'])

    def test_projection_limit_and_truncation_are_explicit(self):
        result = self.builder.preview(self.reader, definition(limit=2))
        self.assertEqual(result['columns'], ['Vendor_Code', 'Country'])
        self.assertEqual(result['rows'], [{'Vendor_Code': 'SYN-001', 'Country': 'TW'},
                                         {'Vendor_Code': 'SYN-002', 'Country': 'TW'}])
        self.assertEqual(result['matched_rows'], 4)
        self.assertEqual(result['source_rows'], 4)
        self.assertTrue(result['truncated'])
        self.assertNotIn('id', result['rows'][0])

    def test_exact_and_literal_contains_filters_combine_with_and(self):
        exact = self.builder.preview(self.reader, definition(filters=[
            {'field': 'Country', 'op': 'eq', 'value': 'TW'}]))
        self.assertEqual(exact['matched_rows'], 2)
        self.assertEqual(exact['source_rows'], 3)
        combined = self.builder.preview(self.reader, definition(filters=[
            {'field': 'Country', 'op': 'contains', 'value': 'TW'},
            {'field': 'City', 'op': 'contains', 'value': 'Tai'},
            {'field': 'City', 'op': 'eq', 'value': 'Taipei'}]))
        self.assertEqual([row['Vendor_Code'] for row in combined['rows']], ['SYN-001', 'SYN-004'])
        for literal in ('%', '_', "' OR 1=1 --", 'tw', '__import__'):
            result = self.builder.preview(self.reader, definition(filters=[
                {'field': 'Country', 'op': 'contains', 'value': literal}]))
            self.assertEqual(result['rows'], [])
        inconsistent = self.builder.preview(self.reader, definition(filters=[
            {'field': 'Country', 'op': 'eq', 'value': 'TW'},
            {'field': 'Country', 'op': 'eq', 'value': 'JP'}]))
        self.assertEqual(inconsistent['matched_rows'], 0)

    def test_grouped_count_is_sorted_and_counts_before_output_limit(self):
        result = self.builder.preview(self.reader, definition(
            columns=['Country'], group_by=['Country'], aggregate='count', limit=2))
        self.assertEqual(result['columns'], ['Country', 'count'])
        self.assertEqual(result['rows'], [{'Country': 'JP', 'count': 1}, {'Country': 'TW', 'count': 2}])
        self.assertEqual(result['matched_rows'], 4)
        self.assertTrue(result['truncated'])
        filtered = self.builder.preview(self.reader, definition(
            columns=['Country', 'City'], group_by=['Country', 'City'], aggregate='count',
            filters=[{'field': 'Country', 'op': 'eq', 'value': 'TW'}]))
        self.assertEqual(filtered['rows'], [{'Country': 'TW', 'City': 'Tainan', 'count': 1},
                                           {'Country': 'TW', 'City': 'Taipei', 'count': 1}])

    def test_count_without_groups_and_empty_results(self):
        result = self.builder.preview(self.reader, definition(columns=[], aggregate='count'))
        self.assertEqual(result['rows'], [{'count': 4}])
        result = self.builder.preview(self.reader, definition(columns=[], aggregate='count',
            filters=[{'field': 'Country', 'op': 'eq', 'value': 'none'}]))
        self.assertEqual(result['rows'], [{'count': 0}])
        grouped = self.builder.preview(self.reader, definition(columns=['Country'], aggregate='count',
            group_by=['Country'], filters=[{'field': 'Country', 'op': 'eq', 'value': 'none'}]))
        self.assertEqual(grouped['rows'], [])

    def test_strict_schema_rejects_permission_code_path_and_unknown_key_tampering(self):
        for key, value in [('owner', 'other'), ('user', {'id': 'writer'}), ('is_admin', True),
                           ('permissions', ['crud']), ('policy', {}), ('sql', 'SELECT *'),
                           ('python', 'print(1)'), ('path', '/private'), ('id', 1), ('version', 99)]:
            supplied = definition(**{key: value})
            with self.subTest(key=key), self.assertRaises(InvalidInput):
                self.builder.validate(self.writer, supplied)
        for source in ('legacy_qsl_records', '../qsl', 'oracle', '', None, {}):
            with self.subTest(source=source), self.assertRaises(InvalidInput):
                self.builder.preview(self.writer, definition(source=source))
        for version in (True, False, None, '1', 0, 2, 1.0):
            with self.subTest(version=version), self.assertRaises(InvalidInput):
                self.builder.validate(self.writer, definition(schema_version=version))
        for supplied in (None, [], {}, {'schema_version': 1, 'source': SOURCE_ID}):
            with self.subTest(supplied=supplied), self.assertRaises(InvalidInput):
                self.builder.validate(self.writer, supplied)

    def test_rejects_invalid_columns_filters_and_aggregation(self):
        bad = [definition(columns=value) for value in ([], 'Country', ['id'], ['Country', 'Country'], [None], [[]])]
        bad += [definition(limit=value) for value in (0, -1, True, '2', None, MAX_PREVIEW_ROWS + 1)]
        bad += [definition(aggregate=value) for value in ('sum', 'eval', {}, True)]
        bad += [definition(group_by=['Country']), definition(aggregate='count', group_by=['Country']),
                definition(columns=['Country', 'City'], group_by=['City', 'Country'], aggregate='count'),
                definition(columns=list(QSL_FIELDS[:4]), group_by=list(QSL_FIELDS[:4]), aggregate='count')]
        bad += [definition(filters=value) for value in ({}, 'Country', [None],
                [{'field': 'Country', 'op': 'eq'}],
                [{'field': 'Country', 'op': 'eq', 'value': 'TW', 'is_admin': True}],
                [{'field': 'Country', 'op': 'regex', 'value': '.*'}],
                [{'field': 'id', 'op': 'eq', 'value': '1'}],
                [{'field': 'Country', 'op': 'eq', 'value': 1}],
                [{'field': 'Country', 'op': 'eq', 'value': '\x00'}],
                [{'field': 'Country', 'op': 'eq', 'value': 'x' * 201}],
                [{'field': 'Country', 'op': 'eq', 'value': 'TW'}] * (MAX_FILTERS + 1))]
        for supplied in bad:
            with self.subTest(supplied=supplied), self.assertRaises(InvalidInput):
                self.builder.validate(self.writer, supplied)

    def test_every_operation_authorizes_fresh_server_policy(self):
        saved = self.builder.save(self.writer, 'Owned report', definition())
        self.policy = Policy(user_ids=['someone-else'])
        for operation in (
                lambda: self.builder.sources(self.writer),
                lambda: self.builder.validate(self.writer, definition()),
                lambda: self.builder.preview(self.writer, definition()),
                lambda: self.builder.save(self.writer, 'Another report', definition()),
                lambda: self.builder.list_definitions(self.writer),
                lambda: self.builder.load(self.writer, saved['id'])):
            with self.assertRaises(PermissionDenied):
                operation()

    def test_underlying_source_policy_is_independent_and_rechecked(self):
        saved = self.builder.save(self.writer, 'Saved', definition())
        self.qsl_policy = Policy(user_ids=['someone-else'])
        for operation in (
                lambda: self.builder.sources(self.writer),
                lambda: self.builder.validate(self.writer, definition()),
                lambda: self.builder.preview(self.writer, definition()),
                lambda: self.builder.save(self.writer, 'New', definition()),
                lambda: self.builder.list_definitions(self.writer),
                lambda: self.builder.load(self.writer, saved['id'])):
            with self.assertRaises(PermissionDenied):
                operation()

    def test_source_and_builder_revocation_during_preview_fail_closed(self):
        original = self.qsl.query
        def revoke_source(user, **kwargs):
            result = original(user, **kwargs)
            self.qsl_policy = Policy(user_ids=['someone-else'])
            return result
        with patch.object(self.qsl, 'query', revoke_source), self.assertRaises(PermissionDenied):
            self.builder.preview(self.writer, definition())
        self.qsl_policy = self.policy
        def revoke_builder(user, **kwargs):
            result = original(user, **kwargs)
            self.policy = Policy(user_ids=['someone-else'])
            return result
        with patch.object(self.qsl, 'query', revoke_builder), self.assertRaises(PermissionDenied):
            self.builder.preview(self.writer, definition())

    def test_readers_cannot_save_and_browser_claims_are_not_identities(self):
        self.assertTrue(self.builder.preview(self.reader, definition())['rows'])
        for user in (self.reader, actor(is_authenticated=False),
                     {'id': 'writer', 'is_admin': True, 'is_dev': True, 'is_authenticated': True},
                     actor(is_dev='true'), actor(is_dev=lambda: True), actor(id=True)):
            with self.subTest(user=user), self.assertRaises(PermissionDenied):
                self.builder.save(user, 'Escalation', definition())
        with self.assertRaises(PermissionDenied):
            self.builder.preview(actor('outsider', 'OTHER', dev=False), definition())

    def test_saved_definitions_are_owner_and_organization_scoped_even_for_admin(self):
        saved = self.builder.save(self.writer, 'Shared name', definition())
        others = [actor('other'), actor('admin', is_admin=True), actor(organization='ORG_QA02')]
        for user in others:
            self.assertEqual(self.builder.list_definitions(user), [])
            with self.assertRaises(RecordNotFound):
                self.builder.load(user, saved['id'])
            own = self.builder.save(user, 'Shared name', definition())
            self.assertNotEqual(own['id'], saved['id'])
        self.assertEqual(self.builder.list_definitions(self.writer),
                         [{'id': saved['id'], 'name': 'Shared name', 'version': 1}])

    def test_create_update_conflict_and_restart_persistence(self):
        saved = self.builder.save(self.writer, 'Persistent', definition())
        self.assertEqual(saved['version'], 1)
        with self.assertRaises(RecordConflict):
            self.builder.save(self.writer, 'Persistent', definition())
        updated = self.other().save(self.writer, 'Persistent', definition(columns=['City']), expected_version=1)
        self.assertEqual(updated['id'], saved['id'])
        self.assertEqual(updated['version'], 2)
        with self.assertRaises(RecordConflict):
            self.builder.save(self.writer, 'Persistent', definition(), expected_version=1)
        restarted = self.other()
        self.assertEqual(restarted.load(self.writer, saved['id']), updated)
        with self.assertRaises(RecordNotFound):
            restarted.save(self.writer, 'Missing', definition(), expected_version=1)
        loaded = restarted.load(self.writer, saved['id'])
        loaded['definition']['columns'].append('Country')
        self.assertEqual(restarted.load(self.writer, saved['id'])['definition']['columns'], ['City'])

    def test_two_connections_conflicting_updates_have_exactly_one_winner(self):
        saved = self.builder.save(self.writer, 'Concurrent', definition())
        other = self.other()
        start = threading.Barrier(2)
        results = []
        def update(service, column):
            start.wait(timeout=10)
            try:
                results.append(service.save(self.writer, 'Concurrent', definition(columns=[column]), 1)['version'])
            except RecordConflict:
                results.append('conflict')
        threads = [threading.Thread(target=update, args=(self.builder, 'City')),
                   threading.Thread(target=update, args=(other, 'Country'))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        self.assertCountEqual(results, [2, 'conflict'])
        self.assertEqual(self.builder.load(self.writer, saved['id'])['version'], 2)

    def test_invalid_names_versions_and_ids_are_rejected(self):
        for name in ('', ' ', ' leading', 'trailing ', 'x' * 121, None, True, '\x00'):
            with self.subTest(name=name), self.assertRaises(InvalidInput):
                self.builder.save(self.writer, name, definition())
        for version in (0, -1, True, '1', 1.0, 9223372036854775807):
            with self.subTest(version=version), self.assertRaises(InvalidInput):
                self.builder.save(self.writer, 'Version', definition(), version)
        for record_id in (0, -1, True, '1', '1 OR 1=1', 1.0, 9223372036854775808):
            with self.subTest(record_id=record_id), self.assertRaises(InvalidInput):
                self.builder.load(self.writer, record_id)

    def test_saved_definition_limit_does_not_prevent_existing_updates(self):
        with patch('reporting_workspace.report_builder.MAX_SAVED_DEFINITIONS', 1):
            self.builder.save(self.writer, 'First', definition())
            with self.assertRaises(InvalidInput):
                self.builder.save(self.writer, 'Second', definition())
            self.assertEqual(self.builder.save(self.writer, 'First', definition(columns=['City']), 1)['version'], 2)

    def test_source_bound_rejects_partial_aggregation_and_filter_can_narrow(self):
        with patch('reporting_workspace.report_builder.MAX_SOURCE_ROWS', 2):
            with self.assertRaises(InvalidInput):
                self.builder.preview(self.writer, definition(columns=['Country'],
                    group_by=['Country'], aggregate='count'))
            result = self.builder.preview(self.writer, definition(filters=[
                {'field': 'Country', 'op': 'eq', 'value': 'JP'}]))
            self.assertEqual(result['matched_rows'], 1)
        self.qsl.max_export_rows = 2
        with self.assertRaises(InvalidInput):
            self.builder.preview(self.writer, definition())

    def test_untrusted_stored_definition_is_revalidated_and_error_is_safe(self):
        saved = self.builder.save(self.writer, 'Corrupt', definition())
        with closing(sqlite3.connect(self.builder_path)) as database, database:
            database.execute('UPDATE report_builder_definitions SET definition=? WHERE id=?',
                             ('{"sql":"private-secret-payload"}', saved['id']))
        with self.assertRaises(StorageUnavailable) as raised:
            self.builder.load(self.writer, saved['id'])
        self.assertNotIn('private-secret-payload', str(raised.exception))

    def test_future_storage_schema_and_nonsecure_sources_fail_closed(self):
        with closing(sqlite3.connect(self.builder_path)) as database, database:
            database.execute('UPDATE report_builder_schema SET version=999')
        with self.assertRaises(StorageUnavailable):
            self.other()
        with self.assertRaises(InvalidInput):
            ReportBuilderService(':memory:', object(), lambda user: self.policy)
        self.qsl.target = 'company-source'
        with self.assertRaises(InvalidInput):
            ReportBuilderService(':memory:', self.qsl, lambda user: self.policy)

    def test_runtime_source_reconfiguration_and_identity_change_fail_closed(self):
        self.qsl.target = 'another-target'
        with self.assertRaises(PermissionDenied):
            self.builder.preview(self.writer, definition())
        self.qsl.target = SOURCE_ID
        original = self.qsl.query
        def change_identity(user, **kwargs):
            result = original(user, **kwargs)
            user.id = 'another-writer'
            return result
        with patch.object(self.qsl, 'query', change_identity), self.assertRaises(PermissionDenied):
            self.builder.preview(self.writer, definition())

    def test_source_database_errors_do_not_expose_private_paths_or_sql(self):
        with patch.object(self.qsl, 'query', side_effect=sqlite3.OperationalError('private-path SQL payload')):
            with self.assertRaises(StorageUnavailable) as raised:
                self.builder.preview(self.writer, definition())
        self.assertNotIn('private-path', str(raised.exception))
        self.assertNotIn('SQL payload', str(raised.exception))

    def test_source_returns_are_validated_before_exposing_rows(self):
        original = self.qsl.query
        def malformed(user, **kwargs):
            result = original(user, **kwargs)
            if kwargs.get('limit') != 1:
                return [{'Country': 'TW', 'secret': 'must-not-leak'}]
            return result
        with patch.object(self.qsl, 'query', malformed), self.assertRaises(StorageUnavailable):
            self.builder.preview(self.writer, definition())


if __name__ == '__main__':
    unittest.main()
