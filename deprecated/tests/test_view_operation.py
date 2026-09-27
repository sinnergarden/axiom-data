import json
import shutil
import tempfile
import unittest
from pathlib import Path
from fixture_locations import fixture_root
from unittest.mock import patch

from axiom_data import ArtifactError, BuildContractError, materialize_views


class ViewOperationTest(unittest.TestCase):
    def setUp(self):
        # Internal checkpoint tests; public real-process coverage is separate.
        self.enterContext(patch('axiom_data.frozen_execution.is_frozen', return_value=True))

    def test_required_failure_resume_and_execution_record_substitution(self):
        run=json.loads(Path('deprecated/history/reports/pr7/run_manifest.json').read_bytes())
        common=dict(symbols=['688981.SH'],start_session='2025-06-10',end_session='2025-06-13')
        views={'adjusted':{'kind':'adjusted_price','config':dict(common,anchor_session='2025-06-13',pit_policy='research_non_pit',decision_cutoff='2025-06-13')},
               'facts':{'kind':'event_fact','config':dict(common,pit_policy='best_effort_vendor_v1',knowledge_cutoff='2025-06-13T23:59:59+08:00')}}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'data';shutil.copytree(fixture_root(run['source_root']),root)
            try:
                args=dict(run_id='required-views',snapshot_id=run['refs']['snapshot_id'],views=views)
                with patch('axiom_data.event_views.build_event_fact_view_from_reader',side_effect=ArtifactError('simulated required failure')):
                    failed=materialize_views(root,**args)
                self.assertEqual(failed['status'],'FAILED');self.assertEqual(set(failed['published_views']),{'adjusted'})
                self.assertFalse(failed['ready_for_consumption'])
                complete=materialize_views(root,**args)
                self.assertEqual(complete['status'],'VIEWS_BUILT')
                self.assertEqual(complete['stage'],'FULL_ADMISSION');self.assertFalse(complete['ready_for_consumption'])
                self.assertEqual(complete['published_views']['adjusted'],failed['published_views']['adjusted'])
                self.assertEqual(complete['published_views']['facts']['kind'], 'event_fact')
                self.assertEqual(materialize_views(root,**args)['published_views'],complete['published_views'])
                legacy=json.loads(json.dumps(views));legacy['facts']['kind']='pr7_fact'
                self.assertEqual(materialize_views(root,**dict(args,views=legacy))['published_views'],complete['published_views'])
                changed=json.loads(json.dumps(views));changed['facts']['config']['end_session']='2025-06-12'
                with self.assertRaisesRegex(ArtifactError,'resume plan'):
                    materialize_views(root,**dict(args,views=changed))
                record=root/'operations/required-views/views.json';state=json.loads(record.read_bytes())
                state['published_views']['facts']['view_id']=run['refs']['pr7_view_id']
                record.write_text(json.dumps(state))
                rejected=materialize_views(root,**args)
                self.assertEqual(rejected['status'],'VIEWS_BUILT');self.assertFalse(rejected['ready_for_consumption'])
                self.assertEqual(rejected['published_views'],complete['published_views'])
                self.assertFalse((root/'current.json').exists())
                with self.assertRaises(BuildContractError):materialize_views(root,**dict(args,snapshot_id='current'))
            finally:
                for p in root.rglob('*'):
                    if p.is_dir():p.chmod(0o755)

    def test_all_five_view_kinds_share_one_checked_snapshot(self):
        from axiom_data import artifacts, SnapshotReader
        run=json.loads(Path('deprecated/history/reports/pr7/run_manifest.json').read_bytes())
        original=fixture_root(run['source_root'])
        old=json.loads((original/'derived/pr6_fact/commits'/run['refs']['pr6_view_id']/'manifest.json').read_bytes())
        common=dict(symbols=['688981.SH'],start_session='2025-06-10',end_session='2025-06-13')
        pit=dict(pit_policy='best_effort_vendor_v1',knowledge_cutoff='2025-06-13T23:59:59+08:00')
        plan={
            'adjusted':dict(kind='adjusted_price',config=dict(common,anchor_session='2025-06-13',pit_policy='research_non_pit',decision_cutoff='2025-06-13')),
            'replay':dict(kind='market_replay',config=common),
            'qlib':dict(kind='market_qlib',config=dict(common,fields=['close'])),
            'pr6':dict(kind='pr6_fact',config=dict(common,**pit,universe_ids=old['scope']['universe_ids'],industry_system=old['scope']['industry_system'])),
            'pr7':dict(kind='pr7_fact',config=dict(common,**pit))}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'data';shutil.copytree(original,root)
            try:
                with patch.object(artifacts,'_load_domain_commit',wraps=artifacts._load_domain_commit) as checked:
                    result=materialize_views(root,run_id='five',snapshot_id=run['refs']['snapshot_id'],views=plan)
                    self.assertEqual(result['status'],'VIEWS_BUILT',result.get('failed'))
                    self.assertEqual(checked.call_count,18)
                self.assertEqual(len(result['published_views']),5)
                from contextlib import ExitStack
                with ExitStack() as stack:
                    builders=[stack.enter_context(patch(name,side_effect=AssertionError('completed builder entered')))
                        for name in ('axiom_data.views._build_adjusted_price_view',
                            'axiom_data.views._build_market_replay_view',
                            'axiom_data.consumption._build_qlib_view',
                            'axiom_data.financial_views.build_financial_fact_view_from_reader',
                            'axiom_data.event_views.build_event_fact_view_from_reader')]
                    resumed=materialize_views(root,run_id='five',snapshot_id=run['refs']['snapshot_id'],views=plan)
                    self.assertEqual(resumed['status'],'VIEWS_BUILT',resumed.get('failed'))
                    self.assertEqual(resumed['published_views'],result['published_views'])
                    self.assertTrue(all(b.call_count==0 for b in builders))
                reader=SnapshotReader(root,run['refs']['snapshot_id'])
                path=root/'canonical/market_daily/commits'/reader.commits['market_daily'].ref.commit_id/'rows.json'
                path.chmod(0o600);path.write_bytes(path.read_bytes()+b' ')
                result=materialize_views(root,run_id='five',snapshot_id=run['refs']['snapshot_id'],views=plan)
                self.assertEqual(result['status'],'FAILED');self.assertEqual(set(result['failed']),{'snapshot'})
                self.assertFalse(result['ready_for_consumption'])
            finally:
                for path in root.rglob('*'):
                    if path.is_dir():path.chmod(0o755)
