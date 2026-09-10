import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from axiom_data import ArtifactError,TushareMarketBuilder,TushareDm1Builder
from axiom_data.session_suspension import qualified_partial_halt,qualified_daily_state


class SessionSuspensionTest(unittest.TestCase):
    def test_v3_only_applies_exact_issuer_qualified_cases(self):
        fixture=json.loads(Path('tests/fixtures/session_suspension_v3.json').read_bytes())
        frozen=json.dumps(fixture,sort_keys=True)
        for item in fixture:
            row,daily=item['row'],item['daily']
            expected='traded' if daily else 'full_day_halt'
            self.assertEqual(qualified_daily_state(row,daily),expected)
            grouped={k:[] for k in ('daily','adj_factor','daily_basic','stk_limit','suspend_d')}
            grouped.update(daily=[daily] if daily else [],suspend_d=[row])
            result=TushareMarketBuilder._market_rows(grouped,{row['ts_code']},'2014-01-01','2026-09-08',partial_halts='session_suspension.v3')
            self.assertEqual(len(result),1)
            self.assertEqual(result[0]['is_suspended'],daily is None)
            with self.assertRaises(ArtifactError):qualified_daily_state(dict(row,suspend_timing=None),daily)
            contradictory=None if daily else {'ts_code':row['ts_code'],'trade_date':row['trade_date'],'vol':1}
            with self.assertRaises(ArtifactError):qualified_daily_state(row,contradictory)
            with self.assertRaises(ArtifactError):qualified_daily_state(dict(row,ts_code='600000.SH'),daily)
        self.assertEqual(json.dumps(fixture,sort_keys=True),frozen)

    def test_resumption_event_does_not_collide_with_full_day_halt_key(self):
        rows=[{'ts_code':'002731.SZ','trade_date':'20260706','suspend_timing':None,'suspend_type':kind}
              for kind in ('S','R')]
        grouped={k:[] for k in ('daily','adj_factor','daily_basic','stk_limit','suspend_d')}
        for observations in (rows,list(reversed(rows))):
            grouped['suspend_d']=observations
            result=TushareMarketBuilder._market_rows(grouped,{'002731.SZ'},'2014-01-01','2026-09-08',partial_halts='session_suspension.v2')
            self.assertEqual(len(result),1)
            self.assertTrue(result[0]['is_suspended'])
            self.assertEqual(result[0]['volume_shares'],0)
            self.assertIsNone(result[0]['close'])
        grouped['suspend_d']=[rows[1]]
        self.assertEqual(TushareMarketBuilder._market_rows(grouped,{'002731.SZ'},'2014-01-01','2026-09-08',partial_halts='session_suspension.v2'),[])

    def test_v2_real_multiple_intervals_and_opening_auction_boundary(self):
        fixture=json.loads(Path('tests/fixtures/session_suspension_v2.json').read_bytes())
        for item in fixture:
            row,daily=item['suspension'],item['daily']
            self.assertTrue(qualified_partial_halt(row,daily,policy='session_suspension.v2'))
            with self.assertRaises(ArtifactError):qualified_partial_halt(row,daily)
            with self.assertRaises(ArtifactError):qualified_partial_halt(row,None,policy='session_suspension.v2')
            grouped={k:[] for k in ('daily','adj_factor','daily_basic','stk_limit','suspend_d')}
            grouped.update(daily=[daily],suspend_d=[row])
            result=TushareMarketBuilder._market_rows(grouped,{row['ts_code']},'2014-01-01','2026-09-08',partial_halts='session_suspension.v2')
            self.assertEqual(len(result),1)
            self.assertFalse(result[0]['is_suspended'])
            self.assertAlmostEqual(result[0]['volume_shares'],daily['vol']*100)
        row,daily=fixture[0]['suspension'],fixture[0]['daily']
        for timing in ('09:15-15:00','09:15-11:30,13:00-15:00',
                       '09:30-10:00,09:45-10:10','09:30-10:00,','9:30-9:99','10:00-09:30'):
            with self.subTest(timing=timing), self.assertRaises(ArtifactError):
                qualified_partial_halt(dict(row,suspend_timing=timing),daily,policy='session_suspension.v2')

    def test_untimed_halt_with_traded_bar_does_not_assert_full_day(self):
        row={'ts_code':'000055.SZ','trade_date':'20150706','suspend_type':'S','suspend_timing':None}
        daily={'ts_code':'000055.SZ','trade_date':'20150706','vol':105232.53}
        self.assertTrue(qualified_partial_halt(row,daily,policy='session_suspension.v2'))
        self.assertFalse(qualified_partial_halt(row,daily))
        self.assertFalse(qualified_partial_halt(row,None,policy='session_suspension.v2'))
        with self.assertRaises(ArtifactError):
            qualified_partial_halt(row,dict(daily,vol=0),policy='session_suspension.v2')

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
