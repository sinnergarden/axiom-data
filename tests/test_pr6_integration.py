import json
import tempfile
import unittest
from pathlib import Path
from axiom_data import BuildApplication, create_snapshot, SnapshotReader, FactView
from axiom_data.domains import DM1_SNAPSHOT_DOMAINS
from axiom_data.artifacts import _DOMAIN_DEPENDENCIES
from axiom_data.pr6_source import Pr6Collector,Pr6Builder
from axiom_data.pr6_views import build_pr6_fact_view
from axiom_data.consumption import QlibViewReader
from test_pr5_dm1 import build_all,collect_all
from test_pr6_artifacts import Client
from test_artifacts import synthetic_source_fixture


def build_industry(root, security_commit, *, empty=False):
    """Small SW history with the existing real taxonomy and source validators."""
    from axiom_data.sw_source import IndustryQualificationCollector
    fixture=json.loads(Path('tests/fixtures/sw2021_golden.json').read_bytes())
    members=list(fixture['members'])
    examples=[next(row for row in members if row['l3_code']==code) for code in ('850412.SI','850531.SI')]
    if not empty:
        members += [dict(examples[0],ts_code='600000.SH',in_date='20260105',out_date='20260106',is_new='N'),
                    dict(examples[1],ts_code='600000.SH',in_date='20260107',out_date=None,is_new='Y')]
    class IndustryClient:
        def query(self, endpoint, *, fields, **params):
            return ([row for row in fixture['taxonomy'] if row['level']==params['level']]
                    if endpoint=='index_classify' else [row for row in members if row['is_new']==params['is_new']])
    collector=IndustryQualificationCollector(root,IndustryClient())
    requests=[('index_classify',{'src':'SW2021','level':level}) for level in ('L1','L2','L3')]
    requests += [('index_member_all',{'is_new':mode,'limit':'1000','offset':'0'}) for mode in ('Y','N')]
    raws=[collector.collect(endpoint,params,retrieved_at='2026-09-01T00:00:00Z').raw_batch_id for endpoint,params in requests]
    builder=Pr6Builder(root,'industry_membership',dependency_commit_ids={'security_master':security_commit},
        builder_config={'symbols':['600000.SH'],'start_session':'2026-01-05','end_session':'2026-01-07',
                        'industry_source_profile':'tushare_sw2021.v1'})
    return BuildApplication('industry_membership',builder).build(None,raws,[],'industry_membership.v3').commit_id


