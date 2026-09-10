"""Complete acquisition visibility, missing observations, and incremental closure."""
import unittest
from axiom_data import BuildApplication, validate_domain_commit_closure
from axiom_data.artifacts import ArtifactError
from axiom_data.pr6_source import Pr6Builder, Pr6Collector
from axiom_data.pit import members, select_group_states
import test_pr6_final_probes as fixtures
from test_pr6_artifacts import Client

class UniverseAcquisitionTest(unittest.TestCase):
    def setUp(self):
        fixtures.FinalProbes.setUp(self)

    def raw(self, start, end, observed, symbols, group='000906.SH', complete=True):
        rows=[dict(index_code=group,con_code=s,trade_date=start,weight=1) for s in symbols]
        return Pr6Collector(self.root,Client(rows)).collect('index_weight',
            dict(index_code=group,start_date=start,end_date=end),retrieved_at=observed,
            membership_complete=complete)

    def build(self, initial, inputs=None, parent=None):
        builder=Pr6Builder(self.root,'universe_membership',
            dependency_commit_ids={'security_master':self.security.commit_id},
            builder_config={'membership_end_exclusive':'2026-01-01',
                'universe_acquisition':{'policy':'complete_bootstrap.v1',
                    'raw_batch_ids':[r.raw_batch_id for r in initial]}})
        ref=BuildApplication('universe_membership',builder).build(parent,
            [r.raw_batch_id for r in (initial if inputs is None else inputs)],[],'universe_membership.v3')
        return validate_domain_commit_closure(self.root,'universe_membership',ref.commit_id)

    def selected(self, commit, day, cutoff='2025-12-01T00:00:00Z', policy='operational_pit_v1'):
        return tuple(r['symbol'] for r in members(commit.rows,group_id='000906.SH',
            target_session=day,knowledge_cutoff=cutoff,policy=policy,
            group_states=commit.manifest['group_states']))

    def test_complete_acquisition_incremental_empty_and_reentry(self):
        a=self.raw('20250101','20250131','2025-04-01T00:00:00Z',['000001.SZ'])
        b=self.raw('20250201','20250228','2025-04-02T00:00:00Z',['000002.SZ'])
        old=self.build([a,b])
        self.assertEqual(len(old.manifest['group_states']),1)
        self.assertEqual(select_group_states(old.manifest['group_states'],policy='operational_pit_v1',knowledge_cutoff='2025-04-01T12:00:00Z'),())
        self.assertEqual(self.selected(old,'2025-01-15'),('000001.SZ',))
        self.assertEqual(self.selected(old,'2025-03-01'),('000002.SZ',))
        self.assertTrue(all('state_raw_refs' not in o for r in old.rows for o in r['observations']))
        empty=self.raw('20250201','20250201','2025-05-01T00:00:00Z',[])
        correction=self.build([a,b],[empty],old.ref.commit_id)
        self.assertEqual(self.selected(correction,'2025-03-01'),())
        self.assertEqual(self.selected(correction,'2025-03-01','2025-04-15T00:00:00Z'),('000002.SZ',))
        back=self.raw('20250301','20250301','2025-06-01T00:00:00Z',['000001.SZ'])
        final=self.build([a,b],[back],correction.ref.commit_id)
        replay=self.build([a,b],[back,b,empty,a])
        self.assertEqual(final.rows,replay.rows)
        self.assertEqual(final.manifest['group_states'],replay.manifest['group_states'])
        for policy in ['operational_pit_v1','best_effort_vendor_v1']:
            self.assertEqual(self.selected(final,'2025-03-15',policy=policy),('000001.SZ',))

    def test_empty_ranged_request_is_not_empty_state(self):
        a=self.raw('20250101','20250131','2025-04-01T00:00:00Z',['000001.SZ'])
        gap=self.raw('20250201','20250228','2025-04-02T00:00:00Z',[],complete=False)
        c=self.build([a,gap])
        self.assertEqual(self.selected(c,'2025-03-01'),('000001.SZ',))
        state=c.manifest['group_states'][0]
        self.assertEqual(state['unobserved_requests'],[gap.raw_batch_id])
        self.assertEqual(len(state['intervals']),1)
        self.assertEqual(state['coverage_from'],'2025-01-01')
        with self.assertRaisesRegex(ArtifactError,'SOURCE_GAP'):
            self.build([gap])
        later=self.raw('20250301','20250331','2025-05-01T00:00:00Z',[],complete=False)
        with self.assertRaisesRegex(ArtifactError,'SOURCE_GAP'):
            self.build([a,gap],[later],c.ref.commit_id)

    def test_incomplete_overlapping_or_backdated_bundle_fails(self):
        a=self.raw('20250101','20250131','2025-04-01T00:00:00Z',['000001.SZ'])
        b=self.raw('20250201','20250228','2025-04-02T00:00:00Z',['000002.SZ'])
        with self.assertRaisesRegex(ArtifactError,'incomplete explicit'):
            self.build([a,b],[a])
        overlap=self.raw('20250115','20250131','2025-04-03T00:00:00Z',['000001.SZ'])
        with self.assertRaisesRegex(ArtifactError,'overlapping'):
            self.build([a,overlap])
        backdated=self.raw('20250301','20250331','2025-04-01T12:00:00Z',['000001.SZ'])
        with self.assertRaisesRegex(ArtifactError,'predates'):
            self.build([a,b],[a,b,backdated])
