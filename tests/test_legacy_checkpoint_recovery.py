"""Exercise actual collection/checkpoint recovery; supplier replies are bounded fixtures."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from axiom_data import ArtifactError, load_raw_batch
from axiom_data.artifacts import _digest, _json_bytes
from axiom_data.bootstrap_sources import collect_bootstrap_sources
from axiom_data.operations import _request, collect_requests
from test_source_completeness import SourceCompletenessTest


class ChildClient:
    def __init__(self, fail_second=False):
        self.calls = []
        self.fail_second = fail_second

    def query(self, endpoint, *, fields, **params):
        scope = (params['start_date'], params['end_date']); self.calls.append(scope)
        if scope == ('20250402', '20250630') and self.fail_second:
            raise ConnectionError('fixture interruption')
        if scope == ('20250101', '20250630'):
            raise AssertionError('the invalid legacy parent must never be recollected')
        period = '20250331' if scope[0] == '20250101' else '20250630'
        return [dict(ts_code='000001.SZ',ann_date='20250701',end_date=period,current_ratio=2)]


def seed_legacy(root, *, checkpoint=True, overlapping_failure=False):
    fixture = type('Fixture', (), {'root': Path(root)})()
    raw = SourceCompletenessTest.raw(fixture, 100)
    loaded = load_raw_batch(root, raw.raw_batch_id)
    params = loaded.manifest['request']['params']
    spec = dict(collector='pr6_indicator',domain='financial_events',endpoint='fina_indicator',params=params,
                economic_scope={'start':params['start_date'],'end':params['end_date']},availability_policy='revision_scan')
    key = _request(spec); batch = 'legacy-recovery-financial_events-0'
    digest = _digest(_json_bytes({'requests':[spec],'request_keys':[key]}))
    failure = {'error_type':'SourceCompletenessError','raw_batch_id':raw.raw_batch_id}
    state = dict(schema_version='collection_run.v1',run_id=batch,plan_digest=digest,requests=[spec],
                 completed={key:raw.raw_batch_id},failed={key:failure} if overlapping_failure else {},
                 stage='COLLECTION',status='COMPLETE')
    directory = Path(root)/'operations'/batch; directory.mkdir(parents=True)
    (directory/'collection.json').write_bytes(_json_bytes(state))
    if checkpoint:
        records = directory/'collection-checkpoints'; records.mkdir()
        (records/(key[7:]+'.json')).write_bytes(_json_bytes(dict(schema_version='collection_checkpoint.v1',
            plan_digest=digest,request_id=key,raw_batch_id=raw.raw_batch_id,
            failure=failure if overlapping_failure else None)))
    plan = {'requests_by_domain':{'financial_events':[spec]}}
    return plan, key, raw, batch


class LegacyCheckpointRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def run_recovery(self, plan, client):
        return collect_bootstrap_sources(self.root,run_id='legacy-recovery',plan=plan,
                                         domains=['financial_events'],client=client)

    def assert_exclusive(self):
        for path in (self.root/'operations').glob('*/collection.json'):
            state = json.loads(path.read_bytes())
            self.assertFalse(set(state['completed']) & set(state['failed']), str(path))
            for key, record in state.get('request_states',{}).items():
                self.assertEqual(key in state['completed'], record['state']=='VALID_COMPLETE')

    def test_legacy_completed_100_executes_children_and_never_reuses_invalid_parent(self):
        plan,key,parent,batch = seed_legacy(self.root,overlapping_failure=True)
        client = ChildClient(); result = self.run_recovery(plan,client)
        self.assertEqual(result['status'],'COMPLETE',result)
        self.assertEqual(client.calls,[('20250101','20250401'),('20250402','20250630')])
        state = json.loads((self.root/'operations'/batch/'collection.json').read_bytes())
        record = state['request_states'][key]
        self.assertEqual(record['state'],'SUPERSEDED_BY_SPLIT')
        self.assertEqual(len(record['child_refs']['request_ids']),2)
        self.assertNotIn(parent.raw_batch_id,result['domains']['financial_events']['completed_raw_batch_ids'])
        self.assertEqual(len(result['domains']['financial_events']['completed_raw_batch_ids']),2)
        self.assert_exclusive()
        with self.assertRaises(ArtifactError): SourceCompletenessTest.build(self,parent)
        self.assertEqual(list((self.root/'canonical/financial_events/commits').glob('*/manifest.json')),[])
        first_graph = copy.deepcopy(record['child_refs'])
        checkpoints = {str(p):p.read_bytes() for p in (self.root/'operations').glob('*/collection-checkpoints/*.json')}
        for _ in range(2):
            self.assertEqual(self.run_recovery(plan,client),result)
        self.assertEqual(len(client.calls),2)
        final = json.loads((self.root/'operations'/batch/'collection.json').read_bytes())
        self.assertEqual(final['request_states'][key]['state'],'SUPERSEDED_BY_SPLIT')
        self.assertEqual(final['request_states'][key]['child_refs'],first_graph)
        self.assertEqual({str(p):p.read_bytes() for p in (self.root/'operations').glob('*/collection-checkpoints/*.json')},checkpoints)
        self.assert_exclusive()

    def test_interrupted_child_resume_executes_only_failed_child(self):
        plan,key,parent,batch = seed_legacy(self.root,checkpoint=False)
        client = ChildClient(fail_second=True)
        first = self.run_recovery(plan,client)
        self.assertEqual(first['status'],'FAILED')
        self.assertEqual(len(client.calls),2)
        self.assertEqual(len(first['domains']['financial_events']['completed_raw_batch_ids']),1)
        self.assert_exclusive(); client.fail_second=False
        final = self.run_recovery(plan,client)
        self.assertEqual(final['status'],'COMPLETE')
        self.assertEqual(client.calls,[('20250101','20250401'),('20250402','20250630'),('20250402','20250630')])
        self.assert_exclusive()
        self.assertEqual(self.run_recovery(plan,client),final)
        self.assertEqual(len(client.calls),3)

    def test_upgraded_needs_split_is_durable_before_recovery_or_source_call(self):
        from axiom_data import operations
        plan,key,parent,batch = seed_legacy(self.root)
        client = ChildClient(); save = operations._save
        def stop_after_upgrade(path, value):
            save(path,value)
            if path.parent.name=='collection-checkpoints' and value.get('state')=='NEEDS_SPLIT':
                raise KeyboardInterrupt('fixture crash after durable upgrade')
        with patch.object(operations,'_save',side_effect=stop_after_upgrade):
            with self.assertRaises(KeyboardInterrupt): self.run_recovery(plan,client)
        self.assertEqual(client.calls,[])
        path = self.root/'operations'/batch/'collection-checkpoints'/(key[7:]+'.json')
        self.assertEqual(json.loads(path.read_bytes())['schema_version'],'collection_checkpoint.v2')
        self.assertEqual(json.loads(path.read_bytes())['state'],'NEEDS_SPLIT')
        result = self.run_recovery(plan,client)
        self.assertEqual(result['status'],'COMPLETE'); self.assertEqual(len(client.calls),2)
        self.assert_exclusive()

    def test_bad_selector_page_cannot_be_a_complete_checkpoint(self):
        from axiom_data.sw_source import load_profile
        definition = load_profile('tushare_industry_qualification.v1')['endpoints']['index_member_all']
        row = {field:None for field in definition['fields']}
        row.update(ts_code='000001.SZ',is_new='Y')
        class BadClient:
            def query(self,*args,**kwargs): return [row]
        spec = dict(collector='industry_qualification',domain='industry_membership',endpoint='index_member_all',
            params={'is_new':'N','limit':'100','offset':'0'},economic_scope={'start':'20140101','end':'20260908'},
            availability_policy='reference_observation')
        state = collect_requests(self.root,run_id='bad-page',requests=[spec],client=BadClient())
        self.assertEqual(state['status'],'FAILED')
        self.assertEqual(state['completed'],{})
        self.assertEqual(state['request_states'][_request(spec)]['state'],'NEEDS_RETRY')
        self.assert_exclusive()

    def test_legacy_frozen_observed_binding_is_normalized_before_rejection(self):
        plan,key,parent,batch = seed_legacy(self.root,checkpoint=False)
        path = self.root/'operations'/batch/'collection.json'
        state = json.loads(path.read_bytes())
        bound = {key:parent.raw_batch_id}
        state['plan_digest'] = _digest(_json_bytes({'requests':state['requests'],
            'request_keys':[key],'observed_raw_batch_ids':bound}))
        state['observed_raw_batch_ids'] = bound
        path.write_bytes(_json_bytes(state))
        client = ChildClient()
        result = collect_requests(self.root,run_id=batch,requests=state['requests'],client=client,
                                  observed_raw_batch_ids=bound)
        self.assertEqual(result['request_states'][key]['state'],'NEEDS_SPLIT')
        self.assertEqual(result['completed'],{})
        self.assertEqual(json.loads(path.read_bytes()),result)
        self.assertEqual(client.calls,[]); self.assert_exclusive()

    def test_invalid_frozen_observed_raw_cannot_be_replaced_by_collection(self):
        plan,key,parent,batch = seed_legacy(self.root,checkpoint=False)
        partial = SourceCompletenessTest.raw(self,1,identity='bound-partial',summary={'complete':False})
        path = self.root/'operations'/batch/'collection.json'
        state = json.loads(path.read_bytes()); bound = {key:partial.raw_batch_id}
        state['completed'] = bound
        state['plan_digest'] = _digest(_json_bytes({'requests':state['requests'],
            'request_keys':[key],'observed_raw_batch_ids':bound}))
        state['observed_raw_batch_ids'] = bound
        path.write_bytes(_json_bytes(state)); client = ChildClient()
        for _ in range(2):
            result = collect_requests(self.root,run_id=batch,requests=state['requests'],client=client,
                                      observed_raw_batch_ids=bound)
            self.assertEqual(result['status'],'FAILED')
            self.assertEqual(result['request_states'][key]['state'],'NEEDS_RETRY')
            self.assertEqual(result['request_states'][key]['raw_batch_id'],partial.raw_batch_id)
            self.assertEqual(result['completed'],{})
        self.assertEqual(client.calls,[]); self.assert_exclusive()

    def test_capped_batch_does_not_fork_unrecorded_pending_runs(self):
        from axiom_data import operations
        plan,key,parent,batch = seed_legacy(self.root,checkpoint=False)
        import shutil
        shutil.rmtree(self.root/'operations')
        template = plan['requests_by_domain']['financial_events'][0]
        requests = [dict(template,params=dict(template['params'],ts_code=f'{i:06d}.SZ')) for i in range(1,5)]
        plan['requests_by_domain']['financial_events'] = requests
        class Client:
            def __init__(self): self.calls=[]
            def query(self,endpoint,*,fields,**params):
                self.calls.append((params['ts_code'],params['start_date'],params['end_date']))
                full = (params['start_date'],params['end_date'])==('20250101','20250630')
                count = 100 if full and params['ts_code']!='000004.SZ' else 1
                period = '20250331' if params['start_date']=='20250101' else '20250630'
                return [dict(ts_code=params['ts_code'],ann_date='20250701',end_date=period,current_ratio=2)]*count
        client=Client(); save=operations._save; fourth=_request(requests[-1])
        def crash(path,value):
            save(path,value)
            if value.get('request_id')==fourth and value.get('state')=='VALID_COMPLETE':
                raise KeyboardInterrupt('crash after fourth request checkpoint')
        with patch.object(operations,'_save',side_effect=crash):
            with self.assertRaises(KeyboardInterrupt): self.run_recovery(plan,client)
        final=self.run_recovery(plan,client)
        self.assertEqual(final['status'],'COMPLETE')
        self.assertEqual(len(client.calls),10)  # Four parent requests and six split children.
        self.assertEqual(client.calls.count(('000004.SZ','20250101','20250630')),1)
        self.assertEqual(self.run_recovery(plan,client),final)
        self.assertEqual(len(client.calls),10)
        self.assertFalse(any('-pending-' in p.name for p in (self.root/'operations').iterdir()))
        self.assert_exclusive()


def recovery_probe(root):
    """Reproducible evidence from real checkpoint I/O and fixture child execution."""
    root = Path(root)
    if root.exists() and any(root.iterdir()):
        raise ArtifactError('probe requires its own empty root')
    root.mkdir(parents=True,exist_ok=True)
    plan,key,parent,batch = seed_legacy(root,overlapping_failure=True)
    client = ChildClient(fail_second=True)
    first = collect_bootstrap_sources(root,run_id='legacy-recovery',plan=plan,domains=['financial_events'],client=client)
    client.fail_second=False
    final = collect_bootstrap_sources(root,run_id='legacy-recovery',plan=plan,domains=['financial_events'],client=client)
    calls = list(client.calls)
    again = collect_bootstrap_sources(root,run_id='legacy-recovery',plan=plan,domains=['financial_events'],client=client)
    record = json.loads((root/'operations'/batch/'collection.json').read_bytes())['request_states'][key]
    assert first['status']=='FAILED' and final['status']=='COMPLETE' and final==again
    assert len(calls)==3 and client.calls==calls and len(record['child_refs']['request_ids'])==2
    assert record['state']=='SUPERSEDED_BY_SPLIT'
    assert parent.raw_batch_id not in final['domains']['financial_events']['completed_raw_batch_ids']
    return {'probe':'legacy_completed_100_split_recovery.v1','root':str(root),'supplier':'deterministic_fixture',
            'parent_raw_batch_id':parent.raw_batch_id,'request_id':key,'parent_state':record['state'],
            'child_request_count':len(record['child_refs']['request_ids']),'child_refs':record['child_refs'],
            'interrupted_status':first['status'],'recovered_status':final['status'],'calls':client.calls,
            'idempotent_resume':final==again and client.calls==calls,
            'admitted_raw_batch_ids':final['domains']['financial_events']['completed_raw_batch_ids']}
