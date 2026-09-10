import json
from pathlib import Path
import unittest
from axiom_data import load_raw_batch,TushareMarketBuilder,ArtifactError
from axiom_data.artifacts import _json_copy,_json_bytes
from axiom_data.contracts import load_contract


class MarketMaterializationTest(unittest.TestCase):
    def test_real_security_batches_equal_eager_mapping(self):
        root=Path('/home/liuming/workspace/axiom/data')
        probe=json.loads(Path('reports/pr8/bootstrap_sources/market_mapper_probe.json').read_bytes())
        raws=[load_raw_batch(root,r) for r in probe['raw_ids']]
        cfg={'symbols':probe['symbols'],'start_session':'2014-01-01','end_session':'2026-09-08','session_suspension_policy':'session_suspension.v1'}
        def build(cfg,raws):return TushareMarketBuilder(root,'market_daily',builder_config=cfg)._build_rows(load_contract('market_daily.v1'),[],raws)
        expected=build(cfg,raws)
        actual=build(dict(cfg,market_source_partitioning='security.v1'),raws)
        self.assertEqual(actual,expected);self.assertEqual(len(actual),56908)
        with self.assertRaises(ArtifactError):build(dict(cfg,market_source_partitioning='security.v1'),raws[:-5])

    def test_json_copy_keeps_deep_isolation_and_exact_content(self):
        original={'metadata':{'values':[1,2.5,None,'行业']}}
        copied=_json_copy(original)
        self.assertEqual(_json_bytes(copied),_json_bytes(original))
        copied['metadata']['values'].append(3)
        self.assertEqual(original['metadata']['values'],[1,2.5,None,'行业'])
        with self.assertRaises(ArtifactError):_json_copy({'value':float('nan')})
