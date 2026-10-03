"""Verify a real Snapshot after moving its complete data and source bundle.

This offline example copies into new directories, reconstructs every enabled
domain from its original Raw, and writes a measured JSON report. It never
contacts a supplier or changes the source/restored current pointers.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import time

import pandas as pd

from axiom_data import Data, QuerySpec, export_bundle, import_bundle, verify_bundle


def run(*, root: Path, snapshot: str, bundle: Path, restored: Path,
        code_root: Path, probe_symbols: tuple[str, ...],
        probe_sessions: tuple[str, ...]) -> dict:
    """Export/import/rebuild a fixed Snapshot and compare original bytes.

    ``probe_symbols`` contains stable security IDs, rather than supplier codes.
    Bundle and restored directories must be absent. Original observations,
    keys, units and missing values are replayed by the package's public API.
    A mismatching partition or reader result fails instead of certifying it.
    """
    original = Data(root)
    pinned = original.resolve(snapshot)
    pointer = root / "current.json"
    before = pointer.read_bytes() if pointer.exists() else None
    manifest = original.store.load_snapshot(pinned)
    started = time.perf_counter()
    exported = export_bundle(root, bundle, snapshot_id=pinned, code_root=code_root)
    checked = verify_bundle(bundle)
    imported_id = import_bundle(bundle, restored)
    assert imported_id == pinned
    moved = Data(restored)
    moved_before = moved.resolve("current")
    domains = tuple(manifest["domains"])
    raw_ids = list(dict.fromkeys(raw_id for domain in manifest["domains"].values()
                                for raw_id in domain["raw_batch_ids"]))
    rebuild_start = time.perf_counter()
    rebuilt = moved.rebuild(base_snapshot=pinned, raw_batch_ids=raw_ids, domains=domains,
                            operation_id="portable-all-domains-rebuild",
                            build_context={"purpose": "offline portable acceptance"},
                            promote=False)
    rebuild_seconds = time.perf_counter() - rebuild_start
    candidate = moved.store.load_snapshot(rebuilt.snapshot_id)
    comparisons = {}
    for name in domains:
        initial, replay = manifest["domains"][name], candidate["domains"][name]
        initial_hashes = {part["partition"]: part["file_sha256"] for part in initial["partitions"]}
        replay_hashes = {part["partition"]: part["file_sha256"] for part in replay["partitions"]}
        same = initial_hashes == replay_hashes and initial["contract"] == replay["contract"]
        assert same, f"offline rebuild changed {name}"
        comparisons[name] = {"partition_bytes_identical": same,
                             "partitions": len(initial_hashes),
                             "rows": sum(part["rows"] for part in initial["partitions"])}
    probe = None
    if probe_symbols and probe_sessions:
        # This checks economic membership under a declared historical
        # assumption. It is not a claim about a historical public vintage.
        query = QuerySpec("universe_membership", ("is_member",), probe_symbols,
                          probe_sessions, "best_effort_vendor_v1",
                          {day: datetime.fromisoformat(f"{day}T16:00:00+08:00")
                           for day in probe_sessions},
                          universe_id="csi1800", purpose="historical_exploration")
        source_read = original.members(snapshot=pinned, query=query)
        restored_read = moved.members(snapshot=pinned, query=query)
        rebuilt_read = moved.members(snapshot=rebuilt.snapshot_id, query=query)
        assert source_read.frame.equals(restored_read.frame)
        assert source_read.frame.equals(rebuilt_read.frame)
        frame = source_read.frame.astype(object)
        probe = {"query_policy": query.pit_policy, "frames_identical": True,
                 "rows": frame.where(pd.notna(frame), None).to_dict(orient="records"),
                 "limitations": source_read.context["limitations"]}
    after = pointer.read_bytes() if pointer.exists() else None
    unchanged = after == before and moved.resolve("current") == moved_before
    assert unchanged
    return {"schema_version": "axiom_data_portable_acceptance_v1",
            "source_data_root": str(root.resolve()), "snapshot_id": pinned,
            "bundle": str(bundle.resolve()), "imported_root": str(restored.resolve()),
            "bundle_id": exported["bundle_id"], "bundle_verified": checked,
            "rebuilt_snapshot_id": rebuilt.snapshot_id, "domains": list(domains),
            "raw_batches": len(raw_ids), "partition_comparisons": comparisons,
            "membership_probe": probe, "current_unchanged": unchanged,
            "rebuild_seconds": rebuild_seconds,
            "elapsed_seconds": time.perf_counter() - started,
            "scope": "same-host directory relocation; all enabled domains; no network calls"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--restored", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--code-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--member-security-ids", nargs="*", default=[],
                        help="Stable IDs from the frozen identity map, not supplier ticker codes")
    parser.add_argument("--member-sessions", nargs="*", default=[])
    args = parser.parse_args()
    report = run(root=args.data_root, snapshot=args.snapshot, bundle=args.bundle,
                 restored=args.restored, code_root=args.code_root,
                 probe_symbols=tuple(args.member_security_ids), probe_sessions=tuple(args.member_sessions))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"snapshot_id": report["snapshot_id"], "domains": len(report["domains"]),
                      "partition_bytes_identical": True, "current_unchanged": True,
                      "report": str(args.report.resolve())}, ensure_ascii=False))


if __name__ == "__main__":
    main()
