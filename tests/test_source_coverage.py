import json
import shutil
import tempfile
import unittest
from pathlib import Path

from axiom_data import (ArtifactError, BuildApplication, SnapshotReader, create_snapshot,
                        load_raw_batch, validate_domain_commit_closure, write_raw_batch)
from axiom_data.pr7_source import Pr7Builder, Pr7Collector
from axiom_data.pr7_views import leaf_facts, request_coverage
from test_pr6_artifacts import Client


class SourceCoverageTest(unittest.TestCase):
    def test_frozen_v1_coverage_manifest_remains_readable_but_cannot_be_extended(self):
        import copy
        from unittest.mock import patch
        from axiom_data import source_completeness
        from axiom_data.artifacts import _digest, _json_bytes, _identity_digest, _derived_identity
        from axiom_data.source_coverage import observation, state, LEGACY_POLICY
        from test_artifacts import rewrite_manifest

        raw_ref = self.raw('20250610', [])
        current_ref = self.build(raw_ref)
        current = validate_domain_commit_closure(self.root, 'holder_count_events', current_ref.commit_id)
        raw = load_raw_batch(self.root, raw_ref.raw_batch_id)
        # Materialize the frozen schema in the test fixture only. Public builders
        # never receive a request to create a legacy coverage artifact.
        manifest = copy.deepcopy(current.manifest)
        manifest['builder_config']['coverage_state_policy'] = LEGACY_POLICY
        for key in ('source_completeness_binding', 'writable_contracts_digest'):
            manifest['builder_config'].pop(key, None)
        manifest['builder_config_digest'] = _digest(_json_bytes(manifest['builder_config']))
        manifest['source_coverage'] = state(None, set(), [observation(raw, policy=LEGACY_POLICY)], policy=LEGACY_POLICY)
        manifest['identity_digest'] = _identity_digest(manifest, 'domain_commit_id')
        identity = _derived_identity('holder_count_events', manifest['identity_digest'])
        manifest['domain_commit_id'] = identity
        base = self.root/'canonical/holder_count_events/commits'
        target = base/identity
        shutil.copytree(base/current_ref.commit_id, target)
        for name in ('manifest.json', 'manifest.sha256'):
            (target/name).chmod(0o644)
        rewrite_manifest(target, manifest)
        frozen_bytes = (target/'manifest.json').read_bytes()
        legacy = validate_domain_commit_closure(self.root, 'holder_count_events', identity)
        self.assertEqual(legacy.manifest['source_coverage'], manifest['source_coverage'])
        self.assertEqual(legacy.rows, current.rows)
        extension = source_completeness._extension()
        extension['endpoints'].pop('stk_holdernumber')
        with patch.object(source_completeness, '_extension', return_value=extension):
            self.assertEqual(validate_domain_commit_closure(self.root, 'holder_count_events', identity).manifest,
                             legacy.manifest)
        self.assertEqual((target/'manifest.json').read_bytes(), frozen_bytes)
        with self.assertRaisesRegex(ArtifactError, 'source coverage upgrade requires a new lineage'):
            self.build(raw_ref, identity)

    def test_direct_v2_build_requalifies_legacy_parent_before_inheriting_rows(self):
        from unittest.mock import patch
        from axiom_data import source_completeness
        parent = self.reader.commits['holder_count_events'].ref
        incoming = self.raw('20250611', [])
        extension = source_completeness._extension()
        extension['endpoints']['stk_holdernumber']['limit'] = 1
        self.config['coverage_state_policy'] = 'source_observations.v2'
        with patch.object(source_completeness, '_extension', return_value=extension):
            # Frozen replay remains legal, while new builds use current admission.
            self.assertEqual(validate_domain_commit_closure(
                self.root, 'holder_count_events', parent.commit_id).ref.commit_id, parent.commit_id)
            self.assertTrue(source_completeness.validate_raw_completeness(
                load_raw_batch(self.root, incoming.raw_batch_id))['complete'])
            from axiom_data.operations import _requalify_sources
            with self.assertRaisesRegex(ArtifactError, 'possibly truncated source payload'):
                _requalify_sources(self.root, {'holder_count_events': parent.commit_id})
            with self.assertRaisesRegex(ArtifactError, 'source coverage upgrade requires a new lineage'):
                self.build(incoming, parent.commit_id)

    def test_current_bootstrap_cannot_reuse_legacy_policy_admission(self):
        from unittest.mock import patch
        from axiom_data import source_completeness
        from axiom_data.operations import bootstrap
        commit = self.reader.commits['holder_count_events'].ref
        extension = source_completeness._extension()
        extension['endpoints'].pop('stk_holdernumber')
        with patch.object(source_completeness, '_extension', return_value=extension):
            # The published fixture retains its frozen validation projection.
            old = validate_domain_commit_closure(self.root, 'holder_count_events', commit.commit_id)
            self.assertEqual(old.ref.commit_id, commit.commit_id)
            result = bootstrap(self.root, run_id='current-policy-required',
                               domain_commit_ids=dict(self.ids, holder_count_events=commit.commit_id))
        self.assertEqual(result['status'], 'FAILED')
        self.assertNotIn('snapshot_id', result)

    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name); self.root = self.directory/'data'
        run = json.loads(Path('reports/pr7/run_manifest.json').read_bytes())
        shutil.copytree(run['source_root'], self.root)
        self.addCleanup(self.writable)
        self.reader = SnapshotReader(self.root, run['refs']['snapshot_id'])
        self.ids = {d:c.ref.commit_id for d,c in self.reader.commits.items()}
        self.config = dict(self.reader.commits['holder_count_events'].manifest['builder_config'],
                           no_change_policy='reuse_equal_state.v1', coverage_state_policy='source_observations.v2')

    def writable(self):
        for p in self.directory.rglob('*'):
            if p.is_dir(): p.chmod(0o755)

    def raw(self, day, rows):
        return Pr7Collector(self.root, Client(rows)).collect('stk_holdernumber',
            {'ts_code':'688981.SH', 'start_date':day, 'end_date':day},
            retrieved_at='2026-09-12T00:00:00Z')

    def build(self, raw, parent=None, root=None):
        root = root or self.root
        builder = Pr7Builder(root, 'holder_count_events', builder_config=self.config,
                            dependency_commit_ids={'security_master':self.ids['security_master']})
        return BuildApplication('holder_count_events', builder).build(
            parent, [raw.raw_batch_id], [], 'holder_count_events.v1')

    def snapshot(self, commit):
        ref = create_snapshot(self.root, dict(self.ids, holder_count_events=commit.commit_id))
        return SnapshotReader(self.root, ref.snapshot_id)

    def test_empty_complete_expands_identity_scope_and_exact_offline_replay(self):
        first = self.raw('20250610', [dict(ts_code='688981.SH',ann_date='20250610',
                         end_date='20250331',holder_num=200)])
        parent = self.build(first); old = self.snapshot(parent)
        original_manifest = old.snapshot.manifest
        with self.assertRaisesRegex(ArtifactError, 'INSUFFICIENT_SCOPE'):
            request_coverage(old, 'holder_count_events', '688981.SH', '2025-06-11')
        empty = self.raw('20250611', [])
        child = self.build(empty, parent.commit_id); new = self.snapshot(child)
        self.assertNotEqual(parent.commit_id, child.commit_id)
        old_commit = old.commits['holder_count_events']; new_commit = new.commits['holder_count_events']
        self.assertEqual(old_commit.rows, new_commit.rows)
        self.assertEqual(old_commit.manifest['logical_content_digest'], new_commit.manifest['logical_content_digest'])
        coverage = new_commit.manifest['source_coverage']; observation = coverage['observations'][0]
        self.assertTrue(observation['payload_admission']['complete'])
        self.assertTrue(observation['empty_result']); self.assertEqual(observation['row_count'], 0)
        self.assertEqual(observation['request']['params']['start_date'], '20250611')
        self.assertNotEqual(coverage['state_digest'], old_commit.manifest['source_coverage']['state_digest'])
        fact = leaf_facts(new, 'holder.number', symbol='688981.SH', target_session='2025-06-11',
                         knowledge_cutoff='2026-09-13T00:00:00Z', pit_policy='operational_pit_v1')
        self.assertEqual(fact['value'], 200)  # Contract: empty request adds coverage, not a holder exit.
        self.assertEqual(self.build(empty, child.commit_id).commit_id, child.commit_id)
        self.assertEqual(self.build(empty, parent.commit_id).commit_id, child.commit_id)
        offline = self.directory/'offline'; shutil.copytree(self.root, offline)
        target = offline/'canonical/holder_count_events/commits'/child.commit_id
        for p in target.rglob('*'):
            if p.is_dir(): p.chmod(0o755)
        target.chmod(0o755); target.parent.chmod(0o755); shutil.rmtree(target)
        self.assertEqual(self.build(empty, parent.commit_id, offline).commit_id, child.commit_id)
        self.assertEqual(validate_domain_commit_closure(offline, 'holder_count_events', child.commit_id).manifest['source_coverage'], coverage)
        old_again = SnapshotReader(self.root, old.snapshot.ref.snapshot_id)
        self.assertEqual(old_again.snapshot.manifest, original_manifest)
        with self.assertRaisesRegex(ArtifactError, 'INSUFFICIENT_SCOPE'):
            request_coverage(old_again, 'holder_count_events', '688981.SH', '2025-06-11')

    def test_empty_partial_or_truncated_is_retained_but_not_admitted(self):
        good = load_raw_batch(self.root, self.raw('20250611', []).raw_batch_id)
        for suffix, status, summary in [('partial','partial',{}), ('truncated','success',{'truncated':True}),
                                        ('incomplete','success',{'complete':False})]:
            m = good.manifest
            ref = write_raw_batch(self.root, 'coverage-'+suffix, domain='holder_count_events',
                source_profile=m['source_profile_ref'], source_profile_version=m['source_profile_version'],
                source_profile_digest=m['source_profile_digest'], request=m['request'],
                retrieved_at=m['retrieved_at'], payload=good.payload, collector_code=m['collector_code_ref'],
                status=status, summary=summary)
            with self.assertRaises(ArtifactError): self.build(ref)
            self.assertEqual(load_raw_batch(self.root, ref.raw_batch_id).payload, b'[]')

    def test_legacy_row_equality_cannot_discard_new_empty_raw(self):
        self.config.pop('coverage_state_policy')
        first = self.raw('20250610', [])
        parent = self.build(first)
        child = self.build(self.raw('20250611', []), parent.commit_id)
        self.assertNotEqual(parent.commit_id, child.commit_id)

    def test_rehashed_forged_coverage_is_rejected_against_immutable_raw(self):
        import copy
        from axiom_data.artifacts import _digest, _json_bytes, _identity_digest, _derived_identity
        ref = self.build(self.raw('20250611', []))
        commit = validate_domain_commit_closure(self.root,'holder_count_events',ref.commit_id)
        manifest = copy.deepcopy(commit.manifest)
        manifest['source_coverage']['observations'][0]['request']['params']['end_date'] = '20250630'
        manifest['identity_digest'] = _identity_digest(manifest,'domain_commit_id')
        identity = _derived_identity('holder_count_events',manifest['identity_digest'])
        manifest['domain_commit_id'] = identity
        base = self.root/'canonical/holder_count_events/commits'
        shutil.copytree(base/ref.commit_id,base/identity)
        payload = _json_bytes(manifest)
        for name, content in [('manifest.json',payload),('manifest.sha256',_digest(payload).encode())]:
            path = base/identity/name; path.chmod(0o644); path.write_bytes(content)
        with self.assertRaisesRegex(ArtifactError,'coverage differs from validated Raw'):
            validate_domain_commit_closure(self.root,'holder_count_events',identity)


