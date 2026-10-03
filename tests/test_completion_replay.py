"""Disk-backed replay checks for D02, D13, D16 and D18."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest

from axiom_data.api import Data
from axiom_data.protocols import IngestBatch, QuerySpec, UpdateRequest
from axiom_engine.runtime import DecisionBatchGate
from axiom_research.view_ref import ViewRef
from axiom_ui import chart_context, project_data_layer


CONTRACT = {
    "contract_id": "completion.replay.daily.v1",
    "logical_key": ["security_id", "session"],
    "fields": {
        "security_id": {"dtype": "string", "nullable": False},
        "session": {"dtype": "date", "nullable": False},
        "close": {"dtype": "float64", "unit": "CNY/share"},
        "volume_shares": {"dtype": "int64", "unit": "shares"},
    },
}
PROFILE = {
    "id": "completion.replay.source.v1",
    "revision_order": "source_sequence_only",
    "field_map": {"security_id": "security_id", "session": "session",
                  "close": "close", "volume_shares": "volume_shares",
                  "revision_id": "revision_id", "revision_sequence": "revision_sequence",
                  "source_available_at": "source_available_at", "evidence_ref": "evidence_ref"},
    "source_units": {"close": "CNY/share", "volume_shares": "shares"},
    "availability": {"timezone": "Asia/Shanghai", "session_release_time": "20:00:00"},
}
BUILD = {"code_ref": "completion-replay-fixed-v1", "contract_ref": CONTRACT["contract_id"]}
SESSIONS = ("2024-01-02", "2024-01-03", "2024-01-04")


def _input(rows: list[dict], observed_at: str) -> IngestBatch:
    return IngestBatch("market_daily", json.dumps(rows, sort_keys=True).encode(),
                       {"fixture": "completion-replay", "sessions": [r["session"] for r in rows]},
                       CONTRACT, PROFILE, observed_at)


def _row(session: str, close: float, volume: int, revision: str, *, evidence: bool = False) -> dict:
    row = {"security_id": "A", "session": session, "close": close,
           "volume_shares": volume, "revision_id": revision, "revision_sequence": 1}
    if evidence:
        row.update(source_available_at="2024-01-03T00:00:00Z",
                   evidence_ref="revision-bound-source-notice-1")
    return row


def _query(sessions: tuple[str, ...] = SESSIONS, *,
           cutoff: str = "2024-01-07T00:00:00Z",
           policy: str = "operational_pit_v1") -> QuerySpec:
    return QuerySpec("market_daily", ("close", "volume_shares"), ("A",), sessions,
                     policy, {session: cutoff for session in sessions})


def _initial(data: Data) -> str:
    # The latest requested session has a value, but the interior date is absent.
    batch = _input([_row("2024-01-02", 10.0, 100, "r-jan2", evidence=True),
                    _row("2024-01-04", 20.0, 200, "r-jan4")],
                   "2024-01-06T00:00:00Z")
    return data.update(base_snapshot=None, request=UpdateRequest(
        (batch,), "replay_initial", BUILD)).snapshot_id


def _semantic_wire(batch) -> dict:
    wire = batch.to_json()
    # Snapshot identity changes in an explicit rebuild; all selected values,
    # units, statuses, usable times and source references should still agree.
    wire["context"].pop("snapshot_id")
    return wire


class CompletionReplayTests(unittest.TestCase):
    def test_same_raw_contract_code_rebuild_and_interior_coverage(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            data = Data(Path(directory) / "root")
            s1 = _initial(data)
            raw_ids = data.store.load_snapshot(s1)["domains"]["market_daily"]["raw_batch_ids"]
            rebuilt = data.rebuild(base_snapshot=s1, raw_batch_ids=raw_ids,
                                   domains=["market_daily"], operation_id="replay_rebuild",
                                   build_context=BUILD, promote=False).snapshot_id
            self.assertEqual(data.resolve(), s1)
            original_domain = data.store.load_snapshot(s1)["domains"]["market_daily"]
            rebuilt_domain = data.store.load_snapshot(rebuilt)["domains"]["market_daily"]
            self.assertEqual(original_domain["contract"], rebuilt_domain["contract"])
            self.assertEqual(original_domain["source_profile"], rebuilt_domain["source_profile"])
            self.assertEqual(original_domain["raw_batch_ids"], rebuilt_domain["raw_batch_ids"])
            self.assertEqual(original_domain["partitions"], rebuilt_domain["partitions"])
            for policy, cutoff, sessions in (
                ("operational_pit_v1", "2024-01-07T00:00:00Z", SESSIONS),
                ("market_pit_safe_v1", "2024-01-04T00:00:00Z", ("2024-01-02",)),
                ("best_effort_vendor_v1", "2024-01-04T00:00:00Z", ("2024-01-02",)),
            ):
                query = _query(sessions, cutoff=cutoff, policy=policy)
                self.assertEqual(_semantic_wire(data.read(snapshot=s1, query=query)),
                                 _semantic_wire(Data(data.store.root).read(snapshot=rebuilt, query=query)))
            scoped = _query()
            self.assertEqual(data.read(snapshot=s1, query=scoped).frame.close.iloc[-1], 20.0)
            report = data.inspect(s1, required_scope=scoped)
            self.assertEqual(report["status"], "limited")
            self.assertEqual({(item["field"], item["session"], item["missing_reason"])
                              for item in report["issues"]}, {
                                  ("close", "2024-01-03", "source_missing"),
                                  ("volume_shares", "2024-01-03", "source_missing")})

    def test_saved_view_ref_replay_and_runtime_session_pin_after_current_moves(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory) / "root"
            data = Data(root)
            s1 = _initial(data)
            query = _query()
            original = data.read(snapshot=s1, query=query)
            saved_ref = ViewRef.from_batch(original).to_dict()
            ref_path = Path(directory) / "saved-view-ref.json"
            ref_path.write_text(json.dumps(saved_ref, sort_keys=True))

            gate = DecisionBatchGate(data)
            self.assertEqual(gate.pin_session("2024-01-04"), s1)
            config = {"cutoff_by_session": {session: "2024-01-07T00:00:00Z"
                                            for session in SESSIONS},
                      "policy": "operational_pit_v1"}
            old_query = QuerySpec("market_daily", ("close", "volume_shares"), ("A",),
                                  SESSIONS, config["policy"], config["cutoff_by_session"])
            s2 = data.update(base_snapshot=s1, request=UpdateRequest((
                _input([_row("2024-01-05", 30.0, 300, "r-jan5")], "2024-01-08T00:00:00Z"),),
                "replay_next", BUILD)).snapshot_id
            self.assertEqual(data.resolve(), s2)
            config["cutoff_by_session"].update({session: "2024-01-09T00:00:00Z"
                                                for session in SESSIONS})
            config["policy"] = "best_effort_vendor_v1"
            pinned = gate.read_decision(decision_session="2024-01-04", query=old_query,
                                        clock="2024-01-09T00:00:00Z")
            self.assertEqual(pinned.context["snapshot_id"], s1)
            self.assertEqual(pinned.context["query"]["pit_policy"], "operational_pit_v1")
            self.assertEqual(old_query.cutoff_by_session[SESSIONS[0]], "2024-01-07T00:00:00Z")
            self.assertEqual(pinned.to_json(), original.to_json())
            self.assertEqual(gate.pin_session("2024-01-05"), s2)
            new_query = _query((*SESSIONS, "2024-01-05"),
                               cutoff=config["cutoff_by_session"][SESSIONS[0]],
                               policy=config["policy"])
            later = gate.read_decision(decision_session="2024-01-05", query=new_query,
                                       clock="2024-01-10T00:00:00Z")
            self.assertEqual(later.context["snapshot_id"], s2)
            self.assertEqual(later.frame.close.iloc[-1], 30.0)
            session_map = {ref.decision_session: asdict(ref) for ref in gate.references}
            map_path = Path(directory) / "saved-session-map.json"
            map_path.write_text(json.dumps(session_map, sort_keys=True))
            recovered_map = json.loads(map_path.read_text())
            self.assertEqual(recovered_map["2024-01-04"]["snapshot_id"], s1)
            self.assertEqual(recovered_map["2024-01-05"]["snapshot_id"], s2)
            self.assertEqual(recovered_map["2024-01-04"]["query_context"]["query"],
                             original.context["query"])
            leaked_reference = gate.references[0]
            leaked_reference.query_context["query"]["fields"][0] = "caller-mutated"
            self.assertEqual(gate.references[0].query_context["query"]["fields"],
                             ["close", "volume_shares"])

            # A new process-facing Data object follows the saved concrete ref,
            # even though its mutable `current` now points at S2.
            reopened = Data(root)
            payload = json.loads(ref_path.read_text())
            restored_ref = ViewRef(**payload)
            restored_query = QuerySpec(domain=restored_ref.domain, **restored_ref.query)
            self.assertEqual(reopened.resolve(), s2)
            replay = reopened.read(snapshot=restored_ref.snapshot_id, query=restored_query)
            self.assertEqual(replay.to_json(), original.to_json())
            chart = chart_context(mode="run_replay", security_id="A",
                                  session_refs={session: restored_ref.to_dict() for session in SESSIONS},
                                  price_basis="unadjusted", adjustment_anchor=None,
                                  time_axis="decision_available",
                                  generated_at="2026-01-01T00:00:00Z")
            layer = project_data_layer(replay, field="close", security_id="A",
                                       role="candles", chart=chart,
                                       generated_at="2026-01-01T00:00:00Z")
            self.assertEqual([point["value"] for point in layer["points"]], [10.0, None, 20.0])
            self.assertEqual(layer["unit"], "CNY/share")
            self.assertEqual(layer["points"][1]["missing_reason"], "source_missing")
            self.assertEqual(layer["points"][0]["provenance"]["raw_batch_id"],
                             original.field_meta["close"]["by_key"][0]["raw_batch_id"])
            self.assertEqual(layer["source_ref"], restored_ref.to_dict())


if __name__ == "__main__":
    unittest.main()
