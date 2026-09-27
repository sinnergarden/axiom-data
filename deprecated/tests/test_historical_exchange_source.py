"""An original market Raw remains an admissible security-boundary source."""
import json
from pathlib import Path
import tempfile
import unittest

from axiom_data import BuildApplication, TushareCollector, load_domain_commit
from axiom_data.exchange_security import ExchangeSecurityBuilder, publish_termination


class HistoricalExchangeSourceTest(unittest.TestCase):
    def test_original_stock_profile_rebuilds_with_official_boundary(self):
        fixtures = Path(__file__).parents[2]/'tests/fixtures'
        row = json.loads((fixtures/'industry_source_comparison.json').read_bytes())['stock']['600687.SH']
        class Client:
            def query(self, endpoint, *, fields, **params):
                return [{key:row[key] for key in fields.split(',')}]
        with tempfile.TemporaryDirectory() as root:
            raw = TushareCollector(root, Client()).collect('stock_basic', {'exchange':'SSE','list_status':'D'},
                profile_version='tushare_phase1.v1', _resume_historical=True,
                retrieved_at='2026-09-09T02:00:00+00:00')
            official = publish_termination(root, exchange='SSE', payload=(fixtures/'sse-delist.json').read_bytes(),
                retrieved_at='2026-09-09T02:00:00+00:00')
            builder = ExchangeSecurityBuilder(root, builder_config=dict(symbols=['600687.SH'],
                start_session='2014-01-01', end_session='2026-09-08'))
            ref = BuildApplication('security_master', builder).build(None,
                [raw.raw_batch_id, official.raw_batch_id], [], 'security_master.v2')
            commit = load_domain_commit(root, 'security_master', ref.commit_id)
            self.assertEqual(commit.rows[0]['delist_session'], '2021-03-04')
            self.assertEqual(commit.manifest['ordered_raw_batch_refs'][0]['source_profile_version'], 'tushare_phase1.v1')
