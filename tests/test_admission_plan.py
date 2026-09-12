import contextlib
import copy
import io
import json
import shutil
import tempfile
import unittest
from importlib.resources import files
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from axiom_data import ArtifactError, validate_admission_plan
from axiom_data.artifacts import _digest, _identity_digest, _derived_identity, _json_bytes, validate_domain_commit_closure
from axiom_data.consumption import MARKET_VIEW_FIELDS


class AdmissionPlanTest(unittest.TestCase):
    def setUp(self):
        run = json.loads(Path('reports/pr7/run_manifest.json').read_bytes())
        self.root = Path(run['source_root'])
        self.snapshot = run['refs']['snapshot_id']
        old = json.loads((self.root / 'derived/pr6_fact/commits' / run['refs']['pr6_view_id'] / 'manifest.json').read_bytes())
        target = dict(symbols=['688981.SH', '000401.SZ'], start_session='2025-06-10', end_session='2025-06-13')
        pit = dict(pit_policy='best_effort_vendor_v1', knowledge_cutoff='2025-06-13T23:59:59+08:00')
        configs = dict(market_replay={}, market_qlib={'fields': list(MARKET_VIEW_FIELDS)},
                       adjusted_price=dict(anchor_session='2025-06-13', decision_cutoff='2025-06-13', pit_policy='research_non_pit'),
                       pr6_fact=dict(pit, universe_ids=old['scope']['universe_ids'], industry_system=old['scope']['industry_system']),
                       pr7_fact=pit)
        views = {kind: dict(kind=kind, config=dict(config, **target)) for kind, config in configs.items()}
        self.plan = dict(snapshot_id=self.snapshot,
                         expected_snapshot_manifest_digest=(self.root / 'snapshots' / self.snapshot / 'manifest.sha256').read_text().strip(),
                         scope_registry_digest=_digest(files('axiom_data.scope').joinpath('pr7_scope.v1.json').read_bytes()),
                         target=target, required_view_configs=configs, views=views)

    def test_complete_geometry_checks_only_reference_closures(self):
        with patch('axiom_data.admission_plan.validate_domain_commit_closure', wraps=validate_domain_commit_closure) as load:
            result = validate_admission_plan(self.root, **self.plan)
        self.assertEqual(result['status'], 'PLAN_VALIDATED')
        self.assertEqual([call.args[1] for call in load.call_args_list], ['security_master', 'trading_calendar'])
        self.assertEqual(result['registry_requirement_count'], 56)
        self.assertEqual(result['registry_feature_count'], 469)
        self.assertEqual(result['admission'], 'NOT_ASSESSED')
        self.assertEqual(result['view_payload_validation'], 'PENDING')
        self.assertFalse(result['ready_for_consumption'])

    def test_sample_fact_views_do_not_cover_full_target(self):
        plan = copy.deepcopy(self.plan)
        plan['views']['pr6_fact']['config']['symbols'] = ['688981.SH']
        plan['views']['pr7_fact']['config']['symbols'] = ['688981.SH']
        result = validate_admission_plan(self.root, **plan)
        self.assertEqual(result['status'], 'INCOMPLETE')
        self.assertEqual({(r['kind'], r['symbol']) for r in result['missing_shard_coverage']},
                         {('pr6_fact', '000401.SZ'), ('pr7_fact', '000401.SZ')})

    def test_disjoint_shards_and_interior_hole(self):
        plan = copy.deepcopy(self.plan)
        first = plan['views'].pop('pr7_fact')
        second = copy.deepcopy(first)
        first['config']['end_session'] = '2025-06-11'
        second['config']['start_session'] = '2025-06-12'
        plan['views'].update(early=first, late=second)
        self.assertEqual(validate_admission_plan(self.root, **plan)['status'], 'PLAN_VALIDATED')
        second['config']['start_session'] = '2025-06-13'
        result = validate_admission_plan(self.root, **plan)
        self.assertEqual(result['status'], 'INCOMPLETE')
        self.assertTrue(all(r['first_missing_session'] == '2025-06-12' for r in result['missing_shard_coverage']))
        second['config']['start_session'] = '2025-06-11'
        with self.assertRaisesRegex(ArtifactError, 'overlapping'):
            validate_admission_plan(self.root, **plan)

    def test_registry_digest_snapshot_digest_and_semantic_substitution(self):
        for key in ('scope_registry_digest', 'expected_snapshot_manifest_digest'):
            with self.subTest(key=key), self.assertRaises(ArtifactError):
                validate_admission_plan(self.root, **dict(self.plan, **{key: 'sha256:' + '0' * 64}))
        for field, value in [('knowledge_cutoff', '2026-01-01T00:00:00Z'), ('pit_policy', 'operational_pit_v1')]:
            plan = copy.deepcopy(self.plan)
            plan['views']['pr7_fact']['config'][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ArtifactError, 'semantics'):
                validate_admission_plan(self.root, **plan)
        plan = copy.deepcopy(self.plan)
        plan['views']['market_qlib']['config']['fields'] = ['close']
        with self.assertRaisesRegex(ArtifactError, 'semantics'):
            validate_admission_plan(self.root, **plan)

    def test_exchange_calendar_and_identity_geometry(self):
        # A geometry fault injection after genuine reference loading. Never edit
        # the immutable real fixture or claim these altered rows passed closure.
        def altered(domain, transform):
            def load(root, name, identity):
                commit = validate_domain_commit_closure(root, name, identity)
                if name != domain:
                    return commit
                return SimpleNamespace(ref=commit.ref, manifest=commit.manifest,
                                       rows=transform(copy.deepcopy(list(commit.rows))))
            return load
        missing_sz = altered('trading_calendar', lambda rows: [r for r in rows if r['exchange'] == 'SSE'])
        with patch('axiom_data.admission_plan.validate_domain_commit_closure', side_effect=missing_sz):
            with self.assertRaisesRegex(ArtifactError, 'SZSE calendar coverage'):
                validate_admission_plan(self.root, **self.plan)
        def delist(rows):
            for row in rows:
                if row['symbol'] == '688981.SH':
                    row['delist_session'] = '2025-06-12'
            return rows
        with patch('axiom_data.admission_plan.validate_domain_commit_closure', side_effect=altered('security_master', delist)):
            with self.assertRaisesRegex(ArtifactError, 'adjusted anchor'):
                validate_admission_plan(self.root, **self.plan)
            plan = copy.deepcopy(self.plan)
            plan['required_view_configs']['adjusted_price']['anchor_session'] = '2025-06-11'
            plan['views']['adjusted_price']['config']['anchor_session'] = '2025-06-11'
            result = validate_admission_plan(self.root, **plan)
        self.assertEqual(result['expected_sessions_by_symbol']['688981.SH'], 2)
        self.assertEqual(result['expected_sessions_by_symbol']['000401.SZ'], 4)

    def test_bad_creation_time_and_normalized_dates(self):
        plan = copy.deepcopy(self.plan)
        plan['views']['market_replay']['config']['created_at'] = 'bad'
        with self.assertRaises(ArtifactError):
            validate_admission_plan(self.root, **plan)
        plan = copy.deepcopy(self.plan)
        plan['target']['start_session'] = '2025-6-10'
        plan['views']['market_replay']['config']['start_session'] = '2025-6-10'
        result = validate_admission_plan(self.root, **plan)
        self.assertEqual(result['status'], 'PLAN_VALIDATED')
        self.assertEqual(result['plan']['target']['start_session'], '2025-06-10')

    def test_reference_raw_corruption_fails(self):
        manifest = json.loads((self.root / 'snapshots' / self.snapshot / 'manifest.json').read_bytes())
        identity = manifest['domain_refs']['security_master']['domain_commit_id']
        commit = validate_domain_commit_closure(self.root, 'security_master', identity)
        raw_id = commit.manifest['ordered_raw_batch_refs'][0]['raw_batch_id']
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'data'
            shutil.copytree(self.root, root)
            try:
                raw = root / 'raw/batches' / raw_id
                raw_manifest = json.loads((raw / 'manifest.json').read_bytes())
                payload = raw / raw_manifest['payload_files'][0]['path']
                payload.chmod(0o600)
                payload.write_bytes(payload.read_bytes() + b' ')
                with self.assertRaises(ArtifactError):
                    validate_admission_plan(root, **self.plan)
            finally:
                for path in root.rglob('*'):
                    if path.is_dir():
                        path.chmod(0o755)

    def test_self_consistent_manifest_with_missing_domain_is_rejected(self):
        manifest = json.loads((self.root / 'snapshots' / self.snapshot / 'manifest.json').read_bytes())
        del manifest['domain_refs']['financial_events']
        manifest['identity_digest'] = _identity_digest(manifest, 'snapshot_id')
        identity = _derived_identity('snapshot', manifest['identity_digest'])
        manifest['snapshot_id'] = identity
        payload = _json_bytes(manifest)
        digest = _digest(payload)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'snapshots' / identity
            target.mkdir(parents=True)
            (target / 'manifest.json').write_bytes(payload)
            (target / 'manifest.sha256').write_text(digest)
            with self.assertRaisesRegex(ArtifactError, 'manifest composition'):
                validate_admission_plan(root, **dict(self.plan, snapshot_id=identity,
                                                      expected_snapshot_manifest_digest=digest))

    def test_cli_incomplete_plan_exits_nonzero(self):
        from axiom_data.cli import main
        plan = copy.deepcopy(self.plan)
        del plan['views']['pr7_fact']
        plan.pop('snapshot_id')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'plan.json'
            path.write_text(json.dumps(plan))
            output = io.StringIO()
            with patch('sys.argv', ['axiom-data', '--data-root', str(self.root), 'validate-admission-plan',
                                   '--snapshot', self.snapshot, '--plan', str(path)]):
                with contextlib.redirect_stdout(output):
                    self.assertEqual(main(), 1)
            self.assertEqual(json.loads(output.getvalue())['status'], 'INCOMPLETE')
