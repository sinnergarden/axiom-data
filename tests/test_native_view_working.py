"""Small independent cold-path, ownership and retained-traceback counterexamples."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pyarrow as pa
from axiom_data import Data, QueryError, QuerySpec, open_native_view
from axiom_data.native_view import _Writer, _ProjectionStore, _replay
from axiom_data.public_evidence import CONTRACT, PROFILE
from axiom_data.reader import _object_size
from axiom_data.storage import LocalStore, _json_bytes
from native_view_fixture import Store, data, source, reads
from test_native_view import limits


class CompleteStore(Store):
    """The ordinary Store columns=None protocol, independent of the wrapper."""
    def read_partition(self,part,*,columns=None,symbols=None,sessions=None):
        if columns is None:
            self.decodes+=1
            return pa.Table.from_pylist(self.rows[part['uri']])
        return super().read_partition(part,columns=columns,symbols=symbols,sessions=sessions)


def evidence_data(conflict=False):
    manifest,rows=source()
    target=rows['market_daily/2024-01'][0]
    target['first_observed_at']='2024-01-04T00:00:00+00:00'
    value=dict(target_domain='market_daily',target_key=_json_bytes({'security_id':'A','session':'2024-01-02'}).decode(),
        target_revision='r1',public_at='2024-01-01T00:00:00+00:00',document_sha256='a'*64,
        raw_batch_id='document',source_url='https://fixture.invalid/document',locator='page 1',asserted_values='{}')
    rows['public_evidence/history']=[value]
    if conflict: rows['public_evidence/history'].append(dict(value,document_sha256='b'*64))
    manifest['domains']['public_evidence']=dict(contract=CONTRACT,source_profile=PROFILE,
        partitions=[dict(partition='history',uri='public_evidence/history',rows=len(rows['public_evidence/history']),file_sha256='0'*64)],coverage={})
    d=data();d.store=CompleteStore(manifest,rows)
    return d


class WorkingOwnersTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def export(self,d,requests,cap=None,name='view'):
        budget=limits()
        if cap is not None: budget['max_working_bytes']=cap
        return d.export_native_view(snapshot='s1',reads=requests,destination=self.root/name,limits=budget)

    def wires(self,result,name='view'):
        with open_native_view(self.root/name,manifest_sha256=result['content_digest'],limits=limits()) as view:
            return [json.loads(b''.join(_replay(view.root,b,view.statistics,view._marks))) for b in view.manifest['batches']]

    def test_independent_evidence_partition_none_cold_and_late_early(self):
        requests=[dict(method='read_market',query=replace(reads()[i]['query'],pit_policy='market_pit_safe_v1')) for i in (0,3)]
        ordinary=evidence_data();ordinary.cache_bytes=0
        want=[ordinary.read_market(snapshot='s1',query=r['query']).to_json() for r in requests]
        native=evidence_data()
        result=self.export(native,requests)
        self.assertEqual(self.wires(result),want)
        self.assertGreater(result['statistics']['peak_owner_charge_bytes']['evidence'],0)
        with open_native_view(self.root/'view',manifest_sha256=result['content_digest'],limits=limits()) as view:
            self.assertGreater(view.statistics['cold_decodes'],view.statistics['file_validations']-view.statistics['coverage_validations'])
        self.assertEqual(next(r['close'] for r in want[1]['records'] if (r['security_id'],r['session'])==('A','2024-01-02')),-0.0)

    def test_evidence_conflict_preserves_ordinary_failure_and_cleanup(self):
        request=[reads()[0]]
        with self.assertRaisesRegex(Exception,'conflicting public evidence') as plain:
            evidence_data(True).read_market(snapshot='s1',query=request[0]['query'])
        d=evidence_data(True);store=d.store
        with self.assertRaises(type(plain.exception)):
            self.export(d,request)
        self.assertIs(d._readers['s1'].store,store)
        self.assertEqual(list(self.root.glob('.native-*')),[])

    def test_state_reference_groups_once_across_days_cutoffs_and_policies(self):
        from axiom_data import local_states
        from axiom_data.native_view import _StateReferences
        requests=[reads()[i] for i in (2,5,9)]
        oracle=json.loads((Path(__file__).parent/'fixtures/native_view_oracle_v1.json').read_bytes())
        original=_StateReferences.prepare;charges=[]
        def measured(plan,query,day):
            refs,temporary=original(plan,query,day)
            owner=plan.static[tuple(query.symbols)][2]
            charge=plan.writer.retained[owner]+plan.writer.retained['state-window']
            charges.append((charge,_object_size(refs)))
            return refs,temporary
        with patch.object(local_states,'_reference_data',wraps=local_states._reference_data) as reference, \
             patch.object(local_states,'_listing_data',wraps=local_states._listing_data) as listing, \
             patch.object(_StateReferences,'prepare',measured):
            result=self.export(data(),requests)
        self.assertEqual(reference.call_count,2)
        self.assertEqual(listing.call_count,1)
        self.assertEqual(self.wires(result),[oracle['cases'][i]['wire'] for i in (2,5,9)])
        owners=result['statistics']['peak_owner_charge_bytes']
        self.assertGreater(owners['state-master'],0);self.assertGreater(owners['state-window'],0)
        self.assertTrue(all(charge>=actual for charge,actual in charges))

    def test_candidate_owner_retains_copies_until_selection_finishes(self):
        from axiom_data import event_reader
        original=event_reader._action_candidates;charges=[]
        def measured(store,row,*,_budget=None):
            candidates=original(store,row,_budget=_budget)
            charges.append((_budget.retained['candidates'],_object_size(candidates)))
            return candidates
        with patch.object(event_reader,'_action_candidates',measured):
            result=self.export(data(),[reads()[18]])
        self.assertTrue(charges);self.assertTrue(all(charge>=actual for charge,actual in charges))
        self.assertGreater(result['statistics']['peak_owner_charge_bytes']['candidates'],0)

    def test_candidate_copy_bound_refuses_before_date_selection(self):
        from axiom_data import sources,event_reader
        d=data();originals=json.loads(d.store.read_raw_record(d.store.get_raw('ambiguous')))*160
        with patch.object(sources,'_rows',return_value=originals) as parse, \
             patch.object(event_reader,'_best_effort_time',wraps=event_reader._best_effort_time) as select:
            with self.assertRaisesRegex(QueryError,'working budget'):
                self.export(d,[reads()[18]],cap=512000)
        parse.assert_called_once();select.assert_not_called()

    def test_large_reference_rejected_before_decode_at_original_cap(self):
        d=data();rows=d.store.rows['security_master/history'];template=rows[0]
        rows[:]=[dict(template,listing_date=f'{1800+i:04d}-01-01') for i in range(200)]
        d.store.manifest['domains']['security_master']['partitions'][0]['rows']=len(rows)
        original=d.store.read_partition
        seen=[]
        def observed(part,**kw): seen.append(part['uri']);return original(part,**kw)
        with patch.object(d.store,'read_partition',side_effect=observed):
            with self.assertRaisesRegex(QueryError,'working budget'):
                self.export(d,[reads()[2]],cap=256000)
        self.assertNotIn('security_master/history',seen)
        self.assertEqual(list(self.root.glob('.native-*')),[])

    def test_raw_candidates_rejected_before_json_parse_at_original_cap(self):
        d=data();payload=d.store.read_raw_record(d.store.get_raw('ambiguous'))
        originals=json.loads(payload)*160
        payload=_json_bytes(originals)
        from axiom_data import sources
        with patch.object(d.store,'read_raw_record',return_value=payload), \
             patch.object(sources,'_rows',wraps=sources._rows) as parse:
            with self.assertRaisesRegex(QueryError,'working budget'):
                self.export(d,[reads()[18]],cap=512000)
        parse.assert_not_called()
        self.assertEqual(list(self.root.glob('.native-*')),[])

    def test_nonmonotone_months_fallback_has_no_prepass(self):
        d=data();market=d.store.manifest['domains']['market_daily']
        january=d.store.rows['market_daily/2024-01']
        february=[dict(r,session='2024-02-'+r['session'][-2:]) for r in january if r['security_id']=='A' and r['revision_id']=='r1']
        d.store.rows['market_daily/2024-02']=february
        market['partitions'].append(dict(market['partitions'][0],partition='2024-02',uri='market_daily/2024-02',rows=len(february)))
        days=('2024-01-02','2024-02-02','2024-01-03','2024-02-03')
        q=QuerySpec('market_daily',('close',),('A',),days,'market_pit_safe_v1',
            {day:'2024-01-03T12:00:00+00:00' for day in days},purpose='market_replay')
        ordinary=Data('/synthetic-native-view',cache_bytes=0);ordinary.store=CompleteStore(d.store.manifest,d.store.rows)
        want=ordinary.read_market(snapshot='s1',query=q).to_json()
        with patch.object(_ProjectionStore,'_native_read_groups',autospec=True,side_effect=_ProjectionStore._native_read_groups) as grouping:
            result=self.export(d,[dict(method='read_market',query=q)])
        self.assertEqual(grouping.call_count,4)
        self.assertEqual(result['statistics']['parquet_decodes'],2)
        self.assertEqual(result['statistics']['parquet_projection_hits'],2)
        self.assertEqual(self.wires(result),[want])
        self.assertIn('5 queried revisions',str(want['context']['limitations']))

    def test_constructor_and_snapshot_check_failures_always_remove_stage(self):
        d=data();reader=d._reader('s1');original=reader.store
        with patch('axiom_data.native_view._Writer',side_effect=QueryError('init failure')):
            with self.assertRaisesRegex(QueryError,'init failure'): self.export(d,[reads()[0]])
        self.assertEqual(list(self.root.glob('.native-*')),[]);self.assertIs(reader.store,original)
        reader._snapshot_path=self.root/'marker';reader._snapshot_mark=(0,)*5
        reader._snapshot_path.write_bytes(b'changed')
        with self.assertRaisesRegex(QueryError,'Snapshot changed'): self.export(d,[reads()[0]])
        self.assertEqual(list(self.root.glob('.native-*')),[]);self.assertIs(reader.store,original)

    def test_bad_coverage_closes_file_even_with_retained_traceback(self):
        result=self.export(data(),[reads()[0]])
        root=self.root/'view';manifest=json.loads((root/'manifest.json').read_bytes())
        uri=next(k for k,v in manifest['files'].items() if v['kind']=='coverage')
        bad=b'{"broken":]'+b' '*70000
        (root/uri).write_bytes(bad)
        desc=manifest['files'][uri];desc.update(bytes=len(bad),sha256='sha256:'+sha256(bad).hexdigest())
        manifest['batches'][0]['coverage'].update(bytes=desc['bytes'],sha256=desc['sha256'])
        raw=_json_bytes(manifest);(root/'manifest.json').write_bytes(raw)
        opened=[];original=Path.open
        def tracked(path,*args,**kw):
            stream=original(path,*args,**kw)
            if path==root/uri: opened.append(stream)
            return stream
        caught=None
        with patch.object(Path,'open',tracked):
            try: open_native_view(root,manifest_sha256='sha256:'+sha256(raw).hexdigest(),limits=limits())
            except QueryError as exc: caught=exc
        self.assertIsNotNone(caught);self.assertIsNotNone(caught.__traceback__)
        self.assertTrue(opened);self.assertTrue(all(s.closed for s in opened))

    def test_existing_other_reader_and_mapping_inputs_are_charged(self):
        d=data();reader=d._reader('s1')
        other=deepcopy(reader);other.snapshot['padding']='x'*300000
        d._readers['other']=other
        with self.assertRaisesRegex(QueryError,'working budget'):
            self.export(d,[reads()[0]],cap=256000)
        self.assertEqual(list(self.root.glob('.native-*')),[])
        self.assertGreater(_object_size(reads()[0]['query'].cutoff_by_session),100)

    def test_real_partition_none_filters_and_early_generator_close(self):
        from test_completion_states import add_domain,row,MARKET
        store=LocalStore(self.root/'source')
        domain=add_domain(store,'market_daily',[row(security_id='A',session='2024-01-02',close=10.,volume=0)],
            MARKET,partition='2024-01')
        snapshot=store.publish_snapshot({'market_daily':domain},parent_snapshot=None,build_context={'test':'small'})['snapshot_id']
        d=Data(store.root,cache_bytes=0);reader=d._reader(snapshot)
        writer=_Writer(self.root,limits(),reader);proxy=_ProjectionStore(store,reader,writer)
        part=domain['partitions'][0]
        self.assertEqual(proxy.read_partition(part).num_rows,1)
        self.assertEqual(proxy.read_partition(part,columns=['close'],symbols=()).num_rows,0)
        proxy.track_positions=True
        chunks=proxy._native_chunks(part,owner='borrowed',columns=['security_id','close'],positions=True)
        rows,positions=next(chunks)
        self.assertEqual(positions,[0]);self.assertEqual(rows[0]['close'],10.)
        chunks.close()
        self.assertEqual(proxy.last_positions,())
        self.assertTrue(all(not k.startswith(('arrow:','row-conversion')) for k in writer.retained))
        self.assertGreaterEqual(writer.retained['borrowed'],_object_size(rows))

    def test_cold_snapshot_budget_refuses_before_normal_loader(self):
        store=LocalStore(self.root/'source');(store.root/'snapshots').mkdir(parents=True)
        (store.root/'snapshots/s1.json').write_bytes(b'x'*5000)
        d=Data(store.root)
        with patch.object(d.store,'load_snapshot',side_effect=AssertionError('allocation forbidden')) as load:
            with self.assertRaisesRegex(QueryError,'Snapshot load exceeds working budget'):
                self.export(d,[reads()[0]],cap=256000)
        load.assert_not_called();self.assertEqual(list(self.root.glob('.native-*')),[])

    def test_cold_preflight_does_not_pin_evicted_reader(self):
        import weakref
        from test_completion_states import add_domain,row,MARKET
        store=LocalStore(self.root/'source')
        domain=add_domain(store,'market_daily',[row(security_id='A',session='2024-01-02',close=10.,volume=0)],
            MARKET,partition='2024-01')
        snapshot=store.publish_snapshot({'market_daily':domain},parent_snapshot=None,build_context={'test':'small'})['snapshot_id']
        d=Data(store.root,max_readers=1)
        previous=data()._reader('s1');held=weakref.ref(previous)
        d._readers['previous']=previous;del previous
        original=_Writer
        def measured(*args,**kwargs):
            self.assertIsNone(held())
            return original(*args,**kwargs)
        with patch('axiom_data.native_view._Writer',side_effect=measured):
            d.export_native_view(snapshot=snapshot,reads=[reads()[0]],destination=self.root/'view',limits=limits())

    def test_local_raw_inflation_and_line_budget_before_decode(self):
        import base64,zlib
        d=data();reader=d._reader('s1')
        store=LocalStore(self.root/'raw-source');(store.root/'raw').mkdir(parents=True)
        path=store.root/'raw/fetches.jsonl'
        profile={'identity_map':{'A':'x'*80000}}
        path.write_bytes(_json_bytes({'batch_id':'huge','source_profile_zlib':base64.b64encode(zlib.compress(_json_bytes(profile))).decode()})+b'\n')
        budget=limits();budget['max_working_bytes']=256000
        writer=_Writer(self.root,budget,reader)
        with self.assertRaisesRegex(QueryError,'profile exceeds working budget'):
            store.get_raw('huge',_budget=writer)
        self.assertEqual(store._profile_cache,{})
        self.assertNotIn('raw-log-line',writer.retained);self.assertNotIn('raw-inflation',writer.retained)
        path.write_bytes(_json_bytes({'batch_id':'huge','padding':'x'*80000})+b'\n')
        with patch.object(store,'_decode_raw_line',wraps=store._decode_raw_line) as parse:
            with self.assertRaisesRegex(QueryError,'log line exceeds working budget'):
                store.get_raw('huge',_budget=writer)
        parse.assert_not_called()

    def test_local_raw_budget_preserves_metadata_cache_and_return_isolation(self):
        import base64,zlib
        reader=data()._reader('s1');store=LocalStore(self.root/'raw-source')
        (store.root/'raw').mkdir(parents=True)
        profile={'identity_map':{'A':'A'},'field_map':{'ex_date':'ex_date'}}
        encoded=base64.b64encode(zlib.compress(_json_bytes(profile))).decode()
        path=store.root/'raw/fetches.jsonl'
        path.write_bytes(b''.join(_json_bytes({'batch_id':key,'source_profile_zlib':encoded})+b'\n' for key in ('one','two')))
        budget=limits();budget['max_working_bytes']=256000
        writer=_Writer(self.root,budget,reader)
        want=LocalStore(store.root).get_raw('one')
        with writer.scope('raw-record'),patch.object(store,'_decode_raw_line',wraps=store._decode_raw_line) as decode:
            first=store.get_raw('one',_budget=writer)
            self.assertEqual(first,want)
            first['source_profile']['identity_map']['A']='polluted'
            second=store.get_raw('one',_budget=writer)
            self.assertEqual(second,want)
        self.assertEqual(decode.call_count,4)
        self.assertEqual(len(store._raw_offsets),2);self.assertEqual(len(store._profile_cache),1)
        self.assertTrue(all(not k.startswith('raw-') for k in writer.retained))
        self.assertLessEqual(writer.statistics['peak_working_charge_bytes'],256000)


if __name__=='__main__':unittest.main()
