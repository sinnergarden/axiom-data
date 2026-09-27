"""Import compatibility and the audited canonical consumer dependency boundary."""
import ast
import importlib
import inspect
import unittest


class ModuleBoundariesTest(unittest.TestCase):
    def test_historical_modules_share_the_current_implementation(self):
        pairs = {'dm2_admission': 'consumer_admission',
                 'pr6_coverage': 'financial_coverage',
                 'dm1_reconciliation': 'reference_reconciliation',
                 'pr7_reconciliation': 'event_reconciliation'}
        for old, current in pairs.items():
            with self.subTest(old=old):
                self.assertIs(importlib.import_module('axiom_data.deprecated.' + old),
                              importlib.import_module('axiom_data.' + current))
        from axiom_data.fundamentals_source import FundamentalsBuilder
        from axiom_data.deprecated.pr6_source import Pr6Builder
        from axiom_data.event_source import EventBuilder
        from axiom_data.deprecated.pr7_source import Pr7Builder
        self.assertIs(FundamentalsBuilder, Pr6Builder)
        self.assertIs(EventBuilder, Pr7Builder)
        self.assertEqual(FundamentalsBuilder.__module__, 'axiom_data.fundamentals_source')
        self.assertEqual(EventBuilder.__module__, 'axiom_data.event_source')

    def test_reader_and_admission_do_not_import_view_or_supplier_rules(self):
        from axiom_data import consumption, financial_coverage
        from axiom_data.financial_views import FIELD_MAP, WIDE_FIELDS
        from axiom_data.domains import fundamentals
        self.assertIs(FIELD_MAP, fundamentals.FIELD_MAP)
        self.assertIs(WIDE_FIELDS, fundamentals.WIDE_FIELDS)
        nodes = [ast.parse(inspect.getsource(consumption.SnapshotReader)),
                 ast.parse(inspect.getsource(consumption.leaf_facts)),
                 ast.parse(inspect.getsource(financial_coverage))]
        for tree in nodes:
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotIn(node.module, {
                        'axiom_data.financial_views', 'axiom_data.event_views',
                        'axiom_data.financial_views', 'axiom_data.event_views',
                        'axiom_data.event_source', 'axiom_data.event_source',
                    })
