import copy
import json
import tempfile
import unittest
from pathlib import Path

from axiom_data import ArtifactError, BuildApplication, load_raw_batch, validate_domain_commit_closure
from axiom_data.artifacts import _validate_domain_rows
from axiom_data.contracts import load_contract
from axiom_data.reference_source import TushareReferenceBuilder, TushareReferenceCollector
from axiom_data.build import BuildContractError
from test_artifacts import build_pack, synthetic_source_fixture
from test_financial_artifacts import Client


@synthetic_source_fixture
class CapitalConflictTest(unittest.TestCase):
    def test_actual_conflict_is_explicit_null_with_unchanged_raw_and_version_dispatch(self):
        source = json.loads(Path('deprecated/tests/fixtures/capital_conflict.json').read_bytes())['rows']
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pack = build_pack(root)
            deps = {'trading_calendar': pack['calendar'].commit_id, 'security_master': pack['security'].commit_id}
            cfg = dict(symbols=['603882.SH'], start_session='2014-01-01', end_session='2026-09-08', storage_policy='domain_time_blocks.v1')
            raw = TushareReferenceCollector(root, Client(source)).collect('security_capital', 'daily_basic',
                dict(ts_code='603882.SH', start_date='20140101', end_date='20260908'), retrieved_at='2026-09-10T00:00:00Z')
            def build(version, config):
                return BuildApplication('security_capital', TushareReferenceBuilder(root, 'security_capital',
                    dependency_commit_ids=deps, builder_config=config)).build(None, [raw.raw_batch_id], [], version)
            with self.assertRaisesRegex(BuildContractError, 'LEGACY_CONTRACT_READ_ONLY'):
                build('security_capital.v1', cfg)
            qualified = dict(cfg, capital_qualification='capital_conflict.v1')
            ref = build('security_capital.v2', qualified)
            commit = validate_domain_commit_closure(root, 'security_capital', ref.commit_id)
            self.assertEqual(len(commit.rows), 15)
            self.assertTrue(all(r['total_shares'] is None and r['circulating_shares'] is None and
                r['missing_reason'] == 'source_capital_conflict' for r in commit.rows))
            self.assertEqual(commit.rows[0]['source_conflict']['float_share'], source[0]['float_share'])
            self.assertEqual(json.loads(load_raw_batch(root, raw.raw_batch_id).payload), source)
            self.assertEqual(build('security_capital.v2', qualified).commit_id, ref.commit_id)
            with self.assertRaises(ArtifactError):
                build('security_capital.v2', cfg)
            with self.assertRaises(BuildContractError):
                build('security_capital.v1', qualified)
            with self.assertRaises(ArtifactError):
                _validate_domain_rows('security_capital', commit.rows, contract=load_contract('security_capital.v1'))
            for change in ({'total_shares': 1}, {'source_conflict': None},
                           {'source_conflict': {'profile': 'capital_conflict.v1', 'total_share': 2, 'float_share': 1}}):
                rows = copy.deepcopy(list(commit.rows)); rows[0].update(change)
                with self.assertRaises(ArtifactError):
                    _validate_domain_rows('security_capital', rows, contract=load_contract('security_capital.v2'))
