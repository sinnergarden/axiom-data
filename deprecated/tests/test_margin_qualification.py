import json,tempfile,unittest
from pathlib import Path
from axiom_data import load_raw_batch
from axiom_data.event_source import EventCollector,EventBuilder
from axiom_data.domains.events import validate_rows
from axiom_data.domains.market import MarketContractError
from axiom_data.contracts import load_contract
from test_financial_artifacts import Client

class MarginQualificationTest(unittest.TestCase):
    def test_actual_negative_repayments_remain_unresolved_with_source_provenance(self):
        for case in json.loads(Path('deprecated/tests/fixtures/margin_negative_repayment.json').read_bytes()):
            with tempfile.TemporaryDirectory() as root:
                s=case['row'];ref=EventCollector(root,Client([s])).collect('margin_detail',
                    dict(ts_code=s['ts_code'],start_date=s['trade_date'],end_date=s['trade_date']),retrieved_at='2026-09-10T00:00:00Z')
                raw=load_raw_batch(root,ref.raw_batch_id);contract=load_contract('margin_daily.v1')
                with self.assertRaisesRegex(MarketContractError,'negative'):
                    validate_rows('margin_daily',EventBuilder(root,'margin_daily')._build_rows(contract,[],[raw]))
                b=EventBuilder(root,'margin_daily',builder_config={'margin_qualification':'margin_negative_repayment.v1'})
                rows=b._build_rows(contract,[],[raw]);validate_rows('margin_daily',rows);row=rows[0]
                self.assertEqual(row['values']['balance'],s['rzye'])
                for key,target in [('rqchl','lend_repay_volume'),('rzche','repay')]:
                    if s[key]<0:
                        self.assertIsNone(row['values'][target]);self.assertEqual(row['missing_reasons'][target],'source_repayment_unresolved')
                    else:self.assertEqual(row['values'][target],s[key])
                self.assertEqual(row['observations'][0]['source_qualification']['negative_source_values'],case['negative'])
                self.assertEqual(json.loads(load_raw_batch(root,ref.raw_batch_id).payload),[s])
                row['observations'][0].pop('source_qualification')
                from axiom_data.pit import fingerprint
                o=row['observations'][0];o['observation_id']=fingerprint({k:v for k,v in o.items() if k!='observation_id'})
                with self.assertRaisesRegex(MarketContractError,'qualification closure'):validate_rows('margin_daily',rows)
