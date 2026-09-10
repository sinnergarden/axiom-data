import tempfile
import unittest
from datetime import date,timedelta
from pathlib import Path

from axiom_data import ArtifactError, SnapshotReader, create_snapshot, inspect_scope
from axiom_data.scope_coverage import session_coverage
from test_artifacts import build_commit, market_row, security_row, write_rows


class ScopeCoverageTest(unittest.TestCase):
    def test_interior_gap_null_zero_and_exchange_scope_remain_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);calendar=[]
            for exchange,opens in [('SZSE',{'2026-01-02','2026-01-05','2026-01-06'}),('SSE',{'2026-01-05'})]:
                previous=None
                for i in range(5):
                    day=(date(2026,1,2)+timedelta(days=i)).isoformat()
                    calendar.append(dict(exchange=exchange,session=day,is_open=day in opens,previous_open_session=previous))
                    if day in opens:previous=day
            symbols=['000001.SZ','600000.SH'];ids={}
            rows=[dict(market_row('2026-01-02'),turnover_rate=None),dict(market_row('2026-01-06'),turnover_rate=0),market_row('2026-01-05','600000.SH')]
            for domain,values in [('trading_calendar',calendar),('security_master',[security_row(),security_row('600000.SH','SSE')]),('market_daily',rows)]:
                write_rows(root,'raw-'+domain,domain,values)
                options=dict(calendar_commit=ids['trading_calendar'],security_commit=ids['security_master']) if domain=='market_daily' else {}
                ids[domain]=build_commit(root,domain,['raw-'+domain],**options).commit_id
            reader=SnapshotReader(root,create_snapshot(root,ids).snapshot_id)
            args=dict(symbols=symbols,start_session='2026-01-02',end_session='2026-01-06',fields=['turnover_rate'])
            report=session_coverage(reader,'market_daily',**args)
            self.assertEqual(inspect_scope(root,reader.snapshot.ref.snapshot_id,domain='market_daily',**args),report)
            sz=report['symbols']['000001.SZ'];sh=report['symbols']['600000.SH']
            self.assertEqual((sz['expected_sessions'],sz['observed_sessions'],sz['missing_sessions']),(3,2,1))
            self.assertEqual(sz['missing_session_mask_hex'],'2')
            self.assertEqual(sz['fields']['turnover_rate'],{'non_null_sessions':1,'null_only_sessions':1})
            self.assertEqual((sh['expected_sessions'],sh['observed_sessions'],sh['missing_sessions']),(1,1,0))
            self.assertEqual(report['admission'],'NOT_ASSESSED')
            self.assertEqual(report['null_observation_counts'],{'turnover_rate':1})
            for changed in [dict(fields=['not_a_field']),dict(symbols=['999999.SH']),dict(end_session='2026-01-07')]:
                with self.assertRaises(ArtifactError):session_coverage(reader,'market_daily',**dict(args,**changed))
