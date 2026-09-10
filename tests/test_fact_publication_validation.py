import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from axiom_data import ArtifactError, SnapshotReader
from axiom_data import pr6_views, pr7_views


class FactPublicationValidationTest(unittest.TestCase):
    def test_each_fact_build_validates_snapshot_once_and_later_load_revalidates(self):
        run=json.loads(Path('reports/pr7/run_manifest.json').read_bytes())
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'data'
            shutil.copytree(run['source_root'],root)
            try:
                built=[]
                for prefix,module in [('pr6',pr6_views),('pr7',pr7_views)]:
                    old=root/'derived'/(prefix+'_fact')/'commits'/run['refs'][prefix+'_view_id']/'manifest.json'
                    manifest=json.loads(old.read_bytes())
                    build=getattr(module,'build_'+prefix+'_fact_view')
                    load=getattr(module,'load_'+prefix+'_fact_view')
                    with patch.object(module,'SnapshotReader',wraps=SnapshotReader) as checked:
                        ref=build(root,run['refs']['snapshot_id'],**manifest['scope'],
                            pit_policy=manifest['pit_policy'],knowledge_cutoff=manifest['knowledge_cutoff'])
                        self.assertEqual(checked.call_count,1)
                        view=load(root,ref.view_id)
                        self.assertEqual(checked.call_count,2)
                        self.assertEqual(view.manifest['snapshot_ref'],manifest['snapshot_ref'])
                    built.append((load,ref.view_id))
                # A new public load must see corruption in the full source closure.
                reader=SnapshotReader(root,run['refs']['snapshot_id'])
                cid=reader.commits['market_daily'].ref.commit_id
                data=root/'canonical/market_daily/commits'/cid/'rows.json'
                data.chmod(0o600);data.write_bytes(data.read_bytes()+b' ')
                for load,identity in built:
                    with self.assertRaises(ArtifactError):load(root,identity)
            finally:
                for path in root.rglob('*'):
                    if path.is_dir():path.chmod(0o755)

    def test_dm1_build_and_load_reuse_only_their_own_checked_snapshot(self):
        from axiom_data import views
        run=json.loads(Path('reports/pr7/run_manifest.json').read_bytes())
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'data';shutil.copytree(run['source_root'],root)
            try:
                common=dict(symbols=['688981.SH'],start_session='2025-06-10',end_session='2025-06-13')
                pairs=[(views.build_adjusted_price_view,views.load_adjusted_price_view,
                        dict(common,anchor_session='2025-06-13',pit_policy='research_non_pit',decision_cutoff='2025-06-13')),
                       (views.build_market_replay_view,views.load_market_replay_view,common)]
                results=[]
                for build,load,config in pairs:
                    with patch.object(views,'SnapshotReader',wraps=SnapshotReader) as checked:
                        ref=build(root,run['refs']['snapshot_id'],**config)
                        self.assertEqual(checked.call_count,1)
                        loaded=load(root,ref.view_id)
                        self.assertEqual(checked.call_count,2)
                        self.assertEqual(ref,loaded.ref)
                        self.assertEqual(len(loaded.rows),4)
                    results.append((load,ref.view_id))
                reader=SnapshotReader(root,run['refs']['snapshot_id'])
                cid=reader.commits['market_daily'].ref.commit_id
                data=root/'canonical/market_daily/commits'/cid/'rows.json'
                data.chmod(0o600);data.write_bytes(data.read_bytes()+b' ')
                for load,identity in results:
                    with self.assertRaises(ArtifactError):load(root,identity)
            finally:
                for path in root.rglob('*'):
                    if path.is_dir():path.chmod(0o755)
