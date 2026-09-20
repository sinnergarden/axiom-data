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
        with patch('axiom_data.view_operation._save',wraps=_save) as saves, patch(
                'axiom_data.views._build_market_replay_view',wraps=_build_market_replay_view) as builder:
            result=self.run_views()
        self.assertEqual(result['status'],'VIEWS_BUILT')
        self.assertEqual(builder.call_count,1)
        self.assertEqual(builder.call_args.kwargs['end_session'],'2025-06-12')
        self.assertEqual(sum(c.args[0]==plan_path for c in saves.call_args_list),1)
        self.assertTrue(all('plan' not in c.args[1] for c in saves.call_args_list if c.args[0]==self.path))
        with patch('axiom_data.view_operation._save',wraps=_save) as saves, patch(
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
