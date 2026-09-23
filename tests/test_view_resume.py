"""Public resume regression using the installed small immutable real fixture."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from axiom_data import ArtifactError, materialize_views
from axiom_data.operations import _save
from axiom_data.views import _build_market_replay_view
from fixture_locations import fixture_root


class ViewResumeTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)/'data'
        run=json.loads(Path('reports/pr7/run_manifest.json').read_bytes())
        shutil.copytree(fixture_root(run['source_root']),self.root)
        config=dict(symbols=['688981.SH'],start_session='2025-06-10',end_session='2025-06-13')
        self.args=dict(run_id='resume',snapshot_id=run['refs']['snapshot_id'],
            views={'first':dict(kind='market_replay',config=config),
                   'second':dict(kind='market_replay',config=dict(config,end_session='2025-06-12'))})
        self.path=self.root/'operations/resume/views.json'

    def tearDown(self):
        for path in self.root.rglob('*'):
            if path.is_dir():path.chmod(0o755)
        self.temp.cleanup()

    def run_views(self):
        return materialize_views(self.root,**self.args)

    def test_interruption_legacy_upgrade_and_plan_written_once(self):
        calls=0
        def interrupt(reader,**config):
            nonlocal calls
            calls+=1
            if calls==2:raise KeyboardInterrupt()
            return _build_market_replay_view(reader,**config)
        with patch('axiom_data.views._build_market_replay_view',side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):self.run_views()
        state=json.loads(self.path.read_bytes())
        plan_path=self.path.with_name('views-plan.json')
        self.assertNotIn('plan',state)
        # Recreate the legacy inline checkpoint without changing the frozen plan.
        state.update(schema_version='required_views_run.v1',plan=json.loads(plan_path.read_bytes()))
        self.path.write_text(json.dumps(state));plan_path.unlink()
        with patch('axiom_data.view_operation.save_progress',wraps=_save) as saves, patch(
                'axiom_data.views._build_market_replay_view',wraps=_build_market_replay_view) as builder:
            result=self.run_views()
        self.assertEqual(result['status'],'VIEWS_BUILT')
        self.assertEqual(builder.call_count,1)
        self.assertEqual(builder.call_args.kwargs['end_session'],'2025-06-12')
        self.assertEqual(sum(c.args[0]==plan_path for c in saves.call_args_list),1)
        self.assertTrue(all('plan' not in c.args[1] for c in saves.call_args_list if c.args[0]==self.path))
        with patch('axiom_data.view_operation.save_progress',wraps=_save) as saves, patch(
                'axiom_data.views._build_market_replay_view') as builder:
            self.assertEqual(self.run_views()['status'],'VIEWS_BUILT')
        self.assertEqual(builder.call_count,0)
        self.assertFalse(any(c.args[0]==plan_path for c in saves.call_args_list))

    def test_missing_and_corrupted_enter_builder(self):
        result=self.run_views()
        target=self.root/'derived/market_replay/commits'/result['published_views']['first']['view_id']
        target.chmod(0o755)
        shutil.rmtree(target)
        with patch('axiom_data.views._build_market_replay_view',wraps=_build_market_replay_view) as builder:
            self.assertEqual(self.run_views()['status'],'VIEWS_BUILT')
        self.assertEqual(builder.call_count,1)
        payload=target/'rows.json';payload.chmod(0o600);payload.write_bytes(b'corrupt')
        with patch('axiom_data.views._build_market_replay_view',wraps=_build_market_replay_view) as builder:
            result=self.run_views()
        self.assertEqual(builder.call_count,1)
        # Existing immutable publisher rejects occupied corrupt output rather
        # than silently replacing published bytes. The builder was retried.
        self.assertEqual(result['status'],'FAILED')
        self.assertNotIn('first',result['published_views'])

    def test_wrong_input_artifact_and_changed_implementation(self):
        result=self.run_views()
        state=json.loads(self.path.read_bytes())
        state['published_views']['first']=result['published_views']['second']
        self.path.write_text(json.dumps(state))
        with patch('axiom_data.views._build_market_replay_view',wraps=_build_market_replay_view) as builder:
            restored=self.run_views()
        self.assertEqual(builder.call_count,1)
        self.assertEqual(restored['published_views'],result['published_views'])
        plan_path=self.path.with_name('views-plan.json')
        plan=json.loads(plan_path.read_bytes());plan['implementation_digest']='sha256:changed'
        plan_path.write_text(json.dumps(plan))
        with patch('axiom_data.views._build_market_replay_view') as builder:
            with self.assertRaisesRegex(ArtifactError,'resume plan or implementation changed'):
                self.run_views()
        self.assertEqual(builder.call_count,0)

    def test_qlib_v1_cannot_satisfy_invalid_adjusted_request(self):
        from axiom_data.consumption import _build_qlib_view
        from axiom_data.artifacts import _digest, _json_bytes
        config=dict(self.args['views']['first']['config'],fields=['close'])
        self.args['views']={'qlib':dict(kind='market_qlib',config=config)}
        valid=self.run_views()
        self.assertEqual(valid['status'],'VIEWS_BUILT')
        with patch('axiom_data.consumption._build_qlib_view',wraps=_build_qlib_view) as builder:
            self.assertEqual(self.run_views()['status'],'VIEWS_BUILT')
        self.assertEqual(builder.call_count,0)
        # Bind the invalid request into the frozen checkpoint, so the public
        # entrypoint must check input legality, not merely plan equality.
        config.update(price_basis='anchor_adjusted',pit_policy='research_non_pit',
                      decision_cutoff='2025-06-13')
        plan_path=self.path.with_name('views-plan.json')
        plan=json.loads(plan_path.read_bytes());plan['views']=self.args['views']
        plan_path.write_bytes(_json_bytes(plan))
        state=json.loads(self.path.read_bytes());state['plan_digest']=_digest(_json_bytes(plan))
        self.path.write_bytes(_json_bytes(state))
        with patch('axiom_data.consumption._build_qlib_view',wraps=_build_qlib_view) as builder:
            rejected=self.run_views()
        self.assertEqual(rejected['status'],'FAILED')
        self.assertEqual(builder.call_count,1)
        self.assertNotIn('qlib',rejected['published_views'])

    def test_qlib_adjusted_exact_resume_and_v1_substitution(self):
        from axiom_data import build_adjusted_price_view, build_qlib_view
        from axiom_data.consumption import _build_qlib_view
        config=dict(self.args['views']['first']['config'],fields=['close'])
        unadjusted=build_qlib_view(self.root,self.args['snapshot_id'],**config)
        adjusted=build_adjusted_price_view(self.root,self.args['snapshot_id'],
            **{k:v for k,v in config.items() if k!='fields'},anchor_session='2025-06-13',
            pit_policy='research_non_pit',decision_cutoff='2025-06-13')
        config.update(price_basis='anchor_adjusted',adjusted_price_view_id=adjusted.view_id,
                      pit_policy='research_non_pit',decision_cutoff='2025-06-13')
        self.args['views']={'qlib':dict(kind='market_qlib',config=config)}
        expected=self.run_views()
        self.assertEqual(expected['status'],'VIEWS_BUILT')
        with patch('axiom_data.consumption._build_qlib_view',wraps=_build_qlib_view) as builder:
            self.assertEqual(self.run_views()['published_views'],expected['published_views'])
        self.assertEqual(builder.call_count,0)
        state=json.loads(self.path.read_bytes())
        state['published_views']['qlib']=dict(kind='market_qlib',view_id=unadjusted.view_id,
                                            manifest_digest=unadjusted.manifest_digest)
        self.path.write_text(json.dumps(state))
        with patch('axiom_data.consumption._build_qlib_view',wraps=_build_qlib_view) as builder:
            restored=self.run_views()
        self.assertEqual(builder.call_count,1)
        self.assertEqual(restored['published_views'],expected['published_views'])
