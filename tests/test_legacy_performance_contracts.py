"""QSL ordered-query optimization preserves persisted and public contracts."""
import csv
import io
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest

from reporting_workspace.legacy_crud import (
    LegacyCrudService, CSV_FIELDS, InvalidInput, PermissionDenied,
)
from reporting_workspace.legacy_policy import Policy


class LegacyQueryPerformanceContracts(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='qsl-index-contract-')
        self.path = str(Path(self.directory.name) / 'synthetic.sqlite')
        self.policy = Policy(orgcode=['SYNTHETIC'], crud_roles=['dev'])
        self.actor = SimpleNamespace(id='synthetic-writer', orgcode='SYNTHETIC-A',
                                     is_authenticated=True, is_dev=True, is_admin=False)
        self.services = []
        self.service = self.open_service()

    def tearDown(self):
        for service in self.services:
            service.close()
        self.directory.cleanup()

    def open_service(self, **options):
        service = LegacyCrudService(self.path, lambda actor: self.policy, **options)
        self.services.append(service)
        return service

    def create(self, code, **changes):
        payload = dict(Material_Type='Synthetic', Vendor_Code=code,
                       Vendor_Name='Synthetic supplier', Country='TW', City='Synthetic city',
                       Rev='A', Supplier_Level='LEVEL 1')
        payload.update(changes)
        return self.service.create(self.actor, payload)

    def test_query_pages_are_id_ordered_with_target_and_soft_delete_boundaries(self):
        other = self.open_service(target='other-synthetic-target')
        created = [self.create(code).record_id for code in ('Z', 'A', 'M', 'B', 'X', 'C')]
        other.create(self.actor, dict(Material_Type='Synthetic', Vendor_Code='OTHER',
                     Vendor_Name='Synthetic supplier', Country='TW', City='Synthetic city',
                     Rev='A', Supplier_Level='LEVEL 1'))
        self.service.delete(self.actor, created[2], 1)
        expected = created[:2] + created[3:]
        pages = [self.service.query(self.actor, limit=2, offset=offset)
                 for offset in (0, 2, 4)]
        self.assertEqual([row['id'] for page in pages for row in page], expected)
        filtered = self.service.query(self.actor, {'Vendor_Name': 'supplier'}, limit=3, offset=1)
        self.assertEqual([row['id'] for row in filtered], expected[1:4])
        self.assertEqual(self.service.query(self.actor, offset=100), [])

    def test_literal_filters_and_csv_xlsx_presentation_are_preserved(self):
        self.create('Z', Vendor_Name='Synthetic 100%_literal', Rev='=1+1')
        self.create('A', Vendor_Name='Synthetic 100Xliteral')
        filtered = self.service.query(self.actor, {'Vendor_Name': '%_'})
        self.assertEqual(len(filtered), 1)
        content = self.service.export_csv(self.actor, {'Vendor_Name': '%_'})
        rows = list(csv.DictReader(io.StringIO(content.decode('utf-8-sig'))))
        expected = {name: str(filtered[0][name]) for name in CSV_FIELDS}
        expected['Rev'] = "'=1+1"
        self.assertEqual(rows, [expected])
        import openpyxl
        workbook = openpyxl.load_workbook(io.BytesIO(self.service.export_xlsx(
            self.actor, {'Vendor_Name': '%_'})), read_only=True, data_only=False)
        try:
            actual = list(workbook.active.iter_rows())
            self.assertEqual(tuple(cell.value for cell in actual[0]), CSV_FIELDS)
            self.assertEqual(tuple(cell.value for cell in actual[1]),
                             tuple(filtered[0][name] for name in CSV_FIELDS))
            self.assertEqual(actual[1][CSV_FIELDS.index('Rev')].data_type, 's')
        finally:
            workbook.close()

    def test_existing_database_acquires_index_without_changing_rows_or_audit(self):
        item = self.create('Z')
        self.service.update(self.actor, item.record_id, 1, {'Rev': 'B'})
        self.create('A')
        before = self.service.query(self.actor)
        audit_before = self.service.audit(self.actor)
        revisions_before = self.service._db.execute(
            'SELECT * FROM legacy_qsl_revisions ORDER BY record_id,version').fetchall()
        self.service.close()
        self.services.remove(self.service)
        # Model an existing v9 database: the new optional index is absent.
        connection = sqlite3.connect(self.path)
        try:
            connection.execute('DROP INDEX IF EXISTS legacy_qsl_active_order')
            connection.commit()
        finally:
            connection.close()
        for _ in range(2):
            service = self.open_service()
            self.assertEqual(service.query(self.actor), before)
            self.assertEqual(service.audit(self.actor), audit_before)
            self.assertEqual(service._db.execute(
                'SELECT * FROM legacy_qsl_revisions ORDER BY record_id,version').fetchall(),
                revisions_before)
            plan = [row[3] for row in service._db.execute(
                'EXPLAIN QUERY PLAN SELECT id,version,Vendor_Name FROM legacy_qsl_records '
                'WHERE target=? AND deleted=0 AND instr(Vendor_Name,?)>0 '
                'ORDER BY id LIMIT ? OFFSET ?', (service.target, 'Synthetic', 1000, 0))]
            self.assertFalse(any('TEMP B-TREE' in detail for detail in plan), plan)

    def test_exact_export_cap_and_overflow_still_fail_without_truncation(self):
        for number in range(4):
            self.create(str(number), Vendor_Name='Synthetic match' if number < 3 else 'Other')
        bounded = self.open_service(max_export_rows=3)
        self.assertEqual(len(bounded.query(self.actor, limit=3)), 3)
        for method in (bounded.export_csv, bounded.export_xlsx):
            with self.subTest(method=method.__name__):
                with self.assertRaises(InvalidInput):
                    method(self.actor)
                self.assertTrue(method(self.actor, {'Vendor_Name': 'Synthetic match'}))
        with self.assertRaises(InvalidInput):
            bounded.query(self.actor, limit=4)

    def test_changed_server_policy_is_rechecked_on_every_query_and_export(self):
        self.create('A')
        self.assertEqual(len(self.service.query(self.actor)), 1)
        self.policy = Policy(orgcode=['DENIED'])
        for method in (self.service.query, self.service.export_csv, self.service.export_xlsx):
            with self.subTest(method=method.__name__), self.assertRaises(PermissionDenied):
                method(self.actor)


if __name__ == '__main__':
    unittest.main()
