"""The real frozen SW history remains readable through Snapshot/Fact/Qlib APIs."""
import json
import math
from pathlib import Path
from fixture_locations import fixture_root
import shutil
import tempfile
import unittest
from axiom_data import BuildApplication, SnapshotReader, create_snapshot, FactView
from axiom_data.fundamentals_source import FundamentalsBuilder
from axiom_data.financial_views import build_financial_fact_view, load_financial_fact_view
from axiom_data.consumption import QlibViewReader


class SwViewTest(unittest.TestCase):
    def test_real_snapshot_fact_qlib_and_nochange(self):
        run=json.loads(Path('deprecated/history/reports/pr7/run_manifest.json').read_bytes())
        source=fixture_root('/home/liuming/workspace/axiom/data')
        plan=json.loads((source/'operations/sw2021-canonical-20260910-r1/build_plan.json').read_bytes())
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'data';shutil.copytree(fixture_root(run['source_root']),root)
            for identity in plan['raw_batch_ids']:
                shutil.copytree(source/'raw/batches'/identity,root/'raw/batches'/identity)
            old=SnapshotReader(root,run['refs']['snapshot_id'])
            commits={d:c.ref.commit_id for d,c in old.commits.items()}
            cfg=dict(plan['config'],symbols=[r['symbol'] for r in old.security_master()])
            cfg.update(storage_policy='domain_time_blocks.v1',no_change_policy='reuse_equal_state.v1')
            app=BuildApplication('industry_membership',FundamentalsBuilder(root,'industry_membership',builder_config=cfg,
                dependency_commit_ids={'security_master':commits['security_master']}))
            built=app.build(None,plan['raw_batch_ids'],[],'industry_membership.v3')
            self.assertEqual(app.build(built.commit_id,plan['raw_batch_ids'],[],'industry_membership.v3').commit_id,built.commit_id)
            commits['industry_membership']=built.commit_id
            snapshot=create_snapshot(root,commits);reader=SnapshotReader(root,snapshot.snapshot_id)
            original=load_financial_fact_view(root,run['refs']['pr6_view_id']).manifest
            scope=dict(original['scope']);scope.pop('interval',None);scope['industry_system']='SW2021'
            args=dict(pit_policy=original['pit_policy'],knowledge_cutoff=original['knowledge_cutoff'])
            view=build_financial_fact_view(root,snapshot.snapshot_id,**scope,**args)
            facts=FactView(root,snapshot.snapshot_id,financial_fact_view_id=view.view_id).read('financial')
            qlib=QlibViewReader(root,view.view_id)
            binary=qlib.market_daily(include_missing=True)
            self.assertEqual(len(facts['rows']),len(binary))
            for direct,exported in zip(facts['rows'],binary):
                self.assertEqual(set(direct),set(exported))
                for key,value in direct.items():
                    if value is None or isinstance(value,str):self.assertEqual(value,exported[key])
                    else:self.assertTrue(math.isclose(value,exported[key],rel_tol=1e-6,abs_tol=1e-6),(key,value,exported[key]))
            self.assertEqual(facts['facts'],qlib.fact_metadata()['rows'])
            for item in facts['facts']:
                field=item['fields']['industry.membership']
                self.assertEqual(field['classification_system'],'SW2021')
                self.assertTrue(field['mapping_profile_digest'])
                self.assertEqual(field['availability_state'],'classified')
            self.assertEqual(old.commits['industry_membership'].ref.commit_id,run['refs']['domain_commit_ids']['industry_membership'])
            self.assertTrue(reader.members('SW2021',scope['start_session'],domain='industry_membership',symbols=scope['symbols'],**args))
