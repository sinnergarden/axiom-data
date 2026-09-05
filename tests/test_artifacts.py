from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from axiom_data import (
    ArtifactConflictError,
    ArtifactError,
    ArtifactNotFoundError,
    BuildApplication,
    MarketDomainBuilder,
    create_snapshot,
    list_catalog,
    load_domain_commit,
    load_raw_batch,
    load_snapshot,
    lookup_catalog,
    rebuild_catalog,
    write_raw_batch,
)


FIXED_TIME = "2026-09-05T09:00:00+08:00"


def json_payload(rows: list[dict[str, object]]) -> bytes:
    return json.dumps(
        rows,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def calendar_rows(*, closed_first: bool = False) -> list[dict[str, object]]:
    return [
        {
            "exchange": "SZSE",
            "session": "2026-01-02",
            "is_open": not closed_first,
            "previous_open_session": None,
        },
        {
            "exchange": "SZSE",
            "session": "2026-01-03",
            "is_open": False,
            "previous_open_session": None if closed_first else "2026-01-02",
        },
        {
            "exchange": "SZSE",
            "session": "2026-01-04",
            "is_open": False,
            "previous_open_session": None if closed_first else "2026-01-02",
        },
        {
            "exchange": "SZSE",
            "session": "2026-01-05",
            "is_open": True,
            "previous_open_session": None if closed_first else "2026-01-02",
        },
    ]


def security_row(symbol: str = "000001.SZ", exchange: str = "SZSE") -> dict[str, object]:
    return {
        "symbol": symbol,
        "exchange": exchange,
        "list_session": "1991-04-03",
        "delist_session": None,
        "status": "source-current-listed",
    }


def market_row(session: str = "2026-01-02", symbol: str = "000001.SZ") -> dict[str, object]:
    return {
        "session": session,
        "symbol": symbol,
        "open": 10.0,
        "high": 11.0,
        "low": 9.0,
        "close": 10.5,
        "pre_close": 8.5,
        "volume_shares": 1_000,
        "amount_cny": 10_200.0,
        "adj_factor": 1.25,
        "up_limit": 11.0,
        "down_limit": 9.0,
        "is_suspended": False,
        "turnover_rate": 0.10,
        "total_market_cap_cny": 100_000_000.0,
        "circulating_market_cap_cny": 80_000_000.0,
    }


def write_rows(
    root: Path,
    raw_batch_id: str,
    domain: str,
    rows: list[dict[str, object]],
) -> None:
    write_raw_batch(
        root,
        raw_batch_id,
        domain=domain,
        source_profile=f"fixture-{domain}.v1",
        request={"fixture": raw_batch_id},
        retrieved_at=FIXED_TIME,
        payload=json_payload(rows),
        collector_code="fixture-writer.v1",
        summary={"rows": len(rows)},
    )


def build_commit(
    root: Path,
    domain: str,
    raw_ids: list[str],
    *,
    parent: str | None = None,
    commit_id: str | None = None,
    calendar_commit: str | None = None,
    security_commit: str | None = None,
) -> object:
    builder = MarketDomainBuilder(
        root,
        domain,
        commit_id=commit_id,
        calendar_commit_id=calendar_commit,
        security_master_commit_id=security_commit,
        builder_config={"format": "canonical-json"},
        created_at=FIXED_TIME,
    )
    return BuildApplication(domain, builder).build(
        parent,
        raw_ids,
        [],
        f"{domain}.v1",
    )


def build_pack(root: Path) -> dict[str, object]:
    write_rows(root, "raw-calendar", "trading_calendar", calendar_rows())
    write_rows(root, "raw-security", "security_master", [security_row()])
    write_rows(root, "raw-market", "market_daily", [market_row()])
    calendar = build_commit(root, "trading_calendar", ["raw-calendar"])
    security = build_commit(root, "security_master", ["raw-security"])
    market = build_commit(
        root,
        "market_daily",
        ["raw-market"],
        calendar_commit=calendar.commit_id,
        security_commit=security.commit_id,
    )
    snapshot = create_snapshot(
        root,
        {
            "trading_calendar": calendar.commit_id,
            "security_master": security.commit_id,
            "market_daily": market.commit_id,
        },
        created_at=FIXED_TIME,
    )
    return {
        "calendar": calendar,
        "security": security,
        "market": market,
        "snapshot": snapshot,
    }


class ArtifactTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "axiom-data"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_raw_batch_is_append_only_and_offline_readable(self) -> None:
        original_payload = b'{"supplier_rows":[{"symbol":"000001.SZ"}]}'
        arguments = {
            "domain": "market_daily",
            "source_profile": "fixture-market.v1",
            "request": {"endpoint": "daily", "date": "2026-01-02"},
            "retrieved_at": FIXED_TIME,
            "payload": original_payload,
            "collector_code": "fixture-writer.v1",
            "summary": {"objects": 1},
        }

        first_ref = write_raw_batch(self.root, "raw-001", **arguments)
        second_ref = write_raw_batch(self.root, "raw-001", **arguments)
        distinct_ref = write_raw_batch(self.root, "raw-002", **arguments)
        loaded = load_raw_batch(self.root, "raw-001")

        self.assertEqual(first_ref, second_ref)
        self.assertNotEqual(first_ref.raw_batch_id, distinct_ref.raw_batch_id)
        self.assertEqual(
            loaded.manifest["payload_files"][0]["content_digest"],
            load_raw_batch(self.root, "raw-002").manifest["payload_files"][0][
                "content_digest"
            ],
        )
        self.assertEqual(loaded.payload, original_payload)
        self.assertEqual(loaded.manifest["payload_files"][0]["path"], "payload.bin")
        self.assertNotIn(str(self.root), json.dumps(loaded.manifest))
        with self.assertRaises(ArtifactConflictError):
            write_raw_batch(
                self.root,
                "raw-001",
                **dict(arguments, payload=b"different supplier response"),
            )
        self.assertEqual(load_raw_batch(self.root, "raw-001").payload, original_payload)

    def test_failed_canonical_build_keeps_raw_and_publishes_no_commit(self) -> None:
        invalid = security_row()
        invalid["exchange"] = "SSE"
        write_rows(self.root, "raw-invalid", "security_master", [invalid])

        with self.assertRaises(ArtifactError):
            build_commit(
                self.root,
                "security_master",
                ["raw-invalid"],
                commit_id="security-invalid",
            )

        self.assertEqual(load_raw_batch(self.root, "raw-invalid").payload, json_payload([invalid]))
        self.assertFalse(
            (self.root / "canonical/security_master/commits/security-invalid").exists()
        )
        self.assertEqual(list((self.root / "staging").iterdir()), [])

    def test_domain_genesis_manifest_and_contract_provenance_are_complete(self) -> None:
        write_rows(self.root, "raw-security", "security_master", [security_row()])
        ref = build_commit(
            self.root,
            "security_master",
            ["raw-security"],
            commit_id="security-001",
        )
        repeated_ref = build_commit(
            self.root,
            "security_master",
            ["raw-security"],
            commit_id="security-001",
        )
        commit = load_domain_commit(self.root, "security_master", ref.commit_id)
        manifest = commit.manifest
        contract_bytes = (
            self.root
            / "canonical/security_master/commits/security-001/contract.json"
        ).read_bytes()

        self.assertIsNone(manifest["parent_commit_ref"])
        self.assertEqual(repeated_ref, ref)
        self.assertEqual(
            [item["raw_batch_id"] for item in manifest["ordered_raw_batch_refs"]],
            ["raw-security"],
        )
        self.assertEqual(manifest["ordered_patch_refs"], [])
        self.assertEqual(
            manifest["contract_digest"],
            f"sha256:{hashlib.sha256(contract_bytes).hexdigest()}",
        )
        self.assertEqual(manifest["builder_identity"], "axiom-data.market-json.v1")
        expected_config = json.dumps(
            {"format": "canonical-json"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        self.assertEqual(
            manifest["builder_config_digest"],
            f"sha256:{hashlib.sha256(expected_config).hexdigest()}",
        )
        self.assertEqual(manifest["output_files"][0]["path"], "rows.json")
        self.assertEqual(manifest["validation_summary"]["status"], "PASS")

    def test_incremental_uses_explicit_parent_and_preserves_raw_order(self) -> None:
        write_rows(self.root, "raw-base", "security_master", [security_row()])
        base = build_commit(self.root, "security_master", ["raw-base"])
        write_rows(
            self.root,
            "raw-second",
            "security_master",
            [security_row("600000.SH", "SSE")],
        )
        write_rows(
            self.root,
            "raw-third",
            "security_master",
            [security_row("600001.SH", "SSE")],
        )

        updated = build_commit(
            self.root,
            "security_master",
            ["raw-third", "raw-second"],
            parent=base.commit_id,
        )
        commit = load_domain_commit(self.root, "security_master", updated.commit_id)

        self.assertEqual(commit.manifest["parent_commit_ref"]["domain_commit_id"], base.commit_id)
        self.assertEqual(
            [item["raw_batch_id"] for item in commit.manifest["ordered_raw_batch_refs"]],
            ["raw-third", "raw-second"],
        )
        self.assertEqual(
            [row["symbol"] for row in commit.rows],
            ["000001.SZ", "600000.SH", "600001.SH"],
        )

    def test_conflicting_raw_values_do_not_use_last_write_wins(self) -> None:
        changed = security_row()
        changed["status"] = "source-current-suspended"
        write_rows(self.root, "raw-first", "security_master", [security_row()])
        write_rows(self.root, "raw-conflict", "security_master", [changed])

        with self.assertRaises(ArtifactConflictError):
            build_commit(
                self.root,
                "security_master",
                ["raw-first", "raw-conflict"],
                commit_id="security-conflict",
            )

        self.assertFalse(
            (self.root / "canonical/security_master/commits/security-conflict").exists()
        )

    def test_missing_wrong_type_cross_contract_and_patch_refs_are_rejected(self) -> None:
        write_rows(self.root, "raw-calendar", "trading_calendar", calendar_rows())
        write_rows(self.root, "raw-security", "security_master", [security_row()])
        calendar = build_commit(self.root, "trading_calendar", ["raw-calendar"])

        with self.assertRaises(ArtifactNotFoundError):
            build_commit(self.root, "security_master", ["raw-missing"])
        with self.assertRaises(ArtifactError):
            build_commit(self.root, "security_master", [calendar.commit_id])
        with self.assertRaises(ArtifactError):
            build_commit(
                self.root,
                "security_master",
                ["raw-security"],
                parent=calendar.commit_id,
            )

        builder = MarketDomainBuilder(
            self.root,
            "security_master",
            commit_id="patch-unsupported",
            created_at=FIXED_TIME,
        )
        with self.assertRaises(ArtifactError):
            BuildApplication("security_master", builder).build(
                None,
                ["raw-security"],
                ["patch-001"],
                "security_master.v1",
            )

    def test_target_collision_does_not_overwrite_original_commit(self) -> None:
        write_rows(self.root, "raw-one", "security_master", [security_row()])
        write_rows(
            self.root,
            "raw-two",
            "security_master",
            [security_row("600000.SH", "SSE")],
        )
        first = build_commit(
            self.root,
            "security_master",
            ["raw-one"],
            commit_id="security-fixed",
        )
        original_digest = load_domain_commit(
            self.root, "security_master", first.commit_id
        ).manifest_digest

        with self.assertRaises(ArtifactConflictError):
            build_commit(
                self.root,
                "security_master",
                ["raw-two"],
                commit_id="security-fixed",
            )

        original = load_domain_commit(self.root, "security_master", "security-fixed")
        self.assertEqual(original.manifest_digest, original_digest)
        self.assertEqual(original.rows[0]["symbol"], "000001.SZ")

    def test_symlink_cannot_bypass_immutable_target(self) -> None:
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        target = self.root / "raw/batches/raw-linked"
        target.parent.mkdir(parents=True)
        target.symlink_to(outside, target_is_directory=True)

        with self.assertRaises(ArtifactConflictError):
            write_raw_batch(
                self.root,
                "raw-linked",
                domain="security_master",
                source_profile="fixture-security.v1",
                request={"fixture": "raw-linked"},
                retrieved_at=FIXED_TIME,
                payload=json_payload([security_row()]),
                collector_code="fixture-writer.v1",
            )

        self.assertEqual(list(outside.iterdir()), [])

        root_link = Path(self.temporary.name) / "linked-root"
        root_link.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ArtifactError):
            write_raw_batch(
                root_link / "nested",
                "raw-through-root-link",
                domain="security_master",
                source_profile="fixture-security.v1",
                request={"fixture": "raw-through-root-link"},
                retrieved_at=FIXED_TIME,
                payload=json_payload([security_row()]),
                collector_code="fixture-writer.v1",
            )
        self.assertEqual(list(outside.iterdir()), [])

    def test_cross_domain_market_validation(self) -> None:
        scenarios = (
            (calendar_rows(), [security_row()], market_row(), None),
            (calendar_rows(), [security_row()], market_row("2026-01-06"), "calendar"),
            (calendar_rows(closed_first=True), [security_row()], market_row(), "closed"),
            (
                calendar_rows(),
                [security_row("600000.SH", "SSE")],
                market_row(),
                "security",
            ),
        )
        for number, (calendar_fixture, security_fixture, market_fixture, error) in enumerate(
            scenarios
        ):
            with self.subTest(error=error):
                root = Path(self.temporary.name) / f"cross-{number}"
                write_rows(root, "raw-calendar", "trading_calendar", calendar_fixture)
                write_rows(root, "raw-security", "security_master", security_fixture)
                write_rows(root, "raw-market", "market_daily", [market_fixture])
                calendar = build_commit(root, "trading_calendar", ["raw-calendar"])
                security = build_commit(root, "security_master", ["raw-security"])
                action = lambda: build_commit(
                    root,
                    "market_daily",
                    ["raw-market"],
                    commit_id="market-result",
                    calendar_commit=calendar.commit_id,
                    security_commit=security.commit_id,
                )
                if error is None:
                    ref = action()
                    self.assertEqual(
                        load_domain_commit(root, "market_daily", ref.commit_id)
                        .manifest["validation_summary"]["cross_domain"],
                        "PASS",
                    )
                else:
                    with self.assertRaises(ArtifactError):
                        action()
                    self.assertFalse(
                        (root / "canonical/market_daily/commits/market-result").exists()
                    )

    def test_snapshot_is_immutable_fixed_composition(self) -> None:
        pack = build_pack(self.root)
        first = load_snapshot(self.root, pack["snapshot"].snapshot_id)
        first_manifest = json.dumps(first.manifest, sort_keys=True)
        repeated = create_snapshot(
            self.root,
            {
                "trading_calendar": pack["calendar"].commit_id,
                "security_master": pack["security"].commit_id,
                "market_daily": pack["market"].commit_id,
            },
            created_at=FIXED_TIME,
        )
        self.assertEqual(repeated, first.ref)

        write_rows(self.root, "raw-market-d2", "market_daily", [market_row("2026-01-05")])
        market_d2 = build_commit(
            self.root,
            "market_daily",
            ["raw-market-d2"],
            parent=pack["market"].commit_id,
            calendar_commit=pack["calendar"].commit_id,
            security_commit=pack["security"].commit_id,
        )
        second = create_snapshot(
            self.root,
            {
                "trading_calendar": pack["calendar"].commit_id,
                "security_master": pack["security"].commit_id,
                "market_daily": market_d2.commit_id,
            },
            created_at=FIXED_TIME,
        )

        self.assertNotEqual(first.ref.snapshot_id, second.snapshot_id)
        self.assertEqual(
            json.dumps(load_snapshot(self.root, first.ref.snapshot_id).manifest, sort_keys=True),
            first_manifest,
        )
        self.assertEqual(
            first.manifest["domain_refs"]["market_daily"]["domain_commit_id"],
            pack["market"].commit_id,
        )

    def test_snapshot_rejects_missing_and_nonexistent_domain_commits(self) -> None:
        pack = build_pack(self.root)
        with self.assertRaises(ArtifactError):
            create_snapshot(
                self.root,
                {
                    "trading_calendar": pack["calendar"].commit_id,
                    "security_master": pack["security"].commit_id,
                },
            )
        with self.assertRaises(ArtifactNotFoundError):
            create_snapshot(
                self.root,
                {
                    "trading_calendar": pack["calendar"].commit_id,
                    "security_master": pack["security"].commit_id,
                    "market_daily": "market-does-not-exist",
                },
            )

    def test_catalog_can_be_deleted_and_rebuilt_from_manifests(self) -> None:
        pack = build_pack(self.root)
        count = rebuild_catalog(self.root)
        before = list_catalog(self.root)
        market_entry = lookup_catalog(
            self.root,
            "domain_commit",
            pack["market"].commit_id,
            domain="market_daily",
        )

        self.assertEqual(count, 7)
        self.assertFalse(Path(market_entry.manifest_path).is_absolute())
        (self.root / "catalog.sqlite").unlink()
        self.assertEqual(
            load_snapshot(self.root, pack["snapshot"].snapshot_id).ref,
            pack["snapshot"],
        )
        rebuild_catalog(self.root)
        self.assertEqual(list_catalog(self.root), before)

    def test_clean_offline_rebuild_has_identical_logical_artifacts(self) -> None:
        root_a = Path(self.temporary.name) / "offline-a"
        pack_a = build_pack(root_a)
        captured = {
            domain: load_domain_commit(root_a, domain, pack_a[name].commit_id)
            for domain, name in (
                ("trading_calendar", "calendar"),
                ("security_master", "security"),
                ("market_daily", "market"),
            )
        }
        snapshot_a = load_snapshot(root_a, pack_a["snapshot"].snapshot_id)
        shutil.rmtree(root_a)

        root_b = Path(self.temporary.name) / "offline-b"
        pack_b = build_pack(root_b)
        snapshot_b = load_snapshot(root_b, pack_b["snapshot"].snapshot_id)
        for domain, name in (
            ("trading_calendar", "calendar"),
            ("security_master", "security"),
            ("market_daily", "market"),
        ):
            rebuilt = load_domain_commit(root_b, domain, pack_b[name].commit_id)
            self.assertEqual(rebuilt.ref, captured[domain].ref)
            self.assertEqual(rebuilt.manifest, captured[domain].manifest)
            self.assertEqual(
                rebuilt.manifest["logical_content_digest"],
                captured[domain].manifest["logical_content_digest"],
            )
            self.assertEqual(rebuilt.contract["fields"], captured[domain].contract["fields"])
            self.assertEqual(rebuilt.rows, captured[domain].rows)
            for field in (
                "parent_commit_ref",
                "ordered_raw_batch_refs",
                "ordered_patch_refs",
                "contract_digest",
                "builder_identity",
                "builder_config",
                "dependency_commit_refs",
            ):
                self.assertEqual(rebuilt.manifest[field], captured[domain].manifest[field])
        self.assertEqual(snapshot_b.ref, snapshot_a.ref)
        self.assertEqual(snapshot_b.manifest, snapshot_a.manifest)


if __name__ == "__main__":
    unittest.main()
