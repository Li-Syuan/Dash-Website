"""Offline synthetic change previews and append-only QSL version history."""
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from types import SimpleNamespace

from reporting_workspace.legacy_crud import (
    InvalidInput, InvalidPreview, InvalidStage, LegacyCrudService,
    PermissionDenied, QSL_FIELDS, RecordConflict, RecordNotFound,
)
from reporting_workspace.legacy_policy import Policy


def actor(name='synthetic-writer', dev=True, authenticated=True):
    return SimpleNamespace(id=name, orgcode='SYNTH_QA01', is_dev=dev,
                           is_admin=False, is_authenticated=authenticated)


def record(code='SYNTHETIC-001', **changes):
    row = dict(Material_Type='Synthetic resin', Vendor_Code=code,
               Vendor_Name='Synthetic supplier', Country='TW', City='Demo',
               Rev='A', Supplier_Level='LEVEL 1')
    row.update(changes)
    return row


class LegacyRevisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, 'synthetic.sqlite')
        self.now = [1000.0]
        self.policy = Policy(orgcode=['SYNTH_QA'], crud_roles=['dev'])
        self.writer = actor()
        self.reader = actor('synthetic-reader', dev=False)
        self.services = []
        self.service = self.other(allow_upload=True)

    def tearDown(self):
        for service in self.services:
            service.close()
        self.tmp.cleanup()

    def other(self, **kwargs):
        service = LegacyCrudService(self.path, lambda user: self.policy,
                                    clock=lambda: self.now[0], **kwargs)
        self.services.append(service)
        return service

    def create(self, **changes):
        return self.service.create(self.writer, record(**changes)).record_id

    def preview(self, record_id, revision='B', version=1):
        return self.service.preview_update(self.writer, record_id,
                                           {'Rev': revision}, version)

    def test_preview_is_normalized_and_does_not_mutate_or_audit(self):
        rid = self.create()
        preview = self.service.preview_update(self.writer, rid,
                    {'Rev': ' B ', 'Supplier_Level': 'LEVEL 1 (KEY SUPPLIER)'}, 1)
        self.assertEqual(preview['action'], 'update')
        self.assertEqual(preview['record_id'], rid)
        self.assertEqual(preview['expected_version'], 1)
        self.assertEqual(preview['expires_at'], 1900.0)
        self.assertEqual(preview['diff'], [{'field': 'Rev', 'before': 'A', 'after': 'B'}])
        self.assertEqual(preview['before']['version'], 1)
        self.assertEqual(preview['after']['version'], 2)
        self.assertEqual(self.service.get(self.reader, rid)['Rev'], 'A')
        self.assertEqual(len(self.service.audit(self.writer)), 1)
        self.assertEqual(len(self.service.history(self.writer, rid)), 1)
        stored = dict(self.service._db.execute('SELECT * FROM legacy_qsl_previews').fetchone())
        self.assertNotIn(preview['token'], json.dumps(stored))
        self.assertEqual(len(stored['token_hash']), 64)

    def test_display_payload_is_detached_from_server_confirmation(self):
        rid = self.create()
        preview = self.preview(rid)
        preview['after']['Rev'] = 'FORGED'
        preview['diff'][0]['after'] = 'FORGED'
        preview['expected_version'] = 900
        preview['record_id'] = 999
        result = self.service.confirm_update(self.writer, preview['token'])
        self.assertTrue(result.committed)
        self.assertEqual((result.record_id, result.version), (rid, 2))
        self.assertEqual(self.service.get(self.reader, rid)['Rev'], 'B')
        revision = self.service.history(self.writer, rid)[0]
        self.assertEqual(revision['changed_fields'], ['Rev'])
        self.assertEqual(revision['snapshot']['Rev'], 'B')

    def test_preview_rejects_unknown_fields_noop_and_invalid_version(self):
        rid = self.create()
        for payload in ({'version': 10}, {'deleted': 1}, {'target': 'other'}, {},
                        {'Rev': ' A '}, {'City': ''}, {'Rev': False}):
            with self.assertRaises(InvalidInput):
                self.service.preview_update(self.writer, rid, payload, 1)
        for version in (0, True, 'bad', None):
            with self.assertRaises(InvalidInput):
                self.preview(rid, version=version)
        with self.assertRaises(RecordConflict):
            self.preview(rid, version=2)
        self.assertEqual(len(self.service.history(self.writer, rid)), 1)

    def test_reader_and_anonymous_cannot_use_history_or_change_endpoints(self):
        rid = self.create()
        token = self.preview(rid)['token']
        for user in (self.reader, actor(authenticated=False)):
            for operation in (
                lambda: self.service.history(user, rid),
                lambda: self.service.preview_update(user, rid, {'Rev': 'B'}, 1),
                lambda: self.service.preview_restore(user, rid, 1, 1),
                lambda: self.service.confirm_update(user, token),
                lambda: self.service.confirm_restore(user, token),
                lambda: self.service.discard_preview(user, token),
            ):
                with self.assertRaises(PermissionDenied):
                    operation()
        self.assertEqual(self.service.get(self.reader, rid)['version'], 1)

    def test_confirm_and_history_recheck_current_policy(self):
        rid = self.create()
        token = self.preview(rid)['token']
        self.policy = Policy(orgcode=['SYNTH_QA'])
        with self.assertRaises(PermissionDenied):
            self.service.confirm_update(self.writer, token)
        with self.assertRaises(PermissionDenied):
            self.service.history(self.writer, rid)
        self.assertEqual(self.service.get(self.reader, rid)['Rev'], 'A')
        self.policy = Policy(orgcode=['SYNTH_QA'], crud_roles=['dev'])
        self.assertTrue(self.service.confirm_update(self.writer, token).committed)

    def test_preview_owner_and_target_binding(self):
        rid = self.create()
        token = self.preview(rid)['token']
        other = self.other(target='another-synthetic-target')
        with self.assertRaises(InvalidPreview):
            self.service.confirm_update(actor('different-writer'), token)
        with self.assertRaises(InvalidPreview):
            self.service.discard_preview(actor('different-writer'), token)
        with self.assertRaises(InvalidPreview):
            other.confirm_update(self.writer, token)
        with self.assertRaises(RecordNotFound):
            other.history(self.writer, rid)
        with self.assertRaises(RecordNotFound):
            other.preview_restore(self.writer, rid, 1, 1)
        self.assertTrue(self.service.confirm_update(self.writer, token).committed)

    def test_expired_invalid_and_replayed_tokens_are_denied(self):
        rid = self.create()
        expired = self.preview(rid)['token']
        self.now[0] += 900
        with self.assertRaises(InvalidPreview):
            self.service.confirm_update(self.writer, expired)
        for token in (None, '', True, '../secret', 'x' * 200, 'x' * 43):
            with self.assertRaises(InvalidPreview):
                self.service.confirm_update(self.writer, token)
        token = self.preview(rid)['token']
        self.service.confirm_update(self.writer, token)
        with self.assertRaises(InvalidPreview):
            self.service.confirm_update(self.writer, token)
        self.assertEqual(len(self.service.history(self.writer, rid)), 2)

    def test_upload_tokens_and_change_tokens_are_not_interchangeable(self):
        rid = self.create()
        preview = self.preview(rid)
        stage = self.service.stage_rows(self.writer, [record('SYNTHETIC-002')])
        with self.assertRaises(InvalidPreview):
            self.service.confirm_update(self.writer, stage)
        with self.assertRaises(InvalidStage):
            self.service.submit_stage(self.writer, preview['token'])
        with self.assertRaises(InvalidPreview):
            self.service.confirm_restore(self.writer, preview['token'])
        self.service.confirm_update(self.writer, preview['token'])
        self.assertEqual(self.service.submit_stage(self.writer, stage).created, 1)

    def test_cancel_invalidates_server_token_without_changing_record(self):
        rid = self.create()
        token = self.preview(rid)['token']
        self.service.discard_preview(self.writer, token)
        with self.assertRaises(InvalidPreview):
            self.service.confirm_update(self.writer, token)
        with self.assertRaises(InvalidPreview):
            self.service.discard_preview(self.writer, token)
        self.assertEqual(self.service.get(self.reader, rid)['version'], 1)
        self.assertEqual(len(self.service.history(self.writer, rid)), 1)

    def test_cleanup_is_target_scoped_and_clears_expired_payloads(self):
        rid = self.create()
        self.preview(rid)
        other = self.other(target='another-synthetic-target')
        other_id = other.create(self.writer, record()).record_id
        other.preview_update(self.writer, other_id, {'Rev': 'B'}, 1)
        self.now[0] += 900
        self.assertEqual(self.service.cleanup_expired_previews(self.writer), 1)
        self.assertEqual(self.service.cleanup_expired_previews(self.writer), 0)
        rows = self.service._db.execute('SELECT target,consumed,payload FROM legacy_qsl_previews').fetchall()
        self.assertEqual([(row['consumed'], row['payload']) for row in rows
                          if row['target'] == self.service.target], [(1, '{}')])
        self.assertEqual(other.cleanup_expired_previews(self.writer), 1)

    def test_preview_conflicts_after_update_or_soft_delete(self):
        rid = self.create()
        first = self.preview(rid)['token']
        self.service.update(self.writer, rid, 1, {'Rev': 'C'})
        with self.assertRaises(RecordConflict):
            self.service.confirm_update(self.writer, first)
        second = self.preview(rid, 'D', 2)['token']
        self.service.delete(self.writer, rid, 2)
        with self.assertRaises(RecordConflict):
            self.service.confirm_update(self.writer, second)
        self.assertEqual(self.service.history(self.writer, rid)[0]['version'], 3)

    def test_all_mutations_capture_actor_time_version_and_deleted_state(self):
        rid = self.create()
        self.now[0] = 1010
        second = actor('synthetic-second-writer')
        self.service.update(second, rid, 1, {'Rev': 'B'})
        self.now[0] = 1020
        self.service.delete(self.writer, rid, 2)
        self.now[0] = 1030
        self.service.restore(second, rid, 3)
        history = self.service.history(self.writer, rid)
        self.assertEqual([row['version'] for row in history], [4, 3, 2, 1])
        self.assertEqual([row['action'] for row in history], ['restore', 'delete', 'update', 'create'])
        self.assertEqual([row['snapshot']['deleted'] for row in history], [0, 1, 0, 0])
        self.assertEqual([row['at'] for row in history], [1030, 1020, 1010, 1000])
        self.assertEqual(history[2]['actor'], second.id)
        self.assertEqual(history[1]['changed_fields'], ['deleted'])
        self.assertEqual(history, self.other().history(self.writer, rid))
        self.assertNotIn('Synthetic supplier', json.dumps(self.service.audit(self.writer)))

    def test_restore_appends_new_version_without_rewriting_prior_history(self):
        rid = self.create()
        self.service.update(self.writer, rid, 1, {'Rev': 'B', 'City': 'Different synthetic city'})
        old_history = self.service.history(self.writer, rid)
        preview = self.service.preview_restore(self.writer, rid, history_version=1, expected_version=2)
        self.assertEqual(preview['action'], 'restore_version')
        self.assertEqual(preview['source_version'], 1)
        self.assertEqual({item['field'] for item in preview['diff']}, {'Rev', 'City'})
        self.assertEqual(self.service.get(self.reader, rid)['version'], 2)
        result = self.service.confirm_update(self.writer, preview['token'])
        self.assertEqual(result.version, 3)
        history = self.service.history(self.writer, rid)
        self.assertEqual(history[1:], old_history)
        self.assertEqual(history[0]['source_version'], 1)
        self.assertEqual(history[0]['action'], 'restore_version')
        self.assertEqual(history[0]['snapshot']['Rev'], 'A')
        self.assertEqual(history[0]['snapshot']['version'], 3)

    def test_deleted_record_and_deleted_snapshot_can_be_restored(self):
        rid = self.create()
        self.service.delete(self.writer, rid, 1)
        recovered = self.service.preview_restore(self.writer, rid, 1, 2)
        self.assertEqual(recovered['diff'], [{'field': 'deleted', 'before': 1, 'after': 0}])
        self.service.confirm_restore(self.writer, recovered['token'])
        deleted = self.service.preview_restore(self.writer, rid, 2, 3)
        self.service.confirm_update(self.writer, deleted['token'])
        self.assertEqual(self.service.query(self.reader), [])
        self.assertEqual(self.service.history(self.writer, rid)[0]['version'], 4)
        self.service.restore(self.writer, rid, 4)
        self.assertEqual(self.service.get(self.reader, rid)['version'], 5)

    def test_restore_missing_noop_cross_record_and_stale_versions_rejected(self):
        rid = self.create()
        other_id = self.create(code='SYNTHETIC-OTHER')
        self.service.update(self.writer, other_id, 1, {'Rev': 'B'})
        with self.assertRaises(RecordNotFound):
            self.service.preview_restore(self.writer, rid, 2, 1)
        with self.assertRaises(InvalidInput):
            self.service.preview_restore(self.writer, rid, 1, 1)
        self.service.update(self.writer, rid, 1, {'Rev': 'B'})
        preview = self.service.preview_restore(self.writer, rid, 1, 2)
        self.service.update(self.writer, rid, 2, {'Rev': 'C'})
        with self.assertRaises(RecordConflict):
            self.service.confirm_restore(self.writer, preview['token'])
        self.assertEqual(self.service.get(self.reader, rid)['Rev'], 'C')

    def test_restore_checks_uniqueness_at_preview_and_confirmation(self):
        rid = self.create()
        self.service.delete(self.writer, rid, 1)
        preview = self.service.preview_restore(self.writer, rid, 1, 2)
        self.create()
        with self.assertRaises(RecordConflict):
            self.service.preview_restore(self.writer, rid, 1, 2)
        with self.assertRaises(RecordConflict):
            self.service.confirm_update(self.writer, preview['token'])
        self.assertEqual(len(self.service.history(self.writer, rid)), 2)
        self.assertEqual(len(self.service.query(self.reader)), 1)

    def test_update_preview_checks_business_key_uniqueness(self):
        rid = self.create()
        self.create(code='SYNTHETIC-OTHER')
        with self.assertRaises(RecordConflict):
            self.service.preview_update(self.writer, rid, {'Vendor_Code': 'SYNTHETIC-OTHER'}, 1)
        preview = self.service.preview_update(self.writer, rid, {'Vendor_Code': 'SYNTHETIC-NEXT'}, 1)
        self.create(code='SYNTHETIC-NEXT')
        with self.assertRaises(RecordConflict):
            self.service.confirm_update(self.writer, preview['token'])
        self.assertEqual(self.service.get(self.reader, rid)['Vendor_Code'], 'SYNTHETIC-001')

    def test_locked_fields_rechecked_at_preview_confirm_and_restore(self):
        rid = self.create()
        preview = self.preview(rid)
        locked = self.other(update_locks=['Rev'])
        with self.assertRaises(InvalidInput):
            locked.preview_update(self.writer, rid, {'Rev': 'B'}, 1)
        with self.assertRaises(InvalidInput):
            locked.confirm_update(self.writer, preview['token'])
        self.service.confirm_update(self.writer, preview['token'])
        with self.assertRaises(InvalidInput):
            locked.preview_restore(self.writer, rid, 1, 2)
        restore = self.service.preview_restore(self.writer, rid, 1, 2)
        with self.assertRaises(InvalidInput):
            locked.confirm_restore(self.writer, restore['token'])

    def test_only_update_does_not_gain_restore_permission_via_generic_confirm(self):
        rid = self.create()
        limited = self.other(only_update=True)
        preview = limited.preview_update(self.writer, rid, {'Rev': 'B'}, 1)
        limited.confirm_update(self.writer, preview['token'])
        restore = self.service.preview_restore(self.writer, rid, 1, 2)
        with self.assertRaises(PermissionDenied):
            limited.preview_restore(self.writer, rid, 1, 2)
        with self.assertRaises(PermissionDenied):
            limited.confirm_update(self.writer, restore['token'])
        self.assertEqual(self.service.get(self.reader, rid)['version'], 2)

    def test_delete_state_restore_works_with_all_business_fields_locked(self):
        rid = self.create()
        self.service.delete(self.writer, rid, 1)
        locked = self.other(update_locks=QSL_FIELDS)
        preview = locked.preview_restore(self.writer, rid, 1, 2)
        self.assertTrue(locked.confirm_restore(self.writer, preview['token']).committed)
        self.assertEqual(locked.get(self.reader, rid)['version'], 3)

    def test_history_rows_and_snapshots_cannot_be_rewritten_or_deleted(self):
        rid = self.create()
        history = self.service.history(self.writer, rid)
        history[0]['snapshot']['Rev'] = 'FORGED'
        history[0]['actor'] = 'FORGED'
        self.assertEqual(self.service.history(self.writer, rid)[0]['snapshot']['Rev'], 'A')
        for query in ('UPDATE legacy_qsl_revisions SET actor=?',
                      'DELETE FROM legacy_qsl_revisions WHERE actor=?'):
            with self.assertRaises(sqlite3.IntegrityError):
                self.service._db.execute(query, ('synthetic-writer',))
        self.assertEqual(len(self.service.history(self.writer, rid)), 1)

    def test_snapshot_failure_rolls_back_mutation_audit_and_consumption(self):
        rid = self.create()
        preview = self.preview(rid)
        self.service._db.execute('''CREATE TRIGGER fail_synthetic_history
            BEFORE INSERT ON legacy_qsl_revisions WHEN NEW.version=2
            BEGIN SELECT RAISE(ABORT, 'Synthetic failure'); END''')
        with self.assertRaises(RecordConflict):
            self.service.confirm_update(self.writer, preview['token'])
        self.assertEqual(self.service.get(self.reader, rid)['Rev'], 'A')
        self.assertEqual(len(self.service.audit(self.writer)), 1)
        self.assertEqual(len(self.service.history(self.writer, rid)), 1)
        self.service._db.execute('DROP TRIGGER fail_synthetic_history')
        self.assertEqual(self.service.confirm_update(self.writer, preview['token']).version, 2)

    def test_lifecycle_and_import_failure_roll_back_history_transactionally(self):
        rid = self.create()
        self.service._db.execute('''CREATE TRIGGER fail_synthetic_history
            BEFORE INSERT ON legacy_qsl_revisions WHEN NEW.version=2
            BEGIN SELECT RAISE(ABORT, 'Synthetic failure'); END''')
        with self.assertRaises(RecordConflict):
            self.service.delete(self.writer, rid, 1)
        payload = record()
        payload.update(id=rid, version=1, Rev='B')
        stage = self.service.stage_rows(self.writer, [payload])
        result = self.service.submit_stage(self.writer, stage)
        self.assertEqual((result.updated, result.failed), (0, 1))
        self.assertEqual(self.service.get(self.reader, rid)['version'], 1)
        self.assertEqual(len(self.service.audit(self.writer)), 1)
        self.assertEqual(len(self.service.history(self.writer, rid)), 1)

    def test_dry_run_and_atomic_failed_import_leave_no_history(self):
        self.service.create(self.writer, record(), dry_run=True)
        self.assertEqual(self.service._db.execute('SELECT COUNT(*) FROM legacy_qsl_revisions').fetchone()[0], 0)
        stage = self.service.stage_rows(self.writer, [record()])
        self.service.submit_stage(self.writer, stage, dry_run=True)
        self.assertEqual(self.service._db.execute('SELECT COUNT(*) FROM legacy_qsl_revisions').fetchone()[0], 0)
        rid = self.create()
        self.service.update(self.writer, rid, 1, {'Rev': 'B'}, dry_run=True)
        self.service.delete(self.writer, rid, 1, dry_run=True)
        duplicates = self.service.stage_rows(self.writer, [record('SYNTHETIC-NEW'), record('SYNTHETIC-NEW')])
        result = self.service.submit_stage(self.writer, duplicates, atomic=True)
        self.assertFalse(result.committed)
        self.assertEqual(len(self.service.history(self.writer, rid)), 1)
        self.assertEqual(self.service._db.execute('SELECT COUNT(*) FROM legacy_qsl_revisions').fetchone()[0], 1)

    def test_import_records_only_successful_versions_and_history(self):
        rid = self.create()
        payload = record()
        payload.update(id=rid, version=1, Rev='B')
        stage = self.service.stage_rows(self.writer, [payload, record('SYNTHETIC-NEW'), record('SYNTHETIC-NEW')])
        result = self.service.submit_stage(self.writer, stage, atomic=False)
        self.assertEqual((result.created, result.updated, result.failed), (1, 1, 1))
        self.assertEqual([row['snapshot']['Rev'] for row in self.service.history(self.writer, rid)], ['B', 'A'])
        self.assertEqual(self.service._db.execute('SELECT COUNT(*) FROM legacy_qsl_revisions').fetchone()[0], 3)
        self.assertEqual(len(self.service.audit(self.writer)), 3)

    def test_refresh_failure_keeps_committed_history_and_consumed_token(self):
        rid = self.create()
        preview = self.preview(rid)
        def broken():
            raise RuntimeError('Synthetic private failure detail')
        result = self.service.confirm_update(self.writer, preview['token'], refresh=broken)
        self.assertTrue(result.committed)
        self.assertTrue(result.refresh_failed)
        self.assertEqual(len(self.service.history(self.writer, rid)), 2)
        with self.assertRaises(InvalidPreview):
            self.service.confirm_update(self.writer, preview['token'])

    def test_preview_and_history_survive_restart(self):
        rid = self.create()
        preview = self.preview(rid)
        self.service.close()
        self.services.remove(self.service)
        reopened = self.other()
        self.assertEqual(reopened.confirm_update(self.writer, preview['token']).version, 2)
        self.assertEqual([row['version'] for row in reopened.history(self.writer, rid)], [2, 1])

    def test_pre_revision_database_gets_only_known_current_baseline(self):
        rid = self.create()
        self.now[0] = 1010
        self.service.update(self.writer, rid, 1, {'Rev': 'B'})
        # Model a pre-feature database, which has rows and field-name-only audit.
        self.service._db.execute('DROP TABLE legacy_qsl_revisions')
        reopened = self.other()
        history = reopened.history(self.writer, rid)
        self.assertEqual(len(history), 1)
        self.assertEqual((history[0]['version'], history[0]['action']), (2, 'baseline'))
        self.assertEqual(history[0]['snapshot']['Rev'], 'B')
        self.assertEqual(history[0]['actor'], self.writer.id)
        self.assertEqual(history[0]['at'], 1010)
        with self.assertRaises(RecordNotFound):
            reopened.preview_restore(self.writer, rid, 1, 2)
        self.assertEqual(self.other().history(self.writer, rid), history)
        reopened.update(self.writer, rid, 2, {'Rev': 'C'})
        self.assertEqual([row['version'] for row in reopened.history(self.writer, rid)], [3, 2])

    def test_legacy_baseline_without_audit_is_marked_explicitly(self):
        rid = self.create()
        self.service._db.execute('DROP TABLE legacy_qsl_revisions')
        self.service._db.execute('DELETE FROM legacy_qsl_audit')
        history = self.other().history(self.writer, rid)
        self.assertEqual(history[0]['actor'], 'legacy-baseline')
        self.assertEqual(history[0]['action'], 'baseline')
        self.assertEqual(history[0]['version'], 1)

    def test_import_preview_has_normalized_diffs_and_same_batch_conflicts(self):
        rid = self.create()
        update = dict(record(), id=rid, version=1, Rev=' B ')
        stage = self.service.stage_rows(self.writer, [update, record('SYNTHETIC-NEW'),
                                                     record('SYNTHETIC-NEW'), update])
        preview = self.service.stage_preview(self.writer, stage)
        self.assertEqual((preview['count'], preview['would_create'], preview['would_update'], preview['failed']),
                         (4, 1, 1, 2))
        self.assertEqual([item['status'] for item in preview['operations']],
                         ['ready', 'ready', 'conflict', 'conflict'])
        self.assertEqual(preview['operations'][0]['diff'],
                         [{'field': 'Rev', 'before': 'A', 'after': 'B'}])
        self.assertEqual(preview['operations'][0]['before']['version'], 1)
        self.assertEqual(preview['operations'][0]['after']['version'], 2)
        self.assertIsNone(preview['operations'][1]['after']['id'])
        self.assertEqual(preview['operations'][1]['after']['Supplier_Level'], 'LEVEL 1(KEY SUPPLIER)')
        self.assertEqual(self.service.get(self.reader, rid)['version'], 1)
        self.assertEqual(len(self.service.audit(self.writer)), 1)
        self.assertEqual(len(self.service.history(self.writer, rid)), 1)
        # The upload token is not consumed by preview and the same validator wins.
        result = self.service.submit_stage(self.writer, stage, atomic=False)
        self.assertEqual((result.created, result.updated, result.failed), (1, 1, 2))

    def test_import_preview_uses_server_locks_and_only_update_restriction(self):
        rid = self.create()
        limited = self.other(only_update=True, allow_upload=True, update_locks=['Rev'])
        rows = [record('SYNTHETIC-NEW'), dict(record(), id=rid, version=1, Rev='B'),
                dict(record(), id=rid)]
        token = limited.stage_rows(self.writer, rows)
        preview = limited.stage_preview(self.writer, token)
        self.assertEqual([item['error'] for item in preview['operations']],
                         ['permission_denied', 'invalid_input', 'invalid_input'])
        self.assertEqual(preview['failed'], 3)
        self.assertEqual(len(limited.history(self.writer, rid)), 1)

    def test_import_preview_bounds_rows_but_validates_entire_batch(self):
        rows = [record('SYNTHETIC-{}'.format(index)) for index in range(11)]
        rows.append(record('SYNTHETIC-10'))
        token = self.service.stage_rows(self.writer, rows)
        preview = self.service.stage_preview(self.writer, token)
        self.assertEqual((len(preview['rows']), len(preview['operations'])), (10, 10))
        self.assertEqual((preview['count'], preview['would_create'], preview['failed']), (12, 11, 1))
        self.assertTrue(preview['truncated'])
        self.assertEqual(self.service.query(self.reader), [])
        self.assertEqual(self.service.audit(self.writer), [])
        self.assertEqual(self.service._db.execute('SELECT COUNT(*) FROM legacy_qsl_revisions').fetchone()[0], 0)

    def test_import_preview_is_advisory_and_submit_rechecks_versions(self):
        rid = self.create()
        token = self.service.stage_rows(self.writer, [dict(record(), id=rid, version=1, Rev='B')])
        self.assertEqual(self.service.stage_preview(self.writer, token)['failed'], 0)
        self.service.update(self.writer, rid, 1, {'Rev': 'C'})
        result = self.service.submit_stage(self.writer, token)
        self.assertEqual((result.updated, result.failed), (0, 1))
        self.assertEqual(self.service.get(self.reader, rid)['Rev'], 'C')

    def _race(self, services, tokens, expected):
        barrier = threading.Barrier(2)
        outcomes = []
        def confirm(service, token):
            barrier.wait()
            try:
                service.confirm_update(self.writer, token)
                outcomes.append('committed')
            except (InvalidPreview, RecordConflict) as error:
                outcomes.append(error.code)
        threads = [threading.Thread(target=confirm, args=(service, token))
                   for service, token in zip(services, tokens)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertCountEqual(outcomes, expected)

    def test_concurrent_same_token_has_one_commit_and_one_history_entry(self):
        rid = self.create()
        token = self.preview(rid)['token']
        self._race((self.service, self.other()), (token, token), ['committed', 'invalid_preview'])
        self.assertEqual([row['version'] for row in self.service.history(self.writer, rid)], [2, 1])
        self.assertEqual(len(self.service.audit(self.writer)), 2)

    def test_concurrent_different_previews_have_one_winner(self):
        rid = self.create()
        first = self.preview(rid, 'B')['token']
        second = self.preview(rid, 'C')['token']
        self._race((self.service, self.other()), (first, second), ['committed', 'record_conflict'])
        self.assertEqual([row['version'] for row in self.service.history(self.writer, rid)], [2, 1])
        self.assertEqual(len(self.service.audit(self.writer)), 2)


if __name__ == '__main__':
    unittest.main()
