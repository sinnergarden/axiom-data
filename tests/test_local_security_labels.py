"""Independent observed labels do not rewrite or reinterpret saved price inputs."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pandas as pd

from axiom_data import (ConflictError, Data, DataBatch, QueryError, load_review_security_labels,
                        save_review_display, save_review_security_labels)
from test_local_review_display import inputs, SESSIONS


def labels():
    rows = [{'security_id': 'ETF', 'listing_date': '2013-05-15', 'name': '新观察中文名称', 'source_code': '513100.SH'}]
    metadata = {f: {'dtype': 'string', 'by_key': [{'security_id': 'ETF', 'listing_date': '2013-05-15',
                    'raw_batch_id': 'new-name-raw', 'revision_id': 'new-name-revision',
                    'first_observed_at': '2026-10-05T01:00:00Z', 'usable_from': '2026-10-05T01:00:00Z',
                    'missing_reason': None}]} for f in ('name', 'source_code')}
    return DataBatch(pd.DataFrame(rows), metadata, {'contract_version': 'data_batch_v1',
          'domain': 'security_master', 'snapshot_id': 's-new-labels', 'reader_version': 'event_reader_v7',
          'query': {'fields': ['name', 'source_code'], 'symbols': ['ETF'], 'cutoff': '2026-10-05T02:00:00Z',
                    'pit_policy': 'operational_pit_v1', 'purpose': 'historical_exploration'}})


class SecurityLabelsTest(unittest.TestCase):
    def display(self, root):
        prices, factors = inputs()
        return save_review_display(prices, factors, anchor_session=SESSIONS[-1], destination=root / 'prices')

    def save(self, root, receipt, batch, **kwargs):
        return save_review_security_labels(batch, destination=root / 'labels', display_manifest=root / 'prices/manifest.json',
                display_manifest_sha256=receipt['manifest_file_ref']['sha256'],
                run_ref={'opaque': 'caller-defined', 'nested': ['no-account-schema', 7]}, **kwargs)

    def test_new_snapshot_and_clock_preserve_native_batch_and_opaque_binding(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); display = self.display(root); batch = labels()
            original = deepcopy(batch.to_json())
            old_bytes = {p.name: p.read_bytes() for p in (root / 'prices').iterdir()}
            receipt = self.save(root, display, batch)
            with patch.object(Data, '__init__', side_effect=AssertionError('loader constructed Data')), \
                 patch.object(Data, 'read', side_effect=AssertionError('loader queried')), \
                 patch.object(Data, 'events', side_effect=AssertionError('loader queried names')), \
                 patch('axiom_data.review_display.adjust_prices', side_effect=AssertionError('loader transformed')):
                loaded = load_review_security_labels(root / 'labels', manifest_sha256=receipt['manifest_file_ref']['sha256'])
            self.assertEqual(loaded['securities']['batch'], original)
            self.assertEqual(loaded['manifest']['run_ref'], {'opaque': 'caller-defined', 'nested': ['no-account-schema', 7]})
            self.assertEqual(loaded['manifest']['display_ref']['sha256'], display['manifest_file_ref']['sha256'])
            self.assertEqual(loaded['manifest']['source_snapshot_id'], 's-new-labels')
            self.assertEqual(loaded['manifest']['label_cutoff'], '2026-10-05T02:00:00Z')
            self.assertIsNone(loaded['securities']['name_valid_from'])
            self.assertIsNone(loaded['securities']['name_valid_to'])
            self.assertEqual(batch.to_json(), original)
            self.assertEqual({p.name: p.read_bytes() for p in (root / 'prices').iterdir()}, old_bytes)

    def test_missing_names_are_reported_without_filling_values(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); display = self.display(root); batch = labels()
            batch.frame.loc[0, 'name'] = None
            batch.field_meta['name']['by_key'][0]['missing_reason'] = 'not_provided'
            receipt = self.save(root, display, batch)
            loaded = load_review_security_labels(root / 'labels', manifest_sha256=receipt['manifest_file_ref']['sha256'])
            self.assertEqual(receipt['missing_name_security_ids'], ['ETF'])
            self.assertIsNone(loaded['securities']['batch']['records'][0]['name'])
            self.assertEqual(loaded['securities']['batch']['field_meta']['name']['by_key'][0]['missing_reason'], 'not_provided')

    def test_opaque_references_cannot_be_coerced_or_lose_colliding_keys(self):
        for value in ({1: 'numeric-key-run', '1': 'string-key-run'}, {'nested': {7: 'run'}},
                      {'tuple': ('run', 1)}, {'invalid': float('nan')}, {'invalid': float('inf')}):
            with self.subTest(value=value), TemporaryDirectory() as tmp:
                root = Path(tmp); display = self.display(root)
                with self.assertRaisesRegex(QueryError, 'strict JSON'):
                    save_review_security_labels(labels(), destination=root / 'labels',
                         display_manifest=root / 'prices/manifest.json',
                         display_manifest_sha256=display['manifest_file_ref']['sha256'], run_ref=value)
                self.assertFalse((root / 'labels').exists())

    def test_rejects_scope_identity_and_observation_clock_without_publication(self):
        for kind in ('query_scope', 'record_scope', 'duplicate', 'observation', 'provenance'):
            with self.subTest(kind=kind), TemporaryDirectory() as tmp:
                root = Path(tmp); display = self.display(root); batch = labels()
                if kind == 'query_scope': batch.context['query']['symbols'] = ['OTHER']
                if kind == 'record_scope': batch.frame.loc[0, 'security_id'] = 'OTHER'
                if kind == 'duplicate': batch = DataBatch(pd.concat([batch.frame, batch.frame]), batch.field_meta, batch.context)
                if kind == 'observation': batch.field_meta['name']['by_key'][0]['first_observed_at'] = '2026-10-06T00:00:00Z'
                if kind == 'provenance': batch.field_meta['source_code']['by_key'] = []
                with self.assertRaises(QueryError): self.save(root, display, batch)
                self.assertFalse((root / 'labels').exists())

    def test_changed_refs_existing_destination_and_write_failure(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); display = self.display(root); batch = labels()
            bad = deepcopy(display); bad['manifest_file_ref']['sha256'] = '0' * 64
            with self.assertRaisesRegex(QueryError, 'display manifest byte'): self.save(root, bad, batch)
            original_write = Path.write_bytes
            def fail_manifest(path, payload):
                if path.name == 'manifest.json': raise OSError('disk full')
                return original_write(path, payload)
            with patch.object(Path, 'write_bytes', fail_manifest):
                with self.assertRaises(OSError): self.save(root, display, batch)
            self.assertFalse((root / 'labels').exists())
            self.assertFalse(list(root.glob('.security-labels-*')))
            receipt = self.save(root, display, batch)
            with self.assertRaises(ConflictError): self.save(root, display, batch)
            path = root / 'labels/securities.json'; path.write_bytes(path.read_bytes() + b' ')
            with self.assertRaisesRegex(QueryError, 'file byte reference'):
                load_review_security_labels(root / 'labels', manifest_sha256=receipt['manifest_file_ref']['sha256'])
