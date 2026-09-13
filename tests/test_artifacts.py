from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import time
import unittest
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from unittest.mock import patch

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
    validate_domain_commit_closure,
    write_raw_batch,
)


FIXED_TIME = "2026-09-05T09:00:00+08:00"


@contextmanager
def synthetic_source_profiles():
    """Admit only this helper's bounded, payload-bound synthetic test sources."""
    from axiom_data import source_completeness as completeness

    domains = (
        "trading_calendar", "security_master", "market_daily", "security_status",
        "price_limits", "corporate_actions", "adjustment_factors", "benchmark_daily",
        "security_capital", "financial_events", "valuation_daily", "universe_membership",
        "industry_membership", "holder_count_events", "top_holders_reports",
        "margin_daily", "moneyflow_daily", "forecast_observations",
    )
    profiles = {f"fixture-{domain}.v1": domain for domain in domains}
    original_policy = completeness.completeness_policy
    original_binding = completeness._validate_raw_binding

    def policy(version, endpoint):
        if profiles.get(version) != endpoint or version not in profiles:
            return original_policy(version, endpoint)
        return dict(status="established", profile_version=version, endpoint=endpoint,
            limit=1_000_000, pagination=None, completeness_rule="strictly_below_limit",
            action="fail_closed_unsplittable", policy_ref="synthetic_test_source.v1#" + endpoint,
            policy_digest=fixture_profile_digest(version),
            historical_completeness="request_complete_best_effort",
            historical_limitations="Synthetic rows establish only the explicit fixture observation.",
            documentation="tests/test_artifacts.py::write_rows",
            evidence="The fixture writer serializes the complete supplied list below its fixed row cap.",
            coverage_semantics="current_snapshot", empty_result_semantics="unknown_observation")

    def binding(raw):
        version = raw.manifest.get("source_profile_version")
        if version not in profiles:
            return original_binding(raw)
        manifest = raw.manifest
        rows = json.loads(raw.payload)
        proof = {"schema_version": "synthetic_test_source.v1", "rows": len(rows),
                 "payload_digest": "sha256:" + hashlib.sha256(raw.payload).hexdigest()}
        if (not isinstance(rows, list) or manifest.get("domain") != profiles[version]
                or manifest.get("source_profile_ref") != version
                or manifest.get("source_profile_digest") != fixture_profile_digest(version)
                or manifest.get("request") != {"fixture": raw.ref.raw_batch_id, "endpoint": profiles[version],
                    "params": {"start_date": "19000101", "end_date": "21001231"}}
                or manifest.get("summary", {}).get("synthetic_source_proof") != proof):
            raise completeness.SourceCompletenessError("invalid synthetic source proof", raw_batch_id=raw.ref.raw_batch_id)

    with patch.object(completeness, 'completeness_policy', policy), patch.object(
            completeness, '_validate_raw_binding', binding):
        yield


def synthetic_source_fixture(test_class):
    original = test_class.setUp
    @wraps(original)
    def setUp(self):
        self.enterContext(synthetic_source_profiles())
        original(self)
    test_class.setUp = setUp
    return test_class


def fixture_profile_digest(profile: str) -> str:
    return f"sha256:{hashlib.sha256(profile.encode()).hexdigest()}"


class EquivalentMarketDomainBuilder(MarketDomainBuilder):
    pass


class ChangedOutputMarketDomainBuilder(MarketDomainBuilder):
    def _build_rows(self, contract, parent_rows, raw_batches):  # type: ignore[no-untyped-def]
        rows = super()._build_rows(contract, parent_rows, raw_batches)
        changed = [dict(row) for row in rows]
        changed[0]["status"] = "source-current-reclassified"
        return changed


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


