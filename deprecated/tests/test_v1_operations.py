import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from axiom_data import ArtifactError, load_raw_batch
from axiom_data.operations import collect_requests


def request(symbol):
    return {'collector': 'pr7', 'domain': 'holder_count_events', 'endpoint': 'stk_holdernumber',
            'params': {'ts_code': symbol, 'start_date': '20250101', 'end_date': '20250630'},
            'economic_scope': {'start': '20250101', 'end': '20250630'},
            'availability_policy': 'revision_scan'}


class Client:
    def __init__(self):
        self.calls = []
        self.fail = True
    def query(self, endpoint, **params):
        self.calls.append(params['ts_code'])
        if self.fail and params['ts_code'] == '000001.SZ':
            raise RuntimeError('private-supplier-token-must-not-appear')
        return [{'ts_code': params['ts_code'], 'ann_date': '20250401',
                 'end_date': '20250331', 'holder_num': 200}]


class OperationTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
    def tearDown(self):
        # Test-owned temporary fixture cleanup only; published Data roots are never unsealed.
        for p in self.root.rglob('*'):
            if p.is_dir(): p.chmod(0o755)
        shutil.rmtree(self.root)

    def test_failed_collection_resumes_exact_raw_and_seals_files(self):
        client = Client()
        plan = [request('600036.SH'), request('000001.SZ')]
        a = collect_requests(self.root, run_id='resume', requests=plan, client=client)
        self.assertEqual(a['status'], 'FAILED')
        self.assertEqual(len(a['completed']), 1)
        raw_id = next(iter(a['completed'].values()))
        raw = load_raw_batch(self.root, raw_id)
        manifest = self.root/'raw/batches'/raw_id/'manifest.json'
        self.assertEqual(manifest.stat().st_mode & 0o222, 0)
        self.assertNotIn('private-supplier-token', (self.root/'operations/resume/collection.json').read_text())
        client.fail = False
        b = collect_requests(self.root, run_id='resume', requests=plan, client=client)
        self.assertEqual(b['status'], 'COMPLETE')
        self.assertEqual(client.calls.count('600036.SH'), 1)
        self.assertEqual(raw.payload, load_raw_batch(self.root, raw_id).payload)
        self.assertFalse((self.root/'current.json').exists())
        with self.assertRaisesRegex(ArtifactError, 'resume plan'):
            collect_requests(self.root, run_id='resume', requests=plan[:1], client=client)

    def test_secret_or_domain_mismatch_rejected_before_run_write(self):
        for change in ('token', 'domain'):
            spec = request('600036.SH')
            if change == 'token': spec['params']['token'] = 'private-value'
            else: spec['domain'] = 'market_daily'
            with self.assertRaises(ArtifactError):
                collect_requests(self.root, run_id='bad', requests=[spec], client=Client())
            self.assertFalse((self.root/'operations').exists())
