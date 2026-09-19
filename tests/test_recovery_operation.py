import json
import contextlib
import io
import shutil
import socket
import tempfile
import unittest
from pathlib import Path
from fixture_locations import fixture_root
from unittest.mock import patch

from axiom_data import ArtifactError, BuildContractError, verify_recovery
from axiom_data.artifacts import _layout
from axiom_data.views import build_market_replay_view


class RecoveryOperationTest(unittest.TestCase):
    def setUp(self):
        self.run = json.loads(Path('reports/pr7/run_manifest.json').read_bytes())
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'restored'
        shutil.copytree(fixture_root(self.run['source_root']), self.root)
        self.snapshot_id = self.run['refs']['snapshot_id']

    def tearDown(self):
        for path in self.root.rglob('*'):
            if path.is_dir():
                path.chmod(0o755)
        self.temp.cleanup()

    def plan(self):
        refs = self.run['refs']
        views = {}
        for kind, key in [('pr6_fact', 'pr6_view_id'), ('pr7_fact', 'pr7_view_id')]:
            target = self.root / 'derived' / kind / 'commits' / refs[key]
            views[kind] = dict(kind=kind, view_id=refs[key],
                              manifest_digest=(target / 'manifest.sha256').read_text().strip())
        digest = (self.root / 'snapshots' / self.snapshot_id / 'manifest.sha256').read_text().strip()
        return dict(run_id='recovery', snapshot_id=self.snapshot_id,
                    expected_snapshot_manifest_digest=digest, views=views)

    def test_offline_recovery_rebuilds_catalog_and_rechecks_completed_run(self):
        plan = self.plan()
        catalog = _layout(self.root).catalog
        if catalog.exists():
            catalog.unlink()
        result = verify_recovery(self.root, **plan)
        self.assertEqual(result['status'], 'RECOVERY_VALIDATED', result)
        self.assertTrue(catalog.is_file())
        self.assertFalse(result['ready_for_consumption'])
        self.assertEqual(result['blocked_external_attempts'], 0)
        self.assertEqual(result['validated_views']['pr6_fact']['schema_version'], 'pr6_fact_view.v2')
        self.assertEqual(verify_recovery(self.root, **plan)['status'], 'RECOVERY_VALIDATED')
        raw = next((self.root / 'raw/batches').iterdir())
        manifest = json.loads((raw / 'manifest.json').read_bytes())
        payload = raw / manifest['payload_files'][0]['path']
        payload.chmod(0o600)
        payload.write_bytes(payload.read_bytes() + b' ')
        self.assertEqual(verify_recovery(self.root, **plan)['status'], 'FAILED')
        state = json.loads((self.root / 'operations/recovery/recovery.json').read_bytes())
        self.assertNotIn('validated_views', state)

    def test_plan_changes_missing_views_and_wrong_digests_fail(self):
        plan = self.plan()
        self.assertEqual(verify_recovery(self.root, **plan)['status'], 'RECOVERY_VALIDATED')
        changed = dict(plan, expected_snapshot_manifest_digest='sha256:' + '0' * 64)
        with self.assertRaisesRegex(ArtifactError, 'resume plan'):
            verify_recovery(self.root, **changed)
        self.assertEqual(verify_recovery(self.root, **dict(changed, run_id='bad-snapshot'))['status'], 'FAILED')
        missing = json.loads(json.dumps(plan))
        missing['run_id'] = 'missing-view'
        missing['views']['pr7_fact']['view_id'] = 'pr7-fact-' + '0' * 64
        self.assertEqual(verify_recovery(self.root, **missing)['status'], 'FAILED')
        with self.assertRaises(ArtifactError):
            verify_recovery(self.root, **dict(plan, views={}))
        with self.assertRaises(BuildContractError):
            verify_recovery(self.root, **dict(plan, snapshot_id='current'))

    def test_rebuild_restores_exact_required_view(self):
        config = dict(symbols=['688981.SH'], start_session='2025-06-10',
                      end_session='2025-06-13', created_at='2026-09-09T00:00:00Z')
        ref = build_market_replay_view(self.root, self.snapshot_id, **config)
        target = _layout(self.root).derived_commits('market_replay') / ref.view_id
        target.chmod(0o755)
        shutil.rmtree(target)
        plan = self.plan()
        plan['views'] = {'replay': dict(kind='market_replay', view_id=ref.view_id,
                                      manifest_digest=ref.manifest_digest)}
        plan['rebuild_views'] = {'replay': dict(kind='market_replay', config=config)}
        result = verify_recovery(self.root, **plan)
        self.assertEqual(result['status'], 'RECOVERY_VALIDATED', result)
        self.assertTrue(target.is_dir())

    def test_invalid_rebuild_arguments_leave_no_run_record(self):
        plan = self.plan()
        plan['views'] = {'replay': dict(plan['views']['pr6_fact'], kind='market_replay')}
        plan['rebuild_views'] = {'replay': {
            'kind': 'market_replay', 'config': {'unexpected_argument': 'invalid'},
        }}
        with self.assertRaisesRegex(ArtifactError, 'rebuild arguments'):
            verify_recovery(self.root, **plan)
        self.assertFalse((self.root / 'operations/recovery/recovery.json').exists())

    def test_fact_view_exact_rebuild_requires_restoring_original_bytes(self):
        plan = self.plan()
        for kind in ('pr6_fact', 'pr7_fact'):
            plan['rebuild_views'] = {kind: {'kind': kind, 'config': {}}}
            with self.subTest(kind=kind):
                with self.assertRaisesRegex(ArtifactError, 'creation-time-pinnable'):
                    verify_recovery(self.root, **plan)
        self.assertFalse((self.root / 'operations/recovery/recovery.json').exists())

    def test_cli_reports_recovery_failure_with_nonzero_result(self):
        from axiom_data.cli import main
        plan = self.plan()
        plan.pop('run_id')
        plan.pop('snapshot_id')
        plan['expected_snapshot_manifest_digest'] = 'sha256:' + '0' * 64
        config = Path(self.temp.name) / 'recovery-plan.json'
        config.write_text(json.dumps(plan))
        output = io.StringIO()
        with patch('sys.argv', ['axiom-data', '--data-root', str(self.root),
                               'verify-recovery', '--snapshot', self.snapshot_id,
                               '--run-id', 'cli-recovery', '--plan', str(config)]):
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(), 1)
        self.assertEqual(json.loads(output.getvalue())['status'], 'FAILED')

    def test_network_attempt_cannot_be_reported_as_recovered(self):
        def swallowed_attempt(_root):
            try:
                socket.create_connection(('example.invalid', 443))
            except PermissionError:
                pass
            return 0
        with patch('axiom_data.recovery.rebuild_catalog', side_effect=swallowed_attempt):
            result = verify_recovery(self.root, **self.plan())
        self.assertEqual(result['status'], 'FAILED')
        self.assertEqual(result['blocked_external_attempts'], 1)

    def test_published_pr6_v1_is_read_with_its_original_contract(self):
        old = json.loads(Path('reports/pr6/run_manifest.json').read_bytes())
        root = Path(self.temp.name) / 'legacy'
        shutil.copytree(fixture_root(old['data_root']), root)
        try:
            snapshot_id = old['artifact_refs']['snapshot']['snapshot_id']
            view_id = old['artifact_refs']['view']['view_id']
            view_path = root / 'derived/pr6_fact/commits' / view_id
            result = verify_recovery(
                root, run_id='legacy', snapshot_id=snapshot_id,
                expected_snapshot_manifest_digest=(root / 'snapshots' / snapshot_id / 'manifest.sha256').read_text().strip(),
                views={'old': dict(kind='pr6_fact', view_id=view_id,
                                  manifest_digest=(view_path / 'manifest.sha256').read_text().strip())},
            )
            self.assertEqual(result['status'], 'RECOVERY_VALIDATED', result)
            self.assertEqual(result['validated_views']['old']['schema_version'], 'pr6_fact_view.v1')
        finally:
            for path in root.rglob('*'):
                if path.is_dir():
                    path.chmod(0o755)
