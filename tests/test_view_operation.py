import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from axiom_data import ArtifactError, BuildContractError, materialize_views


class ViewOperationTest(unittest.TestCase):
    def test_required_failure_resume_and_execution_record_substitution(self):
        run=json.loads(Path('reports/pr7/run_manifest.json').read_bytes())
        common=dict(symbols=['688981.SH'],start_session='2025-06-10',end_session='2025-06-13')
        views={'adjusted':{'kind':'adjusted_price','config':dict(common,anchor_session='2025-06-13',pit_policy='research_non_pit',decision_cutoff='2025-06-13')},
               'facts':{'kind':'pr7_fact','config':dict(common,pit_policy='best_effort_vendor_v1',knowledge_cutoff='2025-06-13T23:59:59+08:00')}}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'data';shutil.copytree(run['source_root'],root)
            try:
                args=dict(run_id='required-views',snapshot_id=run['refs']['snapshot_id'],views=views)
                with patch('axiom_data.pr7_views.build_pr7_fact_view',side_effect=ArtifactError('simulated required failure')):
                    failed=materialize_views(root,**args)
                self.assertEqual(failed['status'],'FAILED');self.assertEqual(set(failed['published_views']),{'adjusted'})
                self.assertFalse(failed['ready_for_consumption'])
                complete=materialize_views(root,**args)
                self.assertEqual(complete['status'],'VIEWS_BUILT')
                self.assertEqual(complete['stage'],'FULL_ADMISSION');self.assertFalse(complete['ready_for_consumption'])
                self.assertEqual(complete['published_views']['adjusted'],failed['published_views']['adjusted'])
                self.assertEqual(materialize_views(root,**args)['published_views'],complete['published_views'])
                changed=json.loads(json.dumps(views));changed['facts']['config']['end_session']='2025-06-12'
                with self.assertRaisesRegex(ArtifactError,'resume plan'):
                    materialize_views(root,**dict(args,views=changed))
                record=root/'operations/required-views/views.json';state=json.loads(record.read_bytes())
                state['published_views']['facts']['view_id']=run['refs']['pr7_view_id']
                record.write_text(json.dumps(state))
                rejected=materialize_views(root,**args)
                self.assertEqual(rejected['status'],'FAILED');self.assertFalse(rejected['ready_for_consumption'])
                self.assertFalse((root/'current.json').exists())
                with self.assertRaises(BuildContractError):materialize_views(root,**dict(args,snapshot_id='current'))
            finally:
                for p in root.rglob('*'):
                    if p.is_dir():p.chmod(0o755)
