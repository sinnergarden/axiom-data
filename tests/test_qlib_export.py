"""Qlib export semantics and actual optional-runtime consumer equivalence."""
from dataclasses import replace
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from axiom_data import Data, DataBatch, DataError, IngestBatch, QuerySpec, UpdateRequest, verify_qlib_export
from axiom_data.cli import main
from axiom_data.qlib_export import _calendar
from axiom_data.storage import LocalStore


DAYS = ("2020-01-02", "2020-01-03", "2020-01-06")
SYMBOLS = ("stock-A", "stock-missing")


def fixture(root, days=DAYS):
    data = Data(root)
    market = {"contract_id": "market.test.v1", "logical_key": ["security_id", "session"],
              "fields": {"security_id": {"dtype": "string"}, "session": {"dtype": "date"},
                         "close": {"dtype": "float64", "unit": "CNY/share"},
                         "volume_shares": {"dtype": "int64", "unit": "share"}}}
    calendar = {"contract_id": "calendar.test.v1", "logical_key": ["session"],
                "fields": {"session": {"dtype": "date"}, "is_open": {"dtype": "bool"}}}
    factor = {"contract_id": "factor.test.v1", "logical_key": ["security_id", "session"],
              "fields": {"security_id": {"dtype": "string"}, "session": {"dtype": "date"},
                         "factor": {"dtype": "float64", "unit": "ratio"}}}
    member = {"contract_id": "member.test.v1", "logical_key": ["membership_id"],
              "fields": {"security_id": {"dtype": "string"}, "universe_id": {"dtype": "string"},
                         "membership_id": {"dtype": "string"}, "effective_from": {"dtype": "date"},
                         "effective_to": {"dtype": "date"}}}
    batches = []
    for domain, contract, rows in (
        ("market_daily", market, [{"security_id": "stock-A", "session": d,
              "close": (10.125,None,12.2)[i % 3], "volume_shares": 100+i} for i,d in enumerate(days)]),
        ("trading_calendar", calendar, [{"session":d,"is_open":True} for d in days]),
        ("adjustment_factors", factor, [{"security_id":s,"session":d,"factor":1+i/4}
                                       for s in SYMBOLS for i,d in enumerate(days)]),
        ("universe_membership", member, [{"security_id":s,"universe_id":"U","membership_id":s,
                                         "effective_from":days[0],"effective_to":None} for s in SYMBOLS])):
        profile = {"id": "synthetic."+domain, "field_map": {k:k for k in contract["fields"]},
                   "source_units": {k:v.get("unit") for k,v in contract["fields"].items() if v.get("unit")},
                   "availability": {"timezone": "Asia/Shanghai", "session_release_time": "20:00:00"}}
        if domain == "trading_calendar":
            # This test calendar is already usable before opening. Early price
            # cutoffs test unknown prices, not an unavailable calendar.
            profile["availability"]["session_release_time"] = "00:00:00"
        batches.append(IngestBatch(domain,json.dumps(rows).encode(),{},contract,profile,"2026-10-03T00:00:00Z"))
    sid = data.update(base_snapshot=None, request=UpdateRequest(tuple(batches),"fixture",{})).snapshot_id
    query = QuerySpec("market_daily",("close","volume_shares"),SYMBOLS,days,
                      "best_effort_vendor_v1",{d:d+"T20:01:00+08:00" for d in days})
    return data,sid,query


class QlibExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.data,self.sid,self.q = fixture(self.root/"data"); self.view=self.root/"view"

    def tearDown(self):
        self.temp.cleanup()

    def export(self, **kwargs):
        return self.data.export_qlib(snapshot=self.sid,queries=(self.q,),destination=self.view,**kwargs)

    def test_native_units_nulls_float32_and_idempotent_query_identity(self):
        before=(self.root/"data/current.json").read_bytes(); m=self.export()
        self.assertEqual(m['fields']['volume_shares']['unit'],'share')
        values=np.fromfile(self.view/'features/stock-a/close.day.bin',dtype='<f4')
        np.testing.assert_allclose(values,[0,10.125,np.nan,12.2],equal_nan=True,rtol=1e-6)
        self.assertTrue(np.isnan(np.fromfile(self.view/'features/stock-missing/close.day.bin',dtype='<f4')[1:]).all())
        self.assertEqual(verify_qlib_export(self.view,data=self.data)['view_id'],m['view_id'])
        with patch.object(self.data,'read',side_effect=AssertionError('existing view must not reread')):
            self.assertEqual(self.export()['view_id'],m['view_id'])
        with self.assertRaisesRegex(DataError,'different immutable'):
            self.export(field_aliases={'market_daily.volume_shares':'volume'})
        self.assertEqual((self.root/'data/current.json').read_bytes(),before)

    def test_calendar_holes_and_ambiguous_instrument_identity_rejected(self):
        incomplete=replace(self.q,sessions=(DAYS[0],DAYS[-1]),cutoff_by_session={d:self.q.cutoff_by_session[d] for d in (DAYS[0],DAYS[-1])})
        with self.assertRaisesRegex(DataError,'every open session'):
            self.data.export_qlib(snapshot=self.sid,queries=(incomplete,),destination=self.view)
        with self.assertRaisesRegex(DataError,'unique safe'):
            self.export(instrument_map={'stock-A':'SAME','stock-missing':'same'})
        self.assertFalse(self.view.exists())

    def test_rounding_failure_is_atomic_and_corruption_is_not_rebuilt(self):
        before=self.data.resolve('current')
        with self.assertRaisesRegex(DataError,'tolerance'):
            self.export(rtol=0,atol=0)
        self.assertFalse(self.view.exists()); self.assertEqual(self.data.resolve('current'),before)
        self.export(); p=self.view/'features/stock-a/close.day.bin'; p.write_bytes(p.read_bytes()+b'bad')
        with self.assertRaisesRegex(DataError,'integrity'):
            verify_qlib_export(self.view)

    def test_cutoffs_change_view_and_unknown_membership_is_not_false(self):
        early=replace(self.q,cutoff_by_session={d:d+'T09:30:00+08:00' for d in DAYS})
        m=self.data.export_qlib(snapshot=self.sid,queries=(early,),destination=self.view)
        self.assertTrue(np.isnan(np.fromfile(self.view/'features/stock-a/close.day.bin',dtype='<f4')[1:]).all())
        membership=replace(self.q,domain='universe_membership',fields=('is_member',),universe_id='U')
        frame=pd.DataFrame([{'security_id':s,'session':d,'is_member':None} for s in SYMBOLS for d in DAYS])
        with patch.object(self.data,'members',return_value=DataBatch(frame,{},{})):
            with self.assertRaisesRegex(DataError,'unknown PIT membership'):
                self.data.export_qlib(snapshot=self.sid,queries=(self.q,),destination=self.root/'unknown',universe_query=membership)
        self.assertFalse((self.root/'unknown').exists())

    def test_cli_uses_saved_queries_without_source_client(self):
        spec=self.root/'spec.json'
        spec.write_text(json.dumps({'queries':[{'domain':self.q.domain,'fields':self.q.fields,'symbols':self.q.symbols,
             'sessions':self.q.sessions,'pit_policy':self.q.pit_policy,'cutoff_by_session':dict(self.q.cutoff_by_session)}]}))
        with patch.object(LocalStore,'load_snapshot',autospec=True,side_effect=LocalStore.load_snapshot) as load:
            self.assertEqual(main(['--data-root',str(self.root/'data'),'qlib-export','--snapshot',self.sid,
                                   '--spec',str(spec),'--destination',str(self.view)]),0)
            self.assertEqual(load.call_count,1)
            self.assertEqual(load.call_args.args[1],self.sid)
        with patch.object(LocalStore,'load_snapshot',autospec=True,side_effect=LocalStore.load_snapshot) as load:
            self.assertEqual(main(['--data-root',str(self.root/'data'),'qlib-export','--spec',str(spec),
                                   '--destination',str(self.root/'alias-view')]),0)
            self.assertEqual(load.call_count,2)  # Alias resolution retains its separate integrity check.
        with patch.object(LocalStore,'load_snapshot',autospec=True,side_effect=LocalStore.load_snapshot) as load:
            self.assertEqual(main(['--data-root',str(self.root/'data'),'qlib-verify','--view',str(self.view),'--against-reader']),0)
            self.assertEqual(load.call_count,1)

    def test_multiple_domains_months_and_members_share_one_fresh_manifest(self):
        data,sid,q=fixture(self.root/'monthly-data',("2020-01-30","2020-01-31","2020-02-03"))
        factor=replace(q,domain='adjustment_factors',fields=('factor',))
        member=replace(q,domain='universe_membership',fields=('is_member',),universe_id='U')
        calendar_uris={part['uri'] for part in data.store.load_snapshot(sid)['domains']['trading_calendar']['partitions']}
        with patch.object(data.store,'load_snapshot',wraps=data.store.load_snapshot) as load, \
                patch('axiom_data.qlib_export._calendar',wraps=_calendar) as calendar, \
                patch.object(data.store,'read_partition',wraps=data.store.read_partition) as partitions, \
                patch.object(data,'read',wraps=data.read) as read:
            result=data.export_qlib(snapshot=sid,queries=(q,factor),destination=self.view,universe_query=member)
            self.assertEqual(load.call_count,1)
            self.assertEqual(calendar.call_count,1)
            self.assertEqual(sum(c.args[0]['uri'] in calendar_uris for c in partitions.call_args_list),2)
            self.assertEqual(read.call_count,6)  # Two monthly batches for each of the three queries.
            self.assertTrue(all(c.kwargs['snapshot']==sid for c in read.call_args_list))
            self.assertEqual(verify_qlib_export(self.view,data=data)['view_id'],result['view_id'])
            self.assertEqual(load.call_count,1)
            data.export_qlib(snapshot=sid,queries=(q,factor),destination=self.root/'second',universe_query=member)
            self.assertEqual(load.call_count,2)  # A warm Data must still recheck every new export.
            self.assertEqual(calendar.call_count,2)  # Calendar validation is local to each export.
            self.assertEqual(sum(c.args[0]['uri'] in calendar_uris for c in partitions.call_args_list),4)
        np.testing.assert_allclose(np.fromfile(self.view/'features/stock-a/factor.day.bin',dtype='<f4'),[0,1,1.25,1.5])
        self.assertEqual((self.view/'instruments/universe.txt').read_text(),
                         'STOCK-A\t2020-01-30\t2020-02-03\nSTOCK-MISSING\t2020-01-30\t2020-02-03\n')

    def test_each_query_keeps_its_calendar_policy_and_cutoff(self):
        # The first query sees the calendar; the second strict query does not.
        strict=replace(self.q,fields=('volume_shares',),pit_policy='operational_pit_v1')
        price=replace(self.q,fields=('close',))
        early=replace(self.q,fields=('volume_shares',),
                      cutoff_by_session={d:d+'T00:00:00+09:00' for d in DAYS})
        for second in (strict,early):
            with self.subTest(policy=second.pit_policy), \
                    patch.object(self.data.store,'load_snapshot',wraps=self.data.store.load_snapshot) as load, \
                    patch('axiom_data.qlib_export._calendar',wraps=_calendar) as calendar:
                with self.assertRaisesRegex(DataError,'calendar is unknown'):
                    self.data.export_qlib(snapshot=self.sid,queries=(price,second),destination=self.view)
                self.assertEqual(load.call_count,1)
                self.assertEqual(calendar.call_count,2)
            self.assertFalse(self.view.exists())

    def test_warm_export_rejects_tampered_manifest_and_partition(self):
        self.data.read(snapshot=self.sid,query=self.q)
        manifest_path=self.root/'data/snapshots'/(self.sid+'.json')
        original=manifest_path.read_bytes(); manifest=json.loads(original)
        manifest['build_context']['tampered']=True
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(DataError,'digest mismatch'):
            self.export()
        self.assertFalse(self.view.exists())
        manifest_path.write_bytes(original)
        self.export()
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(DataError,'digest mismatch'):
            self.export()  # Existing destinations cannot bypass fresh source integrity either.
        manifest_path.write_bytes(original)
        part=manifest['domains']['market_daily']['partitions'][0]
        path=self.root/'data'/part['uri']; path.write_bytes(path.read_bytes()+b'bad')
        with self.assertRaisesRegex(DataError,'SHA-256'):
            self.data.export_qlib(snapshot=self.sid,queries=(self.q,),destination=self.root/'damaged-partition')
        self.assertFalse((self.root/'damaged-partition').exists())

    def test_exports_do_not_mix_snapshots(self):
        original=self.data.store.load_snapshot(self.sid)
        changed=self.data.store.write_partition('market_daily','2020-01',[
            {'security_id':'stock-A','session':d,'close':20.0,'volume_shares':200,
             'revision_id':'r2','first_observed_at':'2026-10-03T00:00:00Z'} for d in DAYS],
            original['domains']['market_daily']['contract'])
        domains=dict(original['domains'])
        domains['market_daily']=dict(domains['market_daily'],partitions=[changed])
        second=self.data.store.publish_snapshot(domains,parent_snapshot=self.sid,build_context={'test':'second'})['snapshot_id']
        self.data.max_readers=1
        with patch.object(self.data.store,'load_snapshot',wraps=self.data.store.load_snapshot) as load:
            for i,sid in enumerate((self.sid,second,self.sid)):
                destination=self.root/('snapshot-'+str(i))
                result=self.data.export_qlib(snapshot=sid,queries=(self.q,),destination=destination)
                self.assertEqual(load.call_count,i+1)
                self.assertEqual(result['snapshot_id'],sid)
                expected=[0,20,20,20] if sid==second else [0,10.125,np.nan,12.2]
                np.testing.assert_allclose(np.fromfile(destination/'features/stock-a/close.day.bin',dtype='<f4'),
                                           expected,equal_nan=True,rtol=1e-6)

    def calendar_snapshot(self, rows):
        manifest=self.data.store.load_snapshot(self.sid)
        domain=manifest['domains']['trading_calendar']
        part=self.data.store.write_partition('trading_calendar','history',rows,domain['contract'])
        domains=dict(manifest['domains'],trading_calendar=dict(domain,partitions=[part]))
        return self.data.store.publish_snapshot(domains,parent_snapshot=self.sid,
                                               build_context={'test':'calendar-revisions'})['snapshot_id']

    def test_calendar_reuse_matches_uncached_export_and_native_provenance(self):
        factor=replace(self.q,domain='adjustment_factors',fields=('factor',))
        member=replace(self.q,domain='universe_membership',fields=('is_member',),universe_id='U')
        read=self.data.read
        observed=[]
        def capture(**kwargs):
            batch=read(**kwargs); observed.append(deepcopy(batch.to_json())); return batch
        with patch.object(self.data,'read',side_effect=capture), \
                patch('axiom_data.qlib_export._calendar',wraps=_calendar) as calendar:
            shared=self.data.export_qlib(snapshot=self.sid,queries=(self.q,factor),
                                         destination=self.view,universe_query=member)
            self.assertEqual(calendar.call_count,1)
        shared_batches=observed[:]; observed.clear()
        # Force validation for each query as the uncached reference, without
        # duplicating its availability/revision algorithm or changing inputs.
        with patch.object(self.data,'read',side_effect=capture), \
                patch('axiom_data.qlib_export._calendar_signature',side_effect=lambda *args: object()), \
                patch('axiom_data.qlib_export._calendar',wraps=_calendar) as calendar:
            uncached=self.data.export_qlib(snapshot=self.sid,queries=(self.q,factor),
                destination=self.root/'uncached',universe_query=member)
            self.assertEqual(calendar.call_count,3)
        self.assertEqual(shared,uncached)
        self.assertEqual(shared_batches,observed)  # Includes field_meta/context, not just numeric values.
        for name in shared['files']:
            self.assertEqual((self.view/name).read_bytes(),(self.root/'uncached'/name).read_bytes())

    def test_calendar_cutoffs_select_distinct_visible_revisions(self):
        rows=[{'session':d,'is_open':True,'revision_id':'early-'+d,
               'revision_sequence':1,'first_observed_at':'2019-12-01T00:00:00Z'} for d in DAYS]
        rows.append({'session':DAYS[0],'is_open':False,'revision_id':'late',
                     'revision_sequence':2,'first_observed_at':'2020-01-02T10:00:00Z'})
        sid=self.calendar_snapshot(rows)
        early=replace(self.q,fields=('close',),pit_policy='operational_pit_v1',
                      cutoff_by_session={d:d+'T00:01:00+08:00' for d in DAYS})
        late=replace(early,fields=('volume_shares',),cutoff_by_session=self.q.cutoff_by_session)
        with patch('axiom_data.qlib_export._calendar',wraps=_calendar) as calendar:
            with self.assertRaisesRegex(DataError,'every open session'):
                self.data.export_qlib(snapshot=sid,queries=(early,late),destination=self.view)
            self.assertEqual(calendar.call_count,2)
        self.assertFalse(self.view.exists())

    def test_hybrid_calendar_keeps_every_session_policy(self):
        first=replace(self.q,fields=('close',),pit_policy='bootstrap_hybrid_v1',
                      policy_by_session={d:'best_effort_vendor_v1' for d in DAYS})
        second=replace(first,fields=('volume_shares',),
                       policy_by_session={d:('operational_pit_v1' if d==DAYS[0]
                                             else 'best_effort_vendor_v1') for d in DAYS})
        with patch('axiom_data.qlib_export._calendar',wraps=_calendar) as calendar:
            with self.assertRaisesRegex(DataError,'calendar is unknown'):
                self.data.export_qlib(snapshot=self.sid,queries=(first,second),destination=self.view)
            self.assertEqual(calendar.call_count,2)
        self.assertFalse(self.view.exists())

    def test_omitted_calendar_date_does_not_borrow_later_strict_cutoff(self):
        rows=[{'session':d,'is_open':True,'revision_id':d,'revision_sequence':1,
               'first_observed_at':'2019-12-01T00:00:00Z'} for d in DAYS]
        rows.append({'session':'2020-01-04','is_open':False,'revision_id':'closed',
                     'revision_sequence':1,'first_observed_at':'2020-01-04T00:00:00+08:00'})
        sid=self.calendar_snapshot(rows)
        vendor=replace(self.q,fields=('close',))
        strict=replace(self.q,fields=('volume_shares',),pit_policy='operational_pit_v1')
        with patch('axiom_data.qlib_export._calendar',wraps=_calendar) as calendar:
            with self.assertRaisesRegex(DataError,'calendar is unknown.*2020-01-04'):
                self.data.export_qlib(snapshot=sid,queries=(vendor,strict),destination=self.view)
            self.assertEqual(calendar.call_count,2)
        self.assertFalse(self.view.exists())

    def test_calendar_reuse_rechecks_changed_source_files(self):
        manifest=self.data.store.load_snapshot(self.sid)
        path=self.root/'data'/manifest['domains']['trading_calendar']['partitions'][0]['uri']
        original=path.read_bytes()
        def corrupt_after_validation(*args):
            _calendar(*args); path.write_bytes(original+b'bad')
        price=replace(self.q,fields=('close',)); volume=replace(self.q,fields=('volume_shares',))
        try:
            with patch('axiom_data.qlib_export._calendar',side_effect=corrupt_after_validation) as calendar:
                with self.assertRaisesRegex(DataError,'SHA-256'):
                    self.data.export_qlib(snapshot=self.sid,queries=(price,volume),destination=self.view)
                self.assertEqual(calendar.call_count,1)
            self.assertFalse(self.view.exists())
        finally:
            path.write_bytes(original)

    def test_calendar_cross_month_open_hole_is_rejected(self):
        data,sid,q=fixture(self.root/'month-hole',('2020-01-30','2020-01-31','2020-02-03'))
        days=(q.sessions[0],q.sessions[-1])
        incomplete=replace(q,sessions=days,cutoff_by_session={d:q.cutoff_by_session[d] for d in days})
        with self.assertRaisesRegex(DataError,'every open session'):
            data.export_qlib(snapshot=sid,queries=(incomplete,),destination=self.view)
        self.assertFalse(self.view.exists())

    def test_calendar_validation_does_not_cross_snapshot_revisions(self):
        price=replace(self.q,fields=('close',)); volume=replace(self.q,fields=('volume_shares',))
        with patch('axiom_data.qlib_export._calendar',wraps=_calendar) as calendar:
            self.data.export_qlib(snapshot=self.sid,queries=(price,volume),destination=self.view)
            rows=[{'session':d,'is_open':d!=DAYS[0],'revision_id':'changed-'+d,
                   'revision_sequence':2,'first_observed_at':'2019-12-01T00:00:00Z'} for d in DAYS]
            changed=self.calendar_snapshot(rows)
            with self.assertRaisesRegex(DataError,'every open session'):
                self.data.export_qlib(snapshot=changed,queries=(price,volume),destination=self.root/'changed-calendar')
            self.assertEqual(calendar.call_count,2)
        self.assertFalse((self.root/'changed-calendar').exists())

    @unittest.skipUnless(importlib.util.find_spec('qlib'), 'install axiom-data[qlib] for actual Qlib acceptance')
    def test_actual_qlib_null_keys_relocation_and_provider_switch(self):
        from axiom_research.qlib_adapter import QlibView
        self.export(); a=QlibView(self.view).activate()
        actual=a.read(fields=('close','volume_shares'))
        expected=self.data.read(snapshot=self.sid,query=self.q).frame
        expected['instrument']=expected['security_id'].str.upper(); expected['datetime']=pd.to_datetime(expected['session'])
        expected=expected.set_index(['instrument','datetime']).sort_index()
        np.testing.assert_allclose(actual['$close'],expected['close'].to_numpy(dtype=float,na_value=np.nan),equal_nan=True,rtol=1e-6)
        np.testing.assert_allclose(actual['$volume_shares'],expected['volume_shares'].to_numpy(dtype=float,na_value=np.nan),equal_nan=True)
        moved=self.root/'moved'; shutil.copytree(self.view,moved); b=QlibView(moved).activate()
        pd.testing.assert_frame_equal(actual,b.read(fields=('close','volume_shares')))
        self.assertEqual(a.reference,b.reference)
        with self.assertRaisesRegex(ValueError,'switched'):
            a.read(fields=('close',))
        verify_qlib_export(moved)

    @unittest.skipUnless(importlib.util.find_spec('qlib'), 'install axiom-data[qlib] for actual Qlib acceptance')
    def test_actual_qlib_membership_intervals_include_exit_and_reentry(self):
        from axiom_research.qlib_adapter import QlibView
        from qlib.data import D
        membership=replace(self.q,domain='universe_membership',fields=('is_member',),universe_id='U')
        frame=pd.DataFrame([{'security_id':s,'session':d,'is_member':(s=='stock-A' and d!=DAYS[1])} for s in SYMBOLS for d in DAYS])
        with patch.object(self.data,'members',return_value=DataBatch(frame,{},{})):
            self.export(universe_query=membership)
        view=QlibView(self.view).activate(); result=view.read(fields=('close',),universe='universe')
        self.assertEqual(tuple(result.index.get_level_values('datetime').strftime('%Y-%m-%d')), (DAYS[0],DAYS[-1]))
        members=D.list_instruments(D.instruments('universe'),start_time=DAYS[0],end_time=DAYS[-1],as_list=False)
        self.assertEqual(len(members['STOCK-A']),2)


if __name__=='__main__': unittest.main()
