"""Cross-repo Data read -> Research adapter -> Core -> UI projection proof."""
from copy import deepcopy
from datetime import datetime, timezone
import unittest

import pyarrow as pa

from axiom_data.protocols import QuerySpec
from axiom_data.reader import SnapshotQueryReader
from axiom_engine.runtime import DecisionBatchGate
from axiom_research.data_adapter import adapt_decision_batch
from axiom_ui import chart_context, project_data_layer, unavailable_layer


REF = "sha256:" + "a" * 64
SESSIONS = ("2024-01-02", "2024-01-03", "2024-01-04")


def time(day, hour):
    return datetime(2024, 1, day, hour, tzinfo=timezone.utc)


class MemoryStore:
    def __init__(self):
        self.manifest = {
            "snapshot_id": "s1",
            "domains": {
                "market_daily": {
                    "contract": {"contract_id": "market_v1",
                                 "logical_key": ["security_id", "session"],
                                 "fields": {"close": {"dtype": "float64", "unit": "CNY/share"}}},
                    "source_profile": {"id": "market_fixture"},
                    "partitions": [{"partition": "2024-01"}], "coverage": {}},
                "universe_membership": {
                    "contract": {"contract_id": "membership_v1",
                                 "logical_key": ["membership_id"],
                                 "fields": {"universe_id": {"dtype": "string"},
                                            "effective_from": {"dtype": "date"},
                                            "effective_to": {"dtype": "date"}}},
                    "source_profile": {"id": "membership_fixture"},
                    "partitions": [{"partition": "history"}],
                    "coverage": {"complete_states": [{
                        "universe_id": "U", "complete": True,
                        "effective_from": "2024-01-01", "effective_to": "2024-01-05",
                        "members": ["A"], "first_observed_at": time(1, 9),
                        "raw_batch_id": "members-v1"}]}}
            }}
        self.partitions = {"2024-01": [
            {"security_id": "A", "session": s, "revision_id": "r1",
             "revision_sequence": 1, "first_observed_at": time(int(s[-2:]), 10),
             "raw_batch_id": "raw-" + s, "source_available_at": None,
             "evidence_ref": None, "close": value}
            for s, value in zip(SESSIONS, (10.0, None, 20.0))], "history": []}

    def load_snapshot(self, snapshot_id):
        assert snapshot_id == "s1"
        return deepcopy(self.manifest)

    def verify_partition(self, part):
        pass

    def read_partition(self, part, *, columns=None, symbols=None, sessions=None):
        rows = self.partitions[part["partition"]]
        selected = [r for r in rows if (symbols is None or r.get("security_id") in symbols)
                    and (sessions is None or r.get("session") in sessions)]
        return pa.Table.from_pylist([{c: r.get(c) for c in columns} for r in selected])


class DataFacade:
    def __init__(self, reader):
        self.reader = reader

    def resolve(self, reference):
        return "s1"

    def read(self, *, snapshot, query):
        assert snapshot == "s1"
        return self.reader.read(query)

    read_market = read


class ConsumerIntegration(unittest.TestCase):
    def test_reader_core_ui_keep_old_snapshot_null_unit_and_clocks(self):
        reader = SnapshotQueryReader(MemoryStore(), "s1")
        data = DataFacade(reader)
        cutoffs = {s: time(int(s[-2:]), 12) for s in SESSIONS}
        facts_query = QuerySpec("market_daily", ("close",), ("A",), SESSIONS,
                                "operational_pit_v1", cutoffs)
        refs_query = QuerySpec("universe_membership", ("is_member",), ("A",),
                               SESSIONS, "operational_pit_v1", cutoffs, universe_id="U")
        gate = DecisionBatchGate(data)
        gate.pin_session(SESSIONS[-1])
        facts = gate.read_decision(decision_session=SESSIONS[-1], query=facts_query,
                                   clock=time(4, 12))
        refs = gate.read_decision(decision_session=SESSIONS[-1], query=refs_query,
                                  clock=time(4, 12))
        adapted = adapt_decision_batch(facts, reference=refs, recipe_ref=REF)
        rows = adapted.execute().to_dict()["rows"]
        self.assertEqual([r["values"] for r in rows],
                         [[10.0, None, None], [None, 10.0, None], [20.0, None, None]])
        self.assertEqual(adapted.plan.to_dict()["input_schema"][0]["unit"], "CNY/share")
        view = adapted.view_ref.to_dict()
        chart = chart_context(mode="run_replay", security_id="A",
            session_refs={s: view for s in SESSIONS}, price_basis="unadjusted",
            adjustment_anchor=None, time_axis="decision_available",
            generated_at="2026-01-01T00:00:00Z")
        layer = project_data_layer(facts, field="close", security_id="A",
                                   role="candles", chart=chart,
                                   generated_at="2026-01-01T00:00:00Z")
        self.assertEqual(layer["points"][1]["value"], None)
        self.assertEqual(layer["points"][1]["missing_reason"], "not_provided")
        self.assertEqual(layer["freshness"]["last_observed_time"],
                         "2024-01-04T10:00:00+00:00")
        self.assertEqual(layer["generated_at"], "2026-01-01T00:00:00Z")
        self.assertEqual(unavailable_layer(role="feature", requested_ref="fb1",
                                           requested_stage="base")["points"], None)
        self.assertEqual(len(gate.references), 2)
        self.assertEqual({ref.snapshot_id for ref in gate.references}, {"s1"})


if __name__ == "__main__":
    unittest.main()
