import tempfile
import unittest
from pathlib import Path
from axiom_data import ArtifactError,TushareDm1Builder,TushareDm1Collector
from axiom_data.artifacts import RawBatches,_DOMAIN_DEPENDENCIES
from axiom_data.contracts import load_contract
from axiom_data.dm1_source import _EXPECTED_ENDPOINTS
from test_pr5_dm1 import collect_all,build_all,FixtureClient,SYMBOLS,BENCHMARKS,START,END


from test_artifacts import synthetic_source_fixture


@synthetic_source_fixture
class Dm1MaterializationTest(unittest.TestCase):
    def test_every_reference_mapper_preserves_values_provenance_and_full_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            commits=build_all(root,collect_all(root))
            collector=TushareDm1Collector(root,FixtureClient())
            for domain,endpoints in _EXPECTED_ENDPOINTS.items():
                symbols=BENCHMARKS if domain=='benchmark_daily' else SYMBOLS
                ids=[]
                for i,symbol in enumerate(symbols):
                    for endpoint in sorted(endpoints):
                        params={'ts_code':symbol}
                        if endpoint!='dividend':params.update(start_date=START.replace('-',''),end_date=END.replace('-',''))
                        ids.append(collector.collect(domain,endpoint,params,
                            retrieved_at=f'2026-09-06T1{i}:00:00+08:00').raw_batch_id)
                config=dict(symbols=list(symbols),start_session=START,end_session=END)
                def builder(cfg):
                    return TushareDm1Builder(root,domain,builder_config=cfg,
                        dependency_commit_ids={d:commits[d] for d in _DOMAIN_DEPENDENCIES[domain]})
                contract=load_contract(domain+'.v1')
                expected=builder(config)._build_rows(contract,[],RawBatches(root,ids))
                batched=builder(dict(config,dm1_source_partitioning='security.v1'))
                actual=batched._build_rows(contract,[],RawBatches(root,ids))
                with self.subTest(domain=domain):
                    self.assertEqual(actual,expected)
                    self.assertFalse(hasattr(batched,'_row_dependency_cache'))
                    with self.assertRaises(ArtifactError):
                        batched._build_rows(contract,[],RawBatches(root,ids[1:]))
