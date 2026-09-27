import json
import shutil
import tempfile
import unittest
from pathlib import Path
from fixture_locations import fixture_root
from unittest.mock import patch

from axiom_data import ArtifactError, BuildApplication, SnapshotReader, create_snapshot
from axiom_data.event_source import EventBuilder, EventCollector
from test_financial_artifacts import Client


class DailyPitPartitionReadTest(unittest.TestCase):
    def test_daily_bounds_preserve_revisions_and_read_complete_month(self):
        run=json.loads(Path('deprecated/history/reports/pr7/run_manifest.json').read_bytes())
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'data';shutil.copytree(fixture_root(run['source_root']),root)
            try:
                old=SnapshotReader(root,run['refs']['snapshot_id'])
                ids={d:c.ref.commit_id for d,c in old.commits.items()}
                source=[dict(ts_code='688981.SH',trade_date=d,buy_elg_amount=3,sell_elg_amount=7,net_mf_amount=-9)
                        for d in ['20250701','20250801','20250610']]
                params=dict(ts_code='688981.SH',start_date='20250601',end_date='20250908')
                a=EventCollector(root,Client(source)).collect('moneyflow',params,retrieved_at='2025-09-09T00:00:00Z')
                b=EventCollector(root,Client([dict(source[-1],buy_elg_amount=4)])).collect(
                    'moneyflow',params,retrieved_at='2025-09-10T00:00:00Z')
                builder=EventBuilder(root,'moneyflow_daily',builder_config={'storage_policy':'domain_time_blocks.v1'},
                    dependency_commit_ids={d:ids[d] for d in ['trading_calendar','security_master']})
                ids['moneyflow_daily']=BuildApplication('moneyflow_daily',builder).build(
                    None,[a.raw_batch_id,b.raw_batch_id],[],'moneyflow_daily.v1').commit_id
                snap=create_snapshot(root,ids)
                with patch('axiom_data.partition_rows.STREAM_ROW_THRESHOLD',1):
                    reader=SnapshotReader(root,snap.snapshot_id)
                    parts=reader.commits['moneyflow_daily'].rows
                    for cutoff,value in [('2025-09-08T12:00:00Z',None),('2025-09-09T12:00:00Z',30000),('2025-09-10T12:00:00Z',40000)]:
                        query=dict(symbols=['688981.SH'],pit_policy='operational_pit_v1',knowledge_cutoff=cutoff)
                        expected=tuple(r for r in reader.as_of('moneyflow_daily',**query) if r['session']=='2025-06-10')
                        with patch.object(parts,'_rows',wraps=parts._rows) as opened:
                            result=reader.as_of('moneyflow_daily',start_session='2025-06-10',end_session='2025-06-10',**query)
                            self.assertEqual(result,expected)
                            self.assertEqual(opened.call_count,0)  # Complete-month projection already verified above.
                        self.assertEqual(result[0]['values']['big_buy'] if result else None,value)
                    for domain in ['financial_events','universe_membership','holder_count_events']:
                        with self.assertRaisesRegex(ArtifactError,'daily'):
                            reader.as_of(domain,start_session='2025-06-10',**query)
                    part=next(p for p in parts.entries if p['key']=='2025-06')
                    path=root/'canonical/moneyflow_daily/objects'/part['object_id']/'rows.json'
                    path.chmod(0o600);path.write_bytes(path.read_bytes()+b' ')
                    with self.assertRaisesRegex(ArtifactError,'partition content digest'):
                        reader.as_of('moneyflow_daily',start_session='2025-06-10',end_session='2025-06-10',**query)
            finally:
                for p in root.rglob('*'):
                    if p.is_dir():p.chmod(0o755)
