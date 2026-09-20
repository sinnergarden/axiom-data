"""History reuse preserves full-scope admission and every daily PIT result."""
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from axiom_data import SnapshotReader
from axiom_data.artifacts import _json_bytes
from axiom_data.domains.market import MarketContractError
from axiom_data.pit import instant
from axiom_data.pr6_views import project
from fixture_locations import fixture_root


class ViewEventHistoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        run=json.loads(Path('reports/pr7/run_manifest.json').read_bytes())
        cls.root=fixture_root(run['source_root'])
        cls.snapshot=run['refs']['snapshot_id']
        cls.view=cls.root/'derived/pr6_fact/commits'/run['refs']['pr6_view_id']
        cls.manifest=json.loads((cls.view/'manifest.json').read_bytes())

    def test_frozen_full_payload_and_daily_selectors_match(self):
        reader=SnapshotReader(self.root,self.snapshot)
        m=self.manifest;scope=m['scope'];policy=m['pit_policy'];cutoff=m['knowledge_cutoff']
        with patch.object(reader,'facts',wraps=reader.facts) as prepared:
            actual=project(reader,scope,policy,cutoff)
        self.assertEqual(_json_bytes(actual),(self.view/'rows.json').read_bytes())
        calls=[c for c in prepared.call_args_list if c.args[0]=='financial_events']
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0].kwargs,{'symbols':scope['symbols']})
        events=[];derived=[]
        for session in actual['sessions']:
            effective=min(instant(cutoff),instant(session+'T23:59:59+08:00')).isoformat()
            args=dict(symbols=scope['symbols'],pit_policy=policy,knowledge_cutoff=effective)
            events.extend(dict(r,target_session=session) for r in reader.as_of('financial_events',**args))
            derived.extend(dict(r,target_session=session) for r in reader.financial_derived(**args))
        self.assertEqual(actual['events'],events)
        self.assertEqual(actual['derived'],derived)

    def test_out_of_scope_financial_conflict_rejects_before_prefetch(self):
        from dataclasses import replace
        reader=SnapshotReader(self.root,self.snapshot)
        m=self.manifest
        scope=dict(m['scope'],symbols=[m['scope']['symbols'][0]])
        commit=reader.commits['financial_events']
        rows=list(commit.rows)
        original=next(r for r in rows if r['symbol'] not in scope['symbols'])
        conflict=copy.deepcopy(original)
        conflict['revision_id']='conflicting-outside-request'
        for observation in conflict.get('observations',[]):
            observation['revision_id']=conflict['revision_id']
        reader.commits['financial_events']=replace(commit,rows=tuple(rows+[conflict]))
        with patch.object(reader,'facts',wraps=reader.facts) as prepared:
            with self.assertRaisesRegex(MarketContractError,'ambiguous simultaneous'):
                project(reader,scope,m['pit_policy'],m['knowledge_cutoff'])
        self.assertFalse(any(c.args[0]=='financial_events' for c in prepared.call_args_list))

    def test_event_ambiguity_matches_direct_selector(self):
        from axiom_data.artifacts import ArtifactError
        from axiom_data.pr7_views import project as project_events
        from test_pr7_projection import Reader,report
        reader=Reader(rows=[report('2025-03-31',100,'2025-04-10T00:00:00Z'),
                            report('2025-03-31',200,'2025-04-10T00:00:00Z')])
        scope=dict(symbols=['600000.SH'],start_session='2025-06-02',end_session='2025-06-04')
        args=dict(symbols=scope['symbols'],pit_policy='operational_pit_v1',knowledge_cutoff='2025-06-04T23:59:59+08:00')
        with self.assertRaisesRegex(ArtifactError,'ambiguous simultaneous PR7'):
            reader.as_of('holder_count_events',**args)
        with patch('axiom_data.pr7_views.request_coverage'):
            with self.assertRaisesRegex(ArtifactError,'ambiguous simultaneous PR7'):
                project_events(reader,scope,args['pit_policy'],args['knowledge_cutoff'])
