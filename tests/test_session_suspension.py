import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from axiom_data import ArtifactError,TushareMarketBuilder,TushareDm1Builder
from axiom_data.session_suspension import qualified_partial_halt


class SessionSuspensionTest(unittest.TestCase):
    def test_real_intraday_events_preserve_traded_bars_and_status(self):
        fixture=json.loads(Path('tests/fixtures/intraday_traded_bars.json').read_bytes())
        grouped={k:[] for k in ('daily','adj_factor','daily_basic','stk_limit','suspend_d')}
        for item in fixture:
            event={k:v for k,v in item['suspension'].items() if k!='raw_ref'}
            self.assertTrue(qualified_partial_halt(event,item['daily']))
            grouped['suspend_d'].append(event);grouped['daily'].append(item['daily'])
        symbols={r['ts_code'] for r in grouped['daily']}
        with self.assertRaisesRegex(ArtifactError,'non-full-day'):
            TushareMarketBuilder._market_rows(grouped,symbols,'2014-01-01','2026-09-08')
        rows=TushareMarketBuilder._market_rows(grouped,symbols,'2014-01-01','2026-09-08',partial_halts=True)
        self.assertEqual(len(rows),len(fixture));self.assertTrue(all(not r['is_suspended'] for r in rows))
        for row,item in zip(rows,fixture):self.assertEqual(row['close'],item['daily']['close'])
        builder=object.__new__(TushareDm1Builder)
        builder.builder_config={'session_suspension_policy':'session_suspension.v1'}
        builder.dependency_commit_ids={'security_master':'security'}
        days=sorted({r['session'] for r in rows})
        deps={'trading_calendar':SimpleNamespace(rows=[{'session':d,'exchange':'SZSE','is_open':True} for d in days]),
              'security_master':SimpleNamespace(rows=[{'symbol':s,'list_session':'1990-01-01','delist_session':None} for s in symbols])}
        builder._dependency=lambda domain:deps[domain]
        raw=SimpleNamespace(ref=SimpleNamespace(raw_batch_id='raw'))
        tables={k:[(r,raw) for r in grouped[k]] for k in ('daily','suspend_d')}
        status=builder._status_rows(tables,sorted(symbols),'2014-01-01','2026-09-08','2026-09-10T00:00:00Z')
        traded={(r['symbol'],r['session']) for r in rows}
        self.assertTrue(all(r['status']=='normal_active' for r in status if (r['symbol'],r['session']) in traded))
        builder.builder_config={}
        with self.assertRaisesRegex(ArtifactError,'intraday'):builder._status_rows(tables,sorted(symbols),'2014-01-01','2026-09-08','2026-09-10T00:00:00Z')

    def test_missing_bar_full_session_and_invalid_timing_fail_closed(self):
        row={'ts_code':'000002.SZ','trade_date':'20151218','suspend_type':'S','suspend_timing':'13:00-15:00'}
        daily={'ts_code':row['ts_code'],'trade_date':row['trade_date'],'vol':1}
        for missing in (None,dict(daily,vol=0),dict(daily,vol=float('inf')),dict(daily,trade_date='20151219')):
            with self.assertRaises(ArtifactError):qualified_partial_halt(row,missing)
        for timing in ('09:30-15:00','15:00-16:00','09:75-10:00','unspecified'):
            with self.assertRaises(ArtifactError):qualified_partial_halt(dict(row,suspend_timing=timing),daily)
        self.assertFalse(qualified_partial_halt(dict(row,suspend_timing=None),None))
