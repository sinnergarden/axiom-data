"""Real subprocess builds from isolated mutable checkouts and immutable inputs."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from axiom_data import ArtifactError, SnapshotReader
from axiom_data.artifacts import _json_bytes
from axiom_data.frozen_execution import capture_code, validate_code
from axiom_data.operations import assemble_candidate
from fixture_locations import fixture_root


class FrozenExecutionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.root = self.directory / 'data'
        run = json.loads(Path('deprecated/history/reports/pr7/run_manifest.json').read_bytes())
        shutil.copytree(fixture_root(run['source_root']), self.root)
        self.snapshot_id = run['refs']['snapshot_id']
        reader = SnapshotReader(self.root, self.snapshot_id)
        raw_ids = [r['raw_batch_id'] for r in reader.commits['holder_count_events'].manifest['ordered_raw_batch_refs']]
        self.inputs = {'holder_count_events': dict(raw_batch_ids=raw_ids,
            contract_version='holder_count_events.v1', config={}, new_lineage=True)}

    def tearDown(self):
        for path in self.directory.rglob('*'):
            if path.is_dir():
                path.chmod(0o755)
        self.temp.cleanup()

    def test_invalid_new_public_requests_leave_no_operation_state(self):
        from axiom_data import materialize_views
        root = self.directory / 'invalid'
        with self.assertRaises(ArtifactError):
            assemble_candidate(root, run_id='invalid', domain_inputs={})
        with self.assertRaises(ArtifactError):
            materialize_views(root, run_id='invalid', snapshot_id=self.snapshot_id, views={})
        self.assertFalse(root.exists())

    def test_current_parent_is_resolved_once_for_an_explicit_run(self):
        pointer = self.root / 'current.json'
        pointer.write_bytes(_json_bytes({'snapshot_id': self.snapshot_id}))
        first = assemble_candidate(self.root, run_id='current-parent', domain_inputs=self.inputs,
                                   parent_snapshot_id='current')
        self.assertEqual(first['status'], 'CANDIDATE_BUILT', first)
        envelope = json.loads((self.root / 'operations/current-parent/frozen-canonical/invocation.json').read_bytes())
        self.assertEqual(envelope['invocation']['kwargs']['parent_snapshot_id'], self.snapshot_id)
        pointer.write_bytes(_json_bytes({'snapshot_id': 'unrelated-snapshot'}))
        resumed = assemble_candidate(self.root, run_id='current-parent', domain_inputs=self.inputs,
                                     parent_snapshot_id='current')
        self.assertEqual(resumed['snapshot_id'], first['snapshot_id'])

    def test_bundle_reuse_and_payload_corruption(self):
        first = capture_code(self.root)
        self.assertEqual(first, capture_code(self.root))
        target, manifest = validate_code(self.root, first)
        self.assertIn('axiom_data/deprecated/scope/daily_contract_snapshot.zip', manifest['files'])
        payload = target / 'axiom_data/build.py'
        payload.chmod(0o644)
        payload.write_bytes(payload.read_bytes() + b'\n# corrupt\n')
        with self.assertRaisesRegex(ArtifactError, 'payload mismatch'):
            capture_code(self.root)

    def test_one_process_for_canonical_operation_and_resume_checks_artifacts(self):
        with patch('axiom_data.frozen_execution.subprocess.run', wraps=subprocess.run) as launch:
            result = assemble_candidate(self.root, run_id='canonical', domain_inputs=self.inputs,
                                        parent_snapshot_id=self.snapshot_id)
        self.assertEqual(result['status'], 'CANDIDATE_BUILT', result)
        self.assertEqual(sum(call.args[0][0] == sys.executable for call in launch.call_args_list), 1)
        commit = SnapshotReader(self.root, result['snapshot_id']).commits['holder_count_events']
        self.assertIn('executed_code_ref', commit.manifest['builder_implementation_ref'])
        restored = self.directory / 'restored'
        shutil.copytree(self.root, restored)
        from axiom_data.offline_guard import deny_external_data
        with deny_external_data(restored) as attempts:
            recovered = assemble_candidate(restored, run_id='canonical', domain_inputs=self.inputs,
                                            parent_snapshot_id=self.snapshot_id)
        self.assertEqual(recovered['snapshot_id'], result['snapshot_id'])
        self.assertEqual(attempts, [])
        path = self.root / 'operations/canonical/build.json'
        state = json.loads(path.read_bytes())
        state['published_commits']['holder_count_events'] = 'missing-commit'
        path.write_bytes(_json_bytes(state))
        resumed = assemble_candidate(self.root, run_id='canonical', domain_inputs=self.inputs,
                                     parent_snapshot_id=self.snapshot_id)
        self.assertEqual(resumed['status'], 'FAILED')
        self.assertNotIn('snapshot_id', resumed)

    def test_all_public_views_share_execution_bundle_and_direct_semantics(self):
        from axiom_data import (materialize_views, build_adjusted_price_view, build_market_replay_view,
            build_qlib_view)
        from axiom_data.financial_views import build_financial_fact_view
        from axiom_data.event_views import build_event_fact_view
        from axiom_data.recovery import _view_loaders
        run = json.loads(Path('deprecated/history/reports/pr7/run_manifest.json').read_bytes())
        old = json.loads((self.root / 'derived/pr6_fact/commits' / run['refs']['pr6_view_id'] / 'manifest.json').read_bytes())
        common = dict(symbols=['688981.SH'], start_session='2025-06-10', end_session='2025-06-13')
        pit = dict(pit_policy='best_effort_vendor_v1', knowledge_cutoff='2025-06-13T23:59:59+08:00')
        plan = {
            'adjusted':dict(kind='adjusted_price',config=dict(common,anchor_session='2025-06-13',pit_policy='research_non_pit',decision_cutoff='2025-06-13')),
            'replay':dict(kind='market_replay',config=common),
            'qlib':dict(kind='market_qlib',config=dict(common,fields=['close'])),
            'financial':dict(kind='financial_fact',config=dict(common,**pit,universe_ids=old['scope']['universe_ids'],industry_system=old['scope']['industry_system'])),
            'event':dict(kind='event_fact',config=dict(common,**pit))}
        builders = dict(adjusted_price=build_adjusted_price_view, market_replay=build_market_replay_view,
            market_qlib=build_qlib_view, financial_fact=build_financial_fact_view, event_fact=build_event_fact_view)
        with patch('axiom_data.frozen_execution.subprocess.run', wraps=subprocess.run) as launch:
            result = materialize_views(self.root, run_id='five', snapshot_id=self.snapshot_id, views=plan)
        self.assertEqual(result['status'], 'VIEWS_BUILT', result)
        self.assertEqual(sum(call.args[0][0] == sys.executable for call in launch.call_args_list), 1)
        code_refs = []
        for label, spec in plan.items():
            published = result['published_views'][label]
            view = _view_loaders()[spec['kind']](self.root, published['view_id'])
            code_refs.append(view.manifest['executed_code_ref'])
            direct = builders[spec['kind']](self.root, self.snapshot_id, **spec['config'])
            self.assertEqual(direct.view_id, published['view_id'])
            self.assertEqual(direct.manifest_digest, published['manifest_digest'])
            if spec['kind'] in {'financial_fact','event_fact'}:
                bundle_path = self.root / 'derived' / spec['kind'] / 'commits' / direct.view_id / 'code_bundle.json'
                self.assertLess(bundle_path.stat().st_size, 400)
                self.assertEqual(json.loads(bundle_path.read_bytes()), view.manifest['executed_code_ref'])
        self.assertTrue(all(ref == code_refs[0] for ref in code_refs))
        self.assertEqual(len(list((self.root / 'code_bundles').iterdir())), 1)
        self.assertEqual(materialize_views(self.root, run_id='five', snapshot_id=self.snapshot_id,
                                          views=plan)['published_views'], result['published_views'])
        payload = (self.root / 'derived/event_fact/commits' /
                   result['published_views']['event']['view_id'] / 'states.json.gz')
        payload.chmod(0o644); payload.write_bytes(b'corrupt data payload')
        damaged = materialize_views(self.root, run_id='five', snapshot_id=self.snapshot_id, views=plan)
        self.assertEqual(damaged['status'], 'FAILED')
        self.assertNotIn('event', damaged['published_views'])


    def test_offline_guard_reaches_real_child_and_catches_swallowed_network_attempt(self):
        checkout = self.directory / 'offline-source'
        shutil.copytree(Path('src/axiom_data'), checkout / 'axiom_data',
                        ignore=shutil.ignore_patterns('__pycache__'))
        (checkout / 'axiom_data/offline_probe.py').write_text(
            "def probe(data_root):\n"
            "    import socket\n"
            "    try: socket.create_connection(('example.invalid',443))\n"
            "    except PermissionError: pass\n"
            "    return 'incorrect-success'\n")
        driver = self.directory / 'offline-driver.py'
        driver.write_text(
            'from axiom_data.frozen_execution import execute\n'
            'from axiom_data.offline_guard import deny_external_data\n'
            'from axiom_data.artifacts import ArtifactError\n'
            f'with deny_external_data({str(self.root)!r}) as attempts:\n'
            '    try:\n'
            f"        execute({str(self.root)!r}, {{'function':'axiom_data.offline_probe.probe',"
            f"'kwargs':{{'data_root':{str(self.root)!r}}}}})\n"
            '    except ArtifactError as exc:\n'
            "        assert 'external data access' in str(exc)\n"
            "        assert attempts == ['network'], attempts\n"
            "    else: raise AssertionError('offline child accepted network access')\n")
        result = subprocess.run([sys.executable, str(driver)], capture_output=True, text=True,
            env=dict(os.environ, PYTHONPATH=str(checkout), PYTHONDONTWRITEBYTECODE='1'))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_pause_mutate_checkout_config_current_and_resume_frozen_source(self):
        checkout = self.directory / 'checkout'
        shutil.copytree(Path('src/axiom_data'), checkout / 'src/axiom_data',
                        ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copy('pyproject.toml', checkout / 'pyproject.toml')
        ready, release = self.directory / 'ready', self.directory / 'release'
        source = checkout / 'src/axiom_data/event_source.py'
        original = source.read_text()
        signature = '    def _build_rows('
        start = original.index(signature, original.index('class EventBuilder'))
        body = original.index('\n', start) + 1
        # This source belongs only to the temporary test checkout. The marker
        # is reached inside the actual frozen business builder, not a mock.
        pause = (f"        from pathlib import Path\n        import time\n"
                 f"        Path({str(ready)!r}).touch()\n"
                 f"        while not Path({str(release)!r}).exists(): time.sleep(0.01)\n")
        source.write_text(original[:body] + pause + original[body:])
        config = self.directory / 'input.json'
        config.write_bytes(_json_bytes(self.inputs))
        result_path = self.directory / 'result.json'
        driver = self.directory / 'driver.py'
        driver.write_text('from axiom_data.operations import assemble_candidate\nimport json,sys\n'
            f"result=assemble_candidate({str(self.root)!r},run_id=sys.argv[1],"
            f"parent_snapshot_id={self.snapshot_id!r},domain_inputs=json.load(open({str(config)!r})))\n"
            f"open({str(result_path)!r},'w').write(json.dumps(result))\n")
        env = dict(os.environ, PYTHONPATH=str(checkout / 'src'), PYTHONDONTWRITEBYTECODE='1')
        process = subprocess.Popen([sys.executable, str(driver), 'paused'], env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 20
            while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(ready.exists(), process.communicate(timeout=1) if process.poll() is not None else 'pause not reached')
            source.write_text(original[:body] + "        raise ValueError('changed checkout executed')\n" + original[body:])
            package_config = checkout / 'src/axiom_data/contracts/holder_count_events.v1.json'
            original_package_config = package_config.read_text()
            package_config.write_text('corrupted mutable contract JSON')
            changed = json.loads(config.read_bytes())
            changed['holder_count_events']['config']['bogus'] = 'changed after capture'
            config.write_bytes(_json_bytes(changed))
            (self.root / 'current.json').write_text('{"snapshot_id":"unrelated-current"}')
            release.touch()
            stdout, stderr = process.communicate(timeout=20)
            self.assertEqual(process.returncode, 0, stderr)
        finally:
            if process.poll() is None:
                process.kill(); process.communicate()
        built = json.loads(result_path.read_bytes())
        self.assertEqual(built['status'], 'CANDIDATE_BUILT', built)
        config.write_bytes(_json_bytes(self.inputs))
        resumed = subprocess.run([sys.executable, str(driver), 'paused'], env=env, capture_output=True, text=True)
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(json.loads(result_path.read_bytes())['snapshot_id'], built['snapshot_id'])
        package_config.write_text(original_package_config)
        fresh = subprocess.run([sys.executable, str(driver), 'fresh'], env=env, capture_output=True, text=True)
        self.assertNotEqual(fresh.returncode, 0)
        self.assertTrue('JSONDecodeError' in fresh.stderr or 'changed checkout executed' in fresh.stderr, fresh.stderr)
        old = json.loads((self.root / 'operations/paused/frozen-canonical/invocation.json').read_bytes())
        new = json.loads((self.root / 'operations/fresh/frozen-canonical/invocation.json').read_bytes())
        self.assertNotEqual(old['code'], new['code'])
