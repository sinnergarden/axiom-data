"""Real public collection, publication and candidate reference requalification."""
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from axiom_data import load_domain_commit
from axiom_data.exchange_security import publish_termination
from axiom_data.operations import _requalify_sources
from axiom_data.domains.dm1 import DM1_SNAPSHOT_DOMAINS, validate_dm1_snapshot_rows
from axiom_data.reference_sources import collect_reference_sources
from test_bootstrap_references_script import script
from test_reference_sources import FIXTURE, SCOPE


class ReferenceBootstrapIntegrationTest(unittest.TestCase):
    def test_real_commits_resolve_through_candidate_source_gate_and_rerun(self):
        self.check_reference_scope(sorted(FIXTURE['security']))

    def test_single_exchange_stock_with_cross_exchange_benchmark(self):
        self.check_reference_scope(['000001.SZ'])

    def check_reference_scope(self, symbols):
        security_rows = list(FIXTURE['security'].values())
        if '000001.SZ' in symbols:
            security_rows.append(dict(FIXTURE['security']['000506.SZ'], ts_code='000001.SZ'))
        class Client:
            calls = 0
            def query(self, endpoint, *, fields, **params):
                self.calls += 1
                if endpoint == 'trade_cal':
                    return [dict(exchange=params['exchange'], cal_date='20200102', is_open=1,
                                 pretrade_date='20191231')]
                if endpoint == 'stock_basic':
                    return [{k: r[k] for k in fields.split(',')} for r in security_rows
                            if r['exchange'] == params['exchange'] and r['list_status'] == params['list_status']]
                if endpoint == 'index_classify':
                    return [r for r in FIXTURE['taxonomy'] if r['level'] == params['level']]
                rows = [r for r in FIXTURE['members'] if r['is_new'] == params['is_new']]
                offset, limit = int(params['offset']), int(params['limit'])
                return rows[offset:offset + limit]

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'operations' / 'original' / 'source-plans' / 'market.json'
            source.parent.mkdir(parents=True)
            source.write_text(json.dumps({'scope': dict(SCOPE, symbols=symbols)}))
            official = {}
            for exchange, name in [('SSE', 'sse-delist.json'), ('SZSE', 'szse-delist.xlsx')]:
                if not any(s.endswith('.SH' if exchange == 'SSE' else '.SZ') for s in symbols):
                    continue
                official[exchange] = publish_termination(root, exchange=exchange,
                    payload=(Path(__file__).parent / 'fixtures' / name).read_bytes(),
                    retrieved_at='2026-09-09T02:00:00+00:00').raw_batch_id
            client = Client()
            def collect(*args, **kwargs):
                return collect_reference_sources(*args, **kwargs, client=client)
            with patch.object(script, 'collect_reference_sources', side_effect=collect):
                args = dict(run_id='original-references', parent_run_id='original',
                            source_plan_path=source,
                            config={'official_termination_raw_batch_ids': official, 'page_size': 2})
                first = script.bootstrap_references(root, **args)
                self.assertEqual(first['status'], 'REFERENCES_VALIDATED')
                ids = first['domain_commit_ids']
                self.assertEqual(set(ids), {'trading_calendar', 'security_master', 'industry_membership'})
                # The candidate path's actual closure/source gate, not a mock.
                _requalify_sources(root, ids)
                for domain, identity in ids.items():
                    self.assertEqual(load_domain_commit(root, domain, identity).ref.commit_id, identity)
                calendar = load_domain_commit(root, 'trading_calendar', ids['trading_calendar'])
                security = load_domain_commit(root, 'security_master', ids['security_master'])
                self.assertEqual({r['exchange'] for r in calendar.rows}, {'SSE', 'SZSE'})
                self.assertEqual({r['symbol'] for r in security.rows}, set(symbols))
                expected_symbols = symbols if any(s.endswith('.SH') for s in symbols) else symbols + SCOPE['benchmarks']
                self.assertEqual(calendar.manifest['builder_config']['symbols'], expected_symbols)
                relations = {d: SimpleNamespace(rows=[]) for d in DM1_SNAPSHOT_DOMAINS}
                relations.update(trading_calendar=calendar, security_master=security,
                    benchmark_daily=SimpleNamespace(rows=[{'benchmark': '000300.SH', 'session': '2020-01-02'}]))
                validate_dm1_snapshot_rows(relations)
                relations['trading_calendar'] = SimpleNamespace(rows=[r for r in calendar.rows if r['exchange'] == 'SZSE'])
                with self.assertRaisesRegex(ValueError, 'benchmark row is not on an open calendar session'):
                    validate_dm1_snapshot_rows(relations)
                before = client.calls
                again = script.bootstrap_references(root, **args)
                self.assertEqual(again['domain_commit_ids'], ids)
                self.assertEqual(client.calls, before)


if __name__ == '__main__':
    unittest.main()
