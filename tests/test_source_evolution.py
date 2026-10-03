"""An additive schema migration uses only saved Raw and preserves old inputs."""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from axiom_data import Data, QuerySpec
from axiom_data.storage import LocalStore
from test_local_updates import CONTRACT, PROFILE, batch, row, stored_rows, update


class SourceEvolutionTest(unittest.TestCase):
    def test_saved_field_can_be_added_but_uncollected_values_remain_null(self):
        with tempfile.TemporaryDirectory() as root:
            store = LocalStore(root)
            first = update(store, None, "before-added-field",
                           batch([dict(row(), supplier_extra=7),
                                  row(session="2020-02-03")]),
                           batch([row(99)], domain="independent_daily"))
            original = store.load_snapshot(first.snapshot_id)
            original_bytes = (Path(root) / "snapshots" / f"{first.snapshot_id}.json").read_bytes()
            receipts = (Path(root) / "raw/fetches.jsonl").read_bytes()
            contract, profile = deepcopy(CONTRACT), deepcopy(PROFILE)
            contract["contract_id"] = "synthetic.daily.v2"
            contract["fields"]["added_value"] = {"dtype": "float64", "unit": "ratio", "nullable": True}
            profile["id"] = "synthetic.v2"
            profile["field_map"]["added_value"] = "supplier_extra"
            profile["source_units"]["supplier_extra"] = "ratio"
            revised = Data(root).rebuild(
                base_snapshot=first.snapshot_id,
                raw_batch_ids=original["domains"]["market_daily"]["raw_batch_ids"],
                domains=("market_daily",), operation_id="add-saved-field",
                build_context={"code_ref": "schema-v2", "reason": "add previously saved field"},
                promote=False, domain_overrides={"market_daily": {
                    "contract": contract, "source_profile": profile}},
            )
            sessions = ("2020-01-02", "2020-02-03")
            result = Data(root).read(snapshot=revised.snapshot_id, query=QuerySpec(
                "market_daily", ("added_value",), ("sec-A",), sessions,
                "operational_pit_v1", {s: "2026-09-30T20:00:00+08:00" for s in sessions}))
            self.assertEqual(result.frame["added_value"].iloc[0], 7)
            self.assertTrue(result.frame["added_value"].isna().iloc[1])
            self.assertEqual(result.field_meta["added_value"]["unit"], "ratio")
            migrated = store.load_snapshot(revised.snapshot_id)
            self.assertEqual(migrated["domains"]["independent_daily"], original["domains"]["independent_daily"])
            self.assertEqual([r["first_observed_at"] for r in stored_rows(store, revised.snapshot_id)],
                             [r["first_observed_at"] for r in stored_rows(store, first.snapshot_id)])
            self.assertEqual((Path(root) / "snapshots" / f"{first.snapshot_id}.json").read_bytes(), original_bytes)
            self.assertEqual((Path(root) / "raw/fetches.jsonl").read_bytes(), receipts)
            self.assertEqual(store.resolve("current"), first.snapshot_id)


if __name__ == "__main__":
    unittest.main()
