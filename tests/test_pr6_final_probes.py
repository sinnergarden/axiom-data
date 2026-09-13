"""Reviewer probes: empty state, historical prefix, future visibility, real old View."""
import json
import unittest
from pathlib import Path
from axiom_data import BuildApplication, validate_domain_commit_closure
from axiom_data.pr6_source import Pr6Builder, Pr6Collector
from axiom_data.pit import financial_derived, members
from axiom_data.pr6_views import load_pr6_fact_view
import test_pr6_artifacts as fixtures
from test_pr6_artifacts import Client
from test_pr6_pit import fact, POLICY, CUTOFF


class FinalProbes(unittest.TestCase):
    def setUp(self):
        fixtures.Pr6ArtifactTest.setUp(self)
        from axiom_data import MarketDomainBuilder,write_raw_batch
        from axiom_data.artifacts import _digest,_json_bytes
        from test_artifacts import security_row, write_rows
        raw=write_rows(self.root,'second-security','security_master',[security_row('000002.SZ')],
                       retrieved_at='2025-01-01T00:00:00Z')
        self.security=BuildApplication('security_master',MarketDomainBuilder(self.root,'security_master')).build(
            self.security.commit_id,[raw.raw_batch_id],[],'security_master.v1')

    def universe_raw(self,group,values,observed):
        rows=[{'index_code':group,'con_code':symbol,'trade_date':'20250101','weight':1} for symbol in values]
        return Pr6Collector(self.root,Client(rows)).collect('index_weight',
            {'index_code':group,'start_date':'20250101','end_date':'20250101'},retrieved_at=observed,membership_complete=True)

    def universe_build(self,raws,parent=None):
        app=BuildApplication('universe_membership',Pr6Builder(self.root,'universe_membership',
            dependency_commit_ids={'security_master':self.security.commit_id},
            builder_config={'membership_end_exclusive':'2026-01-01'}))
        ref=app.build(parent,[r.raw_batch_id for r in raws],[],'universe_membership.v3')
        return validate_domain_commit_closure(self.root,'universe_membership',ref.commit_id)

    def test_universe_empty_state(self):
        old=self.universe_build([self.universe_raw('000906.SH',['000001.SZ','000002.SZ'],'2025-01-02T00:00:00Z')])
        new=self.universe_build([self.universe_raw('000906.SH',[],'2025-02-02T00:00:00Z')],old.ref.commit_id)
        from axiom_data.pit import select_revisions
        from axiom_data.domains.market import MarketContractError
        with self.assertRaisesRegex(MarketContractError,'group_states required'):
            select_revisions(new.rows,policy=POLICY,knowledge_cutoff=CUTOFF)
        for policy in (POLICY,'best_effort_vendor_v1'):
            self.assertEqual(members(new.rows,group_id='000906.SH',target_session='2025-03-01',
                policy=policy,knowledge_cutoff=CUTOFF,group_states=new.manifest["group_states"]),())

    def test_empty_group_isolation_reentry_rebuild_and_source_gap(self):
        from axiom_data.pit import select_group_states, fingerprint
        from axiom_data.artifacts import ArtifactError
        a=self.universe_raw('000906.SH',['000001.SZ'],'2025-01-02T00:00:00Z')
        b=self.universe_raw('000852.SH',['000001.SZ'],'2025-01-02T00:00:00Z')
        empty=self.universe_raw('000906.SH',[],'2025-02-02T00:00:00Z')
        old=self.universe_build([a,b]);new=self.universe_build([empty],old.ref.commit_id)
        for policy in (POLICY,'best_effort_vendor_v1'):
            args=dict(policy=policy,knowledge_cutoff=CUTOFF,group_states=new.manifest['group_states'],target_session='2025-03-01')
            self.assertEqual(members(new.rows,group_id='000906.SH',**args),())
            self.assertEqual(len(members(new.rows,group_id='000852.SH',**args)),1)
            state=next(s for s in select_group_states(new.manifest['group_states'],policy=policy,knowledge_cutoff=CUTOFF) if s['universe_id']=='000906.SH')
            self.assertEqual(state['member_count'],0);self.assertEqual(state['member_set_digest'],fingerprint([]))
        returned=self.universe_raw('000906.SH',['000001.SZ'],'2025-04-02T00:00:00Z')
        final=self.universe_build([returned],new.ref.commit_id)
        self.assertEqual(len(members(final.rows,group_id='000906.SH',target_session='2025-05-01',policy=POLICY,knowledge_cutoff=CUTOFF,group_states=final.manifest['group_states'])),1)
        replay=self.universe_build([returned,empty,b,a]);self.assertEqual(final.rows,replay.rows)
        self.assertEqual(final.manifest['group_states'],replay.manifest['group_states'])
        # Genesis empty can be the parent of a later non-empty observation.
        genesis=self.universe_build([empty]);self.assertEqual(genesis.rows,())
        child=self.universe_build([returned],genesis.ref.commit_id)
        self.assertEqual(len(child.rows),1)
        import tempfile,shutil
        with tempfile.TemporaryDirectory() as directory:
            shutil.copytree(self.root/'raw',Path(directory)/'raw')
            from axiom_data import MarketDomainBuilder
            security=BuildApplication('security_master',MarketDomainBuilder(directory,'security_master')).build(None,['security-fixture','second-security'],[],'security_master.v1')
            app=BuildApplication('universe_membership',Pr6Builder(directory,'universe_membership',dependency_commit_ids={'security_master':security.commit_id},builder_config={'membership_end_exclusive':'2026-01-01'}))
            fresh=app.build(None,[a.raw_batch_id,b.raw_batch_id,empty.raw_batch_id],[],'universe_membership.v3')
            rebuilt=validate_domain_commit_closure(directory,'universe_membership',fresh.commit_id)
            self.assertEqual(rebuilt.manifest['group_states'],new.manifest['group_states'])
        gap=Pr6Collector(self.root,Client([])).collect('index_weight',{'index_code':'000906.SH','start_date':'20250101','end_date':'20250101'},retrieved_at='2025-03-02T00:00:00Z')
        with self.assertRaisesRegex(ArtifactError,'SOURCE_GAP'):self.universe_build([gap],old.ref.commit_id)

    def sequence(self):
        rows=[fact(p,v) for p,v in [('2024-03-31',100),('2024-09-30',600),('2024-12-31',1000)]]
        future=fact('2024-06-30',300,'2025-07-01T00:00:00Z')
        return rows,future

    def test_prefix_stability(self):
        rows,future=self.sequence()
        before=financial_derived(rows,policy=POLICY,knowledge_cutoff=CUTOFF)
        after=financial_derived(rows+[future],policy=POLICY,knowledge_cutoff=CUTOFF)
        self.assertEqual(before,after)

    def test_future_visibility(self):
        rows,future=self.sequence()
        before=financial_derived(rows+[future],policy=POLICY,knowledge_cutoff=CUTOFF)
        after=financial_derived(rows+[future],policy=POLICY,knowledge_cutoff='2025-08-01T00:00:00Z')
        def ttm(values):return next(r for r in values if r['field']=='ttm_revenue' and r['report_period']=='2024-12-31')
        self.assertIsNone(ttm(before)['value']);self.assertEqual(ttm(before)['missing_reason'],'missing_quarter')
        self.assertEqual(ttm(after)['value'],1000)

    def test_real_old_view(self):
        report=json.loads((Path(__file__).resolve().parents[1]/'reports/pr6/run_manifest.json').read_text())
        ref=report['artifact_refs']['view']
        view=load_pr6_fact_view(report['data_root'],ref['view_id'])
        self.assertEqual(view.manifest['schema_version'],'pr6_fact_view.v1')
        self.assertEqual(len(view.rows),8)
        target=Path(report['data_root'])/'derived/pr6_fact/commits'/ref['view_id']
        original=json.loads((target/'rows.json').read_text())
        self.assertEqual(view.rows,tuple(original['wide']))
        self.assertEqual(view.manifest['identity_digest'],ref['identity_digest'])
        from axiom_data.views import FactView
        result=FactView(report['data_root'],report['artifact_refs']['snapshot']['snapshot_id'],pr6_fact_view_id=ref['view_id']).read('pr6')
        self.assertEqual(len(result['rows']),8)
        from axiom_data.consumption import QlibViewReader
        self.assertEqual(len(QlibViewReader(report['data_root'],ref['view_id']).market_daily(include_missing=True)),8)
        # Tamper a copied real artifact, never its published original.
        import shutil,tempfile
        from axiom_data.artifacts import ArtifactError
        with tempfile.TemporaryDirectory() as directory:
            for name in ('raw','canonical','snapshots','derived'):
                shutil.copytree(Path(report['data_root'])/name,Path(directory)/name)
            manifest_path=Path(directory)/'derived/pr6_fact/commits'/ref['view_id']/'manifest.json'
            valid=manifest_path.read_bytes();broken=json.loads(valid)
            broken['schema_version']='pr6_fact_view.v2';manifest_path.write_text(json.dumps(broken))
            with self.assertRaises(ArtifactError):load_pr6_fact_view(directory,ref['view_id'])
            manifest_path.write_bytes(valid)
            data_path=manifest_path.parent/'rows.json';data=json.loads(data_path.read_text())
            data['wide'][0]['values']['income.revenue']=123;data_path.write_text(json.dumps(data))
            with self.assertRaises(ArtifactError):load_pr6_fact_view(directory,ref['view_id'])
        v2=json.loads((Path(__file__).resolve().parents[1]/'reports/pr6-review/run_manifest.json').read_text())
        newer=load_pr6_fact_view(v2['data_root'],v2['artifact_refs']['view']['view_id'])
        self.assertEqual(newer.manifest['schema_version'],'pr6_fact_view.v2')
        self.assertNotEqual(newer.ref.view_id,view.ref.view_id)