@synthetic_source_fixture
class Pr6IntegrationTest(unittest.TestCase):
    def test_snapshot_read_union_cohort_industry_and_qlib(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);baseline=build_all(root,collect_all(root))
            commits={d:baseline[d] for d in DM1_SNAPSHOT_DOMAINS}
            requests={
              'universe_membership': [('index_weight',{'index_code':'000906.SH','start_date':'20260101','end_date':'20260107'},[
                {'index_code':'000906.SH','con_code':symbol,'trade_date':day,'weight':1}
                for day,symbol in [('20260101','600002.SH'),('20260105','600000.SH'),('20260106','600002.SH'),('20260107','600000.SH')]])],
              'financial_events': [('income',{'ts_code':'600000.SH','period':'20250331'},[
                {'ts_code':'600000.SH','ann_date':'20250401','f_ann_date':'20250401','end_date':'20250331','report_type':'1','update_flag':'1','revenue':100,'oper_cost':60,'n_income':10}])],
              'valuation_daily': [('daily_basic',{'ts_code':'600000.SH','trade_date':'20260105'},[
                {'ts_code':'600000.SH','trade_date':'20260105','pe':10,'pb':1,'ps':2}])],
            }
            requests['financial_events'].append(('fina_indicator',{'ts_code':'600000.SH','period':'20250331'},[{'ts_code':'600000.SH','ann_date':'20250401','end_date':'20250331','update_flag':'1','current_ratio':None,'debt_to_assets':None,'grossprofit_margin':None,'roe':None}]))
            for endpoint,values in [('balancesheet',{'accounts_receiv':1,'total_cur_assets':2,'total_cur_liab':1,'total_hldr_eqy_exc_min_int':1,'inventories':1,'total_assets':3}),('cashflow',{'n_cashflow_act':1})]:
                requests['financial_events'].append((endpoint,{'ts_code':'600000.SH','period':'20250331'},[
                    dict(ts_code='600000.SH',ann_date='20250401',f_ann_date='20250401',end_date='20250331',report_type='1',update_flag='1',**values)]))
            for domain,jobs in requests.items():
                refs=[Pr6Collector(root,Client(rows)).collect(endpoint,params,retrieved_at='2026-09-01T00:00:00Z').raw_batch_id for endpoint,params,rows in jobs]
                config={'membership_end_exclusive':'2026-01-08'} if domain=='universe_membership' else {}
                builder=Pr6Builder(root,domain,builder_config=config,dependency_commit_ids={d:commits[d] for d in _DOMAIN_DEPENDENCIES[domain]})
                version=domain+('.v4' if domain=='financial_events' else '.v3' if domain=='universe_membership' else '.v2')
                commits[domain]=BuildApplication(domain,builder).build(None,refs,[],version).commit_id
            commits['industry_membership']=build_industry(root,commits['security_master'])
            snapshot=create_snapshot(root,commits);reader=SnapshotReader(root,snapshot.snapshot_id)
            args={'pit_policy':'best_effort_vendor_v1','knowledge_cutoff':'2026-09-02T00:00:00Z'}
            union=reader.historical_union('000906.SH','2026-01-05','2026-01-07','2026-01-02',**args)
            self.assertIn('600000.SH',union)
            history=reader.market_daily(['600000.SH'],'2026-01-02','2026-01-05')
            self.assertEqual(history[0]['session'],'2026-01-02')
            self.assertNotIn('600000.SH',[r['symbol'] for r in reader.members('000906.SH','2026-01-02',**args)])
            for day,expected in [('2026-01-05',True),('2026-01-06',False),('2026-01-07',True)]:
                self.assertEqual('600000.SH' in [r['symbol'] for r in reader.members('000906.SH',day,**args)],expected)
            for day,expected in [('2026-01-05','850412.SI'),('2026-01-07','850531.SI')]:
                self.assertEqual(reader.members('SW2021',day,domain='industry_membership',**args)[0]['industry_id'],expected)
            view=build_pr6_fact_view(root,snapshot.snapshot_id,symbols=['600000.SH'],start_session='2026-01-05',end_session='2026-01-05',universe_ids=['000906.SH'],industry_system='SW2021',**args)
            fact_view=FactView(root,snapshot.snapshot_id,pr6_fact_view_id=view.view_id)
            result=fact_view.read('pr6');direct=result['rows']
            missing=result['facts'][0]['fields']['financial.ttm_revenue']
            self.assertIsNone(missing['value']);self.assertEqual(missing['missing_reason'],'missing_quarter')
            self.assertTrue(missing['component_revision_refs']);self.assertTrue(missing['derived_ref'])
            self.assertEqual(missing['unit'],'CNY');self.assertEqual(missing['quality_state'],'BLOCKED')
            self.assertEqual(result['industry_mapping']['code_to_industry']['1'],'850412.SI')
            self.assertEqual(QlibViewReader(root,view.view_id).fact_metadata()['rows'],result['facts'])
            from axiom_data.artifacts import ArtifactError
            for kwargs in [{'start_session':'2020-01-01'},{'end_session':'2026-01-07'},{'symbols':['600002.SH']},{'fields':['unknown']}]:
                with self.assertRaises(ArtifactError):fact_view.read('pr6',**kwargs)
            build_args=dict(symbols=['600000.SH'],start_session='2026-01-05',end_session='2026-01-05',universe_ids=['000906.SH'],industry_system='SW2021',**args)
            for change,code in [({'start_session':'2020-01-01'},'INSUFFICIENT_SCOPE'),({'end_session':'2026-01-07'},'INSUFFICIENT_SCOPE'),({'universe_ids':['unknown']},'UNKNOWN_UNIVERSE'),({'industry_system':'unknown'},'unknown industry classification system'),({'symbols':['600002.SH']},'INSUFFICIENT_SCOPE')]:
                with self.assertRaisesRegex(ArtifactError,code):build_pr6_fact_view(root,snapshot.snapshot_id,**dict(build_args,**change))
            with self.assertRaisesRegex(ArtifactError,'UNKNOWN_UNIVERSE'):reader.members('unknown','2026-01-05',**args)
            with self.assertRaisesRegex(ArtifactError,'INSUFFICIENT_SCOPE'):reader.as_of('financial_events',symbols=['600002.SH'],**args)
            membership=reader.membership_facts('000906.SH','2026-01-05',**args)
            self.assertEqual(membership['pit_policy'],args['pit_policy']);self.assertEqual(membership['version'],commits['universe_membership'])
            # Removing mapping from an immutable View cannot remain readable.
            import json
            from axiom_data.pr6_views import load_pr6_fact_view
            path=root/'derived/pr6_fact/commits'/view.view_id/'manifest.json'
            original=path.read_bytes();manifest=json.loads(original);del manifest['industry_mapping']
            path.write_text(json.dumps(manifest))
            with self.assertRaises(ArtifactError):load_pr6_fact_view(root,view.view_id)
            path.write_bytes(original)
            binary=QlibViewReader(root,view.view_id).market_daily(include_missing=True)
            self.assertEqual(direct,binary)
            self.assertEqual(direct[-1]['financial.single_quarter_revenue'],100)
            # Available income/cost do not silently replace a missing supplier margin.
            self.assertIsNone(direct[-1]['indicator.gross_margin'])
            # A real public publication keeps both source observations, while
            # Fact/Qlib expose only the differing income leaf as unavailable.
            source=requests['financial_events'][0][2][0]
            raw=Pr6Collector(root,Client([source,dict(source,revenue=101)])).collect(
                'income',{'ts_code':'600000.SH','period':'20250331'},
                retrieved_at='2026-09-02T00:00:00Z')
            financial=BuildApplication('financial_events',Pr6Builder(root,'financial_events',
                dependency_commit_ids={'security_master':commits['security_master']})).build(
                    commits['financial_events'],[raw.raw_batch_id],[],'financial_events.v4')
            changed=create_snapshot(root,dict(commits,financial_events=financial.commit_id))
            changed_reader=SnapshotReader(root,changed.snapshot_id)
            selected=changed_reader.as_of('financial_events',symbols=['600000.SH'],**args)
            income=next(row for row in selected if row['endpoint']=='income')
            self.assertEqual(income['ambiguous_fields'],['revenue'])
            self.assertEqual(len(income['component_revisions']),2)
            canonical=[r for r in changed_reader.facts('financial_events') if r['endpoint']=='income']
            self.assertEqual(len(canonical),2)
            self.assertEqual(sum(len(r['observations']) for r in canonical),3)
            changed_view=build_pr6_fact_view(root,changed.snapshot_id,**build_args)
            loaded=load_pr6_fact_view(root,changed_view.view_id)
            self.assertEqual(loaded.manifest['schema_version'],'pr6_fact_view.v4')
            self.assertTrue(loaded.manifest['actual_available_scope']['financial_ambiguities'])
            public=FactView(root,changed.snapshot_id,pr6_fact_view_id=changed_view.view_id).read('pr6')
            metadata=public['facts'][0]['fields']
            for leaf in ('income.revenue','financial.single_quarter_revenue','financial.ttm_revenue'):
                self.assertIsNone(metadata[leaf]['value'])
                self.assertEqual(metadata[leaf]['validity'],'unavailable')
                self.assertEqual(metadata[leaf]['missing_reason'],'AMBIGUOUS_SOURCE_REVISION')
                self.assertTrue(metadata[leaf]['component_revision_refs'])
            self.assertEqual(metadata['income.oper_cost']['value'],60)
            self.assertEqual(metadata['income.oper_cost']['validity'],'valid')
            qlib=QlibViewReader(root,changed_view.view_id)
            self.assertEqual(qlib.fact_metadata()['rows'],public['facts'])
            self.assertEqual(qlib.market_daily(include_missing=True),public['rows'])

    def test_snapshots_keep_historical_financial_prefix(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);baseline=build_all(root,collect_all(root));commits={d:baseline[d] for d in DM1_SNAPSHOT_DOMAINS}
            # Explicit empty source observations permit a financial-only Snapshot.
            requests={'universe_membership':('index_weight',{'index_code':'000906.SH','start_date':'20260105','end_date':'20260105'}),
                'valuation_daily':('daily_basic',{'ts_code':'600000.SH','trade_date':'20260105'})}
            for domain,(endpoint,params) in requests.items():
                ref=Pr6Collector(root,Client([])).collect(endpoint,params,retrieved_at='2026-01-06T00:00:00Z',membership_complete=domain=='universe_membership')
                version=domain+('.v4' if domain=='financial_events' else '.v3' if domain=='universe_membership' else '.v2')
                builder=Pr6Builder(root,domain,builder_config={'membership_end_exclusive':'2026-01-06'} if domain=='universe_membership' else {},dependency_commit_ids={d:commits[d] for d in _DOMAIN_DEPENDENCIES[domain]})
                commits[domain]=BuildApplication(domain,builder).build(None,[ref.raw_batch_id],[],version).commit_id
            commits['industry_membership']=build_industry(root,commits['security_master'],empty=True)
            def raw(period,value,observed):
                return Pr6Collector(root,Client([{'ts_code':'600000.SH','ann_date':'20250401','f_ann_date':'20250401',
                    'end_date':period,'report_type':'1','update_flag':'1','revenue':value,'oper_cost':value/2,'n_income':value/10}])).collect(
                    'income',{'ts_code':'600000.SH','period':period},retrieved_at=observed).raw_batch_id
            app=BuildApplication('financial_events',Pr6Builder(root,'financial_events',dependency_commit_ids={'security_master':commits['security_master']}))
            first=app.build(None,[raw(p,v,'2025-05-01T00:00:00Z') for p,v in [('20240331',100),('20240930',600),('20241231',1000)]],[],'financial_events.v4')
            commits['financial_events']=first.commit_id;old=create_snapshot(root,commits)
            late=raw('20240630',300,'2025-07-01T00:00:00Z')
            commits['financial_events']=app.build(first.commit_id,[late],[],'financial_events.v4').commit_id
            new=create_snapshot(root,commits)
            envelope=SnapshotReader(root,new.snapshot_id).membership_facts('000906.SH','2026-01-05',knowledge_cutoff='2026-02-01T00:00:00Z',pit_policy='operational_pit_v1')
            self.assertEqual(envelope['rows'],());self.assertEqual(envelope['group_observation']['member_count'],0)
            args={'pit_policy':'operational_pit_v1','knowledge_cutoff':'2025-06-01T00:00:00Z','symbols':['600000.SH']}
            before=SnapshotReader(root,old.snapshot_id).financial_derived(**args)
            reader=SnapshotReader(root,new.snapshot_id);after=reader.financial_derived(**args)
            self.assertNotEqual(old.snapshot_id,new.snapshot_id);self.assertEqual(before,after)
            ttm=lambda values:next(v for v in values if v['field']=='ttm_revenue' and v['report_period']=='2024-12-31')
            self.assertEqual(ttm(before)['missing_reason'],'missing_quarter')
            self.assertEqual(ttm(reader.financial_derived(**dict(args,knowledge_cutoff='2025-08-01T00:00:00Z')))['value'],1000)
