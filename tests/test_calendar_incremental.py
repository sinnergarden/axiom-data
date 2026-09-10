import json
import tempfile
import unittest
from pathlib import Path

from axiom_data import ArtifactError, BuildApplication, load_raw_batch, validate_domain_commit_closure
from axiom_data.tushare import TushareCollector, TushareMarketBuilder
from test_pr6_artifacts import Client


class CalendarIncrementalTest(unittest.TestCase):
    def test_append_and_overlap_keep_parent_predecessor_without_changing_raw(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);symbols=['688981.SH','000001.SZ']
            def raws(start,end,closed=None):
                refs=[]
                for exchange in ['SSE','SZSE']:
                    rows=[dict(exchange=exchange,cal_date='202609'+str(i).zfill(2),
                        is_open=0 if i==closed else 1,pretrade_date='202609'+str(i-1).zfill(2))
                        for i in range(start,end+1)]
                    refs.append(TushareCollector(root,Client(rows)).collect('trade_cal',
                        dict(exchange=exchange,start_date='202609'+str(start).zfill(2),end_date='202609'+str(end).zfill(2)),
                        retrieved_at='2026-09-10T12:40:00Z').raw_batch_id)
                return refs
            def build(parent,ids,start,end):
                builder=TushareMarketBuilder(root,'trading_calendar',builder_config=dict(symbols=symbols,
                    start_session='2026-09-'+str(start).zfill(2),end_session='2026-09-'+str(end).zfill(2),
                    storage_policy='domain_time_blocks.v1'))
                ref=BuildApplication('trading_calendar',builder).build(parent,ids,[],'trading_calendar.v1')
                return validate_domain_commit_closure(root,'trading_calendar',ref.commit_id)
            parent=build(None,raws(7,8),7,8)
            before={(r['exchange'],r['session']):r for r in parent.rows}
            for start in [9,8]:
                ids=raws(start,10);payloads=[load_raw_batch(root,i).payload for i in ids]
                result=build(parent.ref.commit_id,ids,start,10)
                rows={(r['exchange'],r['session']):r for r in result.rows}
                self.assertEqual(len(rows),8)
                self.assertTrue(all(rows[k]==r for k,r in before.items()))
                for exchange in ['SSE','SZSE']:
                    self.assertEqual(rows[(exchange,'2026-09-09')]['previous_open_session'],'2026-09-08')
                    self.assertEqual(rows[(exchange,'2026-09-10')]['previous_open_session'],'2026-09-09')
                self.assertEqual([load_raw_batch(root,i).payload for i in ids],payloads)
                self.assertEqual(json.loads(payloads[0])[0]['pretrade_date'],'202609'+str(start-1).zfill(2))
            with self.assertRaisesRegex(ArtifactError,'conflicts at canonical key'):
                build(parent.ref.commit_id,raws(8,10,closed=8),8,10)
            self.assertEqual(tuple(parent.rows),tuple(validate_domain_commit_closure(root,'trading_calendar',parent.ref.commit_id).rows))
