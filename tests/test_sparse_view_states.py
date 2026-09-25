"""State boundaries preserve daily PIT output without daily physical copies."""

import copy
import gzip
import unittest

from axiom_data.artifacts import ArtifactError, _json_bytes
from axiom_data.domains.events import LEAF_DOMAINS, NUMERIC_FIELDS
from axiom_data.view_states import SparseDailyRows, encode_states, session_cutoff, unpacked_states


DAYS = ['2025-06-02', '2025-06-03', '2025-06-04', '2025-06-05', '2025-06-06']
CUTOFF = '2025-06-30T15:59:59+00:00'
SYMBOL = '600000.SH'


class SparseViewStatesTest(unittest.TestCase):
    def test_financial_revision_ambiguity_and_membership_boundaries(self):
        fields=('income.revenue','universe.membership','industry.membership')
        rows=[]
        for i,day in enumerate(DAYS):
            revenue=100 if i<3 else 120 if i==3 else None
            values={'income.revenue':revenue,
                    'universe.membership':1 if i<2 or i==4 else 0,
                    'industry.membership':42 if i<2 else 43}
            facts={field:{'value':value,'revision_ref':'revision-2' if i>=3 else 'revision-1',
                          'missing_reason':'AMBIGUOUS_SOURCE_REVISION' if field=='income.revenue' and i==4 else None,
                          'knowledge_cutoff':session_cutoff(CUTOFF,day)}
                   for field,value in values.items()}
            rows.append({'session':day,'symbol':SYMBOL,'values':values,'facts':facts,
                         'provenance':{'income.revenue':facts['income.revenue']['revision_ref']},
                         'knowledge_cutoff':session_cutoff(CUTOFF,day)})
        encoded=encode_states(rows,sessions=DAYS,symbol_sessions={SYMBOL:DAYS},
                              fields=fields,kind='financial',cutoff=CUTOFF)
        stored=encoded['states'][SYMBOL]
        self.assertEqual(len(stored['income.revenue']),3)
        self.assertEqual(len(stored['universe.membership']),4)
        self.assertEqual(stored['income.revenue'][0]['effective_to'],'2025-06-05')
        selected=SparseDailyRows(encoded,symbols=[SYMBOL],fields=fields,kind='financial',cutoff=CUTOFF)
        self.assertEqual(tuple(selected),tuple(rows))
        self.assertEqual(selected[0],rows[0])
        self.assertEqual(selected[-1],rows[-1])
        alternate_gzip=gzip.compress(_json_bytes(encoded),compresslevel=1,mtime=0)
        self.assertEqual(unpacked_states(alternate_gzip,len(_json_bytes(encoded))),encoded)
        self.assertEqual(selected.range(DAYS[2],DAYS[3]),tuple(rows[2:4]))
        self.assertEqual(selected.range(DAYS[4],DAYS[4])[0]['facts']['income.revenue']['missing_reason'],
                         'AMBIGUOUS_SOURCE_REVISION')
        invalid=copy.deepcopy(encoded)
        invalid['states'][SYMBOL]['income.revenue'][1]['effective_from']='2025-06-06'
        with self.assertRaises(ArtifactError):
            SparseDailyRows(invalid,symbols=[SYMBOL],fields=fields,kind='financial',cutoff=CUTOFF)
        invalid=copy.deepcopy(encoded)
        invalid['states'][SYMBOL]['income.revenue'][0]['state']['fact']['value']='not numeric'
        with self.assertRaisesRegex(ArtifactError,'finite numeric'):
            SparseDailyRows(invalid,symbols=[SYMBOL],fields=fields,kind='financial',cutoff=CUTOFF)

    def test_event_long_unchanged_state_and_daily_source_changes(self):
        rows=[]
        for i,day in enumerate(DAYS):
            facts={field:{'value':i if field=='moneyflow.net' else 20 if field=='holder.number' and i>=3
                          else 10 if field=='holder.number' else 0 if field in NUMERIC_FIELDS else None,
                          'revision_ref':'holder-2' if field=='holder.number' and i>=3 else 'holder-1',
                          'missing_reason':None,'knowledge_cutoff':session_cutoff(CUTOFF,day),
                          'target_session':day} for field in LEAF_DOMAINS}
            rows.append({'symbol':SYMBOL,'session':day,'facts':facts,
                         'values':{field:facts[field]['value'] for field in NUMERIC_FIELDS}})
        encoded=encode_states(rows,sessions=DAYS,symbol_sessions={SYMBOL:DAYS},
                              fields=LEAF_DOMAINS,kind='event',cutoff=CUTOFF)
        self.assertEqual(len(encoded['states'][SYMBOL]['forecast.type']),1)
        self.assertEqual(len(encoded['states'][SYMBOL]['holder.number']),2)
        self.assertEqual(len(encoded['states'][SYMBOL]['moneyflow.net']),len(DAYS))
        selected=SparseDailyRows(encoded,symbols=[SYMBOL],fields=LEAF_DOMAINS,kind='event',cutoff=CUTOFF)
        self.assertEqual(tuple(selected),tuple(rows))
        self.assertEqual(selected.range(DAYS[3],DAYS[3]),(rows[3],))


if __name__ == '__main__':
    unittest.main()
