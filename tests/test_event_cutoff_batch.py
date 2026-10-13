"""Multi-cutoff source reuse with the unchanged Data event selector."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
import tempfile
import unittest
from axiom_data import EventQuery, iter_event_cutoffs, DataError, QueryError
from axiom_data.event_reader import read_events
from axiom_data.storage import LocalStore
from test_bulk_event_processing import row, CONTRACT, PROFILE
from copy import deepcopy
from datetime import datetime, timezone


def fixture(store):
    rows=[]
    for i,day in enumerate((28,29),1):
        r=row('2020-03-31',i*100,'r'+str(i),i)
        r.update(first_observed_at=datetime(2026,9,day,tzinfo=timezone.utc),raw_batch_id='synthetic')
        rows.append(r)
    part=store.write_partition('financial_events','period-2020',rows,CONTRACT)
    return store.publish_snapshot({'financial_events':{'contract':deepcopy(CONTRACT),
        'source_profile':deepcopy(PROFILE),'partitions':[part],'raw_batch_ids':[],
        'coverage':{},'build_context':{'synthetic':True}}},parent_snapshot=None,
        build_context={'synthetic':True},promote=False)['snapshot_id']


class EventCutoffBatchTests(unittest.TestCase):
    def test_original_selector_equivalence_and_one_partition_read(self):
        with tempfile.TemporaryDirectory() as temp:
            store=LocalStore(temp)
            snapshot=fixture(store)
            q=EventQuery('financial_events',('revenue',),('sec-1',),'2019-01-01','2020-12-31',
                         '2026-09-28T23:00:00Z','operational_pit_v1','report_period')
            cutoffs=['2026-09-28T23:00:00Z','2026-09-29T23:00:00Z']
            expected=[read_events(store,snapshot,replace(q,cutoff=c)).to_json() for c in cutoffs]
            with patch.object(store,'read_partition',wraps=store.read_partition) as reads, \
                 patch.object(store,'load_snapshot',wraps=store.load_snapshot) as loads:
                actual=[b.to_json() for b in iter_event_cutoffs(SimpleNamespace(store=store),snapshot=snapshot,query=q,cutoffs=cutoffs)]
                self.assertEqual(loads.call_count,1)
                self.assertEqual(reads.call_count,len(store.load_snapshot(snapshot)['domains']['financial_events']['partitions']))
            self.assertEqual(actual,expected)
            with patch.object(store,'read_partition',side_effect=AssertionError('read before budget')):
                with self.assertRaisesRegex(QueryError,'budget exceeded before'):
                    list(iter_event_cutoffs(SimpleNamespace(store=store),snapshot=snapshot,query=q,cutoffs=cutoffs,maximum_cache_bytes=1))

    def test_mutation_between_cutoffs_is_rejected(self):
        for mutate in ('partition','snapshot'):
            with self.subTest(mutate=mutate),tempfile.TemporaryDirectory() as temp:
                store=LocalStore(temp)
                snapshot=fixture(store)
                q=EventQuery('financial_events',('revenue',),('sec-1',),'2019-01-01','2020-12-31',
                             '2026-09-28T23:00:00Z','operational_pit_v1','report_period')
                iterator=iter_event_cutoffs(SimpleNamespace(store=store),snapshot=snapshot,query=q,cutoffs=[q.cutoff,q.cutoff])
                next(iterator)
                path=(store._path(f'snapshots/{snapshot}.json') if mutate=='snapshot' else
                      store._path(store.load_snapshot(snapshot)['domains']['financial_events']['partitions'][0]['uri']))
                path.write_bytes(path.read_bytes()+b' ')
                with self.assertRaises((DataError,OSError)):
                    next(iterator)


if __name__=='__main__': unittest.main()
