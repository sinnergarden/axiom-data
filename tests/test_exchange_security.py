import json
from pathlib import Path
import tempfile
import unittest
from axiom_data import BuildApplication,TushareCollector,TushareMarketBuilder,load_domain_commit,load_raw_batch,ArtifactError
from axiom_data.exchange_security import parse_termination,publish_termination,ExchangeSecurityBuilder

FIX=Path(__file__).parent/'fixtures'


class ExchangeSecurityTest(unittest.TestCase):
    def test_public_policy_rejects_wrong_domain_or_version(self):
        from axiom_data.operations import assemble_candidate
        for domain,policy in [('security_master','latest'),('market_daily','exchange_security.v1')]:
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaisesRegex(ArtifactError,'boundary policy'):
                    assemble_candidate(directory,run_id='boundary-test',domain_inputs={domain:{
                        'raw_batch_ids':['explicit-raw'], 'contract_version':domain+'.v1',
                        'new_lineage':True, 'config':{'security_boundary_policy':policy}}})

    def test_original_tables_and_incomplete_evidence(self):
        sse=parse_termination('SSE',(FIX/'sse-delist.json').read_bytes())
        szse=parse_termination('SZSE',(FIX/'szse-delist.xlsx').read_bytes())
        self.assertEqual((len(sse),len(szse)),(159,208))
        self.assertEqual(sse['600555.SH']['list_session'],'2001-03-28')
        self.assertEqual(sse['900955.SH']['list_session'],'1999-01-18')
        self.assertEqual(szse['000005.SZ']['delist_session'],'2024-04-26')
        broken=json.loads((FIX/'sse-delist.json').read_bytes());broken['result'].pop()
        with self.assertRaises(ArtifactError):parse_termination('SSE',json.dumps(broken).encode())
        with self.assertRaises(ArtifactError):parse_termination('SZSE',b'incomplete file')

    def test_boundary_authority_and_legacy_gate(self):
        fixture=json.loads((FIX/'industry_source_comparison.json').read_bytes())
        rows=[fixture['stock']['600687.SH']]
        class Client:
            def query(self,endpoint,*,fields,**params):return [{k:r[k] for k in fields.split(',')} for r in rows]
        with tempfile.TemporaryDirectory() as directory:
            raw=TushareCollector(directory,Client()).collect('stock_basic',{'exchange':'SSE','list_status':'D'})
            official=publish_termination(directory,exchange='SSE',payload=(FIX/'sse-delist.json').read_bytes(),retrieved_at='2026-09-09T02:00:00+00:00')
            cfg={'symbols':['600687.SH'],'start_session':'2014-01-01','end_session':'2026-09-08'}
            builder=ExchangeSecurityBuilder(directory,builder_config=cfg)
            with self.assertRaisesRegex(ArtifactError,'coverage'):
                BuildApplication('security_master',builder).build(None,[raw.raw_batch_id],[],'security_master.v1')
            result=BuildApplication('security_master',builder).build(None,[raw.raw_batch_id,official.raw_batch_id],[],'security_master.v1')
            commit=load_domain_commit(directory,'security_master',result.commit_id)
            self.assertEqual(commit.rows[0]['delist_session'],'2021-03-04')
            self.assertEqual(json.loads(load_raw_batch(directory,raw.raw_batch_id).payload)[0]['delist_date'],'20210303')
            with self.assertRaisesRegex(ArtifactError,'unverified'):
                BuildApplication('security_master',TushareMarketBuilder(directory,'security_master',builder_config=cfg)).build(None,[raw.raw_batch_id],[],'security_master.v1')
