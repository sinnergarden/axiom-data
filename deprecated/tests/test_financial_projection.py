"""Validate complete membership state before any security projection."""
import copy
import unittest
from fixture_locations import fixture_root
from dataclasses import replace

from axiom_data import SnapshotReader, validate_domain_commit_closure
from axiom_data.artifacts import ArtifactError
from axiom_data.domains.market import MarketContractError
import test_financial_final_probes as fixtures

ROOT=fixture_root('/home/liuming/workspace/axiom/data/forensic/pr6-empty-prefix-compat-20260908-r4/source')
SNAPSHOT='snapshot-8bc43796731f5fd14961d6beff78d74b46eacfb158999e12bf38ce859407548e'
ARGS={'pit_policy':'best_effort_vendor_v1','knowledge_cutoff':'2025-07-01T00:00:00Z'}
DOMAIN='universe_membership'


class MembershipProjectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reader=SnapshotReader(ROOT,SNAPSHOT)

    def test_real_full_and_688981_projection(self):
        complete=self.reader.as_of(DOMAIN,**ARGS)
        expected=tuple(r for r in complete if r['symbol']=='688981.SH')
        self.assertEqual(len(complete),12);self.assertEqual(len(expected),2)
        self.assertEqual(self.reader.as_of(DOMAIN,symbols=['688981.SH'],**ARGS),expected)

    def test_multiple_symbols_projection(self):
        for policy,cutoff in [('best_effort_vendor_v1',ARGS['knowledge_cutoff']),
                              ('operational_pit_v1','2026-10-01T00:00:00Z')]:
            args={'pit_policy':policy,'knowledge_cutoff':cutoff}
            complete=self.reader.as_of(DOMAIN,**args)
            symbols=['688981.SH','600036.SH','000062.SZ']
            expected=tuple(r for r in complete if r['symbol'] in symbols)
            self.assertEqual(self.reader.as_of(DOMAIN,symbols=symbols,**args),expected)

    def test_known_nonmember_and_exit_are_empty(self):
        self.assertEqual(self.reader.as_of(DOMAIN,symbols=['301581.SZ'],**ARGS),())
        self.assertEqual(len(self.reader.members('000906.SH','2025-05-30',symbols=['000401.SZ'],**ARGS)),1)
        self.assertEqual(self.reader.members('000906.SH','2025-06-30',symbols=['000401.SZ'],**ARGS),())

    def test_unknown_security_keeps_admission_error(self):
        with self.assertRaisesRegex(ArtifactError,'INSUFFICIENT_SCOPE'):
            self.reader.as_of(DOMAIN,symbols=['999999.SH'],**ARGS)
        with self.assertRaisesRegex(ArtifactError,'INSUFFICIENT_SCOPE'):
            self.reader.members('000906.SH','2025-06-30',symbols=['999999.SH'],**ARGS)

    def test_missing_unrequested_member_still_fails(self):
        reader=copy.copy(self.reader);reader.commits=dict(reader.commits)
        original=reader.commits[DOMAIN]
        # Simulate loss after artifact loading: the selector must independently
        # reject the complete group even though the requested security is intact.
        reader.commits[DOMAIN]=replace(original,rows=tuple(r for r in original.rows if r['symbol']!='600036.SH'))
        with self.assertRaisesRegex(MarketContractError,'member closure mismatch'):
            reader.as_of(DOMAIN,symbols=['688981.SH'],**ARGS)
        with self.assertRaisesRegex(MarketContractError,'member closure mismatch'):
            reader.members('000906.SH','2025-06-30',symbols=['688981.SH'],**ARGS)

    def test_empty_correction_and_return_with_projection(self):
        fixture=fixtures.FinalProbes('test_universe_empty_state');fixture.setUp();self.addCleanup(fixture.doCleanups)
        first=fixture.universe_build([fixture.universe_raw('000906.SH',['000001.SZ','000002.SZ'],'2025-01-02T00:00:00Z')])
        empty=fixture.universe_build([fixture.universe_raw('000906.SH',[],'2025-02-02T00:00:00Z')],first.ref.commit_id)
        returned=fixture.universe_build([fixture.universe_raw('000906.SH',['000001.SZ'],'2025-04-02T00:00:00Z')],empty.ref.commit_id)
        reader=SnapshotReader.__new__(SnapshotReader)
        reader.commits={'security_master':validate_domain_commit_closure(fixture.root,'security_master',fixture.security.commit_id)}
        for commit,count in [(first,1),(empty,0),(returned,1)]:
            reader.commits[DOMAIN]=commit
            for policy in ['operational_pit_v1','best_effort_vendor_v1']:
                args={'pit_policy':policy,'knowledge_cutoff':'2025-06-01T00:00:00Z','symbols':['000001.SZ']}
                self.assertEqual(len(reader.as_of(DOMAIN,**args)),count)
                self.assertEqual(len(reader.members('000906.SH','2025-05-01',**args)),count)
