import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from axiom_data import SnapshotReader,ArtifactError
from axiom_data import artifacts
from test_pr3_vertical_slice import load_fixture,collect_fixture,build_fixture

class ReaderValidationReuseTest(unittest.TestCase):
    def test_each_read_validates_once_and_rejects_later_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);fixture=load_fixture('listing_slice')
            ids,_=collect_fixture(root,fixture);refs=build_fixture(root,fixture,ids)
            with patch.object(artifacts,'load_domain_commit',wraps=artifacts.load_domain_commit) as checked:
                first=SnapshotReader(root,refs['snapshot'])
                self.assertEqual(checked.call_count,3)
                again=SnapshotReader(root,refs['snapshot'])
                self.assertEqual(checked.call_count,6)
                self.assertEqual(first.commits['market_daily'].rows,again.commits['market_daily'].rows)
            c=first.commits['market_daily'];p=root/'canonical/market_daily/commits'/c.ref.commit_id/'rows.json'
            p.chmod(0o600);p.write_bytes(p.read_bytes()+b' ')
            with self.assertRaises(ArtifactError):SnapshotReader(root,refs['snapshot'])