def security_row(
    symbol: str = "000001.SZ",
    exchange: str = "SZSE",
    *,
    list_session: str | None = "1991-04-03",
    delist_session: str | None = None,
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "exchange": exchange,
        "list_session": list_session,
        "delist_session": delist_session,
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
    *,
    retrieved_at: str = FIXED_TIME,
) -> object:
    profile = f"fixture-{domain}.v1"
    payload = json_payload(rows)
    return write_raw_batch(
        root,
        raw_batch_id,
        domain=domain,
        source_profile=profile,
        source_profile_version=profile,
        source_profile_digest=fixture_profile_digest(profile),
        request={"fixture": raw_batch_id, "endpoint": domain,
                 "params": {"start_date": "19000101", "end_date": "21001231"}},
        retrieved_at=retrieved_at,
        payload=payload,
        collector_code="fixture-writer.v1",
        summary={"rows": len(rows), "synthetic_source_proof": {
            "schema_version": "synthetic_test_source.v1", "rows": len(rows),
            "payload_digest": "sha256:" + hashlib.sha256(payload).hexdigest()}},
    )


def write_legacy_v1_rows(
    root: Path,
    raw_batch_id: str,
    domain: str,
    rows: list[dict[str, object]],
) -> None:
    """Write the exact RawBatch manifest shape published by the PR2 parent."""

    payload = json_payload(rows)
    artifact_dir = root / "raw/batches" / raw_batch_id
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "payload.bin").write_bytes(payload)
    manifest: dict[str, object] = {
        "artifact_type": "raw_batch",
        "schema_version": "raw_batch.v1",
        "raw_batch_id": raw_batch_id,
        "domain": domain,
        "source_profile_ref": f"fixture-{domain}.v1",
        "request": {"fixture": raw_batch_id},
        "retrieved_at": FIXED_TIME,
        "collector_code_ref": "fixture-writer.v1",
        "status": "success",
        "summary": {"rows": len(rows)},
        "payload_files": [
            {
                "path": "payload.bin",
                "content_digest": (
                    f"sha256:{hashlib.sha256(payload).hexdigest()}"
                ),
                "bytes": len(payload),
            }
        ],
    }
    rewrite_manifest(artifact_dir, manifest)


