"""Public column contract and exact frozen-714 compatibility on tiny Parquet."""
from copy import deepcopy
from dataclasses import replace
import gc
import json
from pathlib import Path
import pickle
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
import pyarrow as pa

from axiom_data import ColumnSelection,ConflictError,Data,QueryError
from axiom_data.column_source import _array_ref
from axiom_data.derived import adjust_prices
from axiom_data.protocols import _json_safe
from axiom_data.storage import _json_bytes
from column_source_fixture import prepared,query,requests,adjustment_requests,LIMITS,EARLY,LATE


class ColumnSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.count=0
    def data(self,modify=None,**kw):
        self.count+=1
        return prepared(self.root/str(self.count),modify=modify,**kw)
    def source(self,d,s,limits=LIMITS):
        owner=d.open_column_source(snapshot=s,limits=limits);self.addCleanup(owner.close);return owner
    def assert_columns(self,selection,batch):
        wire=batch.to_json();sessions=selection.axes['sessions'];symbols=selection.axes['security']
        for field in selection.axes['fields']:
            column=selection.columns[field];values=column.values.to_numpy();valid=column.validity.to_numpy()
            self.assertFalse(values.flags.writeable);self.assertFalse(valid.flags.writeable)
            for ordinal,(record,meta) in enumerate(zip(wire['records'],wire['field_meta'][field]['by_key'])):
                key=divmod(ordinal,len(symbols));i,j=key
                self.assertEqual((record['session'],record['security_id']),(sessions[i],symbols[j]))
                value=_json_safe(values[key].item()) if valid[key] else None
                self.assertEqual(_json_bytes(value),_json_bytes(record[field]))
                self.assertEqual(column.missing_reason[key],meta['missing_reason'])
                if selection.derivation is None:
                    for name in ('revision_id','revision_sequence','raw_batch_id','evidence_ref','availability_basis'):
                        self.assertEqual(selection.provenance[name][key],meta[name])
                    clock=column.available_at[key]
                    self.assertEqual(None if np.isnat(clock) else clock,
                        None if meta['usable_from'] is None else pd.Timestamp(meta['usable_from']).tz_convert('UTC').tz_localize(None).to_datetime64())
                else:
                    for lineage,meta_name in (('price','price_provenance'),('factor','factor_provenance'),('anchor_factor','anchor_factor_provenance')):
                        self.assertEqual(selection.provenance[lineage]['revision_id'][key],meta[meta_name]['revision_id'])
    def test_frozen_raw_exact_and_live_columns_out_of_order_cutoffs(self):
        oracle=json.loads((Path(__file__).parent/'fixtures/column_source_oracle_v1.json').read_bytes())
        d,s=self.data();self.assertEqual(s,oracle['snapshot']);owner=self.source(d,s);previous=None
        for q,want in zip(requests(),oracle['raw']):
            with self.subTest(policy=q.pit_policy,domain=q.domain,cutoff=q.cutoff_by_session):
                selected=owner.select(query=q,previous=previous)
                self.assertIsInstance(selected,ColumnSelection)
                self.assertEqual(selected.contract_version,'data_column_selection_v1')
                self.assertEqual(selected.snapshot_ref,s)
                got=selected.to_batch();self.assertEqual(_json_bytes(got.to_json()),_json_bytes(want))
                self.assert_columns(selected,got)
                if previous: previous.close()
                previous=selected
        self.assertEqual(owner.statistics['partition_decodes'],3)
    def test_frozen_adjustment_exact_and_no_legacy_inside_adjust(self):
        oracle=json.loads((Path(__file__).parent/'fixtures/column_source_oracle_v1.json').read_bytes())
        d,s=self.data();owner=self.source(d,s);previous=None
        for (p,f,anchor),want in zip(adjustment_requests(),oracle['adjusted']):
            prices=owner.select(query=p);factors=owner.select(query=f)
            with (patch.object(ColumnSelection,'to_batch',side_effect=AssertionError('implicit batch')),
                    patch.object(owner._reader,'_read',side_effect=AssertionError('implicit legacy selector'))):
                selected=owner.adjust(prices,factors,fields=p.fields,anchor_session=anchor,previous=previous)
            self.assertEqual(owner.statistics['legacy_materializations'],0 if previous is None else oracle['adjusted'].index(want))
            self.assertEqual(set(selected.selected_version_indices),{'price','factor','anchor_factor'})
            self.assertIn(prices.selection_ref,selected.derivation.values())
            # Independent output lifetime, with no pinned full input selections.
            prices.close();factors.close()
            got=selected.to_batch();self.assertEqual(_json_bytes(got.to_json()),_json_bytes(want));self.assert_columns(selected,got)
            if previous: previous.close()
            previous=selected
        self.assertEqual(owner.statistics['partition_decodes'],3)
    def test_warm_source_and_ordinary_read_share_one_reader(self):
        d,s=self.data();owner=self.source(d,s);q=query();a=owner.select(query=q)
        with (patch.object(d.store,'_native_partition_batches',side_effect=AssertionError('warm decode')),
                patch.object(d.store,'read_partition',side_effect=AssertionError('row materialization'))):
            b=owner.select(query=replace(q,cutoff_by_session={x:EARLY for x in q.sessions}),previous=a)
            old=d.read(snapshot=s,query=b._query)
        self.assertEqual(len(d._readers),1);self.assertIs(d._readers[s],owner._reader)
        self.assertEqual(len(owner._reader._cache),3)
        self.assertTrue(all(not hasattr(value,'frame') for value,_ in owner._reader._cache.values()))
        self.assert_columns(b,old)
    def test_late_then_early_reselects_and_evidence_policy_changes(self):
        d,s=self.data();owner=self.source(d,s)
        q=query(sessions=('2024-01-02',),symbols=('A',));late=owner.select(query=q)
        early=owner.select(query=replace(q,cutoff_by_session={q.sessions[0]:EARLY}),previous=late)
        safe=owner.select(query=replace(early._query,pit_policy='market_pit_safe_v1'),previous=early)
        self.assertEqual(late.columns['close'].values[0,0],12.)
        self.assertEqual(early.columns['close'].values[0,0],10.)
        self.assertEqual(safe.columns['close'].values[0,0],12.)
        self.assertEqual(list(early.changed_keys),[('2024-01-02','A')])
        self.assertEqual(safe.provenance['availability_basis'][0,0],'revision_bound_source_evidence')
        self.assertNotEqual(early.selection_ref,safe.selection_ref)
    def test_diff_axes_versions_and_cutoff_without_value_change(self):
        d,s=self.data();owner=self.source(d,s)
        a=owner.select(query=query(sessions=('2024-01-02','2024-01-03'),symbols=('A',)))
        b=owner.select(query=query(sessions=('2024-01-03','2024-01-05'),symbols=('A','C')),previous=a)
        np.testing.assert_array_equal(b.changed_keys.added.to_numpy(),[[0,1],[1,0],[1,1]])
        np.testing.assert_array_equal(b.changed_keys.updated.to_numpy(),np.empty((0,2),dtype='i8'))
        np.testing.assert_array_equal(b.changed_keys.removed.to_numpy(),[[0,0]])
        a.close();self.assertEqual(b.changed_keys.previous_axes['sessions'],('2024-01-02','2024-01-03'))
        c=owner.select(query=replace(b._query,cutoff_by_session={s:'2024-01-07T00:00:00+00:00' for s in b.axes['sessions']}),previous=b)
        self.assertEqual(len(c.changed_keys.updated),4)
        same=owner.select(query=c._query,previous=c);self.assertEqual(len(same.changed_keys.updated),0)
        self.assertEqual(same.selection_ref,c.selection_ref)
    def test_readonly_borrows_and_owned_copies_detach(self):
        d,s=self.data();owner=self.source(d,s);a=owner.select(query=query());borrow=a.columns['close'].values
        owned=borrow.to_numpy();self.assertFalse(owned.flags.writeable)
        with self.assertRaises(ValueError): owned.setflags(write=True)
        with self.assertRaises(TypeError): borrow[0,0]=3
        with self.assertRaisesRegex(QueryError,'advanced indexing'): borrow[[0,1]]
        with self.assertRaises(TypeError): a.query_binding['purpose']='changed'
        with self.assertRaises(AttributeError): a.contract_version='changed'
        with self.assertRaises(TypeError): a.columns['close'].metadata['unit']='changed'
        with self.assertRaises(QueryError): borrow.__array__(copy=False)
        frame=a.to_batch();frame.frame.loc[0,'close']=900;frame.field_meta['close']['by_key'][0]['revision_id']='changed'
        self.assertEqual(a.provenance['revision_id'][0,0],'r1')
        owner.close()
        with self.assertRaises(QueryError): borrow[0,0]
        self.assertEqual(owned.shape,(3,3))
    def test_close_clear_refresh_snapshot_and_partition_mutation_revoke(self):
        for event in ('close','clear','refresh','snapshot','partition'):
            d,s=self.data();owner=self.source(d,s);a=owner.select(query=query());borrow=a.columns['close'].values
            if event=='close': owner.close()
            elif event=='clear': d.clear_cache()
            elif event=='refresh': d._reader(s,refresh=True)
            elif event=='snapshot':
                path=d.store.root/'snapshots'/(s+'.json');path.write_bytes(path.read_bytes()+b' ')
            else:
                path=d.store.root/'market_daily/2024-01.parquet';path.write_bytes(path.read_bytes()+b' ')
            with self.subTest(event=event),self.assertRaises(QueryError): borrow[0,0]
            self.assertTrue(owner._closed)
    def test_process_boundary_pickle_and_owner_mismatch(self):
        d,s=self.data();owner=self.source(d,s);a=owner.select(query=query())
        for value in (owner,a,a.columns['close'].values):
            with self.assertRaises(QueryError): pickle.dumps(value)
        other,t=self.data();other_owner=self.source(other,t);b=other_owner.select(query=query())
        with self.assertRaisesRegex(QueryError,'this owner'): owner.select(query=query(),previous=b)
        with patch('axiom_data.column_source.os.getpid',return_value=owner._pid+1):
            with self.assertRaisesRegex(QueryError,'process boundary'): a.selection_ref
        self.assertTrue(owner._closed)
    def test_budget_failure_cleans_construction_and_pinned_entries_stay_charged(self):
        d,s=self.data();owner=self.source(d,s,dict(cache_bytes=1000,max_working_bytes=1048576))
        with self.assertRaisesRegex(QueryError,'cache/borrow budget'): owner.select(query=query())
        self.assertEqual(owner._temporary,0);self.assertEqual(owner._reader._cached_bytes,0)
        owner.close()
        owner=self.source(d,s);a=owner.select(query=query());before=owner._usage()
        owner._reader._cache.clear();owner._reader._cached_bytes=0
        self.assertEqual(owner._usage(),before)
        with patch.object(d.store,'_native_partition_batches',side_effect=AssertionError('pinned decode')):
            b=owner.select(query=query(),previous=a)
        self.assertEqual(b.selection_ref,a.selection_ref)
        a.close();b.close();gc.collect();self.assertEqual(owner._usage(),0)
        small=self.source(*self.data(),limits=dict(cache_bytes=1048576,max_working_bytes=200000))
        with self.assertRaisesRegex(QueryError,'working budget'): small.select(query=query())
        self.assertEqual(small._temporary,0)
    def test_equal_sequence_conflict_only_sees_requested_projection(self):
        def modify(domains,rows):
            values=rows['market_daily/2024-01.parquet'];row=deepcopy(values[1]);row['close']=55.;values.append(row)
        d,s=self.data(modify);owner=self.source(d,s)
        q=query(fields=('open',),sessions=('2024-01-03',),symbols=('A',))
        self.assertEqual(owner.select(query=q).columns['open'].values[0,0],4.)
        with self.assertRaisesRegex(QueryError,'equal sequence'): owner.select(query=replace(q,fields=('close',)))
    def test_unrequested_nested_and_invalid_outside_scope_do_not_gate(self):
        def modify(domains,rows):
            for row in rows['market_daily/2024-01.parquet']: row['close']=[1.,2.]
        d,s=self.data(modify);owner=self.source(d,s);q=query(fields=('open',))
        self.assertEqual(owner.select(query=q).to_batch().to_json(),d.read(snapshot=s,query=q).to_json())
        with self.assertRaisesRegex(QueryError,'flat scalar'): owner.select(query=replace(q,fields=('close',)))
    def test_requested_invalid_clock_and_evidence_attachment_conflict_match_legacy(self):
        d,s=self.data();owner=self.source(d,s)
        with self.assertRaisesRegex(QueryError,'first_observed_at'): owner.select(query=query(symbols=('OUTSIDE',),sessions=('2024-01-02',)))
        def modify(domains,rows):
            row=rows['market_daily/2024-01.parquet'][4];row['source_available_at']='2024-01-03T00:00:00+00:00'
        d,s=self.data(modify);owner=self.source(d,s)
        owner.select(query=query(symbols=('B',)))
        with self.assertRaises(ConflictError): owner.select(query=query(symbols=('A',)))
    def test_int64_above_2_to_53_all_null_and_empty_partition(self):
        def modify(domains,rows):
            domains['market_daily']['contract']['fields']['close']['dtype']='int64'
            for row in rows['market_daily/2024-01.parquet']: row['close']=2**53+1 if row['security_id']=='A' else None
        d,s=self.data(modify);owner=self.source(d,s);a=owner.select(query=query())
        self.assertEqual(a.columns['close'].values.dtype,np.dtype('int64'))
        self.assertEqual(a.columns['close'].values[0,1],2**53+1);self.assert_columns(a,a.to_batch())
        def empty(domains,rows):
            domains.pop('public_evidence');rows['market_daily/2024-01.parquet']=[]
        d,s=self.data(empty);owner=self.source(d,s);a=owner.select(query=query())
        self.assertFalse(a.columns['close'].validity.to_numpy().any());self.assertEqual(a.columns['close'].missing_reason[0,0],'source_missing')
    def test_declared_integer_keeps_legacy_nonintegral_cast_rejection(self):
        def modify(domains,rows):
            domains['market_daily']['contract']['fields']['close']['dtype']='int64'
            for value in rows['market_daily/2024-01.parquet']: value['close']=1.5
        d,s=self.data(modify);owner=self.source(d,s)
        with self.assertRaises(TypeError): owner.select(query=query())
        self.assertEqual(owner._temporary,0)
    def test_array_identity_binds_dtype_shape_actual_bytes_and_not_strides(self):
        self.assertNotEqual(_array_ref(np.array([1],dtype='i8')),_array_ref(np.array([1],dtype='f8')))
        self.assertNotEqual(_array_ref(np.array([1],dtype='i8')),_array_ref(np.array([2],dtype='i8')))
        self.assertNotEqual(_array_ref(np.array([1],dtype='i8')),_array_ref(np.array([[1]],dtype='i8')))
        self.assertNotEqual(_array_ref(np.array([-0.],dtype='f8')),_array_ref(np.array([0.],dtype='f8')))
        a=np.arange(12,dtype='>i8').reshape(3,4)[:,::2]
        self.assertEqual(_array_ref(a),_array_ref(np.array(a,dtype='<i8',order='C')))
        self.assertTrue(_array_ref(np.empty((0,2),dtype='i8')).startswith('sha256:'))
    def test_io_paging_does_not_change_logical_bindings(self):
        d,s=self.data();owner=self.source(d,s);a=owner.select(query=query())
        ref,blocks,indices=a.selection_ref,a.source_block_refs,a.selected_version_indices['native'].to_numpy()
        owner.close()
        other=Data(d.store.root);original=other.store._native_partition_batches
        def one(part,**kw): return original(part,**dict(kw,batch_size=1))
        with patch.object(other.store,'_native_partition_batches',side_effect=one):
            owner=self.source(other,s);b=owner.select(query=query())
        self.assertEqual(b.selection_ref,ref);self.assertEqual(b.source_block_refs,blocks)
        np.testing.assert_array_equal(b.selected_version_indices['native'].to_numpy(),indices)
        self.assertGreater(owner.statistics['record_batches'],2)
    def test_nanosecond_availability_is_preserved_from_arrow(self):
        stamp=pd.Timestamp('2024-01-01T00:00:00.000000001Z')
        def modify(domains,rows):
            for value in rows['market_daily/2024-01.parquet']: value['first_observed_at']=stamp
        d,s=self.data(modify,column_types={'market_daily':{'first_observed_at':pa.timestamp('ns',tz='UTC')}})
        owner=self.source(d,s);a=owner.select(query=query(symbols=('A',)))
        self.assertEqual(a.columns['close'].available_at[0,0],np.datetime64('2024-01-01T00:00:00.000000001','ns'))
        self.assert_columns(a,a.to_batch())
    def test_real_lru_eviction_cannot_hide_borrowed_blocks_from_budget(self):
        def modify(domains,rows):
            domain=domains['market_daily'];part=deepcopy(domain['partitions'][0])
            part.update(partition='2024-02',uri='market_daily/2024-02.parquet')
            rows[part['uri']]=[dict(rows['market_daily/2024-01.parquet'][0],session='2024-02-02')]
            domain['partitions'].append(part)
        d,s=self.data(modify);owner=self.source(d,s);a=owner.select(query=query());charge=owner._usage();owner.close()
        owner=self.source(d,s,dict(LIMITS,cache_bytes=charge));a=owner.select(query=query())
        q=query(sessions=('2024-02-02',),symbols=('A',))
        with self.assertRaisesRegex(QueryError,'cache/borrow budget'): owner.select(query=q)
        self.assertEqual(owner._usage(),charge);self.assertEqual(a.columns['close'].values[1,1],12.)
        self.assertEqual(owner._temporary,0);a.close();gc.collect()
        b=owner.select(query=q);self.assertEqual(b.columns['close'].values[0,0],10.)
        self.assertLessEqual(owner.statistics['peak_source_bytes'],charge)
    def test_requested_revision_scratch_rejected_before_row_expansion(self):
        def modify(domains,rows):
            domains.pop('public_evidence');base=rows['market_daily/2024-01.parquet'][0]
            rows['market_daily/2024-01.parquet']=[dict(base,revision_id='x'*8000,revision_sequence=i) for i in range(100)]
        d,s=self.data(modify);owner=self.source(d,s,dict(cache_bytes=1048576,max_working_bytes=1048576))
        from axiom_data.column_source import _RowView
        with patch('axiom_data.column_source._RowView',wraps=_RowView) as row:
            with self.assertRaisesRegex(QueryError,'working budget'): owner.select(query=query(symbols=('A',),sessions=('2024-01-02',)))
        self.assertEqual(row.call_count,0);self.assertEqual(owner._temporary,0)
        self.assertLessEqual(owner.statistics['peak_working_bytes'],1048576)
    def test_conflicting_evidence_index_retains_original_domain_scope(self):
        def modify(domains,rows):
            values=rows['public_evidence/history.parquet'];values.append(dict(values[0],public_at='2024-01-03T00:00:00+00:00'))
        d,s=self.data(modify);owner=self.source(d,s)
        with self.assertRaises(ConflictError): owner.select(query=query(symbols=('B',)))
        self.assertEqual(owner._temporary,0)
    def test_explicit_legacy_copy_is_reserved_before_records_by_key(self):
        d,s=self.data();owner=self.source(d,s,dict(cache_bytes=1048576,max_working_bytes=1048576))
        q=query(symbols=tuple('S'+str(i) for i in range(150)),sessions=('2024-01-02',))
        selected=owner.select(query=q)
        with patch.object(owner._reader,'_read',side_effect=AssertionError('expanded result')):
            with self.assertRaisesRegex(QueryError,'working budget'): selected.to_batch()
        self.assertEqual(owner._temporary,0)
    def test_public_rolling_consumption_preserves_output_cutoff_and_anchor(self):
        d,s=self.data();owner=self.source(d,s);previous_price=previous_factor=None;outputs=[]
        for sessions,cutoff,anchor in ((('2024-01-02','2024-01-03'),EARLY,'2024-01-03'),
                (('2024-01-02','2024-01-03'),LATE,'2024-01-03'),
                (('2024-01-03','2024-01-05'),LATE,'2024-01-05')):
            p=query(fields=('close',),sessions=sessions,symbols=('A',),cutoff=cutoff)
            f=query('adjustment_factors',sessions=sessions,symbols=('A',),cutoff=cutoff)
            prices=owner.select(query=p,previous=previous_price);factors=owner.select(query=f,previous=previous_factor)
            adjusted=owner.adjust(prices,factors,fields=('close',),anchor_session=anchor)
            column=adjusted.columns['close']
            outputs.append((column.values.to_numpy(),column.validity.to_numpy(),adjusted.query_binding,adjusted.selection_ref))
            self.assertEqual(adjusted.derivation['anchor_session'],anchor)
            if previous_price: previous_price.close();previous_factor.close()
            previous_price,previous_factor=prices,factors;adjusted.close()
        self.assertEqual(outputs[0][0][0,0],5.);self.assertEqual(outputs[1][0][0,0],3.)
        self.assertFalse(outputs[2][1].any());self.assertEqual(outputs[2][2]['adjustment_anchor'],'2024-01-05')
        self.assertNotEqual(outputs[0][3],outputs[1][3]);self.assertEqual(owner.statistics['partition_decodes'],3)
    def test_adjust_policies_anchor_and_numeric_operation_order(self):
        def modify(domains,rows):
            for value in rows['market_daily/2024-01.parquet']:
                if value['security_id']=='A': value['close']=1e308
            for value in rows['adjustment_factors/2024-01.parquet']:
                if value['security_id']=='A': value['factor']=2.
        d,s=self.data(modify);owner=self.source(d,s);p,f,anchor=adjustment_requests()[0]
        prices=owner.select(query=p);factors=owner.select(query=f)
        adjusted=owner.adjust(prices,factors,fields=('close',),anchor_session=anchor)
        self.assertEqual(adjusted.columns['close'].missing_reason[0,1],'invalid_adjusted_value')
        # (price * factor) / anchor overflows; reassociating as price*(factor/anchor) would incorrectly stay finite.
        old=adjust_prices(prices.to_batch(),factors.to_batch(),fields=('close',),anchor_session=anchor,factor_field='factor')
        self.assert_columns(adjusted,old)
        vendor=owner.select(query=replace(f,pit_policy='best_effort_vendor_v1'))
        with self.assertRaisesRegex(QueryError,'PIT policies'): owner.adjust(prices,vendor,fields=('close',),anchor_session=anchor)
        hp=replace(p,pit_policy='bootstrap_hybrid_v1',policy_by_session={s:'operational_pit_v1' for s in p.sessions})
        hf=replace(f,pit_policy='bootstrap_hybrid_v1',policy_by_session={s:'best_effort_vendor_v1' for s in f.sessions})
        with self.assertRaisesRegex(QueryError,'per-session PIT policies'):
            owner.adjust(owner.select(query=hp),owner.select(query=hf),fields=('close',),anchor_session=anchor)
    def test_decision_cutoff_need_not_fit_available_array_range(self):
        d,s=self.data();owner=self.source(d,s)
        for cutoff in ('1000-01-01T00:00:00+00:00','9999-01-01T00:00:00+00:00'):
            p=query(cutoff=cutoff,sessions=('2024-01-02','2024-01-03'),symbols=('A',))
            f=query('adjustment_factors',cutoff=cutoff,sessions=p.sessions,symbols=p.symbols)
            prices=owner.select(query=p);factors=owner.select(query=f)
            adjusted=owner.adjust(prices,factors,fields=p.fields,anchor_session='2024-01-03')
            old=adjust_prices(prices.to_batch(),factors.to_batch(),fields=p.fields,anchor_session='2024-01-03',factor_field='factor')
            self.assert_columns(adjusted,old)
    def test_adjust_validation_anchor_revision_clock_and_missing_precedence(self):
        d,s=self.data();owner=self.source(d,s);p,f,anchor=adjustment_requests()[0]
        prices=owner.select(query=p);factors=owner.select(query=f)
        a=owner.adjust(prices,factors,fields=p.fields,anchor_session=anchor)
        self.assertEqual(a.columns['close'].values[0,1],3.)
        self.assertEqual(a.provenance['anchor_factor']['revision_id'][0,1],'r2')
        self.assertEqual(a.columns['close'].available_at[0,1],np.datetime64('2024-01-04T00:00:00','us'))
        self.assertEqual(a.columns['close'].missing_reason[1,0],'invalid_price')
        self.assertEqual(a.columns['open'].missing_reason[1,0],'price_missing')
        for changes,message in ((dict(anchor_session='2024-01-04'),'future adjustment'),
                (dict(decision_session='2024-01-02'),'latest price'),(dict(fields=('volume',)),'present in prices')):
            args=dict(fields=p.fields,anchor_session=anchor);args.update(changes)
            with self.assertRaisesRegex(QueryError,message): owner.adjust(prices,factors,**args)
        earlier=owner.select(query=replace(f,cutoff_by_session={s:EARLY for s in f.sessions}))
        with self.assertRaisesRegex(QueryError,'same decision cutoff'): owner.adjust(prices,earlier,fields=p.fields,anchor_session=anchor)
        mixed=owner.select(query=replace(p,cutoff_by_session={p.sessions[0]:EARLY,p.sessions[1]:LATE}))
        with self.assertRaisesRegex(QueryError,'single decision cutoff'): owner.adjust(mixed,factors,fields=p.fields,anchor_session=anchor)
    def test_limits_fixed_snapshot_scope_and_single_owner(self):
        d,s=self.data()
        for snapshot,limits in (('current',LIMITS),(s,{}),(s,dict(LIMITS,cache_bytes=True))):
            with self.assertRaises(QueryError): d.open_column_source(snapshot=snapshot,limits=limits)
        owner=self.source(d,s)
        with self.assertRaisesRegex(QueryError,'already owns'): d.open_column_source(snapshot=s,limits=LIMITS)
        with self.assertRaises(QueryError): owner.select(query=replace(query(),fields=('volume',)))


if __name__=='__main__': unittest.main()
