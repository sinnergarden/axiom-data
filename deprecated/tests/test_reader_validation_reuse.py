import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch
from axiom_data import SnapshotReader,ArtifactError
from axiom_data import artifacts
from test_market_evidence_vertical_slice import load_fixture,collect_fixture,build_fixture

class ReaderValidationReuseTest(unittest.TestCase):
    def test_market_dependency_scan_only_during_explicit_full_validation(self):
        from axiom_data import validate_snapshot_closure
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);fixture=load_fixture('listing_slice')
            ids,_=collect_fixture(root,fixture);refs=build_fixture(root,fixture,ids)
            with patch.object(artifacts,'_validate_market_dependencies',wraps=artifacts._validate_market_dependencies) as checked:
                SnapshotReader(root,refs['snapshot'])
                self.assertEqual(checked.call_count,0)
                SnapshotReader(root,refs['snapshot'])
                self.assertEqual(checked.call_count,0)
                validate_snapshot_closure(root,refs['snapshot'])
                self.assertEqual(checked.call_count,1)
                validate_snapshot_closure(root,refs['snapshot'])
                self.assertEqual(checked.call_count,2)

    def test_publication_checks_composition_once_and_public_load_checks_again(self):
        from axiom_data import create_snapshot, load_snapshot
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);fixture=load_fixture('listing_slice')
            ids,_=collect_fixture(root,fixture);refs=build_fixture(root,fixture,ids)
            reader=SnapshotReader(root,refs['snapshot'])
            commits={d:c.ref.commit_id for d,c in reader.commits.items()}
            with patch.object(artifacts,'load_domain_commit',wraps=artifacts.load_domain_commit) as checked:
                result=create_snapshot(root,commits)
                self.assertEqual(checked.call_count,3)
                self.assertEqual(result.snapshot_id,refs['snapshot'])
                load_snapshot(root,result.snapshot_id)
                self.assertEqual(checked.call_count,6)
            c=reader.commits['market_daily'];p=root/'canonical/market_daily/commits'/c.ref.commit_id/'rows.json'
            p.chmod(0o600);p.write_bytes(p.read_bytes()+b' ')
            with self.assertRaises(ArtifactError):create_snapshot(root,commits)
            with self.assertRaises(ArtifactError):load_snapshot(root,result.snapshot_id)

    def test_inspection_validates_once_without_materializing_public_facts(self):
        from axiom_data.operations import inspect_snapshot
        from axiom_data.layout import DataRootLayout
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);fixture=load_fixture('listing_slice')
            ids,_=collect_fixture(root,fixture);refs=build_fixture(root,fixture,ids)
            pointer=DataRootLayout(root).current_pointer
            pointer.parent.mkdir(parents=True,exist_ok=True)
            pointer.write_text(json.dumps({'snapshot_id':refs['snapshot']}))
            with patch.object(artifacts,'_load_domain_commit',wraps=artifacts._load_domain_commit) as checked,\
                 patch.object(SnapshotReader,'facts',side_effect=AssertionError('inspection must stream canonical rows')):
                a=inspect_snapshot(root,'current')
                self.assertEqual(checked.call_count,3)
                b=inspect_snapshot(root,refs['snapshot'])
                self.assertEqual(checked.call_count,6)
                self.assertEqual(a,b)
            self.assertEqual(a['snapshot_id'],refs['snapshot'])
            self.assertEqual(a['domains']['market_daily']['full_scope_admission'],'NOT_ASSESSED')
            ref=a['domains']['market_daily']['commit_id']
            p=root/'canonical/market_daily/commits'/ref/'rows.json'
            p.chmod(0o600);p.write_bytes(p.read_bytes()+b' ')
            with self.assertRaises(ArtifactError):inspect_snapshot(root,'current')

    def test_each_read_validates_once_and_rejects_later_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);fixture=load_fixture('listing_slice')
            ids,_=collect_fixture(root,fixture);refs=build_fixture(root,fixture,ids)
            with patch.object(artifacts,'_load_domain_commit',wraps=artifacts._load_domain_commit) as checked:
                first=SnapshotReader(root,refs['snapshot'])
                self.assertEqual(checked.call_count,3)
                again=SnapshotReader(root,refs['snapshot'])
                self.assertEqual(checked.call_count,6)
                self.assertEqual(first.commits['market_daily'].rows,again.commits['market_daily'].rows)
            c=first.commits['market_daily'];p=root/'canonical/market_daily/commits'/c.ref.commit_id/'rows.json'
            p.chmod(0o600);p.write_bytes(p.read_bytes()+b' ')
            with self.assertRaises(ArtifactError):SnapshotReader(root,refs['snapshot'])
