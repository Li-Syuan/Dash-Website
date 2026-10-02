"""Synthetic tests only; never connects to company systems."""
import csv
import io
import json
import os
import tempfile
import threading
import unittest
import zipfile
from types import SimpleNamespace

from reporting_workspace.legacy_policy import Policy
from reporting_workspace.legacy_crud import (
    LegacyCrudService, PermissionDenied, InvalidInput, InvalidStage,
    RecordConflict, RecordNotFound, CSV_FIELDS, QSL_FIELDS,
)


def actor(name='writer', org='ORG_QA01', dev=True, admin=False, authenticated=True):
    return SimpleNamespace(id=name, orgcode=org, is_dev=dev,
                           is_admin=admin, is_authenticated=authenticated)


def record(code='SYNTH-001', **changes):
    row = dict(Material_Type='Synthetic resin', Vendor_Code=code,
               Vendor_Name='Synthetic supplier', Country='TW', City='Demo',
               Rev='A', Supplier_Level='LEVEL 1')
    row.update(changes)
    return row


class LegacyCrudTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, 'synthetic.sqlite')
        self.now = [1000.0]
        self.policy = Policy(orgcode=['ORG_QA'], crud_roles=['dev'])
        self.calls = 0
        def resolver(user):
            self.calls += 1
            return self.policy
        self.service = LegacyCrudService(self.path, resolver, allow_upload=True,
                                         clock=lambda: self.now[0])
        self.writer = actor()
        self.reader = actor('reader', dev=False)
        self.services = [self.service]

    def tearDown(self):
        for service in self.services:
            service.close()
        self.tmp.cleanup()

    def other(self, **kwargs):
        service = LegacyCrudService(self.path, lambda user: self.policy,
                                    clock=lambda: self.now[0], **kwargs)
        self.services.append(service)
        return service

    def test_create_read_update_with_fresh_policy(self):
        result = self.service.create(self.writer, record())
        self.assertTrue(result.committed)
        self.assertEqual(result.version, 1)
        row = self.service.query(self.reader)[0]
        self.assertEqual(row['Supplier_Level'], 'LEVEL 1(KEY SUPPLIER)')
        result = self.service.update(self.writer, row['id'], 1, {'Rev': 'B'})
        self.assertEqual(result.version, 2)
        self.assertEqual(self.service.query(self.reader)[0]['Rev'], 'B')
        self.assertGreaterEqual(self.calls, 4)
        self.policy = Policy(orgcode=['ORG_QA'])
        with self.assertRaises(PermissionDenied):
            self.service.update(self.writer, row['id'], 2, {'Rev': 'C'})
        self.assertEqual(self.service.query(self.reader)[0]['Rev'], 'B')

    def test_read_write_matrix_enforced_without_ui(self):
        for denied_user in (actor(authenticated=False), actor('outside', org='OTHER', dev=False)):
            with self.assertRaises(PermissionDenied):
                self.service.query(denied_user)
            with self.assertRaises(PermissionDenied):
                self.service.export_csv(denied_user)
        for read_user in (self.reader, actor('admin', org='OTHER', dev=False, admin=True)):
            self.assertEqual(self.service.query(read_user), [])
            for operation in (
                lambda: self.service.create(read_user, record()),
                lambda: self.service.update(read_user, 1, 1, {'Rev': 'B'}),
                lambda: self.service.delete(read_user, 1, 1),
                lambda: self.service.stage_rows(read_user, [record()]),
                lambda: self.service.audit(read_user),
            ):
                with self.assertRaises(PermissionDenied):
                    operation()

    def test_unknown_and_locked_fields_are_server_checked(self):
        with self.assertRaises(InvalidInput):
            self.service.create(self.writer, record(is_admin=True))
        locked = self.other(create_locks=['Rev'], update_locks=['Vendor_Code'], defaults={'Rev': 'A'})
        with self.assertRaises(InvalidInput):
            locked.create(self.writer, record())
        payload = record()
        del payload['Rev']
        created = locked.create(self.writer, payload)
        with self.assertRaises(InvalidInput):
            locked.update(self.writer, created.record_id, 1, {'Vendor_Code': 'ATTACK'})
        locked.update(self.writer, created.record_id, 1, {'Vendor_Code': payload['Vendor_Code'], 'Rev': 'B'})
        self.assertEqual(locked.query(self.reader)[0]['Rev'], 'B')

    def test_unknown_primary_id_and_invalid_values(self):
        for value in (None, '', '  ', 1, False, 'x' * 256):
            with self.assertRaises(InvalidInput):
                self.service.create(self.writer, record(City=value))
        for rid in (True, 0, -1, '1 OR 1=1', '9' * 50):
            with self.assertRaises(InvalidInput):
                self.service.update(self.writer, rid, 1, {'Rev': 'B'})
        with self.assertRaises(InvalidInput):
            self.service.create(self.writer, record(id=3))

    def test_five_key_unique_and_multisite_supplier(self):
        self.service.create(self.writer, record())
        with self.assertRaises(RecordConflict):
            self.service.create(self.writer, record(Rev='B'))
        self.service.create(self.writer, record(City='Other demo city'))
        self.assertEqual(len(self.service.query(self.reader)), 2)
        self.assertEqual(len(self.service.audit(self.writer)), 2)

    def test_stale_version_cannot_overwrite(self):
        result = self.service.create(self.writer, record())
        self.service.update(self.writer, result.record_id, 1, {'Rev': 'B'})
        with self.assertRaises(RecordConflict):
            self.service.update(self.writer, result.record_id, 1, {'Rev': 'C'})
        with self.assertRaises(RecordConflict):
            self.service.delete(self.writer, result.record_id, 1)
        self.assertEqual(self.service.query(self.reader)[0]['Rev'], 'B')

    def test_delete_is_recoverable_and_restore_checks_uniqueness(self):
        result = self.service.create(self.writer, record())
        self.service.delete(self.writer, result.record_id, 1)
        self.assertEqual(self.service.query(self.reader), [])
        self.service.restore(self.writer, result.record_id, 2)
        self.assertEqual(self.service.query(self.reader)[0]['version'], 3)
        self.service.delete(self.writer, result.record_id, 3)
        self.service.create(self.writer, record())
        with self.assertRaises(RecordConflict):
            self.service.restore(self.writer, result.record_id, 4)

    def test_only_update_applies_to_upload_and_direct_calls(self):
        created = self.service.create(self.writer, record())
        restricted = self.other(only_update=True, allow_upload=True)
        for call in (lambda: restricted.create(self.writer, record('new')),
                     lambda: restricted.delete(self.writer, created.record_id, 1)):
            with self.assertRaises(PermissionDenied):
                call()
        stage = restricted.stage_rows(self.writer, [record('new')])
        result = restricted.submit_stage(self.writer, stage)
        self.assertEqual((result.created, result.failed), (0, 1))
        self.assertEqual(result.errors[0][1], 'permission_denied')
        row = restricted.query(self.writer)[0]
        row['Rev'] = 'B'
        result = restricted.submit_stage(self.writer, restricted.stage_rows(self.writer, [row]))
        self.assertEqual(result.updated, 1)

    def test_upload_flag_enforced(self):
        restricted = self.other(allow_upload=False)
        with self.assertRaises(PermissionDenied):
            restricted.stage_rows(self.writer, [record()])
        with self.assertRaises(PermissionDenied):
            restricted.template_csv(self.writer)

    def test_literal_filters_export_same_selection_and_formula_safety(self):
        self.service.create(self.writer, record('ONE', City='100%_[literal]', Vendor_Name='=DANGEROUS()'))
        self.service.create(self.writer, record('TWO', City='normal'))
        rows = self.service.query(self.reader, {'City': '%_'})
        self.assertEqual(len(rows), 1)
        exported = self.service.export_csv(self.reader, {'City': '%_'}).decode('utf-8-sig')
        parsed = list(csv.DictReader(io.StringIO(exported)))
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]['Vendor_Name'], "'=DANGEROUS()")
        self.assertEqual(rows[0]['Vendor_Name'], '=DANGEROUS()')
        for filters in ({'id': '1'}, {'City': 5}):
            with self.assertRaises(InvalidInput):
                self.service.query(self.reader, filters)

    def test_export_refuses_truncation(self):
        self.service.create(self.writer, record('ONE'))
        self.service.create(self.writer, record('TWO'))
        bounded = self.other(max_export_rows=1)
        with self.assertRaises(InvalidInput):
            bounded.export_csv(self.reader)
        self.assertTrue(bounded.export_csv(self.reader, {'Vendor_Code': 'ONE'}))

    def test_target_isolation(self):
        result = self.service.create(self.writer, record())
        other = self.other(target='another-target', allow_upload=True)
        self.assertEqual(other.query(self.writer), [])
        with self.assertRaises(RecordNotFound):
            other.update(self.writer, result.record_id, 1, {'Rev': 'B'})
        token = self.service.stage_rows(self.writer, [record('new')])
        with self.assertRaises(InvalidStage):
            other.submit_stage(self.writer, token)
        self.assertEqual(other.audit(self.writer), [])

    def test_staging_owner_expiry_replay_and_permission_revocation(self):
        token = self.service.stage_rows(self.writer, [record()])
        with self.assertRaises(InvalidStage):
            self.service.stage_preview(actor('other-writer'), token)
        with self.assertRaises(InvalidStage):
            self.service.submit_stage(actor('other-writer'), token)
        self.assertEqual(self.service.stage_preview(self.writer, token)['count'], 1)
        self.policy = Policy(orgcode=['ORG_QA'])
        with self.assertRaises(PermissionDenied):
            self.service.submit_stage(self.writer, token)
        self.policy = Policy(orgcode=['ORG_QA'], crud_roles=['dev'])
        self.assertEqual(self.service.submit_stage(self.writer, token).created, 1)
        with self.assertRaises(InvalidStage):
            self.service.submit_stage(self.writer, token)
        with self.assertRaises(InvalidStage):
            self.service.stage_preview(self.writer, token)
        expired = self.service.stage_rows(self.writer, [record('NEW')])
        self.now[0] += 7200
        with self.assertRaises(InvalidStage):
            self.service.submit_stage(self.writer, expired)
        self.assertEqual(self.service.cleanup_expired_stages(self.writer), 1)
        self.assertEqual(self.service.cleanup_expired_stages(self.writer), 0)

    def test_stage_token_invalid_input_and_discard(self):
        for token in ('../secret', '', 5, 'x' * 500):
            with self.assertRaises(InvalidStage):
                self.service.submit_stage(self.writer, token)
        token = self.service.stage_rows(self.writer, [record()])
        with self.assertRaises(InvalidStage):
            self.service.discard_stage(actor('other-writer'), token)
        self.service.discard_stage(self.writer, token)
        with self.assertRaises(InvalidStage):
            self.service.submit_stage(self.writer, token)

    def test_partial_success_counts_committed_rows_only(self):
        self.service.create(self.writer, record('EXISTING'))
        token = self.service.stage_rows(self.writer, [record('OK'), record('EXISTING'), record('OK2')])
        result = self.service.submit_stage(self.writer, token, atomic=False)
        self.assertEqual((result.created, result.updated, result.failed), (2, 0, 1))
        self.assertTrue(result.committed)
        self.assertEqual(len(self.service.query(self.reader)), 3)
        self.assertEqual(len(self.service.audit(self.writer)), 3)

    def test_atomic_failure_rolls_back_records_counters_audit(self):
        token = self.service.stage_rows(self.writer, [record(), record()])
        result = self.service.submit_stage(self.writer, token, atomic=True)
        self.assertEqual((result.created, result.updated, result.failed), (0, 0, 1))
        self.assertFalse(result.committed)
        self.assertEqual(self.service.query(self.reader), [])
        self.assertEqual(self.service.audit(self.writer), [])
        with self.assertRaises(InvalidStage):
            self.service.submit_stage(self.writer, token)

    def test_upload_update_requires_existing_id_and_version(self):
        created = self.service.create(self.writer, record())
        row = record()
        row['id'] = created.record_id
        result = self.service.submit_stage(self.writer, self.service.stage_rows(self.writer, [row]))
        self.assertEqual(result.failed, 1)
        row['version'] = 1
        row['Rev'] = 'B'
        result = self.service.submit_stage(self.writer, self.service.stage_rows(self.writer, [row]))
        self.assertEqual(result.updated, 1)
        result = self.service.submit_stage(self.writer, self.service.stage_rows(self.writer, [row]))
        self.assertEqual(result.failed, 1)
        self.assertEqual(self.service.query(self.reader)[0]['Rev'], 'B')

    def test_dry_run_import_and_mutations_rollback(self):
        dry_created = self.service.create(self.writer, record(), dry_run=True)
        self.assertFalse(dry_created.committed)
        self.assertEqual(self.service.query(self.reader), [])
        token = self.service.stage_rows(self.writer, [record()])
        result = self.service.submit_stage(self.writer, token, dry_run=True)
        self.assertEqual((result.created, result.would_create, result.committed), (0, 1, False))
        self.assertEqual(self.service.query(self.reader), [])
        self.assertEqual(self.service.audit(self.writer), [])
        self.assertEqual(self.service.stage_preview(self.writer, token)['count'], 1)
        result = self.service.submit_stage(self.writer, token)
        row = self.service.query(self.reader)[0]
        self.service.update(self.writer, row['id'], 1, {'Rev': 'B'}, dry_run=True)
        self.service.delete(self.writer, row['id'], 1, dry_run=True)
        self.assertEqual(self.service.query(self.reader)[0]['Rev'], 'A')
        self.assertEqual(len(self.service.audit(self.writer)), 1)

    def test_invalid_dry_run_rejected(self):
        with self.assertRaises(InvalidInput):
            self.service.create(self.writer, record(), dry_run='false')

    def test_refresh_failure_does_not_claim_rollback_or_repeat(self):
        def broken():
            raise RuntimeError('sensitive internal detail')
        result = self.service.create(self.writer, record(), refresh=broken)
        self.assertTrue(result.committed)
        self.assertTrue(result.refresh_failed)
        token = self.service.stage_rows(self.writer, [record('NEXT')])
        result = self.service.submit_stage(self.writer, token, refresh=broken)
        self.assertTrue(result.committed)
        self.assertTrue(result.refresh_failed)
        self.assertEqual(result.created, 1)
        self.assertEqual(len(self.service.query(self.reader)), 2)

    def test_audit_excludes_payload(self):
        self.service.create(self.writer, record(Vendor_Name='PRIVATE-SYNTHETIC-VALUE'))
        entries = self.service.audit(self.writer)
        self.assertNotIn('PRIVATE-SYNTHETIC-VALUE', json.dumps(entries))
        self.assertEqual(entries[0]['action'], 'create')
        self.assertIn('Vendor_Name', entries[0]['changed_fields'])

    def test_csv_valid_template_roundtrip(self):
        header = self.service.template_csv(self.writer).decode('utf-8-sig')
        self.assertEqual(next(csv.reader(io.StringIO(header))), list(CSV_FIELDS))
        out = io.StringIO(newline='')
        writer = csv.DictWriter(out, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerow(record())
        token = self.service.stage_csv(self.writer, out.getvalue().encode())
        self.assertEqual(self.service.submit_stage(self.writer, token).created, 1)

    def test_csv_rejects_malformed_duplicates_and_limits(self):
        for content in (b'City,City\na,b\n', b'bad\nheader\n', b'\xff\xfe', b''):
            with self.assertRaises(InvalidInput):
                self.service.stage_csv(self.writer, content)
        bounded = self.other(allow_upload=True, max_rows=1, max_file_bytes=500)
        with self.assertRaises(InvalidInput):
            bounded.stage_rows(self.writer, [record('A'), record('B')])
        with self.assertRaises(InvalidInput):
            bounded.stage_csv(self.writer, b'x' * 501)
        with self.assertRaises(InvalidInput):
            bounded.stage_rows(self.writer, [dict(record(), deleted=True)])

    def test_concurrent_stale_updates_one_winner(self):
        row = self.service.create(self.writer, record())
        other = self.other()
        barrier = threading.Barrier(2)
        outcomes = []
        def write(service, revision):
            barrier.wait()
            try:
                service.update(self.writer, row.record_id, 1, {'Rev': revision})
                outcomes.append('ok')
            except RecordConflict:
                outcomes.append('conflict')
        threads = [threading.Thread(target=write, args=(self.service, 'B')),
                   threading.Thread(target=write, args=(other, 'C'))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertCountEqual(outcomes, ['ok', 'conflict'])
        self.assertEqual(self.service.query(self.reader)[0]['version'], 2)

    def test_concurrent_stage_replay_one_winner(self):
        token = self.service.stage_rows(self.writer, [record()])
        other = self.other(allow_upload=True)
        barrier = threading.Barrier(2)
        outcomes = []
        def submit(service):
            barrier.wait()
            try:
                outcomes.append(service.submit_stage(self.writer, token).created)
            except InvalidStage:
                outcomes.append('used')
        threads = [threading.Thread(target=submit, args=(service,)) for service in (self.service, other)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertCountEqual(outcomes, [1, 'used'])
        self.assertEqual(len(self.service.query(self.reader)), 1)

    def make_xlsx(self, rows, header=CSV_FIELDS):
        try:
            import openpyxl
        except ImportError:
            self.skipTest('Optional openpyxl adapter is not installed')
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.append(list(header))
        for row in rows:
            sheet.append([row.get(name, '') for name in header])
        output = io.BytesIO()
        workbook.save(output)
        workbook.close()
        return output.getvalue()

    def test_xlsx_roundtrip_and_formula_safe_export(self):
        content = self.make_xlsx([record()])
        token = self.service.stage_xlsx(self.writer, content)
        self.assertEqual(self.service.submit_stage(self.writer, token).created, 1)
        row = self.service.query(self.reader)[0]
        self.service.update(self.writer, row['id'], 1, {'Vendor_Name': '=not_formula'})
        exported = self.service.export_xlsx(self.reader)
        import openpyxl
        workbook = openpyxl.load_workbook(io.BytesIO(exported), data_only=False)
        self.assertEqual(workbook.active['E2'].value, '=not_formula')
        self.assertEqual(workbook.active['E2'].data_type, 's')
        workbook.close()

    def test_xlsx_rejects_formula_and_expansion(self):
        formula = self.make_xlsx([record(Vendor_Name='=1+1')])
        with self.assertRaises(InvalidInput):
            self.service.stage_xlsx(self.writer, formula)
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('xl/worksheets/sheet1.xml', 'x' * 100000)
        with self.assertRaises(InvalidInput):
            self.service.stage_xlsx(self.writer, output.getvalue())

    def test_get_authorizes_and_returns_current_version(self):
        result = self.service.create(self.writer, record())
        self.assertEqual(self.service.get(self.reader, result.record_id)['version'], 1)
        with self.assertRaises(PermissionDenied):
            self.service.get(actor(authenticated=False), result.record_id)
        with self.assertRaises(RecordNotFound):
            self.other(target='other').get(self.reader, result.record_id)

    def test_xlsx_rejects_dtd_and_utf16_xml(self):
        self.make_xlsx([record()])  # Optional-dependency skip guard.
        for xml in (b'<!DOCTYPE root [<!ENTITY x "abc">]><root/>',
                    '<root/>'.encode('utf-16')):
            output = io.BytesIO()
            with zipfile.ZipFile(output, 'w') as archive:
                archive.writestr('xl/worksheets/sheet1.xml', xml)
            with self.assertRaises(InvalidInput):
                self.service.stage_xlsx(self.writer, output.getvalue())

    def test_xlsx_enforces_row_and_column_limits(self):
        too_many = self.make_xlsx([record('A'), record('B')])
        bounded = self.other(allow_upload=True, max_rows=1)
        with self.assertRaises(InvalidInput):
            bounded.stage_xlsx(self.writer, too_many)
        invalid_header = self.make_xlsx([record()], header=CSV_FIELDS + ('untrusted',))
        with self.assertRaises(InvalidInput):
            self.service.stage_xlsx(self.writer, invalid_header)


if __name__ == '__main__':
    unittest.main()
