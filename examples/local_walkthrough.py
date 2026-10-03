"""Small, offline demonstration of the real local Data implementation.

Run with Python >=3.11 after installing axiom-data, or PYTHONPATH=src.
All source values are synthetic. A temporary root is removed after the run;
pass --root PATH to retain an isolated demonstration root for inspection.
"""

from dataclasses import replace
import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from axiom_data import Data, IngestBatch, QuerySpec, UpdateRequest


CONTRACT = {
    "contract_id": "synthetic.daily.v1",
    "logical_key": ["security_id", "session"],
    "fields": {"security_id": {"dtype": "string", "nullable": False},
               "session": {"dtype": "date", "nullable": False},
               "close": {"dtype": "float64", "unit": "CNY/share"}},
}
PROFILE = {
    "id": "synthetic.daily.v1", "data_nature": "synthetic",
    "field_map": {"security_id": "security_id", "session": "session", "close": "close"},
    "source_units": {"close": "CNY/share"},
    "availability": {"timezone": "Asia/Shanghai", "session_release_time": "20:00:00",
                     "basis": "demonstration assumption only"},
}


def batch(session="2020-01-02", observed="2026-09-28T10:00:00+08:00"):
    """Create source bytes independently of the canonical writer."""
    rows = [{"security_id": "synthetic-A", "session": session, "close": 10.0},
            {"security_id": "synthetic-B", "session": session, "close": 20.0}]
    return IngestBatch("market_daily", json.dumps(rows).encode(),
                       {"session": session, "data_nature": "synthetic"},
                       CONTRACT, PROFILE, observed)


def run(root):
    data = Data(root)
    initial = data.update(base_snapshot=None, request=UpdateRequest(
        (batch(),), "demo-initial", {"data_nature": "synthetic", "source_state": "working-tree"}))
    query = QuerySpec("market_daily", ("close",), ("synthetic-A", "synthetic-B"),
                      ("2020-01-02",), "best_effort_vendor_v1",
                      {"2020-01-02": "2020-01-02T20:00:00+08:00"})
    result = data.read(snapshot=initial.snapshot_id, query=query)
    print("Synthetic facts (real Parquet / Reader path):")
    print(result.frame.to_string(index=False))
    strict = data.read(snapshot=initial.snapshot_id,
                       query=replace(query, pit_policy="operational_pit_v1"))
    print("2020 operational visibility:",
          [row["missing_reason"] for row in strict.field_meta["close"]["by_key"]])
    repeated = data.update(base_snapshot=initial.snapshot_id, request=UpdateRequest(
        (batch(observed="2026-09-29T10:00:00+08:00"),), "demo-repeat", {}))
    assert not repeated.changed and repeated.snapshot_id == initial.snapshot_id
    updated = data.update(base_snapshot=initial.snapshot_id, request=UpdateRequest(
        (batch(session="2020-01-03"),), "demo-next-session", {}))
    assert updated.changed
    assert data.read(snapshot=initial.snapshot_id, query=query).to_json() == result.to_json()
    print("Duplicate fetch: Raw recorded; Snapshot unchanged.")
    print("New session: new Snapshot; old Snapshot values unchanged.")
    print("Query writes no View directory:", not (Path(root) / "views").exists())
    print("Snapshot:", initial.snapshot_id)
    print("UI response keys:", list(result.to_json()))
    return data, initial, query, result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()
    if args.root:
        run(args.root)
    else:
        with TemporaryDirectory(prefix="axiom-local-demo-") as directory:
            run(Path(directory) / "data")
