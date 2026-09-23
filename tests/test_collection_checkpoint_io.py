import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from axiom_data import ArtifactError
from axiom_data import operations
from axiom_data.artifacts import _json_bytes
from test_v1_operations import Client, request


class CollectionCheckpointIOTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        for path in self.root.rglob('*'):
            if path.is_dir():
                path.chmod(0o755)
        self.temp.cleanup()

    def test_public_collector_name_resumes_frozen_request(self):
        frozen = request('600036.SH')
        public = dict(frozen, collector='events')
        self.assertEqual(operations.validate_request_spec(public), operations.validate_request_spec(frozen))
        client = Client()
        first = operations.collect_requests(self.root, run_id='alias', requests=[frozen], client=client)
        self.assertEqual(first['status'], 'COMPLETE')
        resumed = operations.collect_requests(self.root, run_id='alias', requests=[public], client=client)
        self.assertEqual(resumed['completed'], first['completed'])
        self.assertEqual(client.calls, ['600036.SH'])
        self.assertEqual(resumed['requests'][0]['collector'], 'pr7')

    def test_checkpoint_bytes_grow_linearly(self):
        totals = []
        save = operations.save_progress
        for count in (8, 16):
            writes = []
            def measured(path, value):
                writes.append((path.name, len(_json_bytes(value))))
                return save(path, value)
            plan = [request(f'{600000+i}.SH') for i in range(count)]
            with patch.object(operations, 'save_progress', side_effect=measured):
                result = operations.collect_requests(self.root, run_id=f'scale-{count}', requests=plan, client=Client())
            self.assertEqual(result['status'], 'COMPLETE')
            self.assertEqual(sum(name == 'collection.json' for name, _ in writes), 2)
            self.assertEqual(len(writes), count + 2)
            totals.append(sum(size for _, size in writes))
        self.assertLess(totals[1], totals[0] * 2.1)
        self.assertGreater(totals[1], totals[0] * 1.8)

    def test_crash_after_checkpoint_before_summary_keeps_completed_request(self):
        plan = [request('600036.SH'), request('000001.SZ')]
        client = Client(); client.fail = False
        save = operations.save_progress
        def interrupted(path, value):
            save(path, value)
            if path.parent.name == 'collection-checkpoints':
                raise KeyboardInterrupt('simulated process termination after durable checkpoint')
        with patch.object(operations, 'save_progress', side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                operations.collect_requests(self.root, run_id='crash', requests=plan, client=client)
        old = json.loads((self.root/'operations/crash/collection.json').read_bytes())
        self.assertEqual(old['completed'], {})
        result = operations.collect_requests(self.root, run_id='crash', requests=plan, client=client)
        self.assertEqual(result['status'], 'COMPLETE')
        self.assertEqual(client.calls, ['600036.SH', '000001.SZ'])
        raw_id = next(iter(result['completed'].values()))
        raw = self.root/'raw/batches'/raw_id
        manifest = json.loads((raw/'manifest.json').read_bytes())
        payload = raw/manifest['payload_files'][0]['path']
        payload.chmod(0o600); payload.write_bytes(payload.read_bytes() + b' ')
        failed = operations.collect_requests(self.root, run_id='crash', requests=plan, client=client)
        self.assertEqual(failed['status'], 'FAILED')
        self.assertEqual(client.calls, ['600036.SH', '000001.SZ'])

    def test_legacy_summary_and_checkpoint_plan_binding(self):
        plan = [request('600036.SH')]
        result = operations.collect_requests(self.root, run_id='legacy', requests=plan, client=Client())
        checkpoints = self.root/'operations/legacy/collection-checkpoints'
        shutil.rmtree(checkpoints)
        client = Client()
        self.assertEqual(operations.collect_requests(self.root, run_id='legacy', requests=plan, client=client)['completed'], result['completed'])
        self.assertFalse(client.calls)
        checkpoint = checkpoints/(operations._request(plan[0]).removeprefix('sha256:')+'.json')
        checkpoint.write_text(json.dumps({'schema_version':'collection_checkpoint.v1','plan_digest':'wrong',
            'request_id':operations._request(plan[0]),'raw_batch_id':next(iter(result['completed'].values())),'failure':None}))
        with self.assertRaisesRegex(ArtifactError, 'checkpoint binding'):
            operations.collect_requests(self.root, run_id='legacy', requests=plan, client=client)
