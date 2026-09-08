from types import SimpleNamespace
import unittest
from axiom_data.artifacts import _digest, _json_bytes
from axiom_data.validation import validation_signature, assess_applicability


class ApplicabilityTest(unittest.TestCase):
    def test_normal_new_day_does_not_expire_semantics_but_data_checks_remain(self):
        def signature(day, code='builder.v1'):
            commit=SimpleNamespace(ref=SimpleNamespace(domain='market_daily',commit_id=day,
                contract_version='market_daily.v1'),manifest={'contract_digest':'contract',
                'builder_config':{'end_session':day}, 'builder_implementation_ref':{'revision':code},
                'ordered_raw_batch_refs':[],'parent_commit_ref':None})
            return validation_signature(SimpleNamespace(commits={'market_daily':commit}),
                semantic_scope={'exchanges':['SSE','SZSE'],'storage_start':'2014-01-01'},ruleset_digest='rules')
        first=signature('2026-09-07');second=signature('2026-09-08')
        self.assertEqual(first,second)
        assessment=assess_applicability(first,second)
        self.assertEqual(assessment['version_validation'],'APPLICABLE')
        self.assertEqual(assessment['incremental_data_validation'],'REQUIRED')
        changed=assess_applicability(first,signature('2026-09-08','builder.v2'))
        self.assertEqual(changed['version_validation'],'REQUIRED')
        self.assertEqual(changed['changed_semantics'],['builder_versions'])
