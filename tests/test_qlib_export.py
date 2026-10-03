"""Qlib export semantics and actual optional-runtime consumer equivalence."""
from dataclasses import replace
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


DAYS = ("2020-01-02", "2020-01-03", "2020-01-06")
SYMBOLS = ("stock-A", "stock-missing")


def fixture(root):
    data = Data(root)
    market = {"contract_id": "market.test.v1", "logical_key": ["security_id", "session"],
              "fields": {"security_id": {"dtype": "string"}, "session": {"dtype": "date"},
                         "close": {"dtype": "float64", "unit": "CNY/share"},
                         "volume_shares": {"dtype": "int64", "unit": "share"}}}
    calendar = {"contract_id": "calendar.test.v1", "logical_key": ["session"],
                "fields": {"session": {"dtype": "date"}, "is_open": {"dtype": "bool"}}}
    batches = []
    for domain, contract, rows in (
        ("market_daily", market, [{"security_id": "stock-A", "session": d,
              "close": c, "volume_shares": 100+i} for i,(d,c) in enumerate(zip(DAYS,(10.125,None,12.2)))]),
        ("trading_calendar", calendar, [{"session":d,"is_open":True} for d in DAYS])):
        profile = {"id": "synthetic."+domain, "field_map": {k:k for k in contract["fields"]},
                   "source_units": {k:v.get("unit") for k,v in contract["fields"].items() if v.get("unit")},
                   "availability": {"timezone": "Asia/Shanghai", "session_release_time": "20:00:00"}}
        if domain == "trading_calendar":
            # This test calendar is already usable before opening. Early price
            # cutoffs test unknown prices, not an unavailable calendar.
            profile["availability"]["session_release_time"] = "00:00:00"
        batches.append(IngestBatch(domain,json.dumps(rows).encode(),{},contract,profile,"2026-10-03T00:00:00Z"))
    sid = data.update(base_snapshot=None, request=UpdateRequest(tuple(batches),"fixture",{})).snapshot_id
    query = QuerySpec("market_daily",("close","volume_shares"),SYMBOLS,DAYS,
                      "best_effort_vendor_v1",{d:d+"T20:01:00+08:00" for d in DAYS})
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
        self.assertEqual(main(['--data-root',str(self.root/'data'),'qlib-export','--spec',str(spec),'--destination',str(self.view)]),0)
        self.assertEqual(main(['--data-root',str(self.root/'data'),'qlib-verify','--view',str(self.view),'--against-reader']),0)

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
