import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from axiom_data import ArtifactError, BuildApplication, MarketDomainBuilder, SnapshotReader, create_snapshot
from test_artifacts import market_row, security_row, write_rows


class SessionPartitionReadTest(unittest.TestCase):
    def test_public_reads_prune_months_after_full_validation_and_reject_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            days = [(date(2024,1,1)+timedelta(days=i)).isoformat() for i in range(91)]
            symbols = ['000001.SZ','000002.SZ']
            calendar = [dict(exchange='SZSE',session=d,is_open=True,previous_open_session=days[i-1] if i else None)
                        for i,d in enumerate(days)]
            market = [market_row(d,s) for d in days for s in symbols]
            ids = {}
            for domain, rows in [('trading_calendar',calendar),
                ('security_master',[security_row(s) for s in symbols]), ('market_daily',market)]:
                raw = 'raw-'+domain; write_rows(root,raw,domain,rows)
                options = dict(calendar_commit_id=ids['trading_calendar'],security_master_commit_id=ids['security_master']) if domain=='market_daily' else {}
                builder = MarketDomainBuilder(root,domain,builder_config={'storage_policy':'domain_time_blocks.v1'},**options)
                ids[domain] = BuildApplication(domain,builder).build(None,[raw],[],domain+'.v1').commit_id
            snapshot = create_snapshot(root,ids)
            with patch('axiom_data.partition_rows.STREAM_ROW_THRESHOLD',1):
                reader = SnapshotReader(root,snapshot.snapshot_id)
                rows = reader.commits['market_daily'].rows
                expected = tuple(r for r in market if r['symbol']==symbols[0] and '2024-02-02'<=r['session']<='2024-02-04')
                with patch.object(rows,'_rows',wraps=rows._rows) as opened:
                    self.assertEqual(reader.market_daily([symbols[0]],'2024-02-02','2024-02-04'),expected)
                    self.assertEqual([c.args[0]['key'] for c in opened.call_args_list],['2024-02'])
                self.assertEqual(reader.facts('market_daily',symbols=[symbols[0]],start_session='2024-02-02',end_session='2024-02-04'),expected)
                self.assertEqual(len(reader.market_daily(symbols,'2024-01-31','2024-02-01')),4)
                self.assertEqual(reader.market_daily(symbols,'2025-01-01','2025-01-02'),())
                self.assertEqual(len(reader.facts('market_daily')),182)
                entry = next(e for e in rows.entries if e['key']=='2024-02')
                path = root/'canonical/market_daily/objects'/entry['object_id']/'rows.json'
                # Damage an unrequested security inside the required complete object.
                values = json.loads(path.read_bytes()); values = [r for r in values if r['symbol']!=symbols[1]]
                path.chmod(0o600); path.write_text(json.dumps(values))
                with self.assertRaisesRegex(ArtifactError,'partition content digest'):
                    reader.market_daily([symbols[0]],'2024-02-02','2024-02-04')
                # Every new Reader validates all objects, including other months.
                with self.assertRaisesRegex(ArtifactError,'partition content digest'):
                    SnapshotReader(root,snapshot.snapshot_id)