def rewrite_manifest(artifact_dir: Path, manifest: dict[str, object]) -> None:
    content = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    (artifact_dir / "manifest.json").write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    (artifact_dir / "manifest.sha256").write_text(
        f"sha256:{digest}\n", encoding="ascii"
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
    created_at: str | None = FIXED_TIME,
    builder_type: type[MarketDomainBuilder] = MarketDomainBuilder,
) -> object:
    builder = builder_type(
        root,
        domain,
        commit_id=commit_id,
        calendar_commit_id=calendar_commit,
        security_master_commit_id=security_commit,
        builder_config={"format": "canonical-json"},
        created_at=created_at,
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


class SyntheticSourceIsolationTest(unittest.TestCase):
    def test_scope_restores_production_callables_even_after_failure(self):
        from axiom_data import source_completeness as completeness
        original_policy = completeness.completeness_policy
        original_binding = completeness._validate_raw_binding
        self.assertEqual(original_policy.__module__, 'axiom_data.source_completeness')
        self.assertEqual(original_binding.__module__, 'axiom_data.source_completeness')
        with self.assertRaisesRegex(RuntimeError, 'fixture failure'):
            with synthetic_source_profiles():
                self.assertIsNot(completeness.completeness_policy, original_policy)
                self.assertIsNot(completeness._validate_raw_binding, original_binding)
                raise RuntimeError('fixture failure')
        self.assertIs(completeness.completeness_policy, original_policy)
        self.assertIs(completeness._validate_raw_binding, original_binding)


@synthetic_source_fixture
class ArtifactTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "axiom-data"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_synthetic_source_proof_is_payload_bound_and_legacy_projection_unchanged(self) -> None:
        from axiom_data.source_completeness import validate_raw_completeness, validate_raw_completeness_legacy

        write_rows(self.root, "proof", "security_master", [security_row()])
        raw = load_raw_batch(self.root, "proof")
        self.assertTrue(validate_raw_completeness(raw)["complete"])
        self.assertFalse(validate_raw_completeness_legacy(raw)["complete"])
        manifest = raw.manifest
        forged = write_raw_batch(self.root, "forged-proof", domain="security_master",
            source_profile=manifest["source_profile_ref"], source_profile_version=manifest["source_profile_version"],
            source_profile_digest=manifest["source_profile_digest"],
            request=dict(manifest["request"], fixture="forged-proof"), retrieved_at=FIXED_TIME,
            payload=json_payload([security_row("000002.SZ")]), collector_code="fixture-writer.v1",
            summary=manifest["summary"])
        with self.assertRaisesRegex(ArtifactError, "invalid synthetic source proof"):
            validate_raw_completeness(load_raw_batch(self.root, forged.raw_batch_id))

    def test_raw_batch_is_append_only_and_offline_readable(self) -> None:
        original_payload = b'{"supplier_rows":[{"symbol":"000001.SZ"}]}'
        arguments = {
            "domain": "market_daily",
            "source_profile": "fixture-market.v1",
            "source_profile_version": "fixture-market.v1",
            "source_profile_digest": fixture_profile_digest("fixture-market.v1"),
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
        self.assertEqual(loaded.manifest["schema_version"], "raw_batch.v2")
        self.assertEqual(loaded.manifest["payload_files"][0]["path"], "payload.bin")
        self.assertNotIn(str(self.root), json.dumps(loaded.manifest))
        with self.assertRaises(ArtifactConflictError):
            write_raw_batch(
                self.root,
                "raw-001",
                **dict(arguments, payload=b"different supplier response"),
            )
        self.assertEqual(load_raw_batch(self.root, "raw-001").payload, original_payload)

    def test_pr2_raw_batch_v1_remains_loadable_in_catalog_but_requires_current_proof(self) -> None:
        write_legacy_v1_rows(
            self.root,
            "raw-pr2-security",
            "security_master",
            [security_row()],
        )

        raw = load_raw_batch(self.root, "raw-pr2-security")
        self.assertEqual(raw.manifest["schema_version"], "raw_batch.v1")
        self.assertNotIn("source_profile_version", raw.manifest)
        self.assertNotIn("source_profile_digest", raw.manifest)
        with self.assertRaisesRegex(ArtifactError, "complete source evidence"):
            build_commit(self.root, "security_master", ["raw-pr2-security"])
        self.assertEqual(rebuild_catalog(self.root), 1)
        self.assertEqual(
            lookup_catalog(self.root, "raw_batch", "raw-pr2-security").artifact_id,
            "raw-pr2-security",
        )
        with self.assertRaises(ArtifactConflictError):
            write_raw_batch(
                self.root,
                "raw-pr2-security",
                domain="security_master",
                source_profile="fixture-security_master.v1",
                source_profile_version="fixture-security_master.v1",
                source_profile_digest=fixture_profile_digest(
                    "fixture-security_master.v1"
                ),
                request={"fixture": "raw-pr2-security"},
                retrieved_at=FIXED_TIME,
                payload=json_payload([security_row()]),
                collector_code="fixture-writer.v1",
                summary={"rows": 1},
            )
        self.assertEqual(
            load_raw_batch(self.root, "raw-pr2-security").manifest["schema_version"],
            "raw_batch.v1",
        )

    def test_failed_canonical_build_keeps_raw_and_publishes_no_commit(self) -> None:
        invalid = security_row()
        invalid["exchange"] = "SSE"
        write_rows(self.root, "raw-invalid", "security_master", [invalid])

        with self.assertRaises(ArtifactError):
            build_commit(
                self.root,
                "security_master",
                ["raw-invalid"],
            )

        self.assertEqual(load_raw_batch(self.root, "raw-invalid").payload, json_payload([invalid]))
        commits = self.root / "canonical/security_master/commits"
        self.assertFalse(commits.exists() and any(commits.iterdir()))
        self.assertEqual(list((self.root / "staging").iterdir()), [])

    def test_domain_genesis_manifest_and_contract_provenance_are_complete(self) -> None:
        write_rows(self.root, "raw-security", "security_master", [security_row()])
        ref = build_commit(
            self.root,
            "security_master",
            ["raw-security"],
        )
        repeated_ref = build_commit(
            self.root,
            "security_master",
            ["raw-security"],
            commit_id=ref.commit_id,
        )
        commit = load_domain_commit(self.root, "security_master", ref.commit_id)
        manifest = commit.manifest
        contract_bytes = (
            self.root
            / f"canonical/security_master/commits/{ref.commit_id}/contract.json"
        ).read_bytes()

        self.assertIsNone(manifest["parent_commit_ref"])
        self.assertEqual(repeated_ref, ref)
        self.assertEqual(
            ref.commit_id,
            f"security_master-{manifest['identity_digest'].removeprefix('sha256:')}",
        )
        self.assertEqual(
            [item["raw_batch_id"] for item in manifest["ordered_raw_batch_refs"]],
            ["raw-security"],
        )
        self.assertEqual(manifest["ordered_patch_refs"], [])
        self.assertEqual(
            manifest["contract_digest"],
            f"sha256:{hashlib.sha256(contract_bytes).hexdigest()}",
        )
        self.assertEqual(
            manifest["builder_implementation_ref"]["implementation"],
            "axiom_data.artifacts.MarketDomainBuilder",
        )
        self.assertEqual(manifest['builder_config']['format'], 'canonical-json')
        self.assertEqual(manifest['builder_config']['coverage_state_policy'], 'source_observations.v2')
        self.assertIn('source_completeness_binding', manifest['builder_config'])
        self.assertIn('writable_contracts_digest', manifest['builder_config'])
        expected_config = json.dumps(
            manifest['builder_config'],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        self.assertEqual(
            manifest["builder_config_digest"],
            f"sha256:{hashlib.sha256(expected_config).hexdigest()}",
        )
        self.assertEqual(manifest["output_files"][0]["path"], "rows.json")
        self.assertEqual(manifest["validation_summary"]["status"], "PASS")

    def test_domain_default_time_retry_is_idempotent(self) -> None:
        write_rows(self.root, "raw-security", "security_master", [security_row()])
        first_builder = MarketDomainBuilder(
            self.root,
            "security_master",
            builder_config={"format": "canonical-json"},
        )
        first = BuildApplication("security_master", first_builder).build(
            None, ["raw-security"], [], "security_master.v1"
        )
        first_commit = load_domain_commit(
            self.root, "security_master", first.commit_id
        )

        time.sleep(0.002)
        retry_builder = MarketDomainBuilder(
            self.root,
            "security_master",
            builder_config={"format": "canonical-json"},
        )
        self.assertNotEqual(first_builder.created_at, retry_builder.created_at)
        retried = BuildApplication("security_master", retry_builder).build(
            None, ["raw-security"], [], "security_master.v1"
        )

        self.assertEqual(retried, first)
        self.assertEqual(
            load_domain_commit(self.root, "security_master", retried.commit_id)
            .manifest["created_at"],
            first_commit.manifest["created_at"],
        )

    def test_output_and_builder_implementation_are_identity_inputs(self) -> None:
        write_rows(self.root, "raw-security", "security_master", [security_row()])
        with self.assertRaises(TypeError):
            MarketDomainBuilder(  # type: ignore[call-arg]
                self.root,
                "security_master",
                builder_identity="caller-controlled",
            )
        baseline = build_commit(self.root, "security_master", ["raw-security"])
        changed_output = build_commit(
            self.root,
            "security_master",
            ["raw-security"],
            builder_type=ChangedOutputMarketDomainBuilder,
        )
        changed_implementation = build_commit(
            self.root,
            "security_master",
            ["raw-security"],
            builder_type=EquivalentMarketDomainBuilder,
        )

        baseline_commit = load_domain_commit(
            self.root, "security_master", baseline.commit_id
        )
        output_commit = load_domain_commit(
            self.root, "security_master", changed_output.commit_id
        )
        implementation_commit = load_domain_commit(
            self.root, "security_master", changed_implementation.commit_id
        )
        self.assertNotEqual(baseline.commit_id, changed_output.commit_id)
        self.assertNotEqual(
            baseline_commit.manifest["logical_content_digest"],
            output_commit.manifest["logical_content_digest"],
        )
        self.assertNotEqual(baseline.commit_id, changed_implementation.commit_id)
        self.assertEqual(baseline_commit.rows, implementation_commit.rows)
        self.assertNotEqual(
            baseline_commit.manifest["builder_implementation_ref"],
            implementation_commit.manifest["builder_implementation_ref"],
        )

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
            )

        commits = self.root / "canonical/security_master/commits"
        self.assertFalse(commits.exists() and any(commits.iterdir()))

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
        )
        original_digest = load_domain_commit(
            self.root, "security_master", first.commit_id
        ).manifest_digest

        with self.assertRaises(ArtifactError):
            build_commit(
                self.root,
                "security_master",
                ["raw-two"],
                commit_id=first.commit_id,
            )

        original = load_domain_commit(self.root, "security_master", first.commit_id)
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
                source_profile_version="fixture-security.v1",
                source_profile_digest=fixture_profile_digest("fixture-security.v1"),
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
                source_profile_version="fixture-security.v1",
                source_profile_digest=fixture_profile_digest("fixture-security.v1"),
                request={"fixture": "raw-through-root-link"},
                retrieved_at=FIXED_TIME,
                payload=json_payload([security_row()]),
                collector_code="fixture-writer.v1",
            )
        self.assertEqual(list(outside.iterdir()), [])

    def test_read_paths_reject_symlink_ancestors_and_manifest_escape(self) -> None:
        real_parent = Path(self.temporary.name) / "real-parent"
        real_root = real_parent / "root"
        pack = build_pack(real_root)
        linked_parent = Path(self.temporary.name) / "linked-parent"
        linked_parent.symlink_to(real_parent, target_is_directory=True)
        linked_root = linked_parent / "root"

        with self.assertRaises(ArtifactError):
            load_raw_batch(linked_root, "raw-market")
        with self.assertRaises(ArtifactError):
            load_snapshot(linked_root, pack["snapshot"].snapshot_id)
        with self.assertRaises(ArtifactError):
            rebuild_catalog(linked_root)

        write_rows(self.root, "raw-real-path", "security_master", [security_row()])
        real_artifact = self.root / "raw/batches/raw-real-path"
        (real_artifact / "nested").mkdir()
        (real_artifact / "payload.bin").rename(real_artifact / "nested/payload.bin")
        real_manifest = json.loads((real_artifact / "manifest.json").read_bytes())
        real_manifest["payload_files"][0]["path"] = "nested/payload.bin"
        rewrite_manifest(real_artifact, real_manifest)
        self.assertEqual(
            load_raw_batch(self.root, "raw-real-path").payload,
            json_payload([security_row()]),
        )

        write_rows(self.root, "raw-linked-path", "security_master", [security_row()])
        linked_artifact = self.root / "raw/batches/raw-linked-path"
        outside = Path(self.temporary.name) / "payload-outside"
        outside.mkdir()
        shutil.copy2(linked_artifact / "payload.bin", outside / "payload.bin")
        (linked_artifact / "nested").symlink_to(outside, target_is_directory=True)
        linked_manifest = json.loads(
            (linked_artifact / "manifest.json").read_bytes()
        )
        linked_manifest["payload_files"][0]["path"] = "nested/payload.bin"
        rewrite_manifest(linked_artifact, linked_manifest)
        with self.assertRaises(ArtifactError):
            load_raw_batch(self.root, "raw-linked-path")

        write_rows(self.root, "raw-escape", "security_master", [security_row()])
        escape_artifact = self.root / "raw/batches/raw-escape"
        escape_manifest = json.loads(
            (escape_artifact / "manifest.json").read_bytes()
        )
        escape_manifest["payload_files"][0]["path"] = "../../../../payload.bin"
        rewrite_manifest(escape_artifact, escape_manifest)
        with self.assertRaises(ArtifactError):
            load_raw_batch(self.root, "raw-escape")

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
            (
                calendar_rows(),
                [security_row(list_session="2026-01-03")],
                market_row("2026-01-02"),
                "before-list",
            ),
            (
                calendar_rows(),
                [security_row(delist_session="2026-01-02")],
                market_row("2026-01-02"),
                "at-delist",
            ),
            (
                calendar_rows(),
                [security_row(delist_session="2026-01-02")],
                market_row("2026-01-05"),
                "after-delist",
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
                    self.assertEqual(
                        list((root / "canonical/market_daily/commits").iterdir()), []
                    )

    def test_snapshot_is_immutable_fixed_composition(self) -> None:
        pack = build_pack(self.root)
        first = load_snapshot(self.root, pack["snapshot"].snapshot_id)
        first_manifest = json.dumps(first.manifest, sort_keys=True)
        self.assertEqual(
            first.ref.snapshot_id,
            f"snapshot-{first.manifest['identity_digest'].removeprefix('sha256:')}",
        )
        repeated = create_snapshot(
            self.root,
            {
                "trading_calendar": pack["calendar"].commit_id,
                "security_master": pack["security"].commit_id,
                "market_daily": pack["market"].commit_id,
            },
            snapshot_id=first.ref.snapshot_id,
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
        with self.assertRaises(ArtifactError):
            create_snapshot(
                self.root,
                {
                    "trading_calendar": pack["calendar"].commit_id,
                    "security_master": pack["security"].commit_id,
                    "market_daily": market_d2.commit_id,
                },
                snapshot_id=first.ref.snapshot_id,
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

    def test_snapshot_default_time_retry_is_idempotent(self) -> None:
        pack = build_pack(self.root)
        shutil.rmtree(self.root / "snapshots")
        refs = {
            "trading_calendar": pack["calendar"].commit_id,
            "security_master": pack["security"].commit_id,
            "market_daily": pack["market"].commit_id,
        }
        first = create_snapshot(self.root, refs)
        first_snapshot = load_snapshot(self.root, first.snapshot_id)

        time.sleep(0.002)
        retried = create_snapshot(self.root, refs)

        self.assertEqual(retried, first)
        self.assertEqual(
            load_snapshot(self.root, retried.snapshot_id).manifest["created_at"],
            first_snapshot.manifest["created_at"],
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

    def test_full_closure_and_catalog_reject_deleted_raw(self) -> None:
        pack = build_pack(self.root)
        shutil.rmtree(self.root / "raw/batches/raw-market")

        self.assertEqual(
            load_domain_commit(
                self.root, "market_daily", pack["market"].commit_id
            ).ref,
            pack["market"],
        )
        with self.assertRaises(ArtifactNotFoundError):
            validate_domain_commit_closure(
                self.root, "market_daily", pack["market"].commit_id
            )
        with self.assertRaises(ArtifactNotFoundError):
            load_snapshot(self.root, pack["snapshot"].snapshot_id)
        with self.assertRaises(ArtifactNotFoundError):
            rebuild_catalog(self.root)
        self.assertFalse((self.root / "catalog.sqlite").exists())

    def test_full_closure_rejects_replaced_parent(self) -> None:
        write_rows(self.root, "raw-parent", "security_master", [security_row()])
        parent = build_commit(self.root, "security_master", ["raw-parent"])
        write_rows(
            self.root,
            "raw-child",
            "security_master",
            [security_row("600000.SH", "SSE")],
        )
        child = build_commit(
            self.root,
            "security_master",
            ["raw-child"],
            parent=parent.commit_id,
        )
        write_rows(
            self.root,
            "raw-replacement",
            "security_master",
            [security_row("600001.SH", "SSE")],
        )
        replacement = build_commit(
            self.root, "security_master", ["raw-replacement"]
        )
        commits = self.root / "canonical/security_master/commits"
        shutil.rmtree(commits / parent.commit_id)
        shutil.copytree(commits / replacement.commit_id, commits / parent.commit_id)

        self.assertEqual(
            load_domain_commit(self.root, "security_master", child.commit_id).ref,
            child,
        )
        with self.assertRaises(ArtifactError):
            validate_domain_commit_closure(
                self.root, "security_master", child.commit_id
            )

    def test_clean_offline_rebuild_has_identical_logical_artifacts(self) -> None:
        root_a = Path(self.temporary.name) / "offline-a"
        pack_a = build_pack(root_a)
        base_market_id = pack_a["market"].commit_id
        write_rows(
            root_a,
            "raw-market-incremental",
            "market_daily",
            [market_row("2026-01-05")],
        )
        incremental_market = build_commit(
            root_a,
            "market_daily",
            ["raw-market-incremental"],
            parent=pack_a["market"].commit_id,
            calendar_commit=pack_a["calendar"].commit_id,
            security_commit=pack_a["security"].commit_id,
        )
        recovery_snapshot = create_snapshot(
            root_a,
            {
                "trading_calendar": pack_a["calendar"].commit_id,
                "security_master": pack_a["security"].commit_id,
                "market_daily": incremental_market.commit_id,
            },
            created_at=FIXED_TIME,
        )
        pack_a["market"] = incremental_market
        pack_a["snapshot"] = recovery_snapshot
        self.assertEqual(rebuild_catalog(root_a), 10)
        captured = {
            domain: validate_domain_commit_closure(
                root_a, domain, pack_a[name].commit_id
            )
            for domain, name in (
                ("trading_calendar", "calendar"),
                ("security_master", "security"),
                ("market_daily", "market"),
            )
        }
        snapshot_a = load_snapshot(root_a, pack_a["snapshot"].snapshot_id)
        self.assertEqual(
            captured["market_daily"].manifest["parent_commit_ref"][
                "domain_commit_id"
            ],
            base_market_id,
        )
        raw_payloads = {
            raw_id: load_raw_batch(root_a, raw_id).payload
            for raw_id in (
                "raw-calendar",
                "raw-security",
                "raw-market",
                "raw-market-incremental",
            )
        }

        root_b = Path(self.temporary.name) / "offline-b"
        shutil.copytree(root_a, root_b)
        (root_b / "catalog.sqlite").unlink()
        shutil.rmtree(root_a)

        self.assertEqual(rebuild_catalog(root_b), 10)
        snapshot_b = load_snapshot(root_b, snapshot_a.ref.snapshot_id)
        for raw_id, payload in raw_payloads.items():
            self.assertEqual(load_raw_batch(root_b, raw_id).payload, payload)
        for domain, name in (
            ("trading_calendar", "calendar"),
            ("security_master", "security"),
            ("market_daily", "market"),
        ):
            rebuilt = validate_domain_commit_closure(
                root_b, domain, pack_a[name].commit_id
            )
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
                "builder_implementation_ref",
                "builder_config",
                "dependency_commit_refs",
            ):
                self.assertEqual(rebuilt.manifest[field], captured[domain].manifest[field])
        self.assertEqual(snapshot_b.ref, snapshot_a.ref)
        self.assertEqual(snapshot_b.manifest, snapshot_a.manifest)
        for manifest_path in root_b.rglob("manifest.json"):
            self.assertNotIn(str(root_a), manifest_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
