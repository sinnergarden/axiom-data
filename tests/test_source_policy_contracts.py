import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from axiom_data import load_raw_batch, write_raw_batch
from axiom_data.artifacts import _digest, _json_bytes
from axiom_data.source_completeness import (
    SourceCompletenessError, _extension, completeness_policy, current_contract_binding,
    source_profile_completeness_binding, validate_payload_completeness,
    validate_policy_contract, validate_raw_completeness, validate_raw_completeness_legacy,
)
from axiom_data.tushare import TushareCollector, tushare_source_profile_digest


class SourcePolicyContractTest(unittest.TestCase):
    def test_all_formal_endpoint_contracts_are_executable_and_qualified(self):
        from importlib.resources import files
        extension = _extension()
        for version in extension['profile_versions']:
            profile = __import__('json').loads(files('axiom_data.source_profiles').joinpath(version+'.json').read_bytes())
            for endpoint in profile['endpoints']:
                with self.subTest(version=version, endpoint=endpoint):
                    checked = validate_policy_contract(version, endpoint)
                    self.assertEqual(checked['status'], 'PASS', checked['reasons'])
                    self.assertEqual(checked['policy']['historical_completeness'], 'request_complete_best_effort')

    def test_removing_rules_or_historical_contract_blocks_current_admission(self):
        for mutate in (
            lambda p: p['endpoints'].pop('daily'),
            lambda p: p['endpoints']['daily'].pop('completeness_rule'),
            lambda p: p['endpoints']['daily'].update(completeness_rule='accept_everything'),
            lambda p: p.update(historical_completeness='unknown'),
            lambda p: p['endpoints']['daily'].pop('evidence'),
        ):
            contract = copy.deepcopy(_extension()); mutate(contract)
            with patch('axiom_data.source_completeness._extension', return_value=contract):
                self.assertEqual(validate_policy_contract('tushare_phase1.v1', 'daily')['status'], 'BLOCKED')
                with self.assertRaises(SourceCompletenessError):
                    validate_payload_completeness('tushare_phase1.v1', 'daily', [],
                        params={'ts_code':'000001.SZ','start_date':'20250101','end_date':'20250131'})

    def test_pagination_without_terminal_rule_is_blocked(self):
        contract = copy.deepcopy(_extension())
        contract['endpoints']['index_member_all'].pop('terminal_page_rule')
        with patch('axiom_data.source_completeness._extension', return_value=contract):
            checked = validate_policy_contract('tushare_industry_qualification.v1', 'index_member_all')
            self.assertEqual(checked['status'], 'BLOCKED')
            self.assertIn('missing complete page-series termination rule', checked['reasons'])

    def test_industry_payload_without_explicit_scope_cannot_claim_complete(self):
        for version in ('tushare_sw_pilot.v1','tushare_industry_qualification.v1'):
            with self.subTest(version=version), self.assertRaises(SourceCompletenessError):
                validate_payload_completeness(version,'index_member_all',[])

    def test_required_indicator_rule_cannot_fall_back_to_legacy_policy(self):
        for key in ('completeness_rule', 'limit', 'action'):
            contract = copy.deepcopy(_extension())
            contract['endpoints']['fina_indicator'].pop(key)
            with patch('axiom_data.source_completeness._extension', return_value=contract):
                self.assertEqual(validate_policy_contract('tushare_fina_indicator.v1','fina_indicator')['status'], 'BLOCKED')

    def test_documented_response_contracts_keep_real_caps_and_dates(self):
        for endpoint, cap in [('daily',6000),('daily_basic',6000),('stk_limit',5800)]:
            self.assertEqual(completeness_policy('tushare_phase1.v1', endpoint)['limit'], cap)
        for endpoint in ('income','balancesheet','cashflow'):
            policy = completeness_policy('tushare_pr6.v1', endpoint)
            self.assertIsNone(policy['limit'])
            admitted = validate_payload_completeness('tushare_pr6.v1', endpoint, [],
                params={'ts_code':'000001.SZ','start_date':'20140101','end_date':'20141231'})
            self.assertTrue(admitted['complete'])
            self.assertEqual(admitted['empty_result_semantics'], 'no_source_events_in_scope')
            with self.assertRaises(SourceCompletenessError):
                validate_payload_completeness('tushare_pr6.v1', endpoint, [], params={})
        self.assertEqual(completeness_policy('tushare_fina_indicator.v1','fina_indicator')['date_field'], 'end_date')
        self.assertEqual(completeness_policy('tushare_pr7.v1','top10_holders')['date_field'], 'end_date')

    def test_calendar_requires_all_civil_days_not_only_open_sessions(self):
        params = {'exchange':'SSE','start_date':'20250103','end_date':'20250105'}
        rows = [{'exchange':'SSE','cal_date':day,'is_open':opened,'pretrade_date':'20250102'}
                for day,opened in [('20250103',1),('20250104',0),('20250105',0)]]
        result = validate_payload_completeness('tushare_phase1.v1','trade_cal',rows,params=params)
        self.assertTrue(result['complete'])
        for incomplete in ([], rows[:1], rows[:2]+rows[:1]):
            with self.assertRaises(SourceCompletenessError):
                validate_payload_completeness('tushare_phase1.v1','trade_cal',incomplete,params=params)
        with self.assertRaises(SourceCompletenessError):
            validate_payload_completeness('tushare_phase1.v1','trade_cal',rows,params={**params,'is_open':'1'})

    def test_snapshot_proofs_are_not_empty_sparse_event_proofs(self):
        from axiom_data.sw_source import load_profile
        fields = load_profile('tushare_industry_qualification.v1')['endpoints']['index_classify']['fields']
        row = dict.fromkeys(fields, 'fixture')
        row.update(index_code='801010.SI', src='SW2021', level='L1')
        params = {'src':'SW2021','level':'L1'}
        result = validate_payload_completeness('tushare_industry_qualification.v1','index_classify',[row],params=params)
        self.assertTrue(result['complete'])
        self.assertEqual(result['coverage_semantics'], 'taxonomy_snapshot')
        for rows in ([], [row,row], [{**row,'src':'SW2014'}]):
            with self.assertRaises(SourceCompletenessError):
                validate_payload_completeness('tushare_industry_qualification.v1','index_classify',rows,params=params)
        with self.assertRaisesRegex(SourceCompletenessError, 'original Raw document'):
            validate_payload_completeness('exchange_security.v1','SSE',[{'symbol':'600001.SH'}],params={})

    def test_monthly_and_security_only_sources_preserve_actual_capabilities(self):
        bounded = {'index_code':'000300.SH','start_date':'20250101','end_date':'20250131'}
        result = validate_payload_completeness('tushare_pr6.v1','index_weight',[],params=bounded)
        self.assertTrue(result['complete'])
        self.assertEqual(result['empty_result_semantics'], 'unknown_observation')
        # The supplier recommends monthly queries; a short correction range
        # crossing a month boundary is still a supported date-range request.
        cross_month = {**bounded, 'start_date':'20250715', 'end_date':'20250801'}
        self.assertTrue(validate_payload_completeness('tushare_pr6.v1','index_weight',[],
                                                     params=cross_month)['complete'])
        with self.assertRaises(SourceCompletenessError):
            validate_payload_completeness('tushare_pr6.v1','index_weight',[],params={**bounded,'end_date':'20250201'})
        dividend = validate_payload_completeness('tushare_dm1.v1','dividend',[],params={'ts_code':'000001.SZ'})
        self.assertTrue(dividend['complete'])
        self.assertEqual(dividend['action'], 'fail_closed_unsplittable')
        with self.assertRaises(SourceCompletenessError):
            validate_payload_completeness('tushare_dm1.v1','dividend',[],params={'ts_code':'000001.SZ','start_date':'20250101','end_date':'20250131'})

    def test_extension_changes_identity_and_legacy_projection_stays_frozen(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            class Client:
                def query(self,*args,**kwargs): return []
            params = {'ts_code':'000001.SZ','start_date':'20250101','end_date':'20250131'}
            ref = TushareCollector(root,Client()).collect('daily',params,retrieved_at='2025-02-01T00:00:00Z')
            raw = load_raw_batch(root,ref.raw_batch_id)
            binding = source_profile_completeness_binding('tushare_phase1.v1',tushare_source_profile_digest())
            self.assertEqual(raw.manifest['summary']['source_completeness'], binding)
            self.assertEqual(raw.manifest['source_profile_digest'], tushare_source_profile_digest())
            self.assertTrue(validate_raw_completeness(raw)['complete'])
            legacy = validate_raw_completeness_legacy(raw)
            self.assertFalse(legacy['complete'])
            self.assertNotIn('scope', legacy)
            original_manifest = raw.manifest
            changed = copy.deepcopy(_extension()); changed['historical_limitations'] += ' Contract revision.'
            with patch('axiom_data.source_completeness._extension',return_value=changed):
                self.assertNotEqual(current_contract_binding()['extension_digest'], binding['extension_digest'])
                self.assertNotEqual(source_profile_completeness_binding('tushare_phase1.v1',tushare_source_profile_digest()),binding)
                admission = validate_raw_completeness(raw)
                self.assertTrue(admission['complete'])
                self.assertNotEqual(admission['policy_digest'],binding['extension_digest'])
                self.assertEqual(validate_raw_completeness_legacy(raw),legacy)
                changed_ref = TushareCollector(root,Client()).collect('daily',params,retrieved_at='2025-02-01T00:00:00Z')
                self.assertNotEqual(changed_ref.raw_batch_id,ref.raw_batch_id)
            self.assertEqual(load_raw_batch(root,ref.raw_batch_id).manifest,original_manifest)

    def test_old_unbound_raw_is_revalidated_without_rewriting_it(self):
        from axiom_data.pr6_source import profile_digest, load_pr6_source_profile
        with tempfile.TemporaryDirectory() as root:
            profile = load_pr6_source_profile()
            ref = write_raw_batch(root,'legacy-income',domain='financial_events',
                source_profile='tushare.pr6.income',source_profile_version='tushare_pr6.v1',
                source_profile_digest=profile_digest(),request={'endpoint':'income',
                    'fields':profile['endpoints']['income']['fields'],
                    'params':{'ts_code':'000001.SZ','start_date':'20140101','end_date':'20141231'}},
                retrieved_at='2025-02-01T00:00:00Z',payload=_json_bytes([]),collector_code='fixture',summary={})
            raw = load_raw_batch(root,ref.raw_batch_id)
            self.assertFalse(validate_raw_completeness_legacy(raw)['complete'])
            self.assertTrue(validate_raw_completeness(raw)['complete'])
            self.assertNotIn('source_completeness',load_raw_batch(root,ref.raw_batch_id).manifest['summary'])
