"""Import compatibility and the audited canonical consumer dependency boundary."""
import ast
import importlib
import inspect
import unittest


class ModuleBoundariesTest(unittest.TestCase):
    def test_historical_modules_share_the_current_implementation(self):
        pairs = {
            'dm1_source': 'reference_source', 'dm1_reconciliation': 'reference_reconciliation',
            'dm2_admission': 'consumer_admission', 'pr6_source': 'fundamentals_source',
            'pr6_coverage': 'financial_coverage', 'pr6_views': 'financial_views',
            'pr6_reconciliation': 'financial_reconciliation', 'pr7_source': 'event_source',
            'pr7_views': 'event_views', 'pr7_reconciliation': 'event_reconciliation',
            'domains.dm1': 'domains.reference', 'domains.pr6': 'domains.fundamentals',
            'domains.pr7': 'domains.events',
        }
        for old, current in pairs.items():
            with self.subTest(old=old):
                self.assertIs(importlib.import_module('axiom_data.' + old),
                              importlib.import_module('axiom_data.' + current))
        from axiom_data.fundamentals_source import FundamentalsBuilder, Pr6Builder
        from axiom_data.event_source import EventBuilder, Pr7Builder, select_pr7_revisions
        from axiom_data.pit import select_event_revisions
        self.assertIs(FundamentalsBuilder, Pr6Builder)
        self.assertIs(EventBuilder, Pr7Builder)
        self.assertIs(select_pr7_revisions, select_event_revisions)
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
                        'axiom_data.pr6_views', 'axiom_data.pr7_views',
                        'axiom_data.financial_views', 'axiom_data.event_views',
                        'axiom_data.pr7_source', 'axiom_data.event_source',
                    })
