import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from axiom_data import ArtifactError, BuildApplication, load_raw_batch, validate_domain_commit_closure
from axiom_data.dm1_source import TushareDm1Builder, TushareDm1Collector
from axiom_data.pr7_source import Pr7Builder, Pr7Collector
from test_artifacts import build_pack, build_commit, security_row, write_rows
from test_pr6_artifacts import Client


class SourceLabelQualificationTest(unittest.TestCase):
    def test_actual_zero_pairs_are_unknown_and_other_invalid_pairs_fail(self):
        source = [x['row'] for x in json.loads(Path('tests/fixtures/zero_limit_pairs.json').read_bytes())]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dates = ['2014-01-27', '2015-06-12', '2015-08-18']
            calendar = []
            for e in ('SSE', 'SZSE'):
                day = date.fromisoformat(dates[0]); previous = None
                while day.isoformat() <= dates[-1]:
                    d = day.isoformat()
                    calendar.append(dict(exchange=e, session=d, is_open=d in dates, previous_open_session=previous))
                    if d in dates: previous = d
                    day += timedelta(days=1)
            securities = sorted([security_row(x['ts_code'], 'SSE' if x['ts_code'].endswith('.SH') else 'SZSE') for x in source], key=lambda x:x['symbol'])
            deps = {}
            for d, rows in [('security_master', securities), ('trading_calendar', calendar)]:
                write_rows(root, 'raw-'+d, d, rows)
                deps[d] = build_commit(root, d, ['raw-'+d]).commit_id
            cfg = dict(symbols=[x['symbol'] for x in securities], start_session='2014-01-01', end_session='2026-09-08')
            raws = [TushareDm1Collector(root, Client([x])).collect('price_limits', 'stk_limit',
                dict(ts_code=x['ts_code'], start_date='20140101', end_date='20260908'), retrieved_at='2026-09-10T00:00:00Z') for x in source]
            def build(config):
                return BuildApplication('price_limits', TushareDm1Builder(root, 'price_limits', dependency_commit_ids=deps,
                    builder_config=config)).build(None, [r.raw_batch_id for r in raws], [], 'price_limits.v1')
            with self.assertRaisesRegex(ArtifactError, 'ordered bounds'): build(cfg)
            ref = build(dict(cfg, limit_qualification='zero_limit_pair.v1'))
            commit = validate_domain_commit_closure(root, 'price_limits', ref.commit_id)
            qualified = [r for r in commit.rows if r['rule_ref'] == 'zero_limit_pair.v1']
            self.assertEqual(len(qualified), 3)
            self.assertTrue(all(r['limit_state'] == 'unknown' and r['upper_limit'] is None and r['lower_limit'] is None for r in qualified))
            self.assertEqual(json.loads(load_raw_batch(root, raws[0].raw_batch_id).payload), [source[0]])
            # Equal positive bounds retain their error; the qualifier is only zero/zero.
            changed = dict(source[0], up_limit=1, down_limit=1)
            raws[0] = TushareDm1Collector(root, Client([changed])).collect('price_limits', 'stk_limit',
                dict(ts_code=changed['ts_code'], start_date='20140101', end_date='20260908'), retrieved_at='2026-09-10T01:00:00Z')
            with self.assertRaisesRegex(ArtifactError, 'ordered bounds'): build(dict(cfg, limit_qualification='zero_limit_pair.v1'))

    def test_actual_forecast_labels_require_new_contract_and_remain_verbatim(self):
        source = [x['row'] for x in json.loads(Path('tests/fixtures/forecast_source_types.json').read_bytes())['examples'].values()]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            securities = sorted([security_row(x['ts_code']) for x in source], key=lambda x:x['symbol'])
            write_rows(root, 'raw-security', 'security_master', securities)
            security = build_commit(root, 'security_master', ['raw-security'])
            raw = [Pr7Collector(root, Client([x])).collect('forecast',
                dict(ts_code=x['ts_code'], start_date='20140101', end_date='20260908'), retrieved_at='2026-09-10T00:00:00Z') for x in source]
            def build(version, config):
                return BuildApplication('forecast_observations', Pr7Builder(root, 'forecast_observations',
                    dependency_commit_ids={'security_master':security.commit_id}, builder_config=config)).build(None, [r.raw_batch_id for r in raw], [], version)
            with self.assertRaisesRegex(ArtifactError, 'unsupported forecast enum'): build('forecast_observations.v1', {})
            cfg = dict(forecast_source_types='forecast_source_types.v1')
            ref = build('forecast_observations.v2', cfg)
            commit = validate_domain_commit_closure(root, 'forecast_observations', ref.commit_id)
            self.assertEqual({r['values']['type'] for r in commit.rows}, {'增亏', '减亏', '不确定', '其他'})
            self.assertEqual(json.loads(load_raw_batch(root, raw[0].raw_batch_id).payload), [source[0]])
            self.assertEqual(build('forecast_observations.v2', cfg).commit_id, ref.commit_id)
            with self.assertRaises(ArtifactError): build('forecast_observations.v2', {})
            with self.assertRaises(ArtifactError): build('forecast_observations.v1', cfg)
            unknown = dict(source[0], type='unrecognized-new-label')
            raw[0] = Pr7Collector(root, Client([unknown])).collect('forecast',
                dict(ts_code=unknown['ts_code'], start_date='20140101', end_date='20260908'), retrieved_at='2026-09-10T01:00:00Z')
            with self.assertRaisesRegex(ArtifactError, 'unsupported forecast enum'): build('forecast_observations.v2', cfg)
