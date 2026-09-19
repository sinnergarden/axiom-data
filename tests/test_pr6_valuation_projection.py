import json
import shutil
import tempfile
import unittest
from pathlib import Path
from fixture_locations import fixture_root
from unittest.mock import patch

from axiom_data import ArtifactError, BuildApplication, SnapshotReader, create_snapshot
from axiom_data.pr6_source import Pr6Builder, Pr6Collector
from axiom_data.pr6_views import _valuation_at, project
from test_pr6_artifacts import Client


class ValuationProjectionTest(unittest.TestCase):
    def test_scoped_selection_keeps_future_and_aba_revisions(self):
        run=json.loads(Path('reports/pr7/run_manifest.json').read_bytes())
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'data';shutil.copytree(fixture_root(run['source_root']),root)
            try:
                old=SnapshotReader(root,run['refs']['snapshot_id']);ids={d:c.ref.commit_id for d,c in old.commits.items()}
                source=[dict(ts_code='688981.SH',trade_date=d,pe=3,pb=2,ps=1) for d in ['20250610','20250701','20250801']]
                params=dict(ts_code='688981.SH',start_date='20250601',end_date='20250908');raw=[]
                for day,records in [(9,source),(10,[dict(source[0],pe=4)]),(11,[source[0]])]:
                    raw.append(Pr6Collector(root,Client(records)).collect('daily_basic',params,retrieved_at=f'2025-09-{day:02d}T00:00:00Z').raw_batch_id)
                builder=Pr6Builder(root,'valuation_daily',builder_config={'storage_policy':'domain_time_blocks.v1'},dependency_commit_ids={d:ids[d] for d in ['trading_calendar','security_master']})
                ids['valuation_daily']=BuildApplication('valuation_daily',builder).build(None,raw,[],'valuation_daily.v2').commit_id
                sid=create_snapshot(root,ids).snapshot_id
                with patch('axiom_data.partition_rows.STREAM_ROW_THRESHOLD',1):
                    reader=SnapshotReader(root,sid);parts=reader.commits['valuation_daily'].rows
                    for policy in ['operational_pit_v1','best_effort_vendor_v1']:
                        for day,value in [(8,None),(9,3),(10,4),(11,3)]:
                            cutoff=f'2025-09-{day:02d}T12:00:00Z'
                            expected=tuple(r for r in reader.as_of('valuation_daily',symbols=['688981.SH'],pit_policy=policy,knowledge_cutoff=cutoff) if r['session']=='2025-06-10')
                            with patch.object(parts,'_rows',wraps=parts._rows) as opened:
                                result=_valuation_at(reader,['688981.SH'],'2025-06-10',policy,cutoff)
                                self.assertEqual(result,expected)
                                self.assertEqual([c.args[0]['key'] for c in opened.call_args_list],['2025-06'])
                            if policy=='operational_pit_v1':self.assertEqual(result[0]['values']['pe'] if result else None,value)
                    part=next(p for p in parts.entries if p['key']=='2025-06');path=root/'canonical/valuation_daily/objects'/part['object_id']/'rows.json'
                    path.chmod(0o600);path.write_bytes(path.read_bytes()+b' ')
                    with self.assertRaises(ArtifactError):_valuation_at(reader,['688981.SH'],'2025-06-10','best_effort_vendor_v1',cutoff)
            finally:
                for p in root.rglob('*'):
                    if p.is_dir():p.chmod(0o755)

    def test_projection_still_requires_complete_scope_admission(self):
        run=json.loads(Path('reports/pr7/run_manifest.json').read_bytes());root=fixture_root(run['source_root'])
        manifest=json.loads((root/'derived/pr6_fact/commits'/run['refs']['pr6_view_id']/'manifest.json').read_bytes())
        reader=SnapshotReader(root,run['refs']['snapshot_id'])
        # A month with no valuation rows cannot be bypassed by the scoped selector.
        scope=dict(manifest['scope'],start_session='2025-07-01',end_session='2025-07-01')
        with self.assertRaises(ArtifactError):project(reader,scope,manifest['pit_policy'],manifest['knowledge_cutoff'])
