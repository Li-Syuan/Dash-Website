"""Offline governance, schema upgrade and concurrent mock-run acceptance tests."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import multiprocessing
from pathlib import Path
import secrets
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from demo_services import AccessDenied
from reporting_workspace.crud import Conflict, NotFound, StateUnavailable, ValidationError
from reporting_workspace.errors import ProviderUnavailable
from reporting_workspace.governance import (ACTIONS, CombinedCatalog, ManagedReports,
                                             SyntheticManagedProvider, next_candidate)
from reporting_workspace.providers import DemoIdentityProvider
from reporting_workspace.state import StateStore, StateError, _SCHEMA_DDL_V2


ADMIN = dict(id='admin-a',role='admin',org='A')
USER = dict(id='user-a',role='user',org='A')
OTHER = dict(id='other-a',role='user',org='A')
FOREIGN_ADMIN = dict(id='admin-b',role='admin',org='B')
FOREIGN_USER = dict(id='user-b',role='user',org='B')


def identities():
    return DemoIdentityProvider({u['id']:dict(password='test-only',role=u['role'],org=u['org'])
                                 for u in (ADMIN,USER,OTHER,FOREIGN_ADMIN,FOREIGN_USER)})


def simulate_worker(path, identifier, version, start, queue):
    service = ManagedReports(StateStore(path),identities())
    if not start.wait(15):
        queue.put(('timeout',0))
        return
    try:
        result = service.simulate_schedule(ADMIN,identifier,version)
        queue.put((result['status'],len(service.mail.messages)))
    except Exception as error:
        queue.put((type(error).__name__,0))


def snapshot(path):
    with sqlite3.connect(str(path)) as connection:
        return tuple(connection.iterdump())


class GovernanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'state.sqlite'
        self.store = StateStore(self.path)
        self.identities = identities()
        self.service = ManagedReports(self.store,self.identities)
        self.report = self.create()

    def create(self, **changes):
        return self.service.create_report(ADMIN,dict(name='Synthetic report',source_key='synthetic-monthly',**changes))

    def grant(self,kind='user',subject='user-a',actions=None):
        self.report = self.service.set_grant(ADMIN,self.report['id'],self.report['version'],kind,subject,
                                            ['view','export','maintain'] if actions is None else actions)
        return self.report

    def schedule(self,**changes):
        return self.service.create_schedule(ADMIN,self.report['id'],self.report['version'],
                                            dict(recipients=['tester@example.invalid'],enabled=True,**changes))

    def test_default_deny_no_user_metadata_or_data_and_no_admin_cross_tenant(self):
        self.assertEqual(self.service.list_reports(USER),[])
        self.assertEqual(self.service.permissions(USER,self.report['id']),())
        for user in (USER,OTHER):
            for operation in (lambda:self.service.rows(user,self.report['id']),
                              lambda:self.service.export(user,self.report['id']),
                              lambda:self.service.get_report(user,self.report['id']),
                              lambda:self.service.get_report(user,self.report['id'],for_maintenance=True)):
                with self.assertRaises(AccessDenied): operation()
        for user in (FOREIGN_ADMIN,FOREIGN_USER):
            self.assertEqual(self.service.list_reports(user),[])
            for operation in (lambda:self.service.get_report(user,self.report['id']),
                              lambda:self.service.permissions(user,self.report['id']),
                              lambda:self.service.export(user,self.report['id'])):
                with self.assertRaises(NotFound): operation()
        self.assertEqual(len(self.service.rows(ADMIN,self.report['id'])),12)

    def test_user_role_and_org_grants_are_additive_and_export_needs_view(self):
        self.grant(actions=['view'])
        self.assertEqual(self.service.permissions(USER,self.report['id']),('view',))
        with self.assertRaises(AccessDenied): self.service.export(USER,self.report['id'])
        self.grant('role','user',['view','export'])
        self.grant('user','user-a',[])
        self.assertEqual(self.service.permissions(USER,self.report['id']),('view','export'))
        self.grant('role','user',[])
        with self.assertRaises(AccessDenied): self.service.rows(USER,self.report['id'])
        self.grant('org','A',['view','maintain'])
        self.assertEqual(self.service.permissions(OTHER,self.report['id']),('view','maintain'))
        self.assertEqual(self.service.list_reports(FOREIGN_USER),[])

    def test_subject_validation_never_changes_identity_roles_or_crosses_org(self):
        for kind,subject,actions in [('role','superadmin',['view']),('user','user-b',['view']),
                                     ('user','unknown',['view']),('user',' user-a',['view']),
                                     ('user','user-a',['export']),('role','user',['view','view']),
                                     ('path','anything',['view'])]:
            with self.subTest(kind=kind,subject=subject),self.assertRaises(ValidationError):
                self.service.set_grant(ADMIN,self.report['id'],1,kind,subject,actions)
        with self.assertRaises(AccessDenied): self.service.set_grant(ADMIN,self.report['id'],1,'org','B',['view'])
        lookup = self.identities.get_user
        with patch.object(self.identities,'get_user',side_effect=lambda identifier:
                          dict(USER,id='other-a') if identifier == 'user-a' else lookup(identifier)):
            with self.assertRaises(ValidationError): self.service.set_grant(ADMIN,self.report['id'],1,'user','user-a',['view'])
        self.assertEqual(self.identities.get_user('user-a')['role'],'user')
        for operation in (lambda:self.service.create_report(USER,{'name':'x','source_key':'synthetic-monthly'}),
                          lambda:self.service.set_grant(USER,self.report['id'],1,'role','user',['view']),
                          lambda:self.service.list_grants(USER,self.report['id']),
                          lambda:self.service.list_audit(USER)):
            with self.assertRaises(AccessDenied): operation()

    def test_maintainer_only_metadata_and_disabled_report_control_is_admin(self):
        self.grant()
        updated=self.service.update_report(USER,self.report['id'],self.report['version'],
                                            {'name':'User label','category':'Operations','description':'Description'})
        for payload in ({'enabled':False},{'org':'B'},{'source_key':'other'},{'version':123}):
            with self.assertRaises((AccessDenied,ValidationError)):
                self.service.update_report(USER,updated['id'],updated['version'],payload)
        with self.assertRaises(AccessDenied): self.service.archive_report(USER,updated['id'],updated['version'])
        disabled=self.service.update_report(ADMIN,updated['id'],updated['version'],{'enabled':False})
        self.assertEqual(self.service.permissions(USER,disabled['id']),('maintain',))
        self.assertEqual(self.service.get_report(USER,disabled['id'],for_maintenance=True)['name'],'User label')
        self.assertEqual(self.service.list_reports(USER),[])
        with self.assertRaises(AccessDenied): self.service.export(ADMIN,disabled['id'])
        with self.assertRaises(AccessDenied): self.service.rows(USER,disabled['id'])

    def test_archiving_reversible_versioned_and_grants_return_after_restore(self):
        self.grant()
        archived=self.service.archive_report(ADMIN,self.report['id'],self.report['version'])
        self.assertEqual(self.service.permissions(USER,archived['id']),())
        self.assertEqual(self.service.list_reports(ADMIN),[])
        self.assertEqual(len(self.service.list_reports(ADMIN,include_archived=True,admin=True)),1)
        with self.assertRaises(Conflict): self.service.restore_report(ADMIN,archived['id'],self.report['version'])
        restored=self.service.restore_report(ADMIN,archived['id'],archived['version'])
        self.assertIsNone(restored['archived_at'])
        self.assertEqual(self.service.permissions(USER,restored['id']),ACTIONS)

    def test_report_create_replay_and_changes_conflict(self):
        key=secrets.token_hex(16)
        payload={'name':'Idempotent','source_key':'synthetic-monthly'}
        first=self.service.create_report(ADMIN,payload,request_key=key)
        self.assertEqual(first,self.service.create_report(ADMIN,payload,request_key=key))
        with self.assertRaises(Conflict): self.service.create_report(ADMIN,dict(payload,name='Other'),request_key=key)
        self.service.update_report(ADMIN,first['id'],1,{'name':'Changed'})
        with self.assertRaises(Conflict): self.service.create_report(ADMIN,payload,request_key=key)
        self.assertNotIn('create_key',first)

    def test_report_validation_and_unknown_server_fields(self):
        for payload in ({'name':''},{'name':'x'*121},{'category':'x'*65},{'description':'x'*241},
                        {'enabled':1},{'name':'bad\x00value'},{'org':'B'},{'archived_at':1}):
            with self.subTest(payload=payload),self.assertRaises(ValidationError):
                self.service.update_report(ADMIN,self.report['id'],1,payload)
        for source in ('../sql','https://example.invalid','SELECT 1','unknown'):
            with self.assertRaises(ValidationError):
                self.service.create_report(ADMIN,{'name':'X','source_key':source})
        for version in (True,0,1.0,'1',2**64):
            with self.assertRaises(ValidationError):self.service.update_report(ADMIN,self.report['id'],version,{'name':'X'})

    def test_stale_versions_and_threaded_updates_have_one_winner(self):
        def update(label):
            try:return self.service.update_report(ADMIN,self.report['id'],1,{'name':label})['version']
            except Conflict:return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool:
            values=list(pool.map(update,['one','two']))
        self.assertEqual(sorted(map(str,values)),['2','conflict'])
        self.assertEqual(len(self.service.list_audit(ADMIN)),2)

    def test_no_change_preserves_version_and_audit(self):
        current=self.service.update_report(ADMIN,self.report['id'],1,{'name':self.report['name']})
        self.assertEqual(current['version'],1)
        self.assertEqual(len(self.service.list_audit(ADMIN)),1)

    def test_atomic_audit_contains_actor_changed_fields_and_no_payload(self):
        updated=self.service.update_report(ADMIN,self.report['id'],1,{'name':'private-report-title'})
        event=self.service.list_audit(ADMIN)[0]
        self.assertEqual(event['actor_id'],'admin-a')
        self.assertEqual(event['changed_fields'],['name'])
        self.assertEqual((event['before_version'],event['after_version']),(1,2))
        self.assertNotIn('private-report-title',json.dumps(event))
        with patch.object(self.service,'_audit',side_effect=sqlite3.OperationalError('private-sql-marker')):
            with self.assertRaises(StateUnavailable): self.service.update_report(ADMIN,updated['id'],2,{'name':'lost-update'})
        self.assertEqual(self.service.get_report(ADMIN,updated['id'])['name'],'private-report-title')
        self.assertEqual(self.service.list_audit(FOREIGN_ADMIN),[])

    def test_schedule_validation_synthetic_only_and_no_arbitrary_execution(self):
        for changes in ({'recipients':['real@example.com']},{'recipients':['x@example.invalid\nBcc:a@example.invalid']},
                        {'recipients':[]},{'recipients':['A@example.invalid','a@example.invalid']},
                        {'time':'24:00'},{'time':'9:00'},{'timezone':'America/New_York'},
                        {'weekday':True},{'weekday':7},{'cadence':'cron'},{'enabled':1},{'sql':'SELECT 1'}):
            with self.subTest(changes=changes),self.assertRaises(ValidationError):
                self.service.create_schedule(ADMIN,self.report['id'],1,dict({'recipients':['x@example.invalid']},**changes))
        with self.assertRaises(AccessDenied):self.service.create_schedule(USER,self.report['id'],1,{'recipients':['x@example.invalid']})

    def test_next_candidate_explicit_zones_daily_weekly_and_aware_only(self):
        value={'time':'09:00','timezone':'Asia/Taipei','cadence':'daily','weekday':0}
        now=datetime(2026,10,2,0,59,tzinfo=timezone.utc)
        self.assertEqual(next_candidate(value,now),'2026-10-02T01:00:00+00:00')
        self.assertEqual(next_candidate(value,datetime(2026,10,2,1,0,tzinfo=timezone.utc)),'2026-10-03T01:00:00+00:00')
        value.update(cadence='weekly',weekday=0)
        self.assertEqual(next_candidate(value,now),'2026-10-05T01:00:00+00:00')
        with self.assertRaises(ValidationError):next_candidate(value,datetime(2026,1,1))

    def test_schedule_replay_versions_and_tenant_boundaries(self):
        key=secrets.token_hex(16)
        payload={'recipients':['tester@example.invalid'],'enabled':True}
        schedule=self.service.create_schedule(ADMIN,self.report['id'],1,payload,request_key=key)
        again=self.service.create_schedule(ADMIN,self.report['id'],1,payload,request_key=key)
        self.assertEqual(schedule['id'],again['id'])
        self.assertEqual(schedule['engine_status'],'not_started')
        self.assertIsNotNone(schedule['next_run_at_utc'])
        with self.assertRaises(NotFound):self.service.get_schedule(FOREIGN_ADMIN,schedule['id'])
        changed=self.service.update_schedule(ADMIN,schedule['id'],1,{'enabled':False})
        self.assertIsNone(changed['next_run_at_utc'])
        with self.assertRaises(Conflict):self.service.update_schedule(ADMIN,schedule['id'],1,{'time':'10:00'})
        with self.assertRaises(AccessDenied):self.service.simulate_schedule(ADMIN,schedule['id'],2)
        self.assertNotIn('tester@example.invalid',json.dumps(self.service.list_audit(ADMIN)))

    def test_mock_claim_once_per_version_durable_across_service_restart(self):
        schedule=self.schedule()
        first=self.service.simulate_schedule(ADMIN,schedule['id'],1)
        duplicate=ManagedReports(StateStore(self.path),self.identities).simulate_schedule(ADMIN,schedule['id'],1)
        self.assertEqual((first['status'],first['duplicate']),('succeeded',False))
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(len(self.service.mail.messages),1)
        self.assertEqual(len(self.service.list_runs(ADMIN,self.report['id'])),1)
        self.service.update_schedule(ADMIN,schedule['id'],1,{'time':'10:30'})
        self.assertFalse(self.service.simulate_schedule(ADMIN,schedule['id'],2)['duplicate'])
        self.assertEqual(len(self.service.mail.messages),2)

    def test_two_processes_capture_one_message_for_same_version(self):
        schedule=self.schedule()
        context=multiprocessing.get_context('spawn')
        start,queue=context.Event(),context.Queue()
        processes=[context.Process(target=simulate_worker,args=(str(self.path),schedule['id'],1,start,queue)) for _ in range(2)]
        for process in processes:process.start()
        start.set()
        results=[queue.get(timeout=30) for _ in processes]
        for process in processes:
            process.join(30)
            self.assertEqual(process.exitcode,0)
        self.assertEqual(sum(item[1] for item in results),1,results)
        self.assertTrue(all(item[0] in ('running','succeeded') for item in results),results)

    def test_uncertain_completion_is_not_replayed_or_relabelled(self):
        schedule=self.schedule()
        real=self.service._audit
        def fail_completion(connection,user,report_id,resource_id,action,*args,**kwargs):
            if action=='mock.succeeded':raise sqlite3.OperationalError('private audit failure')
            return real(connection,user,report_id,resource_id,action,*args,**kwargs)
        with patch.object(self.service,'_audit',side_effect=fail_completion):
            with self.assertRaises(StateUnavailable):self.service.simulate_schedule(ADMIN,schedule['id'],1)
        result=self.service.simulate_schedule(ADMIN,schedule['id'],1)
        self.assertEqual((result['status'],result['duplicate']),('running',True))
        self.assertEqual(len(self.service.mail.messages),1)

    def test_mock_failure_fixed_reason_and_no_retry(self):
        schedule=self.schedule()
        with patch.object(self.service.provider,'managed_rows',side_effect=RuntimeError('private-payload-marker')):
            result=self.service.simulate_schedule(ADMIN,schedule['id'],1)
        self.assertEqual((result['status'],result['error_code']),('failed','provider_unavailable'))
        self.assertNotIn('private-payload-marker',json.dumps(self.service.list_runs(ADMIN,self.report['id'])))
        self.assertTrue(self.service.simulate_schedule(ADMIN,schedule['id'],1)['duplicate'])
        self.assertEqual(self.service.mail.messages,[])

    def test_current_identity_and_disabled_report_rechecked_for_mock(self):
        schedule=self.schedule()
        self.identities.user_db['admin-a']['role']='user'
        with patch.object(self.service.provider, 'managed_rows') as provider:
            with self.assertRaises(AccessDenied):
                self.service.simulate_schedule(ADMIN,schedule['id'],1)
            provider.assert_not_called()
        self.assertEqual(self.service.mail.messages,[])
        self.identities.user_db['admin-a']['role']='admin'
        self.assertEqual(self.service.list_runs(ADMIN,self.report['id']),[])
        self.service.update_report(ADMIN,self.report['id'],1,{'enabled':False})
        with self.assertRaises(AccessDenied):self.service.simulate_schedule(ADMIN,schedule['id'],1)

    def test_obsolete_user_grant_can_be_revoked_and_frees_capacity(self):
        self.grant()
        del self.identities.user_db['user-a']
        self.report=self.service.set_grant(ADMIN,self.report['id'],self.report['version'],'user','user-a',[])
        self.assertEqual(self.service.list_grants(ADMIN,self.report['id']),[])
        self.identities.user_db['user-a']=dict(password='test',role='user',org='A')
        self.assertEqual(self.service.permissions(USER,self.report['id']),())

    def test_schedule_disabled_during_provider_read_cannot_capture(self):
        schedule=self.schedule()
        real=self.service.provider.managed_rows
        def disable(user,source):
            self.service.update_schedule(ADMIN,schedule['id'],1,{'enabled':False})
            return real(user,source)
        with patch.object(self.service.provider,'managed_rows',side_effect=disable):
            result=self.service.simulate_schedule(ADMIN,schedule['id'],1)
        self.assertEqual(result['error_code'],'configuration_changed')
        self.assertEqual(self.service.mail.messages,[])

    def test_identity_role_or_org_change_during_provider_read_cannot_release(self):
        real=self.service.provider.managed_rows
        def change_identity(user,source):
            self.identities.user_db['admin-a']['org']='B'
            return real(user,source)
        with patch.object(self.service.provider,'managed_rows',side_effect=change_identity):
            with self.assertRaises(AccessDenied):self.service.rows(ADMIN,self.report['id'])

    def test_provider_revoke_during_read_cannot_release_rows(self):
        self.grant(actions=['view'])
        real=self.service.provider.managed_rows
        def revoke(user,source):
            self.service.set_grant(ADMIN,self.report['id'],self.report['version'],'user','user-a',[])
            return real(user,source)
        with patch.object(self.service.provider,'managed_rows',side_effect=revoke):
            with self.assertRaises(AccessDenied):self.service.rows(USER,self.report['id'])

    def test_export_escapes_formula_cells_and_provider_failures_are_sanitized(self):
        rows=[dict(period=' \t=1+1',department=' @CMD ',revenue=1,cost=0,profit=1)]
        with patch.object(self.service.provider,'managed_rows',return_value=rows):
            result=self.service.export(ADMIN,self.report['id'])
        self.assertIn("'=1+1",result)
        self.assertIn("'@CMD",result)
        with patch.object(self.service.provider,'managed_rows',return_value=[dict(rows[0],profit=float('nan'))]):
            with self.assertRaises(ProviderUnavailable):self.service.rows(ADMIN,self.report['id'])

    def test_prod_adapter_is_explicit_no_legacy_fallback_and_mock_always_denied(self):
        class Legacy:
            is_demo=False
            def rows(self,user):raise AssertionError('Never use legacy rows')
        unsupported=ManagedReports(self.store,self.identities,Legacy(),is_demo=False)
        self.assertEqual(unsupported.sources(),[])
        with self.assertRaises(AccessDenied):unsupported.rows(ADMIN,self.report['id'])
        class Configured(SyntheticManagedProvider):is_demo=False
        configured=ManagedReports(self.store,self.identities,Configured(),is_demo=False)
        self.assertEqual(len(configured.rows(ADMIN,self.report['id'])),12)
        schedule=self.schedule()
        with self.assertRaises(AccessDenied):configured.simulate_schedule(ADMIN,schedule['id'],1)
        class Bad(Legacy):managed_sources=[{}]
        self.assertEqual(ManagedReports(self.store,self.identities,Bad(),is_demo=False).sources(),[])

    def test_storage_fail_closed_and_foreign_keys_active(self):
        unavailable=ManagedReports(None,self.identities)
        with self.assertRaises(StateUnavailable):unavailable.list_reports(ADMIN)
        with self.store._transaction(write=True) as connection:
            self.assertEqual(connection.execute('PRAGMA foreign_keys').fetchone()[0],1)
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute('INSERT INTO report_grants VALUES (?,?,?,?,?,?,?,?)',
                                   ('B',self.report['id'],'role','user',1,1,0,1))

    def test_combined_catalog_keeps_static_cards_and_authorized_generated_links(self):
        class Registry:
            def catalog(self,user):return ('legacy',)
        combined=CombinedCatalog(Registry(),self.service)
        self.assertEqual(combined.catalog(USER),('legacy',))
        self.grant(actions=['view'])
        values=combined.catalog(USER)
        self.assertEqual(values[0],'legacy')
        self.assertEqual(values[1].path,'/reports?report='+self.report['id'])
        self.assertEqual(values[1].page_id,'managed.'+self.report['id'])
        self.assertEqual(combined.catalog(FOREIGN_USER),('legacy',))


class GovernanceMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'v2.sqlite'
        with sqlite3.connect(str(self.path)) as connection:
            for ddl in _SCHEMA_DDL_V2.values():connection.execute(ddl)
            connection.execute('PRAGMA user_version=2')
            connection.execute("INSERT INTO leases VALUES ('lease','owner','token',9,99999999999)")
            connection.execute("INSERT INTO job_runs VALUES ('job','run','owner','token','running',1,NULL,NULL)")
            connection.execute("INSERT INTO audit_events VALUES (42,1,'runtime.started','owner',NULL,'success',NULL,NULL)")
            connection.execute('INSERT INTO report_definitions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                               ('a'*32,'A','b'*64,'c'*32,'Existing','Metadata','manual',1,7,1,2,None))

    def test_real_v2_migration_preserves_every_legacy_table_and_backup(self):
        store=StateStore(self.path)
        with store._transaction() as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0],3)
            self.assertEqual(connection.execute('SELECT version FROM report_definitions').fetchone()[0],7)
            self.assertEqual(connection.execute('SELECT fencing FROM leases').fetchone()[0],9)
            self.assertEqual(connection.execute('SELECT token FROM job_runs').fetchone()[0],'token')
        self.assertEqual(store.list_audit()[0].event_id,42)
        backup=Path(self.temp.name)/'backup.sqlite'
        store.backup_to(backup)
        self.assertEqual(StateStore(backup).list_uncertain_jobs()[0].state,'running')
        store.record_audit('runtime.started')
        self.assertEqual(store.list_audit()[-1].event_id,43)

    def test_bad_v2_schema_is_refused_before_any_ddl(self):
        with sqlite3.connect(str(self.path)) as connection:connection.execute('DROP INDEX report_definitions_org_updated')
        before=snapshot(self.path)
        with self.assertRaises(StateError):StateStore(self.path)
        self.assertEqual(snapshot(self.path),before)

    def test_migration_final_failure_rolls_back_every_new_table_and_version(self):
        before=snapshot(self.path)
        real=StateStore._validate_schema
        def fail(connection,version=3):
            real(connection,version)
            if version==3:raise StateError('validation failure')
        with patch.object(StateStore,'_validate_schema',side_effect=fail):
            with self.assertRaises(StateError):StateStore(self.path)
        self.assertEqual(snapshot(self.path),before)
