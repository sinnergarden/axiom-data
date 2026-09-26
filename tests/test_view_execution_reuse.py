"""One isolated matrix for prerequisite admission and immutable reuse."""
import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from contextlib import ExitStack

from axiom_data import SnapshotReader, materialize_views
from axiom_data import frozen_execution
from axiom_data.operations import repair
from axiom_data.artifacts import _json_bytes
import test_admission_plan


class ViewExecutionReuseTest(unittest.TestCase):
    def setUp(self):
        source=test_admission_plan.AdmissionPlanTest();source.setUp()
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)/'data'
        shutil.copytree(source.root,self.root);self.snapshot=source.snapshot
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(lambda:[p.chmod(0o755) for p in self.root.rglob('*') if p.is_dir()])
        geometry=dict(symbols=['688981.SH'],start_session='2025-06-10',end_session='2025-06-13')
        self.plan={k:dict(kind=k,config=dict(c,**geometry)) for k,c in source.plan['required_view_configs'].items()}
        token=frozen_execution._active.set(frozen_execution.capture_code(self.root))
        self.addCleanup(frozen_execution._active.reset,token)

    def run_views(self,run,snapshot=None,plan=None):
        result=materialize_views(self.root,run_id=run,snapshot_id=snapshot or self.snapshot,views=plan or self.plan)
        return result

    def changed_snapshot(self,domain):
        reader=SnapshotReader(self.root,self.snapshot);commit=reader.commits[domain]
        # Synthetic provenance-only mutation: identical canonical bytes and Raw,
        # but a new immutable parent link. Public Snapshot validation must accept it.
        from axiom_data.artifacts import _commit_ref, _identity_digest, _derived_identity, _write_manifest, create_snapshot
        manifest=copy.deepcopy(commit.manifest)
        manifest['parent_commit_ref']=_commit_ref(commit)
        digest=_identity_digest(manifest,'domain_commit_id')
        identity=_derived_identity(domain,digest)
        manifest.update(domain_commit_id=identity,identity_digest=digest)
        source=self.root/'canonical'/domain/'commits'/commit.ref.commit_id
        target=source.parent/identity
        shutil.copytree(source,target)
        target.chmod(0o755)
        for name in ('manifest.json','manifest.sha256'):
            (target/name).chmod(0o644);(target/name).unlink()
        _write_manifest(target,manifest)
        refs={d:c.ref.commit_id for d,c in reader.commits.items()};refs[domain]=identity
        snapshot=create_snapshot(self.root,refs)
        self.assertNotEqual(snapshot.snapshot_id,self.snapshot)
        return snapshot.snapshot_id

    def test_five_family_rebind_matches_fresh_and_resume(self):
        first=self.run_views('first');self.assertEqual(first['status'],'VIEWS_BUILT',first)
        changed=self.changed_snapshot('benchmark_daily')
        candidates={k:dict(v,reuse_candidate=first['published_views'][k]) for k,v in self.plan.items()}
        with patch('axiom_data.financial_views.project',side_effect=AssertionError('financial projection on reuse')), \
             patch('axiom_data.event_views.project',side_effect=AssertionError('event projection on reuse')), \
             patch('axiom_data.views._build_adjusted_price_view',side_effect=AssertionError('adjusted projection on reuse')), \
             patch('axiom_data.views._build_market_replay_view',side_effect=AssertionError('replay projection on reuse')), \
             patch('axiom_data.consumption._build_qlib_view',side_effect=AssertionError('qlib projection on reuse')), \
             patch('axiom_data.consumption.SnapshotReader',wraps=SnapshotReader) as readers:
            reused=self.run_views('reuse',changed,candidates)
        self.assertEqual(reused['status'],'VIEWS_BUILT',reused)
        self.assertEqual(set(reused.get('reused_views',[])),set(self.plan))
        self.assertEqual(readers.call_count,2) # one target and one shared candidate Snapshot
        fresh=self.run_views('fresh',changed)
        self.assertEqual(fresh['status'],'VIEWS_BUILT',fresh)
        for label in self.plan:
            self.assertEqual(reused['published_views'][label],fresh['published_views'][label])
            self.assertNotEqual(first['published_views'][label]['view_id'],fresh['published_views'][label]['view_id'])
        resumed=self.run_views('reuse',changed,candidates)
        self.assertEqual(resumed['published_views'],reused['published_views'])

    def test_later_bad_family_prevents_first_publication(self):
        mutations={
            'adjusted_price':dict(anchor_session='2025-06-09'),
            'market_replay':dict(symbols=['999999.SH']),
            'market_qlib':dict(adjusted_price_view_id='missing',price_basis='anchor_adjusted'),
            'pr6_fact':dict(universe_ids=['missing']),
            'pr7_fact':dict(end_session='2025-07-01')}
        for index,(kind,mutation) in enumerate(mutations.items()):
            plan={'first':copy.deepcopy(self.plan['market_qlib']),'later':copy.deepcopy(self.plan[kind])}
            plan['later']['config'].update(mutation)
            with self.subTest(kind=kind):
                result=self.run_views('bad-'+str(index),plan=plan)
                self.assertEqual(result['status'],'FAILED',result)
                self.assertEqual(result['published_views'],{})
                self.assertTrue(result['failed'])

    def test_relevant_provenance_and_request_changes_use_normal_build(self):
        first=self.run_views('first');self.assertEqual(first['status'],'VIEWS_BUILT',first)
        changed=self.changed_snapshot('adjustment_factors')
        old=SnapshotReader(self.root,self.snapshot);new=SnapshotReader(self.root,changed)
        self.assertEqual(tuple(old.commits['adjustment_factors'].rows),tuple(new.commits['adjustment_factors'].rows))
        candidate=dict(self.plan['adjusted_price'],reuse_candidate=first['published_views']['adjusted_price'])
        from axiom_data.views import _build_adjusted_price_view
        with patch('axiom_data.views._build_adjusted_price_view',wraps=_build_adjusted_price_view) as build:
            result=self.run_views('provenance',changed,{'adjusted':candidate})
        self.assertEqual(result['status'],'VIEWS_BUILT',result);self.assertEqual(build.call_count,1)
        self.assertFalse(result.get('reused_views'))
        for index,change in enumerate((dict(end_session='2025-06-12',anchor_session='2025-06-12'),
                                      dict(decision_cutoff='2025-06-12'))):
            spec=copy.deepcopy(candidate);spec['config'].update(change)
            result=self.run_views('config-'+str(index),plan={'adjusted':spec})
            self.assertEqual(result['status'],'VIEWS_BUILT',result)
            self.assertFalse(result.get('reused_views'))
        spec=copy.deepcopy(candidate);spec['config'].update(pit_policy='strict_decision_time')
        result=self.run_views('strict',plan={'adjusted':spec})
        self.assertEqual(result['status'],'FAILED',result)
        self.assertEqual(result['published_views'],{})

    def test_bad_candidate_and_changed_code_do_not_reuse(self):
        first=self.run_views('first');changed=self.changed_snapshot('benchmark_daily')
        candidate=dict(self.plan['market_qlib'],reuse_candidate=first['published_views']['market_qlib'])
        old=self.root/'exports/qlib'/candidate['reuse_candidate']['view_id']/'calendars/day.txt'
        old.chmod(0o644);old.write_bytes(b'corrupt')
        result=self.run_views('bad-candidate',changed,{'qlib':candidate})
        self.assertEqual(result['status'],'VIEWS_BUILT',result);self.assertFalse(result.get('reused_views'))
        from axiom_data.view_operation import _reuse_view
        from axiom_data.views import build_adjusted_price_view,_load_adjusted_price_view
        candidate=dict(self.plan['adjusted_price'],reuse_candidate=first['published_views']['adjusted_price'])
        with patch('axiom_data.frozen_execution.executed_code_ref',return_value={'different':'code'}):
            self.assertIsNone(_reuse_view(SnapshotReader(self.root,changed),candidate,
                build_adjusted_price_view,_load_adjusted_price_view,{},{}))

    def test_shared_preparation_error_names_scope_and_retains_message(self):
        from axiom_data import ArtifactError
        plan={str(i):copy.deepcopy(self.plan['pr7_fact']) for i in range(2)}
        with patch('axiom_data.event_views.event_view_batch',side_effect=ArtifactError('source request context')):
            result=self.run_views('shared',plan=plan)
        self.assertEqual(result['status'],'FAILED');self.assertEqual(result['published_views'],{})
        self.assertEqual(set(result['failed']),{'0','1'})
        for failure in result['failed'].values():
            self.assertEqual(failure['stage'],'shared_preparation')
            self.assertEqual(failure['reason'],'source request context')
            self.assertEqual(failure['scope']['labels'],['0','1'])

    def test_public_frozen_invocation_accepts_candidates_and_existing_target(self):
        token=frozen_execution._active.set(None)
        try:
            first=self.run_views('public-first')
            self.assertEqual(first['status'],'VIEWS_BUILT',first)
            changed=self.changed_snapshot('benchmark_daily')
            fresh=self.run_views('public-fresh',changed)
            candidates={k:dict(v,reuse_candidate=first['published_views'][k]) for k,v in self.plan.items()}
            reused=self.run_views('public-reuse',changed,candidates)
            self.assertEqual(reused['status'],'VIEWS_BUILT',reused)
            self.assertEqual(reused['published_views'],fresh['published_views'])
            self.assertEqual(set(reused['reused_views']),set(self.plan))
            self.assertEqual(self.run_views('public-first')['published_views'],first['published_views'])
        finally:
            frozen_execution._active.reset(token)
