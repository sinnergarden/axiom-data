"""Frozen whole-wire oracle, hostile storage boundaries and actual counters."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from axiom_data import open_native_view,QueryError
from axiom_data.native_view import _replay
from axiom_data.storage import _json_bytes
from native_view_fixture import data,reads

HERE=Path(__file__).parent


def limits(part=1048576,rows=2):
    return {'max_part_bytes':part,'max_working_bytes':256*1048576,
            'max_saved_bytes':64*1048576,'max_rows_per_block':rows}


class NativeViewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'view'
        self.oracle=json.loads((HERE/'fixtures/native_view_oracle_v1.json').read_text())
        self.assertEqual(self.oracle['oracle_commit'],'8bebf14742c36277a7ba4d3b913ac7c1bdca102d')
        self.assertEqual(self.oracle['fixture_sha256'],sha256((HERE/'native_view_fixture.py').read_bytes()).hexdigest())

    def export(self,part=1048576,rows=2):
        d=data()
        # Public read/to_json whole-result routes are forbidden in this producer.
        with patch.object(d,'read',side_effect=AssertionError('whole read forbidden')), \
             patch('axiom_data.protocols.DataBatch.to_json',side_effect=AssertionError('whole to_json forbidden')):
            result=d.export_native_view(snapshot='s1',reads=reads(),destination=self.root,limits=limits(part,rows))
        self.assertEqual(d.store.loads,1)
        return result

    def open(self,result,**kw):
        return open_native_view(self.root,manifest_sha256=result['content_digest'],limits=limits(**kw))

    def test_full_old_canonical_wire_at_one_and_four_mib(self):
        for part in (1048576,4*1048576):
            with self.subTest(part=part):
                self.root=Path(self.temp.name)/str(part)
                result=self.export(part=part)
                with self.open(result,part=part) as view:
                    for batch,want in zip(view.manifest['batches'],self.oracle['cases']):
                        self.assertEqual(batch['native_ref'],want['native_ref'])
                        self.assertEqual(b''.join(_replay(view.root,batch,view.statistics,view._marks)),_json_bytes(want['wire']))
                        actual=[];metadata={n:[] for n in batch['field_meta']};ordinals=[]
                        for block in view.iter_blocks(native_ref=batch['native_ref']):
                            actual.extend(block['records']);ordinals.extend(block['ordinals'])
                            for n,h in block['field_meta'].items():metadata[n].extend(h['by_key'])
                        self.assertEqual(_json_bytes(actual),_json_bytes(want['wire']['records']))
                        self.assertEqual(ordinals,list(range(batch['row_count'])))
                        for n,values in metadata.items():self.assertEqual(values,want['wire']['field_meta'][n]['by_key'])

    def test_selected_ordinals_and_no_warm_readmission(self):
        result=self.export()
        with self.open(result) as view:
            batch=view.manifest['batches'][0];before=dict(view.statistics)
            blocks=list(view.iter_blocks(native_ref=batch['native_ref'],sessions=('2024-01-02',)))
            self.assertEqual([i for b in blocks for i in b['ordinals']],[3,4,5])
            for key in ('file_validations','coverage_validations','logical_replays','admission_reads'):
                self.assertEqual(view.statistics[key],before[key])
            self.assertGreater(view.statistics['selected_decodes'],0)
            list(view.iter_blocks(native_ref=batch['native_ref'],sessions=('2024-01-02',)))
            self.assertGreater(view.statistics['selected_decodes'],before.get('selected_decodes',0))

    def test_return_isolation_and_close(self):
        result=self.export();view=self.open(result);ref=view.manifest['batches'][0]['native_ref']
        block=next(view.iter_blocks(native_ref=ref));expected=deepcopy(block)
        block['records'][0]['close']=888;block['field_meta']['close']['by_key'][0]['revision_id']='polluted'
        copy=view.manifest;copy['batches'][0]['context']['snapshot_id']='other'
        self.assertEqual(next(view.iter_blocks(native_ref=ref)),expected)
        view.close()
        with self.assertRaisesRegex(QueryError,'closed'):list(view.iter_blocks(native_ref=ref))

    def test_coverage_is_shared_and_cold_unique_file_validation(self):
        result=self.export()
        with self.open(result) as view:
            manifest=view.manifest
            coverage={b['coverage']['uri'] for b in manifest['batches']}
            self.assertEqual(len(coverage),3)
            self.assertEqual(view.statistics['coverage_validations'],3)
            self.assertEqual(view.statistics['file_validations'],len(manifest['files']))
            self.assertEqual(view.statistics['logical_replays'],len(manifest['batches']))
            self.assertGreater(result['statistics']['parquet_projection_hits'],0)

    def test_event_symbol_groups_reuse_decode_and_preserve_full_context(self):
        requests=[r for r in reads() if r['method']=='events']
        results=[]
        for size in (1,64):
            self.root=Path(self.temp.name)/('groups'+str(size))
            result=data().export_native_view(snapshot='s1',reads=requests,destination=self.root,
                limits=limits(),source_symbol_block=size)
            results.append(result)
            with self.open(result) as view:
                for batch,want in zip(view.manifest['batches'],self.oracle['cases'][10:]):
                    self.assertEqual(batch['native_ref'],want['native_ref'])
                    self.assertEqual(b''.join(_replay(view.root,batch,{},view._marks)),_json_bytes(want['wire']))
                with self.assertRaises(QueryError):
                    list(view.iter_blocks(native_ref=view.manifest['batches'][0]['native_ref'],sessions=['2024-01-02']))
        self.assertEqual(results[0]['native_refs'],results[1]['native_refs'])
        # EX and RECORD have two different requested column projections.
        self.assertEqual(results[0]['statistics']['parquet_decodes'],4)
        self.assertEqual(results[1]['statistics']['parquet_decodes'],4)
        self.assertEqual(results[0]['statistics']['event_source_groups'],36)
        self.assertEqual(results[1]['statistics']['event_source_groups'],12)

    def test_duplicate_requests_fail_before_publication(self):
        request=reads()[0]
        with self.assertRaisesRegex(QueryError,'duplicate native'):
            data().export_native_view(snapshot='s1',reads=[request,request],destination=self.root,limits=limits())
        self.assertFalse(self.root.exists())

    def test_event_cache_pressure_refuses_repeated_decompression(self):
        d=data();d.cache_bytes=0
        with self.assertRaisesRegex(QueryError,'exceed Reader cache'):
            d.export_native_view(snapshot='s1',reads=[reads()[10]],destination=self.root,
                limits=limits(),source_symbol_block=1)
        self.assertFalse(self.root.exists())
        result=d.export_native_view(snapshot='s1',reads=[reads()[10]],destination=self.root,
            limits=limits(),source_symbol_block=64)
        self.assertEqual(result['statistics']['parquet_decodes'],2)

    def test_source_mutation_before_publication_is_refused(self):
        d=data();calls=0
        def verify(part):
            nonlocal calls
            calls+=1
            if calls>1: raise QueryError('source partition changed')
        with patch.object(d.store,'verify_partition',side_effect=verify):
            with self.assertRaisesRegex(QueryError,'changed'):
                d.export_native_view(snapshot='s1',reads=[reads()[0]],destination=self.root,limits=limits())
        self.assertFalse(self.root.exists())

    def test_real_small_parquet_source_and_immutable_exports(self):
        from axiom_data import Data, QuerySpec
        from axiom_data.storage import LocalStore
        from test_completion_states import add_domain, row, MARKET
        source=Path(self.temp.name)/'data';store=LocalStore(source)
        domain=add_domain(store,'market_daily',[row(security_id='A',session='2024-01-02',close=10.,volume=0)],
            MARKET,partition='2024-01')
        snapshot=store.publish_snapshot({'market_daily':domain},parent_snapshot=None,
            build_context={'test':'synthetic native view'})['snapshot_id']
        original={str(p.relative_to(source)):sha256(p.read_bytes()).hexdigest() for p in source.rglob('*') if p.is_file()}
        q=QuerySpec('market_daily',('close',),('A',),('2024-01-02',),'operational_pit_v1',
            {'2024-01-02':'2024-01-06T00:00:00+00:00'},purpose='market_replay')
        d=Data(source,cache_bytes=1048576)
        result=d.export_native_view(snapshot=snapshot,
            reads=[{'method':'read_market','query':q}],destination=self.root,limits=limits())
        with self.open(result) as view:
            self.assertEqual(next(view.iter_blocks(native_ref=result['native_refs'][0]))['records'][0]['close'],10.)
        self.assertEqual(original,{str(p.relative_to(source)):sha256(p.read_bytes()).hexdigest()
            for p in source.rglob('*') if p.is_file()})
        snapshot_file=source/'snapshots'/(snapshot+'.json')
        snapshot_file.write_bytes(snapshot_file.read_bytes()+b' ')
        with self.assertRaisesRegex(QueryError,'Snapshot changed after Reader load'):
            d.export_native_view(snapshot=snapshot,reads=[{'method':'read_market','query':q}],
                destination=Path(self.temp.name)/'second-view',limits=limits())

    def test_changed_selected_or_coverage_bytes_are_refused(self):
        for kind in ('array','coverage'):
            with self.subTest(kind=kind):
                self.root=Path(self.temp.name)/kind;result=self.export()
                with self.open(result) as view:
                    uri=next(p for p,d in view.manifest['files'].items() if d['kind']==kind)
                    p=self.root/uri;p.write_bytes(p.read_bytes()+b' ')
                    with self.assertRaisesRegex(QueryError,'changed'):
                        list(view.iter_blocks(native_ref=view.manifest['batches'][0]['native_ref']))

    def rewrite_manifest(self,mutation):
        p=self.root/'manifest.json';m=json.loads(p.read_bytes());mutation(m)
        raw=_json_bytes(m);p.write_bytes(raw)
        return {'content_digest':'sha256:'+sha256(raw).hexdigest()}

    def test_false_ordinal_and_native_digest_rejected(self):
        result=self.export()
        result=self.rewrite_manifest(lambda m:m['batches'][0]['blocks'][0].update(ordinal=1))
        with self.assertRaisesRegex(QueryError,'ordinal'):self.open(result)
        # New wrapper digest cannot authorize a different original native ref.
        result=self.rewrite_manifest(lambda m:(m['batches'][0]['blocks'][0].update(ordinal=0),m['batches'][0].update(native_ref='sha256:'+'0'*64)))
        with self.assertRaisesRegex(QueryError,'logical identity'):self.open(result)

    def test_corrupt_part_and_extra_file_rejected(self):
        result=self.export();m=json.loads((self.root/'manifest.json').read_bytes())
        uri=next(p for p,d in m['files'].items() if d['kind']=='array');p=self.root/uri
        raw=p.read_bytes();p.write_bytes(raw.replace(b'null',b'true',1) if b'null' in raw else raw+b' ')
        with self.assertRaises(QueryError):self.open(result)
        p.write_bytes(raw);(self.root/'untracked').write_bytes(b'x')
        with self.assertRaisesRegex(QueryError,'untracked'):self.open(result)

    def test_budget_failure_and_existing_destination_preserve_storage(self):
        d=data();small=limits();small['max_saved_bytes']=20
        with self.assertRaisesRegex(QueryError,'budget'):
            d.export_native_view(snapshot='s1',reads=reads(),destination=self.root,limits=small)
        self.assertFalse(self.root.exists())
        result=self.export();before=(self.root/'manifest.json').read_bytes()
        with self.assertRaisesRegex(QueryError,'already exists'):self.export()
        self.assertEqual((self.root/'manifest.json').read_bytes(),before)


if __name__=='__main__':unittest.main()
