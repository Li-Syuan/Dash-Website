"""Offline engine tests: real timer, persisted DAGs, provenance and fencing."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from demo_services import AccessDenied
from reporting_workspace.crud import Conflict, NotFound, StateUnavailable, ValidationError
from reporting_workspace.etl_adapters import ETLRegistry, JobSpec, SnapshotAdapter, StepSpec, default_registry
from reporting_workspace.etl_dispatch import ETLDispatch, LOCAL_ZONE, MAX_BACKFILL_DAYS, MAX_SNAPSHOT_BYTES
from reporting_workspace.providers import DemoIdentityProvider

ADMIN = dict(id='admin-a',role='admin',org='A')
USER = dict(id='user-a',role='user',org='A')
ADMIN_B = dict(id='admin-b',role='admin',org='B')
JOB = 'synthetic-sales-daily'
INVENTORY = 'synthetic-inventory-health'


def identities():
    return DemoIdentityProvider({u['id']:dict(password='test-only',role=u['role'],org=u['org'])
                                 for u in (ADMIN,USER,ADMIN_B)})


def custom_registry(source=None, clean=None, summary=None, retry_safe=True, org='A', **step_options):
    source = source or (lambda context, upstream:[dict(record_id='row-1',amount=5),dict(record_id='row-2',amount=7)])
    clean = clean or (lambda context, upstream:[dict(row) for row in upstream['source']])
    summary = summary or (lambda context, upstream:[dict(record_id='summary',amount=sum(row['amount'] for row in upstream['clean']))])
    steps = (
        StepSpec('source',SnapshotAdapter('source-adapter','v1',source),required_fields=('record_id','amount'),unique_fields=('record_id',)),
        StepSpec('clean',SnapshotAdapter('clean-adapter','v1',clean,retry_safe),('source',),
                 required_fields=('record_id','amount'),nonnegative_fields=('amount',),count_matches='source',**step_options),
        StepSpec('summary',SnapshotAdapter('summary-adapter','v1',summary),('clean',)))
    return ETLRegistry((JobSpec('custom-job','Custom synthetic job','Offline test fixture.',org,steps,'summary'),))


class RegistryTests(unittest.TestCase):
    def test_defaults_are_two_independent_topological_pipelines(self):
        jobs = default_registry().snapshot()
        self.assertEqual(set(jobs),{JOB,INVENTORY})
        for job in jobs.values():
            self.assertEqual([s.name for s in job.steps],['source','validate','summary'])
            self.assertEqual(len(job.signature),64)

    def test_cycle_missing_dependency_and_duplicate_step_rejected(self):
        adapter = SnapshotAdapter('adapter','v1',lambda c,u:[])
        bad = (
            (StepSpec('a',adapter,('b',)),StepSpec('b',adapter,('a',))),
            (StepSpec('a',adapter,('missing',)),),
            (StepSpec('a',adapter),StepSpec('a',adapter)))
        for steps in bad:
            with self.subTest(steps=steps),self.assertRaises(ValueError):
                JobSpec('job','Synthetic','Test pipeline','A',steps,'a')

    def test_unchecked_branch_cannot_be_left_out_of_publication(self):
        adapter = SnapshotAdapter('adapter','v1',lambda c,u:[])
        with self.assertRaises(ValueError):
            JobSpec('job','Synthetic','Test pipeline','A',(StepSpec('a',adapter),StepSpec('b',adapter)),'b')

    def test_registry_is_trusted_callable_only_and_frozen_for_engine(self):
        with self.assertRaises(ValueError):
            SnapshotAdapter('source','v1','lambda: exec(code)')
        with self.assertRaises(ValueError):
            StepSpec('step','submitted.sql')
        registry = default_registry()
        snapshot = registry.snapshot()
        with self.assertRaises(TypeError):
            snapshot['new'] = snapshot[JOB]
        with self.assertRaises(ValueError):
            registry.register(snapshot[JOB])

    def test_signature_binds_adapter_version_rules_and_access(self):
        job = default_registry().snapshot()[JOB]
        changes = [replace(job,org='B'),replace(job,allowed_user_ids=('only-user',)),
                   replace(job,steps=(replace(job.steps[0],max_rows=5),)+job.steps[1:]),
                   replace(job,steps=(replace(job.steps[0],adapter=replace(job.steps[0].adapter,version='v2')),)+job.steps[1:])]
        self.assertTrue(all(changed.signature!=job.signature for changed in changes))


class ETLEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'etl.sqlite'
        self.identities = identities()
        self.engine = self.make()

    def make(self,path=None,**kwargs):
        engine = ETLDispatch(path or self.path,self.identities,**kwargs)
        self.addCleanup(engine.stop)
        return engine

    def custom(self,**kwargs):
        engine_options = kwargs.pop('engine_options',{})
        return self.make(path=Path(self.temp.name)/('custom-'+str(len(list(Path(self.temp.name).glob('custom-*'))))+'.sqlite'),
                         registry=custom_registry(**kwargs),**engine_options)

    def wait_for(self,predicate,timeout=4):
        deadline = time.monotonic()+timeout
        while time.monotonic()<deadline:
            value=predicate()
            if value:
                return value
            time.sleep(.01)
        self.fail('Expected real worker condition was not observed.')

    def test_construction_configuration_and_import_do_not_start_worker(self):
        with patch('reporting_workspace.etl_dispatch.threading.Thread') as thread:
            engine=self.make()
            self.assertFalse(engine.worker_running)
            self.assertEqual(engine.status(USER,JOB)['runs'],[])
            engine.configure(ADMIN,JOB,True,60)
            thread.assert_not_called()
        self.assertEqual(engine.status(USER,JOB)['timezone'],'Asia/Taipei')

    def test_both_default_jobs_publish_real_outputs_and_safe_detail(self):
        for job in (JOB,INVENTORY):
            run=self.engine.run_now(ADMIN,job)
            self.assertEqual(run['status'],'succeeded')
            self.assertEqual(run['row_count'],1)
            self.assertTrue(all(s['status']=='succeeded' for s in run['steps']))
            self.assertTrue(all(check['passed'] for step in run['steps'] for check in step['checks']))
            self.assertIn('+08:00',run['started_at'])
            self.assertGreaterEqual(run['duration_seconds'],0)
            self.assertFalse(run['retry_allowed'])
            public=json.dumps(self.engine.status(USER,job))
            for forbidden in ('snapshot','digest','token','actor_id','synthetic-sale-1','/tmp/'):
                self.assertNotIn(forbidden,public)
        with sqlite3.connect(str(self.path)) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM etl_publications').fetchone()[0],2)

    def test_readers_cannot_manage_and_foreign_tenants_cannot_read_jobs(self):
        self.assertEqual(len(self.engine.jobs(USER)),2)
        self.assertEqual(self.engine.jobs(ADMIN_B),[])
        for method in (lambda:self.engine.status(ADMIN_B,JOB),lambda:self.engine.run_now(ADMIN_B,JOB),
                       lambda:self.engine.run_now(USER,JOB),lambda:self.engine.configure(USER,JOB,True,60),
                       lambda:self.engine.backfill(USER,JOB,'2026-01-01','2026-01-01')):
            with self.assertRaises(AccessDenied):
                method()
        run=self.engine.run_now(ADMIN,JOB)
        with self.assertRaises(AccessDenied):
            self.engine.run_detail(ADMIN_B,run['run_id'])

    def test_job_specific_allowlist_and_other_org_registry(self):
        registry=custom_registry(org='B')
        engine=self.make(path=Path(self.temp.name)/'tenant-b.sqlite',registry=registry)
        self.assertEqual(engine.jobs(ADMIN),[])
        self.assertEqual(engine.run_now(ADMIN_B,'custom-job')['status'],'succeeded')
        restricted=replace(default_registry().snapshot()[JOB],allowed_user_ids=('admin-a',))
        other=self.make(path=Path(self.temp.name)/'restricted.sqlite',registry=ETLRegistry((restricted,)))
        self.assertEqual(other.jobs(USER),[])
        self.assertEqual(len(other.jobs(ADMIN)),1)

    def test_identity_reload_stale_claim_and_provider_outage(self):
        for spoof in (dict(USER,role='admin'),dict(ADMIN,org='B'),None):
            with self.assertRaises(AccessDenied):
                self.engine.jobs(spoof)
        self.identities.user_db[ADMIN['id']]['role']='user'
        with self.assertRaises(AccessDenied):
            self.engine.run_now(ADMIN,JOB)
        with patch.object(self.identities,'get_user',side_effect=RuntimeError('SECRET_DATABASE')):
            with self.assertRaises(AccessDenied) as caught:
                self.engine.jobs(USER)
            self.assertNotIn('SECRET_DATABASE',str(caught.exception))

    def test_interval_and_versions_are_validated_without_mutation(self):
        for value in (True,False,None,'60',0,-1,59,86401,float('nan'),float('inf'),10**400):
            with self.subTest(value=str(value)[:15]),self.assertRaises(ValidationError):
                self.engine.configure(ADMIN,JOB,True,value)
        for enabled in (0,1,'true',None):
            with self.assertRaises(ValidationError):
                self.engine.configure(ADMIN,JOB,enabled,60)
        for version in (True,0,-1,'1',2**100):
            with self.assertRaises(ValidationError):
                self.engine.configure(ADMIN,JOB,True,60,version)
        self.assertEqual(self.engine.status(USER,JOB)['version'],1)
        changed=self.engine.configure(ADMIN,JOB,True,60,1)
        self.assertEqual(changed['version'],2)
        with self.assertRaises(Conflict):
            self.engine.configure(ADMIN,JOB,False,60,1)

    def test_real_timer_start_stop_is_idempotent_and_stops_future_runs(self):
        engine=self.make(min_interval_seconds=.03,poll_interval_seconds=.01)
        engine.configure(ADMIN,JOB,True,.03)
        self.assertTrue(engine.start())
        self.assertFalse(engine.start())
        self.wait_for(lambda:len(engine.status(USER,JOB)['runs'])>=2)
        self.assertTrue(engine.stop())
        runs=engine.status(USER,JOB)['runs']
        self.assertTrue(all(run['trigger']=='timer' for run in runs))
        time.sleep(.08)
        self.assertEqual(len(engine.status(USER,JOB)['runs']),len(runs))
        self.assertTrue(engine.stop())

    def test_missed_ticks_coalesce_and_disabled_jobs_do_not_run(self):
        engine=self.make(min_interval_seconds=.01)
        engine.configure(ADMIN,JOB,True,.01)
        with sqlite3.connect(str(self.path)) as connection:
            connection.execute('UPDATE etl_config SET next_run_at=? WHERE job_id=?',(time.time()-100,JOB))
        self.assertEqual(len(engine.tick()),1)
        self.assertEqual(len(engine.status(USER,JOB)['runs']),1)
        self.assertEqual(engine.status(USER,INVENTORY)['runs'],[])
        engine.configure(ADMIN,JOB,False,.01)
        time.sleep(.03)
        self.assertEqual(engine.tick(),[])

    def test_worker_revocation_disables_due_schedule(self):
        engine=self.make(min_interval_seconds=.01)
        engine.configure(ADMIN,JOB,True,.01)
        self.identities.user_db[ADMIN['id']]['role']='user'
        time.sleep(.02)
        engine.tick()
        status=engine.status(USER,JOB)
        self.assertFalse(status['enabled'])
        self.assertEqual(status['runs'],[])

    def test_request_dedup_survives_restart_and_conflicting_payload_is_rejected(self):
        first=self.engine.run_now(ADMIN,JOB,'2026-01-01','request-one')
        restarted=self.make()
        again=restarted.run_now(ADMIN,JOB,'2026-01-01','request-one')
        self.assertEqual(first['run_id'],again['run_id'])
        for operation in (lambda:restarted.run_now(ADMIN,JOB,'2026-01-02','request-one'),
                          lambda:restarted.run_now(ADMIN,INVENTORY,'2026-01-01','request-one'),
                          lambda:restarted.backfill(ADMIN,JOB,'2026-01-01','2026-01-01','request-one')):
            with self.assertRaises(Conflict):
                operation()
        self.assertEqual(len(restarted.status(USER,JOB)['runs']),1)

    def test_dates_reject_future_impossible_noncanonical_and_wrong_types(self):
        future=(datetime.now(LOCAL_ZONE).date()+timedelta(days=1)).isoformat()
        for date in ('2026-1-1','2026-02-30','2026-01-01T00:00:00','0000-01-01',future,1,True,{},'x'*10000):
            with self.subTest(date=str(date)[:20]),self.assertRaises(ValidationError):
                self.engine.run_now(ADMIN,JOB,date)
        self.assertEqual(self.engine.status(USER,JOB)['runs'],[])

    def test_invalid_request_ids_cannot_mutate(self):
        for request in ('','x'*129,'secret with spaces',"'; DROP TABLE etl_runs",True,{},'繁體'):
            with self.assertRaises(ValidationError):
                self.engine.run_now(ADMIN,JOB,request_id=request)
        self.assertEqual(self.engine.status(USER,JOB)['runs'],[])

    def test_backfill_is_inclusive_and_overlapping_dates_reuse_original_runs(self):
        first=self.engine.backfill(ADMIN,JOB,'2026-01-01','2026-01-03','batch-a')
        self.assertEqual([r['business_date'] for r in first['runs']],['2026-01-01','2026-01-02','2026-01-03'])
        repeated=self.make().backfill(ADMIN,JOB,'2026-01-01','2026-01-03','batch-a')
        self.assertEqual([r['run_id'] for r in first['runs']],[r['run_id'] for r in repeated['runs']])
        overlap=self.engine.backfill(ADMIN,JOB,'2026-01-03','2026-01-04','batch-b')
        self.assertEqual(first['runs'][2]['run_id'],overlap['runs'][0]['run_id'])
        self.assertEqual(len(self.engine.status(USER,JOB)['runs']),4)

    def test_backfill_reversed_and_over31_nonmutating(self):
        for dates in (('2026-02-01','2026-01-01'),('2026-01-01','2026-02-01')):
            with self.assertRaises(ValidationError):
                self.engine.backfill(ADMIN,JOB,*dates)
        self.assertEqual(self.engine.status(USER,JOB)['runs'],[])
        self.assertEqual(MAX_BACKFILL_DAYS,31)

    def test_backfill_requires_both_explicit_dates(self):
        for dates in ((None,'2026-01-01'),('2026-01-01',None),(None,None)):
            with self.assertRaises(ValidationError):
                self.engine.backfill(ADMIN,JOB,*dates)
        self.assertEqual(self.engine.status(USER,JOB)['runs'],[])

    def test_unique_key_requires_present_nonnull_and_records_observed_count(self):
        for source in (lambda c,u:[dict(amount=1)],lambda c,u:[dict(record_id=None,amount=1)],
                       lambda c,u:[dict(record_id='same',amount=1),dict(record_id='same',amount=2)]):
            adapter=SnapshotAdapter('source','v1',source)
            registry=ETLRegistry((JobSpec('unique-job','Unique checks','Synthetic fixture','A',
                       (StepSpec('source',adapter,unique_fields=('record_id',)),),'source'),))
            engine=self.make(path=Path(self.temp.name)/(str(id(source))+'.sqlite'),registry=registry)
            run=engine.run_now(ADMIN,'unique-job')
            self.assertEqual(run['error_code'],'quality_failed')
            self.assertEqual(run['steps'][0]['row_count'],len(source({},{})))
            with sqlite3.connect(engine.path) as connection:
                self.assertIsNone(connection.execute('SELECT snapshot FROM etl_steps').fetchone()[0])

    def test_duplicate_request_recovers_expired_crash_without_worker_tick(self):
        engine=self.make(lease_ttl_seconds=.05)
        claimed=engine._claim(ADMIN,engine.registry[JOB],'2026-01-01','manual','manual:expired','expired')
        time.sleep(.07)
        replay=self.make().run_now(ADMIN,JOB,'2026-01-01','expired')
        self.assertEqual(replay['run_id'],claimed['run_id'])
        self.assertEqual(replay['error_code'],'interrupted')
        self.assertFalse(replay['retry_allowed'])

    def test_backfill_exact31_dates_is_accepted(self):
        result=self.engine.backfill(ADMIN,INVENTORY,'2026-01-01','2026-01-31','maximum-batch')
        self.assertEqual(len(result['runs']),31)
        self.assertTrue(all(run['status']=='succeeded' for run in result['runs']))

    def test_failure_skips_descendants_and_hides_source_exception(self):
        def fail(context,upstream):
            raise RuntimeError('SECRET password COMPANY_DB /private/company.csv')
        engine=self.custom(clean=fail)
        run=engine.run_now(ADMIN,'custom-job')
        self.assertEqual(run['status'],'failed')
        self.assertEqual(run['error_code'],'adapter_failed')
        self.assertEqual([s['status'] for s in run['steps']],['succeeded','failed','skipped'])
        self.assertTrue(run['retry_allowed'])
        self.assertNotIn('SECRET',json.dumps(engine.status(USER,'custom-job')))
        self.assertIsNone(engine.status(USER,'custom-job')['publication'])

    def test_retry_preserves_upstream_snapshot_and_provenance(self):
        calls={'source':0,'clean':0}
        def source(c,u):
            calls['source']+=1
            return [dict(record_id='row',amount=calls['source'])]
        def clean(c,u):
            calls['clean']+=1
            if calls['clean']==1:
                raise ValueError('Transient synthetic failure')
            return u['source']
        engine=self.custom(source=source,clean=clean)
        first=engine.run_now(ADMIN,'custom-job','2026-01-01','initial')
        retry=engine.retry(ADMIN,first['run_id'],'retry-one')
        self.assertEqual(retry['status'],'succeeded')
        self.assertEqual(retry['attempt'],2)
        self.assertEqual(retry['parent_run_id'],first['run_id'])
        self.assertEqual(retry['root_run_id'],first['run_id'])
        self.assertEqual(retry['steps'][0]['reused_from_run_id'],first['run_id'])
        self.assertEqual(calls,{'source':1,'clean':2})
        self.assertEqual(engine.retry(ADMIN,first['run_id'],'retry-one')['run_id'],retry['run_id'])
        self.assertEqual(engine.run_detail(USER,first['run_id'])['status'],'failed')
        with sqlite3.connect(engine.path) as c:
            snapshots=c.execute("SELECT snapshot FROM etl_steps WHERE name='source' ORDER BY run_id").fetchall()
            self.assertEqual(snapshots[0][0],snapshots[1][0])

    def test_retry_chain_is_capped_at_three_attempts(self):
        def fail(c,u):
            raise RuntimeError('safe synthetic failure')
        engine=self.custom(clean=fail)
        run=engine.run_now(ADMIN,'custom-job')
        for attempt in (2,3):
            run=engine.retry(ADMIN,run['run_id'])
            self.assertEqual(run['attempt'],attempt)
        self.assertFalse(run['retry_allowed'])
        with self.assertRaises(Conflict):
            engine.retry(ADMIN,run['run_id'])
        self.assertEqual(len(engine.status(USER,'custom-job')['runs']),3)

    def test_unsafe_adapter_and_changed_config_cannot_retry(self):
        def fail(c,u):
            raise ValueError()
        unsafe=self.custom(clean=fail,retry_safe=False)
        run=unsafe.run_now(ADMIN,'custom-job')
        self.assertFalse(run['retry_allowed'])
        with self.assertRaises(Conflict):
            unsafe.retry(ADMIN,run['run_id'])
        safe=self.custom(clean=fail)
        failed=safe.run_now(ADMIN,'custom-job')
        safe.configure(ADMIN,'custom-job',True,60)
        self.assertFalse(safe.run_detail(ADMIN,failed['run_id'])['retry_allowed'])
        with self.assertRaises(Conflict):
            safe.retry(ADMIN,failed['run_id'])

    def test_corrupt_snapshot_and_provenance_cannot_retry(self):
        def fail(c,u):
            raise ValueError()
        for corruption in ("snapshot='[]'","row_count=99","digest='bad'","checks_json='[]'","reused_from_run_id='missing'"):
            engine=self.custom(clean=fail)
            run=engine.run_now(ADMIN,'custom-job')
            with sqlite3.connect(engine.path) as c:
                c.execute("UPDATE etl_steps SET "+corruption+" WHERE name='source'")
            self.assertFalse(engine.run_detail(ADMIN,run['run_id'])['retry_allowed'])
            with self.assertRaises(Conflict):
                engine.retry(ADMIN,run['run_id'])

    def test_dependency_input_mutation_does_not_mutate_persisted_upstream(self):
        def mutate(c,u):
            u['source'][0]['amount']=999
            return u['source']
        engine=self.custom(clean=mutate)
        run=engine.run_now(ADMIN,'custom-job')
        self.assertEqual(run['status'],'succeeded')
        with sqlite3.connect(engine.path) as c:
            raw=c.execute("SELECT snapshot FROM etl_steps WHERE name='source'").fetchone()[0]
            self.assertNotIn('999',raw)

    def test_count_missing_duplicate_and_negative_quality_checks(self):
        cases=(lambda c,u:[],lambda c,u:[{'record_id':'x'},{'record_id':'y'}],
               lambda c,u:[dict(record_id='x',amount=-1),dict(record_id='y',amount=3)],
               lambda c,u:[dict(record_id='x',amount=True),dict(record_id='y',amount=3)])
        for clean in cases:
            engine=self.custom(clean=clean)
            run=engine.run_now(ADMIN,'custom-job')
            self.assertEqual(run['error_code'],'quality_failed')
            self.assertTrue(any(not check['passed'] for check in run['steps'][1]['checks']))
        duplicate=self.custom(source=lambda c,u:[dict(record_id='same',amount=1),dict(record_id='same',amount=2)])
        self.assertEqual(duplicate.run_now(ADMIN,'custom-job')['error_code'],'quality_failed')

    def test_snapshot_contract_rejects_nonlist_nested_nan_big_and_oversized_values(self):
        outputs=({},None,True,[dict(record_id='x',amount=float('nan'))],
                 [dict(record_id='x',amount=float('inf'))],[dict(record_id='x',amount=10**400)],
                 [dict(record_id='x',amount=[])],[dict(record_id='x'*257,amount=1)],
                 [dict(record_id='x',amount=1)]*1001)
        for output in outputs:
            engine=self.custom(source=lambda c,u,out=output:out)
            run=engine.run_now(ADMIN,'custom-job')
            self.assertEqual(run['error_code'],'snapshot_invalid')
            self.assertIsNone(engine.status(USER,'custom-job')['publication'])
        self.assertEqual(MAX_SNAPSHOT_BYTES,262144)

    def test_publication_last_success_is_retained_after_later_failure(self):
        state={'fail':False}
        def clean(c,u):
            if state['fail']:
                raise RuntimeError()
            return u['source']
        engine=self.custom(clean=clean)
        success=engine.run_now(ADMIN,'custom-job','2026-01-01')
        state['fail']=True
        self.assertEqual(engine.run_now(ADMIN,'custom-job','2026-01-01')['status'],'failed')
        self.assertEqual(engine.status(USER,'custom-job')['publication']['run_id'],success['run_id'])

    def test_same_job_active_lease_blocks_overlapping_instances_but_dedups_same_request(self):
        entered,release=threading.Event(),threading.Event()
        def source(c,u):
            entered.set()
            if not release.wait(4):
                raise RuntimeError()
            return [dict(record_id='row',amount=1)]
        engine=self.custom(source=source)
        other=self.make(path=engine.path,registry=custom_registry(source=source))
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(engine.run_now,ADMIN,'custom-job','2026-01-01','inflight')
            self.assertTrue(entered.wait(2))
            self.assertEqual(other.run_now(ADMIN,'custom-job','2026-01-01','inflight')['status'],'running')
            with self.assertRaises(Conflict):
                other.run_now(ADMIN,'custom-job','2026-01-01','different')
            release.set()
            self.assertEqual(future.result(4)['status'],'succeeded')
        self.assertEqual(len(other.status(USER,'custom-job')['runs']),1)

    def test_expired_old_worker_cannot_publish_over_successor(self):
        entered,release=threading.Event(),threading.Event()
        calls={'count':0}
        def source(c,u):
            calls['count']+=1
            if calls['count']==1:
                entered.set()
                release.wait(4)
            return [dict(record_id='row',amount=calls['count'])]
        registry=custom_registry(source=source)
        engine=self.make(path=Path(self.temp.name)/'fenced.sqlite',registry=registry,lease_ttl_seconds=.05)
        other=self.make(path=engine.path,registry=registry,lease_ttl_seconds=.5)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending=pool.submit(engine.run_now,ADMIN,'custom-job','2026-01-01','old')
            self.assertTrue(entered.wait(2))
            time.sleep(.07)
            newer=other.run_now(ADMIN,'custom-job','2026-01-01','new')
            self.assertEqual(newer['status'],'succeeded')
            release.set()
            older=pending.result(4)
        self.assertEqual(older['error_code'],'interrupted')
        self.assertFalse(older['retry_allowed'])
        self.assertEqual(other.status(USER,'custom-job')['publication']['run_id'],newer['run_id'])

    def test_restart_recovers_interrupted_attempt_without_automatic_replay(self):
        engine=self.make(lease_ttl_seconds=.05)
        job=engine.registry[JOB]
        claimed=engine._claim(ADMIN,job,'2026-01-01','manual','manual:crash','crash')
        time.sleep(.07)
        restarted=self.make()
        self.assertEqual(restarted.tick(),[])
        detail=restarted.run_detail(USER,claimed['run_id'])
        self.assertEqual(detail['error_code'],'interrupted')
        self.assertFalse(detail['retry_allowed'])
        self.assertEqual(restarted.run_now(ADMIN,JOB,'2026-01-01','crash')['run_id'],claimed['run_id'])
        with self.assertRaises(Conflict):
            restarted.retry(ADMIN,claimed['run_id'])
        self.assertIsNone(restarted.status(USER,JOB)['publication'])

    def test_lease_timeout_fails_and_preserves_no_publication(self):
        def slow(c,u):
            time.sleep(.07)
            return [dict(record_id='row',amount=1)]
        engine=self.custom(source=slow,engine_options=dict(lease_ttl_seconds=.05))
        run=engine.run_now(ADMIN,'custom-job')
        self.assertEqual(run['error_code'],'lease_lost')
        self.assertFalse(run['retry_allowed'])
        self.assertIsNone(engine.status(USER,'custom-job')['publication'])

    def test_current_identity_revocation_inside_adapter_blocks_effects(self):
        def revoke(c,u):
            self.identities.user_db[ADMIN['id']]['role']='user'
            return u['source']
        engine=self.custom(clean=revoke)
        with self.assertRaises(AccessDenied):
            engine.run_now(ADMIN,'custom-job')
        run=engine.status(USER,'custom-job')['runs'][0]
        self.assertEqual(run['error_code'],'permission_revoked')
        self.assertIsNone(engine.status(USER,'custom-job')['publication'])

    def test_configuration_changed_inside_adapter_blocks_publication(self):
        holder={}
        def change(c,u):
            holder['engine'].configure(ADMIN,'custom-job',True,60)
            return u['source']
        engine=self.custom(clean=change)
        holder['engine']=engine
        run=engine.run_now(ADMIN,'custom-job')
        self.assertEqual(run['error_code'],'configuration_changed')
        self.assertIsNone(engine.status(USER,'custom-job')['publication'])

    def test_registry_drift_and_wrong_schema_are_rejected(self):
        changed=replace(default_registry().snapshot()[JOB],org='B')
        with self.assertRaises(StateUnavailable):
            self.make(registry=ETLRegistry((changed,default_registry().snapshot()[INVENTORY])))
        with sqlite3.connect(str(self.path)) as connection:
            connection.execute('CREATE TABLE unexpected(secret TEXT)')
        with self.assertRaises(StateUnavailable):
            self.engine.status(USER,JOB)
        with self.assertRaises(StateUnavailable):
            self.make()

    def test_unversioned_nonempty_future_schema_and_wal_rejected(self):
        for suffix,statement in (('nonempty','CREATE TABLE old(id TEXT)'),('future','PRAGMA user_version=99'),('wal','PRAGMA journal_mode=WAL')):
            path=Path(self.temp.name)/(suffix+'.sqlite')
            with sqlite3.connect(str(path)) as connection:
                connection.execute(statement)
            with self.assertRaises(StateUnavailable):
                self.make(path=path)

    def test_unknown_job_run_and_malformed_identifiers_are_safe(self):
        for job in ('missing',{},None):
            with self.assertRaises(NotFound):
                self.engine.status(USER,job)
        for run in ('missing','0'*32,{},None):
            with self.assertRaises(NotFound):
                self.engine.run_detail(USER,run)


if __name__=='__main__':
    unittest.main()