class CoveragePolicyTransitionTest(unittest.TestCase):
    def test_same_raw_is_revalidated_on_v1_to_v2_and_v2_replay_is_stable(self):
        from types import SimpleNamespace
        from axiom_data.source_coverage import state, POLICY, LEGACY_POLICY
        from axiom_data.artifacts import _digest, _json_bytes
        raw_id = 'same-immutable-raw'
        legacy_observation = {'raw_ref': {'raw_batch_id': raw_id},
                              'payload_admission': {'complete': True, 'policy_digest': 'old'}}
        old = state(None, set(), [legacy_observation], policy=LEGACY_POLICY)
        self.assertEqual(old['state_digest'], _digest(_json_bytes({
            'parent_coverage_digest': None, 'observations': [legacy_observation]})))
        parent = SimpleNamespace(manifest={'source_coverage': old})
        revised = dict(legacy_observation, payload_admission={
            'complete': True, 'policy_digest': 'new', 'coverage_semantics': 'bounded_date_observation'})
        upgraded = state(parent, {raw_id}, [revised], policy=POLICY)
        self.assertNotEqual(upgraded['state_digest'], old['state_digest'])
        self.assertEqual(upgraded['observations'], [revised])
        self.assertEqual(upgraded['parent_coverage_digest'], old['state_digest'])
        different = dict(revised, payload_admission={'complete': True, 'policy_digest': 'different'})
        self.assertNotEqual(upgraded['state_digest'], state(parent, {raw_id}, [different])['state_digest'])
        replay = state(SimpleNamespace(manifest={'source_coverage': upgraded}), {raw_id}, [revised])
        self.assertEqual(replay['state_digest'], upgraded['state_digest'])
        self.assertEqual(replay['observations'], [])
        self.assertEqual(state(parent, {raw_id}, [legacy_observation], policy=LEGACY_POLICY)['state_digest'],
                         old['state_digest'])

    def test_same_v2_raw_revalidated_when_effective_contract_changes(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from axiom_data.source_coverage import state
        item = {'raw_ref': {'raw_batch_id': 'same-raw'},
                'payload_admission': {'complete': True, 'policy_digest': 'revision-one'}}
        with patch('axiom_data.source_completeness.current_contract_binding', return_value={'revision': 1}):
            first = state(None, set(), [item])
        parent = SimpleNamespace(manifest={'source_coverage': first})
        revised = dict(item, payload_admission={'complete': True, 'policy_digest': 'revision-two'})
        with patch('axiom_data.source_completeness.current_contract_binding', return_value={'revision': 2}):
            second = state(parent, {'same-raw'}, [revised])
            replay = state(SimpleNamespace(manifest={'source_coverage': second}), {'same-raw'}, [revised])
        self.assertNotEqual(first['policy_binding_digest'], second['policy_binding_digest'])
        self.assertNotEqual(first['state_digest'], second['state_digest'])
        self.assertEqual(second['observations'], [revised])
        self.assertEqual(second['state_digest'], replay['state_digest'])
