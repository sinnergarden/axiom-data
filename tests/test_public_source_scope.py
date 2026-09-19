import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from fixture_locations import fixture_root
from types import SimpleNamespace

from axiom_data import (ArtifactError, BuildApplication, SnapshotReader, bootstrap, daily,
                        load_raw_batch, plan_daily, repair, validate_domain_commit_closure)
from axiom_data.artifacts import _DOMAIN_DEPENDENCIES
from axiom_data.dm1_source import TushareDm1Builder
from axiom_data.operations import _validate_domain_inputs
from axiom_data.public_source_scope import validate_security_scope


class PublicSourceScopeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'data'
        run = json.loads(Path('reports/pr7/run_manifest.json').read_bytes())
        shutil.copytree(fixture_root(run['source_root']), self.root); self.addCleanup(self.writable)
        self.old = SnapshotReader(self.root, run['refs']['snapshot_id'])

    def writable(self):
        for p in self.root.rglob('*'):
            if p.is_dir(): p.chmod(0o755)

    def inputs(self):
        # Explicit semantic scope from the published fixture; implementation and
        # source profile fields are installed by the same public builder classes.
        keys = {'symbols', 'start_session', 'end_session', 'membership_end_exclusive'}
        result = {}
        from axiom_data.contracts import writable_contracts
        versions = writable_contracts()['domains']
        for domain, commit in self.old.commits.items():
            result[domain] = dict(raw_batch_ids=[r['raw_batch_id'] for r in commit.manifest['ordered_raw_batch_refs']],
                contract_version=versions[domain]['current'], new_lineage=True,
                config={k:v for k,v in commit.manifest['builder_config'].items() if k in keys})
        result['corporate_actions']['config']['corporate_action_observations'] = 'corporate_action_observations.v1'
        result['security_capital']['config']['capital_qualification'] = 'capital_conflict.v1'
        result['forecast_observations']['config']['forecast_source_types'] = 'forecast_source_types.v1'
        # Current industry writes use the same frozen SW source plan as the
        # dedicated real-source integration, with this fixture's eight securities.
        source = fixture_root('/home/liuming/workspace/axiom/data')
        sw_plan = json.loads((source/'operations/sw2021-canonical-20260910-r1/build_plan.json').read_bytes())
        for raw_id in sw_plan['raw_batch_ids']:
            target = self.root/'raw/batches'/raw_id
            if not target.exists():
                shutil.copytree(source/'raw/batches'/raw_id, target)
        result['industry_membership'].update(raw_batch_ids=sw_plan['raw_batch_ids'],
            config=dict(symbols=[row['symbol'] for row in self.old.security_master()],
                        start_session=sw_plan['config']['start_session'], end_session=sw_plan['config']['end_session'],
                        industry_source_profile='tushare_sw2021.v1'))
        for domain in ('adjustment_factors', 'security_capital'):
            result[domain]['config']['security_session_scope'] = 'exchange_security.v1'
            result[domain]['config']['dm1_source_partitioning'] = 'security.v1'
        # Public requests intentionally accept one security. Reuse the captured
        # factor values in explicit per-security observations for all four paths.
        from axiom_data import TushareDm1Collector
        from test_pr6_artifacts import Client
        factor = result['adjustment_factors']
        raw = load_raw_batch(self.root, factor['raw_batch_ids'][0])
        factor['raw_batch_ids'] = []
        for symbol in factor['config']['symbols']:
            rows = [r for r in json.loads(raw.payload) if r['ts_code'] == symbol]
            params = dict(raw.manifest['request']['params'], ts_code=symbol)
            ref = TushareDm1Collector(self.root, Client(rows)).collect('adjustment_factors', 'adj_factor',
                params, retrieved_at=raw.manifest['retrieved_at'])
            factor['raw_batch_ids'].append(ref.raw_batch_id)
        factor['config']['dm1_source_partitioning'] = 'security.v1'
        return result

    def test_direct_bootstrap_daily_repair_produce_identical_scoped_artifacts(self):
        inputs = self.inputs()
        result = bootstrap(self.root, run_id='scope-bootstrap', domain_inputs=inputs)
        self.assertEqual(result['status'], 'CANDIDATE_BUILT', result.get('failed'))
        baseline = SnapshotReader(self.root, result['snapshot_id'])
        for domain in ('adjustment_factors', 'security_capital'):
            spec = inputs[domain]
            deps = {d:baseline.commits[d].ref.commit_id for d in _DOMAIN_DEPENDENCIES[domain]}
            config = dict(spec['config'], storage_policy='domain_time_blocks.v1',
                          no_change_policy='reuse_equal_state.v1', coverage_state_policy='source_observations.v2')
            direct = BuildApplication(domain, TushareDm1Builder(self.root, domain,
                builder_config=config, dependency_commit_ids=deps)).build(None, spec['raw_batch_ids'], [], spec['contract_version'])
            expected = baseline.commits[domain]
            self.assertEqual(direct.commit_id, expected.ref.commit_id)
            fixed = repair(self.root, run_id='scope-repair-'+domain, snapshot_id=result['snapshot_id'], domain_inputs={domain:spec})
            self.assertEqual(fixed['status'], 'CANDIDATE_BUILT', fixed.get('failed'))
            self.assertEqual(fixed['domain_commit_ids'][domain], direct.commit_id)
            sources = []
            for raw_id in spec['raw_batch_ids']:
                request = load_raw_batch(self.root, raw_id).manifest['request']
                params = request['params']
                sources.append(dict(collector='dm1',domain=domain,endpoint=request['endpoint'],params=params,
                    economic_scope={'start':params['start_date'],'end':params['end_date']},availability_policy='session_close'))
            plan = plan_daily(self.root, result['snapshot_id'], source_requests=sources)
            observed = {s['request_id']:r for s,r in zip(plan['source_requests'], spec['raw_batch_ids'])}
            update = daily(self.root, run_id='scope-daily-'+domain, snapshot_id=result['snapshot_id'],
                source_requests=sources, observed_raw_batch_ids=observed,
                domain_inputs={domain:dict(spec, raw_batch_ids=[])})
            self.assertIn(update['status'], ('CANDIDATE_BUILT','NO_CHANGE'), update.get('failed'))
            self.assertEqual(update['domain_commit_ids'][domain], direct.commit_id)
            self.assertEqual(validate_domain_commit_closure(self.root,domain,direct.commit_id).rows, expected.rows)
            # Exercise the real incremental path as well as genesis equivalence.
            symbol = spec['config']['symbols'][0]
            rows = [r for raw_id in spec['raw_batch_ids'] for r in json.loads(load_raw_batch(self.root,raw_id).payload)
                    if r['ts_code']==symbol and r['trade_date']=='20250613']
            self.assertTrue(rows)
            # D-M1 v1 binds Raw date bounds and rejects conflicting
            # reobservations. Replay exact Raw for one security, preserving the
            # complete parent state for all the other securities.
            patch = load_raw_batch(self.root,spec['raw_batch_ids'][0]).ref
            params = sources[0]['params']
            small = dict(spec['config'],symbols=[symbol])
            patch_spec = dict(spec,raw_batch_ids=[patch.raw_batch_id],config=small,new_lineage=False)
            effective = dict(small,storage_policy='domain_time_blocks.v1',no_change_policy='reuse_equal_state.v1',
                             coverage_state_policy='source_observations.v2')
            child = BuildApplication(domain,TushareDm1Builder(self.root,domain,builder_config=effective,
                dependency_commit_ids=deps)).build(direct.commit_id,[patch.raw_batch_id],[],spec['contract_version'])
            fixed = repair(self.root,run_id='scope-patch-repair-'+domain,snapshot_id=result['snapshot_id'],
                           domain_inputs={domain:patch_spec})
            self.assertEqual(fixed['status'],'CANDIDATE_BUILT',fixed.get('failed'))
            self.assertEqual(fixed['domain_commit_ids'][domain],child.commit_id)
            source = sources[0]
            planned = plan_daily(self.root,result['snapshot_id'],source_requests=[source])
            updated = daily(self.root,run_id='scope-patch-daily-'+domain,snapshot_id=result['snapshot_id'],
                source_requests=[source],domain_inputs={domain:dict(patch_spec,raw_batch_ids=[])},
                observed_raw_batch_ids={planned['source_requests'][0]['request_id']:patch.raw_batch_id})
            self.assertEqual(updated['status'],'CANDIDATE_BUILT',updated.get('failed'))
            self.assertEqual(updated['domain_commit_ids'][domain],child.commit_id)
            child_rows = validate_domain_commit_closure(self.root,domain,child.commit_id).rows
            self.assertEqual({(r['symbol'],r['session']) for r in child_rows},
                             {(r['symbol'],r['session']) for r in expected.rows})

    def test_public_schema_rejects_unrelated_domains_unknown_keys_and_bad_bounds(self):
        good = self.inputs()['adjustment_factors']
        _validate_domain_inputs({'adjustment_factors':good})
        cases = [dict(good, config=dict(good['config'], unknown=True)),
                 dict(good, config=dict(good['config'], symbols=['bad'])),
                 dict(good, config=dict(good['config'], start_session='2025-02-30')),
                 dict(good, config=dict(good['config'], start_session='2099-01-01')),
                 dict(good, config=dict(good['config'], security_session_scope='unknown.v1'))]
        for spec in cases:
            with self.assertRaises((ArtifactError, ValueError)):
                _validate_domain_inputs({'adjustment_factors':spec})
        unrelated = dict(good, contract_version='security_status.v1')
        with self.assertRaises(ArtifactError): _validate_domain_inputs({'security_status':unrelated})
        with self.assertRaises(ArtifactError): _validate_domain_inputs({'security_capital':good})

    def test_scope_guard_rejects_raw_holes_missing_exchange_and_mapping_mismatch(self):
        config = dict(symbols=['000001.SZ'],start_session='2025-06-10',end_session='2025-06-11',
                      security_session_scope='exchange_security.v1')
        master = [dict(symbol='000001.SZ',exchange='SZSE',list_session='2000-01-01',delist_session=None)]
        calendar = [dict(exchange='SZSE',session=d,is_open=True) for d in ('2025-06-10','2025-06-11')]
        def check(params, securities=master, days=calendar):
            raw = SimpleNamespace(manifest={'request':{'params':params}})
            deps = {'security_master':SimpleNamespace(rows=securities), 'trading_calendar':SimpleNamespace(rows=days)}
            validate_security_scope('adjustment_factors',config,[raw],deps)
        full = dict(ts_code='000001.SZ',start_date='20250610',end_date='20250611')
        check(full)
        with self.assertRaisesRegex(ArtifactError,'scope|coverage'):
            check(dict(full,end_date='20250610'))
        with self.assertRaisesRegex(ArtifactError,'calendar coverage'):
            check(full,days=[dict(r,exchange='SSE') for r in calendar])
        with self.assertRaises((ArtifactError,ValueError)):
            check(full,securities=[dict(master[0],exchange='SSE')])
        with self.assertRaisesRegex(ArtifactError,'unknown security'):
            check(full,securities=[])

    def test_loader_rejects_rehashed_manifest_claiming_unrequested_scope(self):
        from axiom_data import TushareDm1Collector
        from axiom_data.artifacts import _digest, _identity_digest, _derived_identity, _json_bytes
        from test_pr6_artifacts import Client
        original = self.old.commits['adjustment_factors']
        raw = load_raw_batch(self.root, original.manifest['ordered_raw_batch_refs'][0]['raw_batch_id'])
        rows = [r for r in json.loads(raw.payload) if r['ts_code']=='688981.SH' and r['trade_date']=='20250610']
        self.assertTrue(rows)
        ref = TushareDm1Collector(self.root,Client(rows)).collect('adjustment_factors','adj_factor',
            dict(ts_code='688981.SH',start_date='20250610',end_date='20250610'), retrieved_at=raw.manifest['retrieved_at'])
        config = dict(symbols=['688981.SH'],start_session='2025-06-10',end_session='2025-06-10',
                      security_session_scope='exchange_security.v1')
        deps = {d:self.old.commits[d].ref.commit_id for d in _DOMAIN_DEPENDENCIES['adjustment_factors']}
        built = BuildApplication('adjustment_factors',TushareDm1Builder(self.root,'adjustment_factors',
            builder_config=config,dependency_commit_ids=deps)).build(None,[ref.raw_batch_id],[],'adjustment_factors.v1')
        commit = validate_domain_commit_closure(self.root,'adjustment_factors',built.commit_id)
        manifest = copy.deepcopy(commit.manifest)
        manifest['builder_config']['end_session']='2025-06-11'
        manifest['builder_config_digest']=_digest(_json_bytes(manifest['builder_config']))
        manifest['identity_digest']=_identity_digest(manifest,'domain_commit_id')
        forged_id=_derived_identity('adjustment_factors',manifest['identity_digest'])
        manifest['domain_commit_id']=forged_id
        base=self.root/'canonical/adjustment_factors/commits'
        shutil.copytree(base/built.commit_id,base/forged_id)
        path=base/forged_id/'manifest.json';path.chmod(0o644);path.write_bytes(_json_bytes(manifest))
        sidecar=base/forged_id/'manifest.sha256';sidecar.chmod(0o644);sidecar.write_text(_digest(_json_bytes(manifest)))
        with self.assertRaisesRegex(ArtifactError,'scope|coverage'):
            validate_domain_commit_closure(self.root,'adjustment_factors',forged_id)

    def test_daily_rejects_widening_before_collection(self):
        spec = self.inputs()['adjustment_factors']
        request = load_raw_batch(self.root,spec['raw_batch_ids'][0]).manifest['request']
        source = dict(collector='dm1',domain='adjustment_factors',endpoint='adj_factor',
            params=dict(request['params'],start_date='20250613',end_date='20250613'),
            economic_scope={'start':'20250613','end':'20250613'},availability_policy='session_close')
        class NoSource:
            def query(self,*a,**kw): raise AssertionError('must reject before collection')
        with self.assertRaisesRegex(ArtifactError,'exceeds requested'):
            daily(self.root,run_id='scope-widen',snapshot_id=self.old.snapshot.ref.snapshot_id,
                  source_requests=[source],domain_inputs={'adjustment_factors':dict(spec,raw_batch_ids=[])},client=NoSource())

    def test_all_operations_reject_manually_published_100_row_indicator(self):
        from test_source_completeness import SourceCompletenessTest
        from axiom_data.source_completeness import SourceCompletenessError
        # This helper writes immutable Raw directly, bypassing every collector.
        ref = SourceCompletenessTest.raw(self, 100)
        raw = load_raw_batch(self.root, ref.raw_batch_id)
        spec = dict(raw_batch_ids=[ref.raw_batch_id], contract_version='financial_events.v3',
                    config={}, new_lineage=True)
        before = set((self.root/'canonical/financial_events/commits').iterdir())
        inputs = self.inputs(); inputs['financial_events'] = spec
        built = bootstrap(self.root, run_id='cap-bootstrap', domain_inputs=inputs)
        self.assertEqual(built['status'], 'FAILED')
        self.assertEqual(built['failed'], {'financial_events':{'error_type':'SourceCompletenessError'}})
        fixed = repair(self.root, run_id='cap-repair', snapshot_id=self.old.snapshot.ref.snapshot_id,
                       domain_inputs={'financial_events':spec})
        self.assertEqual(fixed['failed'], built['failed'])
        params = raw.manifest['request']['params']
        source = dict(collector='pr6_indicator',domain='financial_events',endpoint='fina_indicator',params=params,
            economic_scope={'start':params['start_date'],'end':params['end_date']},availability_policy='revision_scan')
        plan = plan_daily(self.root,self.old.snapshot.ref.snapshot_id,source_requests=[source])
        with self.assertRaises(SourceCompletenessError):
            daily(self.root,run_id='cap-daily',snapshot_id=self.old.snapshot.ref.snapshot_id,
                source_requests=[source],domain_inputs={'financial_events':dict(spec,raw_batch_ids=[])},
                observed_raw_batch_ids={plan['source_requests'][0]['request_id']:ref.raw_batch_id})
        self.assertEqual(set((self.root/'canonical/financial_events/commits').iterdir()), before)
        self.assertEqual(load_raw_batch(self.root,ref.raw_batch_id).payload, raw.payload)
