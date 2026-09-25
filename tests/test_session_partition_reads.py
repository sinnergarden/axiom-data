import json
import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from axiom_data import ArtifactError, BuildApplication, MarketDomainBuilder, SnapshotReader, create_snapshot, validate_snapshot_closure
from test_artifacts import market_row, security_row, write_rows, synthetic_source_fixture


class UncachedReader(SnapshotReader):
    def _session_rows(self, domain, start, end, symbols=None):
        return super()._session_rows(domain,start,end)


@synthetic_source_fixture
class SessionPartitionReadTest(unittest.TestCase):
    def test_public_benchmark_facts_preserve_symbol_filter_behavior(self):
        from collections import OrderedDict
        from types import SimpleNamespace
        from axiom_data.contracts import load_contract
        from axiom_data.layout import DataRootLayout
        from axiom_data.partition_rows import PartitionRows
        from axiom_data.partitions import publish_partitions, POLICY
        with tempfile.TemporaryDirectory() as directory:
            layout=DataRootLayout(Path(directory))
            source=[dict(session='2024-02-01',benchmark='000300.SH',close=3500.,
                source_available_at=None,first_observed_at='2024-02-02T00:00:00Z',
                availability_basis='terminal_history_observed',pit_qualification='best_effort',source_ref='fixture')]
            contract=load_contract('benchmark_daily.v1')
            entries=publish_partitions(layout,'benchmark_daily',source)
            rows=PartitionRows(layout,'benchmark_daily',dict(partition_policy=POLICY,
                output_files=[],partitions=entries),contract)
            commit=SimpleNamespace(rows=rows,contract=contract)
            reader=SnapshotReader.__new__(SnapshotReader)
            reader.commits={'benchmark_daily':commit}
            reader._security_projection=OrderedDict();reader._security_projection_bytes=0
            baseline=UncachedReader.__new__(UncachedReader);baseline.commits=reader.commits
            for bounds in ({},dict(start_session='2024-02-01',end_session='2024-02-01')):
                for symbols in (['000001.SZ'],['000300.SH']):
                    with self.subTest(symbols=symbols,bounds=bounds):
                        actual=reader.facts('benchmark_daily',symbols=symbols,**bounds)
                        self.assertEqual(actual,())
                        self.assertEqual(actual,baseline.facts('benchmark_daily',symbols=symbols,**bounds))
                self.assertEqual(reader.facts('benchmark_daily',**bounds),tuple(source))
            self.assertFalse(reader._security_projection)

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
                baseline = UncachedReader(root,snapshot.snapshot_id)
                rows = reader.commits['market_daily'].rows
                expected = tuple(r for r in market if r['symbol']==symbols[0] and '2024-02-02'<=r['session']<='2024-02-04')
                with patch.object(rows,'_rows',wraps=rows._rows) as opened:
                    self.assertEqual(reader.market_daily([symbols[0]],'2024-02-02','2024-02-04'),expected)
                    self.assertEqual([c.args[0]['key'] for c in opened.call_args_list],['2024-02'])
                self.assertEqual(reader.facts('market_daily',symbols=[symbols[0]],start_session='2024-02-02',end_session='2024-02-04'),expected)
                with patch.object(rows,'_rows',wraps=rows._rows) as opened:
                    for day in ('2024-02-02','2024-02-03','2024-02-04'):
                        actual=reader.market_daily([symbols[0]],day,day)
                        self.assertEqual(actual,tuple(r for r in expected if r['session']==day))
                    self.assertEqual(opened.call_count,0)
                # Caller mutation must not alter subsequent cached results.
                changed=reader.market_daily([symbols[0]],'2024-02-02','2024-02-04')
                changed[0]['close']=-1
                self.assertEqual(reader.market_daily([symbols[0]],'2024-02-02','2024-02-04'),expected)
                self.assertEqual(len(reader.market_daily(symbols,'2024-01-31','2024-02-01')),4)
                self.assertEqual(reader.market_daily(symbols,'2025-01-01','2025-01-02'),())
                self.assertEqual(len(reader.facts('market_daily')),182)
                for requested in ([symbols[0]],symbols,list(reversed(symbols)),['999999.SZ']):
                    self.assertEqual(reader.market_daily(requested,'2024-01-31','2024-03-01'),
                                     baseline.market_daily(requested,'2024-01-31','2024-03-01'))
                    self.assertEqual(reader.facts('market_daily',symbols=requested,fields=['symbol','session','close']),
                                     baseline.facts('market_daily',symbols=requested,fields=['symbol','session','close']))
                entry = next(e for e in rows.entries if e['key']=='2024-02')
                path = root/'canonical/market_daily/objects'/entry['object_id']/'rows.json'
                original=path.read_bytes();stat=path.stat()
                altered=original.replace(b'10.5',b'10.6',1)
                self.assertNotEqual(altered,original)
                self.assertEqual(len(altered),len(original))
                path.chmod(0o600);path.write_bytes(altered)
                os.utime(path,ns=(stat.st_atime_ns,stat.st_mtime_ns))
                with self.assertRaisesRegex(ArtifactError,'partition content digest'):
                    reader.market_daily([symbols[0]],'2024-02-02','2024-02-04')
                path.write_bytes(original)
                # Damage an unrequested security inside the required complete object.
                values = json.loads(path.read_bytes()); values = [r for r in values if r['symbol']!=symbols[1]]
                path.chmod(0o600); path.write_text(json.dumps(values))
                with self.assertRaisesRegex(ArtifactError,'partition content digest'):
                    reader.market_daily([symbols[0]],'2024-02-02','2024-02-04')
                # Ordinary reads verify consumed files; an explicit audit still
                # rejects damage anywhere in the immutable Snapshot closure.
                SnapshotReader(root,snapshot.snapshot_id)
                self.assertEqual(reader.market_daily([symbols[0]],'2024-01-01','2024-01-02'),
                                 tuple(r for r in market if r['symbol']==symbols[0] and r['session'] in days[:2]))
                with self.assertRaisesRegex(ArtifactError,'partition content digest'):
                    validate_snapshot_closure(root,snapshot.snapshot_id)

    def test_projection_exhausts_full_input_and_bounds_cache(self):
        from types import SimpleNamespace
        from axiom_data.partition_rows import PartitionRows
        from axiom_data.layout import DataRootLayout
        from axiom_data.contracts import load_contract
        from axiom_data.artifacts import _json_bytes, _digest
        from collections import OrderedDict
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            values=[market_row('2024-02-01',s) for s in ('000001.SZ','000002.SZ')]
            payload=_json_bytes(values);identity=_digest(payload).split(':')[1]
            path=root/'canonical/market_daily/objects'/identity/'rows.json'
            path.parent.mkdir(parents=True);path.write_bytes(payload)
            entry=dict(key='2024-02',object_id=identity,content_digest='sha256:'+identity,rows=3,bytes=len(payload))
            parts=PartitionRows(DataRootLayout(root),'market_daily',dict(partition_policy='domain_time_blocks.v1',output_files=[],partitions=[entry]),load_contract('market_daily.v1'))
            reader=SnapshotReader.__new__(SnapshotReader)
            reader.commits={'market_daily':SimpleNamespace(rows=parts)}
            reader._security_projection=OrderedDict();reader._security_projection_bytes=0
            # A matching first row cannot hide a bad complete row count.
            with self.assertRaisesRegex(ArtifactError,'row count'):
                list(reader._session_rows('market_daily','2024-02-01','2024-02-01',{'000001.SZ'}))
            self.assertFalse(reader._security_projection)
            entry['rows']=2
            for i in range(140):
                list(reader._session_rows('market_daily','2024-02-01','2024-02-01',{f'{i:06d}.SZ'}))
            self.assertLessEqual(len(reader._security_projection),128)
            self.assertLessEqual(reader._security_projection_bytes,8*1024*1024)
            with patch.object(parts,'_rows',wraps=parts._rows) as opened:
                for _ in range(2):
                    list(reader._session_rows('market_daily','2024-02-01','2024-02-01',
                                             {f'{i:06d}.SZ' for i in range(65)}))
                self.assertEqual(opened.call_count,2)
