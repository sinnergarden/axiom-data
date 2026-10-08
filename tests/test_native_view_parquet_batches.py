"""Only the streaming decoder boundary, dictionary expansion and batch identity."""
from copy import deepcopy
from contextlib import closing
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq
from axiom_data import Data, QueryError, QuerySpec, open_native_view
from axiom_data.native_view import _ProjectionStore,_Writer,_replay
from axiom_data.storage import LocalStore,_json_bytes
from native_view_fixture import source,reads,Store
from test_native_view import limits


class ParquetStore(LocalStore):
    """Synthetic inline manifest; all partition and Raw I/O is actual LocalStore."""
    def __init__(self,root,manifest):
        super().__init__(root);self.manifest=deepcopy(manifest)
    def load_snapshot(self,snapshot):
        assert snapshot=='s1';return deepcopy(self.manifest)


class ParquetBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def prepared(self,manifest,rows,name='data',row_group_size=1000):
        store=ParquetStore(self.root/name,manifest);store.root.mkdir()
        for domain in store.manifest['domains'].values():
            for part in domain['partitions']:
                path=store._path(part['uri']);path.parent.mkdir(parents=True,exist_ok=True)
                pq.write_table(pa.Table.from_pylist(rows[part['uri']]),path,compression='zstd',row_group_size=row_group_size)
                part['file_sha256']=sha256(path.read_bytes()).hexdigest();part['rows']=len(rows[part['uri']])
        d=Data(store.root,cache_bytes=1048576);d.store=store
        return d

    def long_values(self,value,count=1000):
        manifest,rows=source();domain=manifest['domains']['market_daily']
        manifest['domains']={'market_daily':domain}
        domain['contract']['fields']['message']={'dtype':'string','unit':None}
        template=rows['market_daily/2024-01'][0]
        rows={'market_daily/2024-01':[dict(template,message=value,revision_id=f'r{i}',revision_sequence=i) for i in range(count)]}
        return self.prepared(manifest,rows)

    def query(self):
        return QuerySpec('market_daily',('message',),('A',),('2024-01-02',),'operational_pit_v1',
            {'2024-01-02':'2024-01-06T00:00:00+00:00'},purpose='market_replay')

    def test_dictionary_amplification_rejected_before_python_not_after_whole_arrow(self):
        d=self.long_values('x'*8000)
        budget=limits();budget['max_working_bytes']=2*1048576
        captured=[];original=_Writer
        def measured(*a,**kw):
            writer=original(*a,**kw);captured.append(writer);return writer
        with patch('axiom_data.native_view._Writer',side_effect=measured):
            with self.assertRaisesRegex(QueryError,'Python conversion.*before row expansion'):
                d.export_native_view(snapshot='s1',reads=[{'method':'read_market','query':self.query()}],
                    destination=self.root/'view',limits=budget)
        stats=captured[0].statistics
        reader=d._readers['s1']
        self.assertLess(stats['peak_decoded_arrow_batch_buffer_bytes'],100000)
        self.assertGreaterEqual(stats['peak_python_logical_value_bytes'],512000)
        self.assertGreater(stats['peak_python_conversion_reservation_bytes'],2*1048576)
        self.assertEqual(stats.get('python_batches_converted',0),0)
        self.assertLessEqual(stats['peak_accepted_working_charge_bytes'],2*1048576)
        self.assertEqual(reader._cached_bytes,0)
        self.assertFalse((self.root/'view').exists());self.assertEqual(list(self.root.glob('.native-*')),[])
        self.assertIs(reader.store,d.store)

    def test_single_value_decoder_overshoot_is_observed_rejected_and_closed(self):
        d=self.long_values('x'*(3*1048576),count=1)
        budget=limits();budget['max_working_bytes']=2*1048576
        captured=[];opened=[];writer_original=_Writer;file_original=pq.ParquetFile
        def writer(*a,**kw):
            value=writer_original(*a,**kw);captured.append(value);return value
        def parquet(*a,**kw):
            value=file_original(*a,**kw);opened.append(value);return value
        with patch('axiom_data.native_view._Writer',side_effect=writer),patch.object(pq,'ParquetFile',side_effect=parquet):
            with self.assertRaisesRegex(QueryError,'decoded Arrow batch.*before retention'):
                d.export_native_view(snapshot='s1',reads=[{'method':'read_market','query':self.query()}],
                    destination=self.root/'view',limits=budget)
        stats=captured[0].statistics
        self.assertGreater(stats['peak_decoded_arrow_batch_buffer_bytes'],2*1048576)
        self.assertLessEqual(stats['peak_accepted_working_charge_bytes'],2*1048576)
        self.assertEqual(stats.get('python_batches_converted',0),0)
        self.assertEqual(d._readers['s1']._cached_bytes,0)
        self.assertTrue(opened);self.assertTrue(all(f.closed for f in opened))
        self.assertEqual(list(self.root.glob('.native-*')),[]);self.assertFalse((self.root/'view').exists())

    def scattered(self):
        manifest,rows=source()
        manifest['domains']={k:v for k,v in manifest['domains'].items() if k in ('market_daily','corporate_actions')}
        raw_store=LocalStore(self.root/'raw-maker')
        raw=raw_store.write_raw(Store(*source()).read_raw_record(Store(*source()).get_raw('ambiguous')),
            request={'fixture':True},source_profile=Store(*source()).get_raw('ambiguous')['source_profile'],
            observed_at='2024-01-01T00:00:00+00:00')
        originals=rows['corporate_actions/history-1']
        filler=dict(originals[2],security_id='X',logical_event_key='outside')
        expanded=[dict(filler,logical_event_key=f'outside-{i}') for i in range(260)]
        for position,index in ((100,0),(5,1),(180,2),(200,3),(90,4),(250,5)):
            expanded[position]=dict(originals[index])
            if expanded[position]['raw_batch_id']=='ambiguous':expanded[position]['raw_batch_id']=raw['batch_id']
        rows['corporate_actions/history-1']=expanded
        correction=rows['corporate_actions/history-2'][0]
        rows['corporate_actions/history-2']=[dict(filler,logical_event_key=f'other-{i}') for i in range(100)]
        rows['corporate_actions/history-2'][80]=correction
        market=rows['market_daily/2024-01']
        base=market[0];scattered=[dict(base,security_id='X') for _ in range(150)]
        for position,index in ((1,0),(66,1),(127,2),(149,3)):scattered[position]=market[index]
        rows['market_daily/2024-01']=scattered
        d=self.prepared(manifest,rows,row_group_size=100)
        import shutil
        shutil.copytree(raw_store.root/'raw',d.store.root/'raw')
        return d

    def test_cross_batch_pit_ordinals_and_event_groups_match_ordinary_reader(self):
        d=self.scattered();requests=[reads()[i] for i in (0,3,10,12,18,20)]
        plain=Data(d.store.root,cache_bytes=0);plain.store=ParquetStore(d.store.root,d.store.manifest)
        want=[getattr(plain,r['method'])(snapshot='s1',query=r['query']).to_json() for r in requests]
        results=[]
        for size in (1,64):
            destination=self.root/f'view-{size}'
            result=d.export_native_view(snapshot='s1',reads=requests,destination=destination,limits=limits(),source_symbol_block=size)
            with open_native_view(destination,manifest_sha256=result['content_digest'],limits=limits()) as view:
                actual=[json.loads(b''.join(_replay(view.root,b,view.statistics,view._marks))) for b in view.manifest['batches']]
                self.assertEqual(actual,want)
                for batch,wire in zip(view.manifest['batches'],want):
                    ordinals=[];records=[]
                    for block in view.iter_blocks(native_ref=batch['native_ref']):
                        ordinals.extend(block['ordinals']);records.extend(block['records'])
                    self.assertEqual(ordinals,list(range(len(wire['records']))));self.assertEqual(records,wire['records'])
            results.append(result)
        self.assertEqual(results[0]['native_refs'],results[1]['native_refs'])
        self.assertEqual(results[0]['statistics']['parquet_decodes'],3)
        self.assertEqual(results[1]['statistics']['parquet_decodes'],0)
        self.assertGreater(results[0]['statistics']['parquet_record_batches_decoded'],3)
        self.assertGreater(results[1]['statistics']['parquet_projection_hits'],0)

    def test_actual_dictionary_buffers_cached_once_and_warm_has_no_decode(self):
        d=self.long_values('x'*8000);reader=d._reader('s1');writer=_Writer(self.root,limits(),reader)
        proxy=_ProjectionStore(d.store,reader,writer);part=reader.snapshot['domains']['market_daily']['partitions'][0]
        with patch.object(d.store,'_native_partition_batches',wraps=d.store._native_partition_batches) as decode:
            for _ in range(2):
                with closing(proxy._projection_batches(part,['message'])) as batches:
                    seen=[(offset,table.num_rows) for offset,table in batches]
                self.assertEqual(sum(n for _,n in seen),1000)
        self.assertEqual(decode.call_count,1)
        self.assertEqual(writer.statistics['parquet_decodes'],1)
        self.assertEqual(writer.statistics['parquet_projection_hits'],1)
        table=next(iter(reader._cache.values()))[0]
        self.assertTrue(pa.types.is_dictionary(table.column(0).type))
        self.assertLess(table.get_total_buffer_size(),200000)
        self.assertLess(reader._cached_bytes,250000)

    def test_native_nested_rejection_does_not_narrow_ordinary_reader(self):
        d=self.long_values('ok',count=1);part=d.store.manifest['domains']['market_daily']['partitions'][0]
        path=d.store._path(part['uri'])
        pq.write_table(pa.table({'nested':[[1,2,3]]}),path)
        part['file_sha256']=sha256(path.read_bytes()).hexdigest()
        self.assertEqual(d.store.read_partition(part,columns=['nested']).to_pylist(),[{'nested':[1,2,3]}])
        with self.assertRaisesRegex(QueryError,'flat scalar fact columns'):
            list(d.store._native_partition_batches(part,columns=['nested']))

    def test_native_extension_rejection_and_streamed_full_file_hash(self):
        d=self.long_values('ok',count=1);part=d.store.manifest['domains']['market_daily']['partitions'][0]
        path=d.store._path(part['uri'])
        pq.write_table(pa.table({'uuid':pa.array([b'0'*16],type=pa.uuid())}),path)
        part['file_sha256']=sha256(path.read_bytes()).hexdigest()
        with patch.object(Path,'read_bytes',side_effect=AssertionError('whole file hash forbidden')):
            self.assertEqual(d.store.read_partition(part,columns=['uuid']).num_rows,1)
            with self.assertRaisesRegex(QueryError,'flat scalar fact columns'):
                list(d.store._native_partition_batches(part,columns=['uuid']))

    def test_event_projection_cache_shortfall_stops_before_repeat_decode(self):
        d=self.scattered();d.cache_bytes=1
        captured=[];original=_Writer
        def writer(*a,**kw):
            value=original(*a,**kw);captured.append(value);return value
        with patch('axiom_data.native_view._Writer',side_effect=writer):
            with self.assertRaisesRegex(QueryError,'exceed Reader cache'):
                d.export_native_view(snapshot='s1',reads=[reads()[10]],destination=self.root/'view',
                    limits=limits(),source_symbol_block=1)
        self.assertEqual(captured[0].statistics['parquet_decodes'],2)
        self.assertFalse((self.root/'view').exists());self.assertEqual(list(self.root.glob('.native-*')),[])

    def test_cold_snapshot_has_no_file_multiplier_and_reports_actual_graph(self):
        from test_completion_states import add_domain,row,MARKET
        store=LocalStore(self.root/'source')
        domain=add_domain(store,'market_daily',[row(security_id='A',session='2024-01-02',close=10.,volume=0)],
            MARKET,partition='2024-01')
        snapshot=store.publish_snapshot({'market_daily':domain},parent_snapshot=None,
            build_context={'padding':'x'*40000})['snapshot_id']
        d=Data(store.root,cache_bytes=65536)
        q=QuerySpec('market_daily',('close',),('A',),('2024-01-02',),'operational_pit_v1',
            {'2024-01-02':'2024-01-06T00:00:00+00:00'},purpose='market_replay')
        budget=limits();budget['max_working_bytes']=1048576
        with patch.object(d.store,'load_snapshot',wraps=d.store.load_snapshot) as load:
            result=d.export_native_view(snapshot=snapshot,reads=[{'method':'read_market','query':q}],
                destination=self.root/'view',limits=budget)
        load.assert_called_once()
        stats=result['statistics']
        self.assertTrue(stats['snapshot_loaded_during_export'])
        self.assertGreater(stats['snapshot_manifest_file_bytes']*48,budget['max_working_bytes'])
        self.assertLess(stats['snapshot_graph_bytes'],budget['max_working_bytes'])
        self.assertLessEqual(stats['peak_accepted_working_charge_bytes'],budget['max_working_bytes'])
        with open_native_view(self.root/'view',manifest_sha256=result['content_digest'],limits=limits()) as view:
            self.assertEqual(next(view.iter_blocks(native_ref=result['native_refs'][0]))['records'][0]['close'],10.)

    def test_cold_loaded_graph_rejection_does_not_leave_an_admitted_reader(self):
        d=self.long_values('ok',count=1)
        d.store.manifest['large_scalar']='x'*1500000
        budget=limits();budget['max_working_bytes']=1048576
        with patch.object(d.store,'load_snapshot',wraps=d.store.load_snapshot) as load:
            with self.assertRaisesRegex(QueryError,'working budget'):
                d.export_native_view(snapshot='s1',reads=[{'method':'read_market','query':self.query()}],
                    destination=self.root/'view',limits=budget)
        load.assert_called_once();self.assertNotIn('s1',d._readers)
        self.assertFalse((self.root/'view').exists());self.assertEqual(list(self.root.glob('.native-*')),[])


if __name__=='__main__':unittest.main()
