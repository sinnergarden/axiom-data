from pathlib import Path
import tempfile
import unittest

from axiom_data import DataRootLayout, LayoutError


class DataRootLayoutTest(unittest.TestCase):
    def test_layout_matches_the_data_root_contract_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "axiom-data"
            layout = DataRootLayout(root)

            self.assertEqual(layout.raw_batches, root / "raw/batches")
            self.assertEqual(layout.raw_objects, root / "raw/objects")
            self.assertEqual(layout.domain_commits("market"), root / "canonical/market/commits")
            self.assertEqual(layout.domain_objects("market"), root / "canonical/market/objects")
            self.assertEqual(layout.derived_commits("adjusted-price"), root / "derived/adjusted-price/commits")
            self.assertEqual(layout.derived_objects("adjusted-price"), root / "derived/adjusted-price/objects")
            self.assertEqual(layout.snapshots, root / "snapshots")
            self.assertEqual(layout.qlib_exports, root / "exports/qlib")
            self.assertEqual(layout.patches, root / "patches")
            self.assertEqual(layout.build_provenance, root / "build_provenance")
            self.assertEqual(layout.staging, root / "staging")
            self.assertEqual(layout.reports, root / "reports")
            self.assertEqual(layout.current_pointer, root / "current.json")
            self.assertEqual(layout.catalog, root / "catalog.sqlite")
            self.assertFalse(root.exists())

    def test_relative_root_and_unsafe_segments_are_rejected(self) -> None:
        with self.assertRaises(LayoutError):
            DataRootLayout(Path("relative"))
        with self.assertRaises(LayoutError):
            DataRootLayout(Path("/var/lib/axiom-data")).domain_commits("../market")


if __name__ == "__main__":
    unittest.main()
