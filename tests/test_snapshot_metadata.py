"""Detached metadata reuses identity admission without exposing a mutable owner."""
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
import weakref
from unittest.mock import patch

from axiom_data import Data, QueryError, UpdateRequest
from axiom_data.storage import _json_digest
from test_local_api import daily_batch, query


class SnapshotMetadataTests(unittest.TestCase):
    def fixture(self, root):
        data=Data(root)
        sid=data.update(base_snapshot=None,request=UpdateRequest((daily_batch(),),'metadata-test',{})).snapshot_id
        return data,sid

    def test_metadata_read_and_warm_queries_share_one_admission(self):
        with tempfile.TemporaryDirectory() as root:
            data,sid=self.fixture(root)
            expected=data.store.load_snapshot(sid)['domains']['market_daily']
            with patch.object(data.store,'load_snapshot',wraps=data.store.load_snapshot) as load:
                metadata=data.describe_snapshot(snapshot=sid,domains=['market_daily'],include_domain_refs=True)
                data.read(snapshot=sid,query=query())
                again=data.describe_snapshot(snapshot=sid,domains=['market_daily'])
                self.assertEqual(load.call_count,1)
            entry=metadata['domains']['market_daily']
            canonical=json.dumps(expected,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
            self.assertEqual(entry['domain_ref'],'sha256:'+sha256(canonical).hexdigest())
            self.assertEqual(set(entry),{'contract','source_profile','domain_ref'})
            self.assertEqual(again['domains']['market_daily'],{k:expected[k] for k in ('contract','source_profile')})
            entry['contract']['fields']['close']['unit']='mutated'
            self.assertNotEqual(data.describe_snapshot(snapshot=sid,domains=['market_daily'])['domains']['market_daily']
                                ['contract']['fields']['close']['unit'],'mutated')
            with self.assertRaisesRegex(QueryError,'absent'):
                data.describe_snapshot(snapshot=sid,domains=['not_present'])

    def test_qlib_refresh_releases_old_reader_and_metadata_stays_detached(self):
        from test_qlib_export import fixture
        with tempfile.TemporaryDirectory() as root:
            data,sid,q=fixture(Path(root)/'data')
            with patch.object(data.store,'load_snapshot',wraps=data.store.load_snapshot) as load:
                metadata=data.describe_snapshot(snapshot=sid,domains=['market_daily'])
                old=weakref.ref(data._readers[sid])
                data.export_qlib(snapshot=sid,queries=(q,),destination=Path(root)/'view')
                self.assertIsNone(old())
                self.assertEqual(load.call_count,2)  # cold admission + unchanged export refresh
                self.assertEqual(data.describe_snapshot(snapshot=sid,domains=['market_daily']),metadata)
                self.assertEqual(load.call_count,2)

    def test_changed_file_rejected_on_warm_projection_and_during_digest(self):
        with tempfile.TemporaryDirectory() as root:
            data,sid=self.fixture(root); path=Path(root)/'snapshots'/f'{sid}.json'
            data.describe_snapshot(snapshot=sid,domains=['market_daily'])
            original=path.read_bytes()
            # Same bytes at a new inode still invalidate the admitted epoch.
            replacement=path.with_suffix('.replacement');replacement.write_bytes(original);replacement.replace(path)
            with self.assertRaisesRegex(QueryError,'changed after admission'):
                data.describe_snapshot(snapshot=sid,domains=['market_daily'])
            fresh=Data(root)
            def change_during_digest(value):
                result=_json_digest(value)
                replacement.write_bytes(original);replacement.replace(path)
                return result
            fresh.read(snapshot=sid,query=query())
            with patch('axiom_data.storage._json_digest',side_effect=change_during_digest), \
                 self.assertRaisesRegex(QueryError,'changed after admission'):
                fresh.describe_snapshot(snapshot=sid,domains=['market_daily'],include_domain_refs=True)

    def test_cold_identity_tamper_and_alias_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            data,sid=self.fixture(root); path=Path(root)/'snapshots'/f'{sid}.json'
            body=json.loads(path.read_bytes());body['build_context']['tampered']=True
            path.write_text(json.dumps(body))
            with self.assertRaisesRegex(ValueError,'content digest mismatch'):
                data.describe_snapshot(snapshot=sid,domains=['market_daily'])
            with self.assertRaises(QueryError):
                data.describe_snapshot(snapshot='current',domains=['market_daily'])


if __name__=='__main__':unittest.main()
