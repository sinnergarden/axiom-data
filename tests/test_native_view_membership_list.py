"""Observed membership list shape and the nine consumer request forms only."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq
from axiom_data import Data, EventQuery, QueryError, QuerySpec, open_native_view
from axiom_data.event_sources import CONTRACTS
from axiom_data.native_view import _ProjectionStore, _Writer, _replay
from axiom_data.reader import _object_size
from axiom_data.storage import LocalStore, _json_bytes
from axiom_data.vendor_membership import CONTRACT as MEMBERSHIP, PROFILE


MEASURED={}
SESSIONS=('2023-12-29','2024-01-02','2024-01-03','2024-01-04','2024-01-05',
    '2024-01-08','2024-01-09','2024-01-10','2024-01-11','2024-01-12',
    '2024-01-15','2024-01-16','2024-01-17','2024-01-18','2024-01-19',
    '2024-01-22','2024-01-23','2024-01-24','2024-01-25','2024-01-26')
SYMBOLS=('SIM_A','SIM_B')
LIST_TYPE=pa.list_(pa.field('element',pa.string()))


def limits(working=64*1048576):
    return dict(max_part_bytes=1048576,max_working_bytes=working,
                max_saved_bytes=16*1048576,max_rows_per_block=16)


def spec(name,keys,fields):
    return dict(contract_id=name+'.synthetic.v1',logical_key=keys,
        fields={n:dict(dtype=t,nullable=True) for n,t in fields.items()})


def fixture(root,*,dependency_values=None):
    """Public LocalStore, observed physical field names/types, invented values."""
    store=LocalStore(root);domains={}
    stamp='2023-12-01T00:00:00+00:00'
    def row(**values):
        return dict(revision_id='r1',revision_sequence=1,first_observed_at=stamp,
                    source_available_at=None,evidence_ref=None,**values)
    def add(name,contract,rows,*,profile=None,coverage=None,partition='history'):
        raw=store.write_raw(b'[]',request={'fixture_domain':name},
            source_profile={'id':'synthetic'},observed_at=stamp)
        rows=[dict(r,raw_batch_id=raw['batch_id']) for r in rows]
        part=store.write_partition(name,partition,rows,contract)
        domains[name]=dict(contract=contract,source_profile=profile or {'id':'synthetic',
            'availability':{'timezone':'Asia/Shanghai','session_release_time':'18:00:00'}},
            partitions=[part],raw_batch_ids=[raw['batch_id']],coverage=coverage or {},build_context={})
    coverage={'verified_through':'2024-01-26','complete_states':[
        dict(universe_id='csi300',state_id='old',source_snapshot_date='2023-12-01',
             complete=True,members=list(SYMBOLS),effective_from='2023-12-01',effective_to='2024-01-15',
             revision_id='state1',revision_sequence=1,first_observed_at=stamp,
             raw_batch_id='state-old',dependency_raw_batch_ids=['state-old','shared']),
        dict(universe_id='csi300',state_id='new',source_snapshot_date='2024-01-15',
             complete=True,members=['SIM_A'],effective_from='2024-01-15',effective_to=None,
             revision_id='state2',revision_sequence=2,first_observed_at='2024-01-15T00:00:00+00:00',
             raw_batch_id='state-new',dependency_raw_batch_ids=['state-new'])]}
    member=[row(security_id=s,universe_id='csi300',membership_id='m-'+s,
        effective_from='2023-12-01',effective_to=None if s=='SIM_A' else '2024-01-15',
        dependency_raw_batch_ids=[] if s=='SIM_A' else None) for s in SYMBOLS]
    # The selected correction has ordered, duplicate and nullable dependencies;
    # the older empty/null parent lists remain in the actual Arrow source.
    # Ordinary Reader remains the authority for merging/deduplicating them.
    member.append(dict(member[0],revision_id='r2',revision_sequence=2,
        first_observed_at='2024-01-03T00:00:00+00:00',dependency_raw_batch_ids=['源🙂','shared','源🙂',None]))
    if dependency_values is not None:
        member=[row(security_id='SIM_A',universe_id='csi300',membership_id=f'm-{i}',
            effective_from='2023-12-01',effective_to=None,dependency_raw_batch_ids=dependency_values)
            for i in range(64)]
    add('universe_membership',deepcopy(MEMBERSHIP),member,profile=deepcopy(PROFILE),coverage=coverage)
    if dependency_values is not None:
        return Data(store.root,cache_bytes=4*1048576),store.publish_snapshot(domains,
            parent_snapshot=None,build_context={'synthetic':True})['snapshot_id']
    days=('2023-12-28',*SESSIONS)
    daily=spec('market_daily',['security_id','session'],dict(security_id='string',session='date',
        open='float64',high='float64',low='float64',close='float64',pre_close='float64',
        volume_shares='int64',amount_cny='float64'))
    add('market_daily',daily,[row(security_id=s,session=day,open=10.,high=11.,low=9.,
        close=10.,pre_close=9.,volume_shares=100,amount_cny=1000.) for day in days for s in SYMBOLS])
    add('price_limits',deepcopy(CONTRACTS['stk_limit']),[row(security_id=s,session=day,
        up_limit=11.,down_limit=9.) for day in SESSIONS for s in SYMBOLS])
    add('adjustment_factors',spec('factor',['security_id','session'],
        dict(security_id='string',session='date',factor='float64')),
        [row(security_id=s,session=day,factor=1.) for day in days for s in SYMBOLS])
    add('trading_calendar',spec('calendar',['exchange','session'],
        dict(exchange='string',session='date',is_open='bool')),
        [row(exchange='SSE',session=day,is_open=True) for day in days])
    add('security_master',spec('master',['security_id','listing_date'],dict(
        exchange='string',listing_date='date',delisting_date='date',vendor_delist_date='date',list_status='string')),
        [row(security_id=s,exchange='SSE',listing_date='2020-01-01',delisting_date=None,
            vendor_delist_date=None,list_status='L') for s in SYMBOLS])
    add('security_status',spec('status',['security_id','session'],dict(session='date',
        is_suspended='bool',status_reason='string',suspend_timing='string')),
        [row(security_id=s,session=day,is_suspended=False,status_reason=None,suspend_timing=None)
            for day in days for s in SYMBOLS])
    add('listing_events',spec('listing',['security_id','listing_date','event_type'],dict(
        event_type='string',delisting_date='date',event_date='date',event_state='string',
        exchange='string',listing_date='date',source_code='string',vendor_list_status='string')),
        [row(security_id=s,listing_date='2020-01-01',event_type='listed',exchange='SSE',
            event_date='2020-01-01',event_state='value',delisting_date=None,source_code=s,
            vendor_list_status='L') for s in SYMBOLS])
    contract=deepcopy(CONTRACTS['dividend'])
    action=row(security_id='SIM_A',report_period='2023-12-31',announcement_date='2024-01-01',
        process_status='implemented',implementation_announcement_date='2024-01-01',
        record_date='2024-01-08',ex_date='2024-01-09',cash_dividend_before_tax_per_share=0.2,
        bonus_shares_per_share=0.,capital_transfer_shares_per_share=0.,source_issue=None,
        source_candidate_count=1,candidate_economic_dates=None)
    for n,f in contract['fields'].items():
        if f.get('status_field'):action[f['status_field']]='value'
    add('corporate_actions',contract,[action],profile={'id':'synthetic.dividend','endpoint':'dividend',
        'revision_order':'terminal_observation_v1','availability':{'date_field':'announcement_date',
            'date_rule':'next_open','timezone':'Asia/Shanghai','session_release_time':'09:30:00',
            'next_open_session_by_date':{'2024-01-01':'2024-01-02'}}})
    snapshot=store.publish_snapshot(domains,parent_snapshot=None,build_context={'synthetic':True})['snapshot_id']
    return Data(store.root,cache_bytes=4*1048576),snapshot


def requests():
    def daily(method,domain,fields,days=SESSIONS,**kw):
        return dict(method=method,query=QuerySpec(domain,tuple(fields),SYMBOLS,tuple(days),
            'best_effort_vendor_v1',{d:d+'T20:30:00+08:00' for d in days},
            purpose='decision_facts' if method=='members' else 'market_replay',**kw))
    market=('open','high','low','close','volume_shares','amount_cny')
    events=('implementation_announcement_date','record_date','ex_date','cash_dividend_before_tax_per_share',
        'bonus_shares_per_share','capital_transfer_shares_per_share','source_issue','source_candidate_count',
        'candidate_economic_dates')
    return [daily('members','universe_membership',('is_member',),universe_id='csi300'),
        daily('states','market_daily',('close',)),daily('read_market','market_daily',market),
        daily('read_market','price_limits',('up_limit','down_limit')),
        daily('read_market','adjustment_factors',('factor',)),
        *[dict(method='events',query=EventQuery(domain='corporate_actions',fields=events,symbols=SYMBOLS,
            pit_policy='best_effort_vendor_v1',cutoff='2024-01-26T20:30:00+08:00',time_field=field,
            start='2023-12-29',end='2024-01-26',purpose='market_replay')) for field in ('ex_date','record_date')],
        daily('read_market','market_daily',market,('2023-12-28',)),
        daily('read_market','adjustment_factors',('factor',),('2023-12-28',))]


class MembershipListTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def test_all_nine_request_forms_match_complete_ordinary_canonical_wire(self):
        data,snapshot=fixture(self.root/'source');reads=requests()
        oracle=Data(data.store.root,cache_bytes=0)
        expected=[_json_bytes(getattr(oracle,r['method'])(snapshot=snapshot,query=r['query']).to_json()) for r in reads]
        expected_refs=['sha256:'+sha256(w).hexdigest() for w in expected]
        result=data.export_native_view(snapshot=snapshot,reads=reads,destination=self.root/'view',
            limits=limits(),source_symbol_block=1)
        self.assertEqual(result['native_refs'],expected_refs)
        with open_native_view(self.root/'view',manifest_sha256=result['content_digest'],limits=limits()) as view:
            actual=[b''.join(_replay(view.root,b,view.statistics,view._marks)) for b in view.manifest['batches']]
            self.assertEqual(actual,expected)
            wire=json.loads(actual[0])
            dependencies=[m['dependency_raw_batch_ids'] for m in wire['field_meta']['is_member']['by_key']]
            self.assertTrue(any(values[:3]==['源🙂','shared',None] for values in dependencies))
            for batch in view.manifest['batches']:
                ordinals=[i for block in view.iter_blocks(native_ref=batch['native_ref']) for i in block['ordinals']]
                self.assertEqual(ordinals,list(range(batch['row_count'])))
        # The admitted cache retains actual list arrays and their child buffers.
        cached=[entry[0] for key,entry in data._readers[snapshot]._cache.items() if key.startswith('native-arrow-batches:')]
        self.assertTrue(any('dependency_raw_batch_ids' in table.column_names and
            pa.types.is_list(table.schema.field('dependency_raw_batch_ids').type) for table in cached))
        MEASURED['nine_requests']={'canonical_exact_count':9,'native_refs':expected_refs,
            'statistics':result['statistics'],'synthetic_symbols':2,'calendar_sessions':20}

    def test_sliced_list_bound_preserves_null_empty_and_nonzero_child_offsets(self):
        data,snapshot=fixture(self.root/'source');reader=data._reader(snapshot)
        writer=_Writer(self.root,limits(),reader);proxy=_ProjectionStore(data.store,reader,writer)
        array=pa.array([['ignored'*100000],None,[],['汉字🙂',None,'a','a'],['trailing'*100000]],type=LIST_TYPE)
        table=pa.table({'dependency_raw_batch_ids':array}).slice(1,3)
        self.assertGreater(table.column(0).chunk(0).offset,0)
        bound=proxy._python_bound(table)
        self.assertEqual(writer.statistics['peak_python_logical_value_bytes'],12)
        self.assertEqual(writer.statistics['peak_python_list_elements'],4)
        self.assertEqual(writer.statistics['peak_python_list_containers'],3)
        rows=table.to_pylist()
        self.assertEqual([r['dependency_raw_batch_ids'] for r in rows],[None,[],['汉字🙂',None,'a','a']])
        self.assertLess(bound,20000);self.assertGreaterEqual(4*bound+512*len(rows),4*_object_size(rows)+512*len(rows))
        MEASURED['offset_slice']={'logical_utf8_bytes':12,'list_elements':4,'list_containers':3,
            'python_graph_bound_bytes':bound,'actual_python_graph_bytes':_object_size(rows)}

    def test_only_bound_membership_dependency_string_list_is_admitted(self):
        data,snapshot=fixture(self.root/'source');reader=data._reader(snapshot)
        part=reader.snapshot['domains']['universe_membership']['partitions'][0]
        with self.assertRaisesRegex(QueryError,'flat scalar'):
            list(data.store._native_partition_batches(part,columns=['dependency_raw_batch_ids']))
        for name,value in (('other_list',[['x']]),('dependency_raw_batch_ids',[[1,2]])):
            with self.subTest(field=name):
                path=self.root/(name+'.parquet');pq.write_table(pa.table({name:value}),path)
                test_part=dict(uri=str(path.relative_to(data.store.root)) if path.is_relative_to(data.store.root) else 'unsupported/'+name,
                    file_sha256=sha256(path.read_bytes()).hexdigest())
                target=data.store._path(test_part['uri']);target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(path.read_bytes())
                with self.assertRaisesRegex(QueryError,'flat scalar'):
                    list(data.store._native_partition_batches(test_part,columns=[name],_membership_dependencies=True))

    def test_list_expansion_shortfalls_reject_before_python_and_release_borrows(self):
        for label,values in (('utf8',['汉字🙂'*80]*16),('elements',['']*1500)):
            with self.subTest(kind=label):
                data,snapshot=fixture(self.root/label,dependency_values=values)
                captured=[];opened=[];writer_type=_Writer;file_type=pq.ParquetFile
                def writer(*a,**kw):
                    value=writer_type(*a,**kw);captured.append(value);return value
                def parquet(*a,**kw):
                    value=file_type(*a,**kw);opened.append(value);return value
                with patch('axiom_data.native_view._Writer',side_effect=writer),patch.object(pq,'ParquetFile',side_effect=parquet):
                    with self.assertRaisesRegex(QueryError,'Python conversion.*before row expansion'):
                        data.export_native_view(snapshot=snapshot,reads=[requests()[0]],destination=self.root/('view-'+label),
                            limits=limits(8*1048576))
                statistics=captured[0].statistics
                self.assertEqual(statistics.get('python_batches_converted',0),0)
                self.assertGreater(statistics['peak_python_conversion_reservation_bytes'],8*1048576)
                self.assertLessEqual(statistics['peak_accepted_working_charge_bytes'],8*1048576)
                self.assertEqual(data._readers[snapshot]._cached_bytes,0)
                self.assertIs(data._readers[snapshot].store,data.store)
                self.assertTrue(all(f.closed for f in opened))
                self.assertFalse((self.root/('view-'+label)).exists());self.assertEqual(list(self.root.glob('.native-*')),[])
                for owner in ('projection-build','decoded-arrow-batch','arrow-filter','row-conversion','conversion-lengths'):
                    self.assertNotIn(owner,captured[0].retained)
                MEASURED[label+'_shortfall']=statistics


if __name__=='__main__':unittest.main()
