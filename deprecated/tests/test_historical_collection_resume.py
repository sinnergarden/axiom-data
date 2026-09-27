"""Previously published request keys and pending work retain their protocol."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from axiom_data import load_raw_batch
from axiom_data.event_source import EventCollector
from axiom_data.operations import collect_requests


class HistoricalCollectionResumeTest(unittest.TestCase):
    def test_partial_checkpoint_preserves_keys_profile_and_observation_time(self):
        def request(symbol):
            return dict(collector='pr7', domain='holder_count_events', endpoint='stk_holdernumber',
                        params=dict(ts_code=symbol, start_date='20250101', end_date='20250630'),
                        economic_scope=dict(start='20250101', end='20250630'),
                        availability_policy='revision_scan')
        requests = [request('600036.SH'), request('000001.SZ')]
        # Recorded from the baseline implementation, not recomputed by current helpers.
        keys = ['sha256:3006ef2658b34307ba603d7eedf9a0f6b8de28732b0d5179bf4af0f54112652a',
                'sha256:616096e6904d4d707895fc0f02d753e620e56e1857820c50c1aeebf4d4a6a2ce']
        digest = 'sha256:5031d475f52824ca6494bc64df235087c6ac44172c5cecb7ee8607de4e42ecb7'
        class Client:
            def __init__(self): self.calls = []
            def query(self, endpoint, **params):
                self.calls.append(params['ts_code'])
                return [dict(ts_code=params['ts_code'], ann_date='20250401', end_date='20250331', holder_num=200)]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = Client()
            first = EventCollector(root, client).collect('stk_holdernumber', requests[0]['params'],
                profile_version='tushare_pr7.v1', _resume_historical=True, retrieved_at='2025-07-01T00:00:00+00:00')
            first_bytes = (root/'raw/batches'/first.raw_batch_id/'manifest.json').read_bytes()
            run = root/'operations'/'historical-partial'
            run.mkdir(parents=True)
            state = dict(schema_version='collection_run.v1', run_id='historical-partial',
                plan_digest=digest, requests=requests, completed={keys[0]:first.raw_batch_id},
                failed={keys[1]:{'error_type':'RuntimeError'}}, stage='COLLECTION', status='FAILED')
            (run/'collection.json').write_text(json.dumps(state))
            client.calls.clear()
            with patch('axiom_data.event_source._retrieved_at', return_value='2025-07-02T00:00:00+00:00'):
                result = collect_requests(root, run_id='historical-partial', requests=requests, client=client)
            self.assertEqual(result['status'], 'COMPLETE')
            self.assertEqual(result['plan_digest'], digest)
            self.assertEqual(result['requests'], requests)
            self.assertEqual(set(result['completed']), set(keys))
            self.assertEqual(client.calls, ['000001.SZ'])
            self.assertEqual((root/'raw/batches'/first.raw_batch_id/'manifest.json').read_bytes(), first_bytes)
            pending = load_raw_batch(root, result['completed'][keys[1]]).manifest
            self.assertEqual(pending['source_profile_version'], 'tushare_pr7.v1')
            self.assertEqual(pending['source_profile_ref'], 'tushare.pr7.stk_holdernumber')
            self.assertEqual(pending['retrieved_at'], '2025-07-02T00:00:00+00:00')
            self.assertEqual(load_raw_batch(root, first.raw_batch_id).manifest['retrieved_at'], '2025-07-01T00:00:00+00:00')

            current = collect_requests(root, run_id='current-run', requests=requests, client=client)
            self.assertEqual(current['status'], 'COMPLETE')
            self.assertTrue(all(spec['collector']=='events' for spec in current['requests']))
            for raw_id in current['completed'].values():
                manifest = load_raw_batch(root, raw_id).manifest
                self.assertEqual(manifest['source_profile_version'], 'tushare_events.v1')
                self.assertEqual(manifest['source_profile_ref'], 'tushare.events.stk_holdernumber')
                self.assertNotRegex(json.dumps(manifest), r'(?i)(?:pr|dm|phase)[_-]?\d+')
