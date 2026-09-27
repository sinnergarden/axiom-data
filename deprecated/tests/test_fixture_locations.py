import copy
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from fixture_locations import FORENSIC_ROOTS, LEGACY_DATA_ROOT, fixture_root


class FixtureLocationsTest(unittest.TestCase):
    def test_fixed_mapping_preserves_all_eight_identities_and_suffixes(self):
        self.assertEqual(len(FORENSIC_ROOTS), 8)
        with patch.dict(os.environ, {}, clear=True):
            for name in FORENSIC_ROOTS:
                with self.subTest(name=name):
                    recorded = LEGACY_DATA_ROOT / 'forensic' / name
                    expected = Path('/var/lib/axiom-data/forensic') / name
                    self.assertEqual(fixture_root(recorded), expected)
                    self.assertEqual(fixture_root(recorded / 'source/snapshots/explicit-id'),
                                     expected / 'source/snapshots/explicit-id')
            self.assertEqual(fixture_root(LEGACY_DATA_ROOT),
                             Path('/var/lib/axiom-data/forensic/pr8-source-qualification-20260913'))

    def test_override_changes_only_the_explicit_storage_location(self):
        recorded = LEGACY_DATA_ROOT / 'forensic/pr7-dm2-20260909-r1/admission-r5/source'
        with patch.dict(os.environ, {'AXIOM_TEST_FORENSIC_ROOT': '/tmp/axiom-fixture-validation'}):
            self.assertEqual(fixture_root(recorded),
                             Path('/tmp/axiom-fixture-validation/pr7-dm2-20260909-r1/admission-r5/source'))
            self.assertEqual(fixture_root(LEGACY_DATA_ROOT),
                             Path('/tmp/axiom-fixture-validation/pr8-source-qualification-20260913'))
        for destination in ('', 'relative/archive', '/tmp/../archive'):
            with self.subTest(destination=destination), patch.dict(
                    os.environ, {'AXIOM_TEST_FORENSIC_ROOT': destination}):
                with self.assertRaisesRegex(ValueError, 'absolute path without traversal'):
                    fixture_root(recorded)

    def test_unknown_paths_and_prefix_lookalikes_are_rejected(self):
        with patch.dict(os.environ, {}, clear=True):
            for recorded in ('relative/source', str(LEGACY_DATA_ROOT / 'raw/batches/one'),
                    str(LEGACY_DATA_ROOT / 'forensic'),
                    str(LEGACY_DATA_ROOT / 'forensic/uninventoried-run/source'),
                    str(LEGACY_DATA_ROOT / 'forensic/pr7-dm2-20260909-r1-other/source'),
                    str(LEGACY_DATA_ROOT / 'forensic/pr7-dm2-20260909-r1/../other'),
                    '/var/lib/axiom-data/forensic/pr7-dm2-20260909-r1/source'):
                with self.subTest(recorded=recorded), self.assertRaises(ValueError):
                    fixture_root(recorded)

    def test_location_adaptation_preserves_frozen_report_bytes_and_ids(self):
        reports = Path(__file__).resolve().parents[2] / 'deprecated' / 'history' / 'reports'
        with patch.dict(os.environ, {}, clear=True):
            for name in ('pr6', 'pr6-review', 'pr6-empty-prefix-compat'):
                path = reports / name / 'run_manifest.json'
                frozen_bytes = path.read_bytes()
                recorded = json.loads(frozen_bytes)
                original = copy.deepcopy(recorded)
                located = dict(recorded, data_root=str(fixture_root(recorded['data_root'])),
                               offline_root=str(fixture_root(recorded['offline_root'])))
                self.assertNotEqual(located['data_root'], recorded['data_root'])
                self.assertNotEqual(located['data_root'], located['offline_root'])
                self.assertEqual(located['artifact_refs'], recorded['artifact_refs'])
                self.assertEqual(located['offline_artifact_refs'], recorded['offline_artifact_refs'])
                self.assertEqual(recorded, original)
                self.assertEqual(path.read_bytes(), frozen_bytes)

    def test_reference_evidence_embedded_locations_leave_publication_paths_and_files_unchanged(self):
        reports = Path(__file__).resolve().parents[2] / 'deprecated/history/reports/pr5'
        for name, keys in (
                ('run_manifest', ('d01_snapshot_coexistence',)),
                ('dm1_acceptance_matrix', ('gates', 'D01', 'evidence'))):
            path = reports / (name + '.json')
            frozen_bytes = path.read_bytes()
            recorded = json.loads(frozen_bytes)
            located = copy.deepcopy(recorded)
            original_evidence, located_evidence = recorded, located
            for key in keys:
                original_evidence = original_evidence[key]
                located_evidence = located_evidence[key]
            located_evidence['validation_root'] = str(fixture_root(original_evidence['validation_root']))
            self.assertNotEqual(located_evidence['validation_root'], original_evidence['validation_root'])
            for key in ('old_snapshot', 'new_snapshot'):
                self.assertEqual(located_evidence[key], original_evidence[key])
            if name == 'run_manifest':
                self.assertEqual(located['forensic_closure'], recorded['forensic_closure'])
            self.assertEqual(path.read_bytes(), frozen_bytes)
