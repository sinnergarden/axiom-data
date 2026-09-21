"""Financial rebuild ordering and bounded Raw lifetime regressions."""
import copy
import json
import tempfile
import unittest
import weakref
from pathlib import Path
from unittest.mock import patch

from axiom_data.artifacts import RawBatch, RawBatchRef, RawBatches
from axiom_data.contracts import load_contract
from axiom_data.pr6_source import Pr6Builder

FIXTURE = Path(__file__).parent / 'fixtures' / 'financial_revision' / 'pr6-480cfa7d0400011c3adfcb532a724f202e65fbe15663730d840bbe37bd6a5b9c'


class FinancialRebuildTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest = json.loads((FIXTURE / 'manifest.json').read_bytes())
        self.payload = (FIXTURE / 'payload.bin').read_bytes()

    def raw(self, identity, when=None):
        manifest = copy.deepcopy(self.manifest)
        if when:
            manifest['retrieved_at'] = when
        return RawBatch(RawBatchRef(identity, 'sha256:' + '1' * 64), manifest, self.payload)

    def mapped(self, raws, parent=()):
        return Pr6Builder(self.root, 'financial_events')._build_rows(
            load_contract('financial_events.v3'), parent, raws)

    def test_tied_observation_provenance_and_input_permutation(self):
        # The old implementation sorted equal-time Raw IDs before normalization.
        a, z = self.raw('a'), self.raw('z')
        first = self.mapped([a, z])
        self.assertEqual(first, self.mapped([z, a]))
        self.assertEqual(first[0]['source_ref'], 'a')
        self.assertEqual(len(first[0]['observations']), 2)

    def test_raw_payload_lifetime_is_bounded(self):
        class TrackedRaw(RawBatch):
            pass
        live = []
        maximum = 0
        def load(root, identity):
            nonlocal maximum
            source = self.raw(identity)
            raw = TrackedRaw(source.ref, source.manifest, source.payload)
            live[:] = [ref for ref in live if ref() is not None]
            live.append(weakref.ref(raw))
            maximum = max(maximum, len(live))
            return raw
        with patch('axiom_data.artifacts.load_raw_batch', side_effect=load):
            rows = self.mapped(RawBatches(self.root, [f'raw-{i:03}' for i in range(80)]))
        self.assertLessEqual(maximum, 2)
        self.assertEqual(len(rows[0]['observations']), 80)

    def test_incremental_replay_matches_genesis(self):
        older = self.raw('older', '2026-09-14T00:00:00Z')
        later = self.raw('later', '2026-09-15T00:00:00Z')
        parent = self.mapped([older])
        with patch('axiom_data.artifacts.load_raw_batch', return_value=older) as load:
            incremental = self.mapped([later], parent)
        load.assert_called_once_with(self.root, 'older')
        self.assertEqual(incremental, self.mapped([later, older]))
