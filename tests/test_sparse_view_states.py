"""State boundaries preserve daily PIT output without daily physical copies."""

import copy
import gzip
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from axiom_data import FactView
from axiom_data.artifacts import ArtifactError, _json_bytes
from axiom_data.consumption import QlibViewReader, feature_bytes
from axiom_data.domains.events import LEAF_DOMAINS, NUMERIC_FIELDS
from axiom_data.view_states import SparseDailyRows, encode_states, session_cutoff, unpacked_states


DAYS = ['2025-06-02', '2025-06-03', '2025-06-04', '2025-06-05', '2025-06-06']
CUTOFF = '2025-06-30T15:59:59+00:00'
SYMBOL = '600000.SH'


class SparseViewStatesTest(unittest.TestCase):
    def test_multi_security_public_reads_follow_requested_order(self):
        symbols=('688981.SH','600036.SH')
        days=DAYS[:2]
        for kind,fields,version in (('financial',('income.revenue',),'pr6_fact_view.v5'),
                                    ('event',LEAF_DOMAINS,'pr7_fact_view.v4')):
            with self.subTest(kind=kind):
                rows=[]
                for day in days:
                    for symbol in symbols:
                        facts={field:{'value':1 if symbol==symbols[0] else 2,
                                      'knowledge_cutoff':session_cutoff(CUTOFF,day),
                                      **({'target_session':day} if kind=='event' else {})}
                               for field in fields}
                        if kind=='financial':
                            rows.append({'session':day,'symbol':symbol,
                                         'values':{field:facts[field]['value'] for field in fields},
                                         'facts':facts,'provenance':{},
                                         'knowledge_cutoff':session_cutoff(CUTOFF,day)})
                        else:
                            rows.append({'session':day,'symbol':symbol,'facts':facts,
                                         'values':{field:facts[field]['value'] for field in NUMERIC_FIELDS}})
                states=encode_states(rows,sessions=days,
                    symbol_sessions={symbol:days for symbol in reversed(symbols)},
                    fields=fields,kind=kind,cutoff=CUTOFF)
                sparse=SparseDailyRows(states,symbols=symbols,fields=fields,kind=kind,cutoff=CUTOFF)
                manifest={'schema_version':version,'artifact_type':'pr6_fact_view' if kind=='financial'
                          else 'pr7_fact_view','scope':{'symbols':list(symbols),
                          'start_session':days[0],'end_session':days[-1]},
                          'validated_scope':{'symbols':list(symbols),
                          'start_session':days[0],'end_session':days[-1]},
                          'fields':list(fields if kind=='financial' else NUMERIC_FIELDS),
                          'snapshot_ref':{'snapshot_id':'snapshot-test'},
                          'industry_mapping':{}}
                qlib=object.__new__(QlibViewReader)
                qlib.view=SimpleNamespace(manifest=manifest,rows=sparse,
                                          ref=SimpleNamespace(view_id='view-test'))
                temporary_root=tempfile.TemporaryDirectory()
                self.addCleanup(temporary_root.cleanup)
                legacy=object.__new__(QlibViewReader)
                legacy.data_root=Path(temporary_root.name)
                legacy.path=legacy.data_root/'legacy'
                legacy.calendar=lambda: tuple(days)
                legacy_manifest=dict(manifest,schema_version=
                    'pr6_fact_view.v4' if kind=='financial' else 'pr7_fact_view.v3')
                legacy_manifest['instrument_storage_scope']=[
                    {'symbol':symbol,'storage_path':symbol,
                     'start_session':days[0],'end_session':days[-1],
                     'valid_sessions':days} for symbol in symbols]
                legacy.view=SimpleNamespace(manifest=legacy_manifest,rows=tuple(rows),
                                            ref=SimpleNamespace(view_id='view-test'))
                for symbol in symbols:
                    directory=legacy.path/symbol
                    directory.mkdir(parents=True)
                    for field in manifest['fields']:
                        values=[row['values'][field] for row in rows if row['symbol']==symbol]
                        (directory/f'{field}.day.bin').write_bytes(feature_bytes(0,values))
                for requested in (symbols,tuple(reversed(symbols))):
                    expected=[(day,symbol) for day in days for symbol in requested]
                    self.assertEqual([(r['session'],r['symbol']) for r in sparse.range(symbols=requested)],expected)
                    self.assertEqual([(r['session'],r['symbol']) for r in sparse.range(
                        days[0],days[0],requested)],expected[:2])
                    self.assertEqual(qlib.market_daily(include_missing=True,symbols=requested),
                                     legacy.market_daily(include_missing=True,symbols=requested))
                    self.assertEqual(qlib.market_daily(symbols=requested),
                                     legacy.market_daily(symbols=requested))
                    self.assertEqual([(r['session'],r['symbol']) for r in qlib.market_daily(
                        include_missing=True,symbols=requested)],sorted(expected))
                    self.assertEqual([r['symbol'] for r in qlib.as_of(days[0],symbols=requested)],
                                     sorted(requested))
                    self.assertEqual(qlib.as_of(days[0],symbols=requested),
                                     legacy.as_of(days[0],symbols=requested))
                    if kind=='financial':
                        manifest.update(pit_policy='best_effort_vendor_v1',cutoff_policy='session_end',
                            knowledge_cutoff=CUTOFF,pit_qualification='best_effort',domain_refs={},
                            actual_available_scope={},membership_ref={})
                        public=object.__new__(FactView)
                        public.financial=qlib.view
                        result=public.read('financial',symbols=requested,
                            start_session=days[0],end_session=days[-1])
                        self.assertEqual([(r['session'],r['symbol']) for r in result['rows']],expected)
                        self.assertEqual([(r['session'],r['symbol']) for r in result['facts']],expected)
                self.assertEqual([(r['session'],r['symbol']) for r in qlib.fact_metadata()['rows']],
                                 [(day,symbol) for day in days for symbol in symbols])
                self.assertEqual(qlib.fact_metadata()['rows'],legacy.fact_metadata()['rows'])
                self.assertEqual(qlib.market_daily(),legacy.market_daily())
                self.assertEqual(qlib.as_of(days[0]),legacy.as_of(days[0]))

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
