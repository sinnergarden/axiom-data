"""Immutable pages use the same selector admission as industry mapping."""
import json
from datetime import date, timedelta
from pathlib import Path
import tempfile
import unittest

from axiom_data import ArtifactError, load_raw_batch, write_raw_batch
from axiom_data.artifacts import _json_bytes
from axiom_data.contracts import load_contract
from axiom_data.industry_qualification import observation
from axiom_data.fundamentals_source import FundamentalsBuilder
from axiom_data.source_completeness import SourceCompletenessError, validate_raw_completeness
from axiom_data.sw_source import load_profile, profile_digest


PROFILE = 'tushare_industry_qualification.v1'


class PaginationSelectorsTest(unittest.TestCase):
    def test_current_requalification_keeps_complete_series_per_commit(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from axiom_data.artifacts import _raw_ref
        from axiom_data.operations import _requalify_sources
        old = [self.page(0, 2), self.page(2, 1)]
        new = [self.page(0, 2), self.page(2, 1)]
        nodes = {
            'old': SimpleNamespace(manifest={'ordered_raw_batch_refs': [_raw_ref(r) for r in old],
                                            'parent_commit_ref': None}),
            'new': SimpleNamespace(manifest={'ordered_raw_batch_refs': [_raw_ref(r) for r in new],
                                            'parent_commit_ref': {'domain_commit_id': 'old'}})}
        def verified(root, domain, identity):
            return nodes[identity], frozenset()
        with patch('axiom_data.artifacts._validated_domain_commit_with_raw_closure', side_effect=verified):
            _requalify_sources(self.root, {'industry_membership': 'new'})
            nodes['new'].manifest['ordered_raw_batch_refs'].pop()
            with self.assertRaises(ArtifactError):
                _requalify_sources(self.root, {'industry_membership': 'new'})

    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.serial = 0

    def page(self, offset, count, *, endpoint='index_member_all', selector=None,
             row_change=None, request_change=None, manifest_change=None):
        self.serial += 1
        definition = load_profile(PROFILE)['endpoints'][endpoint]
        params = {'is_new':'N','limit':'2','offset':str(offset), **(selector or {})}
        records = []
        for index in range(offset, offset + count):
            row = {'l1_code':'801160.SI','l1_name':'公用事业','l2_code':'801161.SI',
                'l2_name':'电力','l3_code':'851161.SI','l3_name':'风力发电',
                'ts_code':'000001.SZ','name':'fixture','in_date':(date(2020,1,1)+timedelta(days=index)).strftime('%Y%m%d'),
                'out_date':'20250101','is_new':'N'}
            if endpoint == 'ci_index_member':
                row.update(l1_code='CI005010.CI', l2_code='CI005011.CI', l3_code='CI005012.CI')
            row.update(row_change or {})
            records.append(row)
        request = {'endpoint':endpoint,'params':params,'fields':definition['fields']}
        request.update(request_change or {})
        kwargs = dict(domain='industry_membership',source_profile='tushare.sw-pilot.'+endpoint,
            source_profile_version=PROFILE,source_profile_digest=profile_digest(PROFILE),request=request,
            retrieved_at='2026-09-12T00:00:00Z',payload=_json_bytes(records),collector_code='fixture',summary={})
        kwargs.update(manifest_change or {})
        ref = write_raw_batch(self.root,'page-'+str(self.serial),**kwargs)
        return load_raw_batch(self.root,ref.raw_batch_id)

    def assert_rejected_by_shared_gate_and_mapper(self, first, pages, bad, reason):
        with self.assertRaisesRegex(SourceCompletenessError,reason) as raised:
            validate_raw_completeness(first,evidence=pages)
        self.assertEqual(raised.exception.raw_batch_id,bad.ref.raw_batch_id)
        with self.assertRaisesRegex(SourceCompletenessError,reason):
            validate_raw_completeness(bad)
        with self.assertRaisesRegex(ArtifactError,reason):
            observation(self.root,bad.ref.raw_batch_id)
        builder = FundamentalsBuilder(self.root,'industry_membership',builder_config={
            'industry_source_profile':'tushare_sw2021.v1','symbols':['000001.SZ'],
            'start_session':'2020-01-01','end_session':'2024-12-31'})
        # SW mapping calls the formal per-Raw observation validator before it
        # can build taxonomy or perform source-history qualification.
        with self.assertRaisesRegex(ArtifactError,reason):
            builder._build_rows(load_contract('industry_membership.v3'),(),[bad])

    def test_valid_three_page_series_for_both_real_endpoints(self):
        for endpoint in ('index_member_all','ci_index_member'):
            pages=[self.page(offset,count,endpoint=endpoint,selector={'ts_code':'000001.SZ'})
                   for offset,count in ((0,2),(2,2),(4,1))]
            self.assertTrue(validate_raw_completeness(pages[0],evidence=pages)['complete'])
            for raw in pages:
                self.assertFalse(validate_raw_completeness(raw)['complete'])
                self.assertEqual(observation(self.root,raw.ref.raw_batch_id)[1],
                                 json.loads(raw.payload))

    def test_middle_and_terminal_mode_or_symbol_mismatch_cannot_complete(self):
        for position in (1,2):
            for field,bad_value in (('is_new','Y'),('ts_code','600036.SH')):
                pages=[self.page(offset,count,selector={'ts_code':'000001.SZ'},
                       row_change={field:bad_value} if index==position else None)
                       for index,(offset,count) in enumerate(((0,2),(2,2),(4,1)))]
                self.assert_rejected_by_shared_gate_and_mapper(
                    pages[0],pages,pages[position],'request_scope_mismatch:'+field)

    def test_industry_selector_mismatch_and_injected_date_selector_fail(self):
        first=self.page(0,2,selector={'l3_code':'851161.SI'})
        wrong=self.page(2,1,selector={'l3_code':'851161.SI'},row_change={'l3_code':'851162.SI'})
        self.assert_rejected_by_shared_gate_and_mapper(first,[first,wrong],wrong,'request_scope_mismatch:l3_code')
        # These real membership endpoints do not support date request filters.
        # Rejecting unsupported selectors avoids inventing provider capability.
        wrong=self.page(2,1,selector={'l3_code':'851161.SI','start_date':'20200101','end_date':'20241231'})
        self.assert_rejected_by_shared_gate_and_mapper(first,[first,wrong],wrong,'bounded selector')

    def test_page_field_and_source_bindings_are_required(self):
        first=self.page(0,2)
        changes=[{'request_change':{'fields':['ts_code']}},
                 {'manifest_change':{'source_profile':'tushare.sw-pilot.ci_index_member'}},
                 {'manifest_change':{'domain':'financial_events'}}]
        for change in changes:
            wrong=self.page(2,1,**change)
            self.assert_rejected_by_shared_gate_and_mapper(first,[first,wrong],wrong,'binding mismatch')
        wrong=self.page(2,1,row_change={'unexpected':'field'})
        self.assert_rejected_by_shared_gate_and_mapper(first,[first,wrong],wrong,'response_field_mismatch')

    def test_page_request_series_cannot_switch_selector(self):
        first=self.page(0,2,selector={'ts_code':'000001.SZ'})
        other=self.page(2,1,selector={'ts_code':'600036.SH'},row_change={'ts_code':'600036.SH'})
        with self.assertRaisesRegex(SourceCompletenessError,'misbound immutable page series'):
            validate_raw_completeness(first,evidence=[first,other])
        with self.assertRaisesRegex(SourceCompletenessError,'response binding'):
            validate_raw_completeness(first,evidence=[self.page(0,2,selector={'ts_code':'000001.SZ'}),
                                                      self.page(2,1,selector={'ts_code':'000001.SZ'})])
