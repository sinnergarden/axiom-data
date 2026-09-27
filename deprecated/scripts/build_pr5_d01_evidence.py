#!/usr/bin/env python3
"""Freeze explicit old/new Snapshot evidence for the PR5 D01 gate."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from axiom_data import create_snapshot, load_snapshot
from axiom_data.domains import MARKET_DOMAINS
from axiom_data.evidence import validate_d01_snapshot_coexistence


def snapshot_ref(snapshot: Any) -> dict[str, str]:
    return {
        "snapshot_id": snapshot.ref.snapshot_id,
        "manifest_digest": snapshot.ref.manifest_digest,
        "identity_digest": snapshot.manifest["identity_digest"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--source-snapshot-id", required=True)
    parser.add_argument("--validation-root", type=Path, required=True)
    parser.add_argument("--evidence-path", type=Path, required=True)
    args = parser.parse_args()

    source_root = args.source_root.resolve()
    validation_root = args.validation_root.resolve()
    if validation_root.exists():
        raise SystemExit("D01 validation root already exists; immutable evidence is not overwritten")

    source = load_snapshot(source_root, args.source_snapshot_id)
    if source.manifest["schema_version"] != "data_snapshot.v2":
        raise SystemExit("D01 new Snapshot must be the D-M1 v2 Snapshot")

    validation_root.mkdir(parents=True)
    shutil.copytree(source_root / "raw", validation_root / "raw")
    shutil.copytree(source_root / "canonical", validation_root / "canonical")
    old = create_snapshot(
        validation_root,
        {
            domain: source.manifest["domain_refs"][domain]["domain_commit_id"]
            for domain in MARKET_DOMAINS
        },
        created_at=source.manifest["created_at"],
    )
    old_manifest = validation_root / "snapshots" / old.snapshot_id / "manifest.json"
    old_bytes_before_new = old_manifest.read_bytes()

    shutil.copytree(
        source_root / "snapshots" / source.ref.snapshot_id,
        validation_root / "snapshots" / source.ref.snapshot_id,
    )
    old_loaded = load_snapshot(validation_root, old.snapshot_id)
    new_loaded = load_snapshot(validation_root, source.ref.snapshot_id)
    if old_manifest.read_bytes() != old_bytes_before_new:
        raise SystemExit("old Snapshot changed while publishing new Snapshot evidence")

    evidence = {
        "schema_version": "axiom_data.pr5_d01_snapshot_coexistence.v1",
        "validation_root": str(validation_root),
        "old_snapshot": snapshot_ref(old_loaded),
        "new_snapshot": snapshot_ref(new_loaded),
        "old_snapshot_manifest_digest_before_new": old_loaded.ref.manifest_digest,
        "old_domain_refs": old_loaded.manifest["domain_refs"],
        "new_domain_refs": new_loaded.manifest["domain_refs"],
    }
    validate_d01_snapshot_coexistence(evidence)
    args.evidence_path.parent.mkdir(parents=True, exist_ok=True)
    args.evidence_path.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(evidence, sort_keys=True))


if __name__ == "__main__":
    main()
