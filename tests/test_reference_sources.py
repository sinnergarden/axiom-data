import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from axiom_data import ArtifactError, load_raw_batch
from axiom_data.operations import collect_requests
from axiom_data.reference_sources import plan_reference_requests, collect_reference_sources
from axiom_data.source_completeness import SourceCompletenessError, page_evidence


SCOPE = dict(symbols=['000001.SZ'], start_session='2020-01-02', end_session='2020-01-02',
             financial_observation_start='2019-01-01', benchmarks=['000300.SH'], universe_ids=['000906.SH'])
FIXTURE = json.loads((Path(__file__).parent / 'fixtures/sw2021_golden.json').read_text())


class Client:
    def __init__(self, count):
        self.count = count
        self.calls = []
        self.fail = None
        self.mismatch = False
        self.duplicate = False
        self.bad_calendar = False

    def query(self, endpoint, *, fields, **params):
        self.calls.append((endpoint, dict(params)))
        if endpoint == 'trade_cal':
            return [] if self.bad_calendar else [dict(exchange=params['exchange'], cal_date='20200102',
                                                      is_open=1, pretrade_date='20191231')]
        if endpoint == 'stock_basic':
            return [] if params['list_status'] != 'L' else [dict(ts_code='000001.SZ', name='fixture',
                exchange='SZSE', list_status='L', list_date='19910403', delist_date=None)]
        if endpoint == 'index_classify':
            return [r for r in FIXTURE['taxonomy'] if r['level'] == params['level']]
        offset, limit = int(params['offset']), int(params['limit'])
        if self.fail == (params['is_new'], offset):
            raise ConnectionError('fixture transport interruption')
        template = next(r for r in FIXTURE['members'] if r['is_new'] == params['is_new'])
        rows = [dict(template, in_date=f'202001{i + 1:02d}')
                for i in range(offset, min(offset + limit, self.count))]
        if self.mismatch and rows:
            rows[0]['is_new'] = 'N' if params['is_new'] == 'Y' else 'Y'
        if self.duplicate and offset and rows:
            rows[0]['in_date'] = '20200101'
        return rows


class ReferenceSourcesTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.plan = plan_reference_requests(SCOPE, page_size=2)

    def run_source(self, client, **kwargs):
        return collect_reference_sources(self.root, run_id='bootstrap-reference',
                                         plan=self.plan, client=client, **kwargs)

    def test_short_full_and_multiple_full_pages_are_closed_by_shared_validator(self):
        for count, expected_offsets in [(1, [0]), (3, [0, 2]), (5, [0, 2, 4]), (4, [0, 2, 4])]:
            with self.subTest(count=count), tempfile.TemporaryDirectory() as root:
                client = Client(count)
                result = collect_reference_sources(root, run_id='pages', plan=self.plan, client=client)
                self.assertEqual(result['status'], 'COMPLETE')
                for mode in ('Y', 'N'):
                    self.assertEqual([int(p['offset']) for ep, p in client.calls
                                      if ep == 'index_member_all' and p['is_new'] == mode], expected_offsets)
                raws = [load_raw_batch(root, r) for r in result['raw_batch_ids']['industry_membership']]
                self.assertTrue(page_evidence(raws))

    def test_full_page_missing_next_page_regression_and_resume(self):
        client = Client(5)
        client.fail = ('Y', 2)
        failed = self.run_source(client)
        self.assertEqual(failed['status'], 'PARTIAL')
        saved = list(failed['raw_batch_ids']['industry_membership'])
        raws = [load_raw_batch(self.root, r) for r in saved]
        with self.assertRaises(SourceCompletenessError):
            page_evidence(raws)
        self.assertFalse(list(self.root.glob('canonical/*/commits/*/manifest.json')))
        client.fail = None
        client.calls.clear()
        complete = self.run_source(client)
        self.assertEqual(complete['status'], 'COMPLETE')
        self.assertTrue(set(saved) <= set(complete['raw_batch_ids']['industry_membership']))
        self.assertEqual(client.calls[0][1], {'is_new': 'Y', 'limit': '2', 'offset': '2'})
        client.calls.clear()
        self.assertEqual(self.run_source(client), complete)
        self.assertEqual(client.calls, [])

    def test_request_mismatch_and_page_overlap_are_rejected(self):
        for attribute in ('mismatch', 'duplicate'):
            with self.subTest(attribute=attribute), tempfile.TemporaryDirectory() as root:
                client = Client(3)
                setattr(client, attribute, True)
                if attribute == 'duplicate':
                    with self.assertRaisesRegex(SourceCompletenessError, 'duplicate'):
                        collect_reference_sources(root, run_id='bad', plan=self.plan, client=client)
                else:
                    self.assertEqual(collect_reference_sources(root, run_id='bad', plan=self.plan,
                                                               client=client)['status'], 'PARTIAL')

    def test_public_validation_and_frozen_plan_cannot_be_bypassed(self):
        client = Client(1)
        client.bad_calendar = True
        self.assertEqual(self.run_source(client)['status'], 'PARTIAL')
        self.assertFalse(list(self.root.glob('canonical/*/commits/*/manifest.json')))
        changed = json.loads(json.dumps(self.plan))
        changed['requests'][0]['params']['end_date'] = '20200103'
        with self.assertRaises(ArtifactError):
            collect_reference_sources(self.root, run_id='other', plan=changed, client=client)
        with self.assertRaisesRegex(ArtifactError, 'context changed'):
            self.run_source(client, context={'different': True})

    def test_identity_matches_direct_public_collector(self):
        stamp = '2026-09-13T00:00:00+00:00'
        with patch('axiom_data.tushare._retrieved_at', return_value=stamp), \
             patch('axiom_data.sw_source._retrieved_at', return_value=stamp), \
             tempfile.TemporaryDirectory() as direct_root:
            result = self.run_source(Client(1))
            direct = collect_requests(direct_root, run_id='direct',
                                      requests=self.plan['requests'] + self.plan['page_groups'], client=Client(1))
            ids = {r for values in result['raw_batch_ids'].values() for r in values}
            self.assertEqual(ids, set(direct['completed'].values()))

    def test_execution_uses_frozen_copy_not_mutable_callers_plan(self):
        client = Client(1)
        original = client.query
        def mutate(endpoint, **params):
            self.plan['page_groups'][0]['params']['is_new'] = 'N'
            return original(endpoint, **params)
        client.query = mutate
        result = self.run_source(client)
        self.assertEqual(result['status'], 'COMPLETE')
        self.assertEqual([p['is_new'] for ep, p in client.calls if ep == 'index_member_all'], ['Y', 'N'])


if __name__ == '__main__':
    unittest.main()
