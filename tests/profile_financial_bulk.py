"""Bounded, mocked 1,800-security x four-quarter financial processing probe.

Run with ``PYTHONPATH=src .venv/bin/python3.12 tests/profile_financial_bulk.py``. This is a
local processing and storage measurement, not a supplier throughput benchmark.
"""

from __future__ import annotations

from datetime import datetime, timezone
import gc
import json
from pathlib import Path
import platform
import tempfile
from time import perf_counter
import tracemalloc

from axiom_data.event_reader import read_events
from axiom_data.event_sources import collect_event_response
from axiom_data.protocols import EventQuery
from axiom_data.storage import LocalStore
from axiom_data.updates import apply_saved_raw, rebuild_from_raw


COUNT = 1800
PERIODS = ("20200331", "20200630", "20200930", "20201231")
OUTPUT = Path(__file__).resolve().parents[1] / "docs" / "financial-bulk-synthetic.json"


def objects(store: LocalStore, pattern: str) -> dict[str, int]:
    paths = list(store.root.glob(pattern))
    return {"count": len(paths), "bytes": sum(path.stat().st_size for path in paths)}


def main() -> None:
    codes = tuple(f"{index:06d}.SZ" for index in range(1, COUNT + 1))
    identities = {code: f"sec-{code}" for code in codes}
    responses = {
        period: [{"ts_code": code, "ann_date": "20210420", "f_ann_date": "20210420",
                  "end_date": period, "report_type": "1",
                  "total_revenue": float((quarter + 1) * 1000 + index),
                  "n_income_attr_p": float((quarter + 1) * 100 + index)}
                 for index, code in enumerate(codes)]
        for quarter, period in enumerate(PERIODS)
    }

    class MockClient:
        def query(self, _endpoint, **params):
            return responses[params["period"]]

    phases = {}
    tracemalloc.start()

    def measure(name, action):
        gc.collect()
        before, _ = tracemalloc.get_traced_memory()
        tracemalloc.reset_peak()
        started = perf_counter()
        result = action()
        _current, peak = tracemalloc.get_traced_memory()
        phases[name] = {"elapsed_seconds": round(perf_counter() - started, 3),
                        "tracemalloc_start_bytes": before,
                        "tracemalloc_peak_bytes": peak,
                        "tracemalloc_peak_above_start_bytes": peak - before}
        return result

    with tempfile.TemporaryDirectory(prefix="axiom-financial-profile-") as directory:
        store = LocalStore(directory)

        def fetch(label, day):
            return [collect_event_response(
                store, client=MockClient(), endpoint="income_vip",
                params={"period": period, "report_type": "1"},
                identity_map=identities,
                observed_at=datetime(2026, 9, day, tzinfo=timezone.utc),
                operation_id=f"{label}-{period}",
                next_open_session_by_date={"2021-04-20": "2021-04-21"})["batch_id"]
                for period in PERIODS]

        first_ids = measure("mock_fetch_and_raw", lambda: fetch("first", 28))
        first = measure("initial_publish", lambda: apply_saved_raw(
            store, base_snapshot=None, raw_batch_ids=first_ids,
            operation_id="profile-publish-first", build_context={"profile": "initial"},
            promote=False))
        initial_objects = {
            "raw_payload": objects(store, "raw/objects/*/payload.bin"),
            "canonical": objects(store, "canonical/**/*.parquet"),
            "snapshots": objects(store, "snapshots/*.json"),
        }
        repeat_ids = measure("mock_repeat_fetch_and_raw", lambda: fetch("repeat", 29))
        repeat = measure("same_fact_publish", lambda: apply_saved_raw(
            store, base_snapshot=first.snapshot_id, raw_batch_ids=repeat_ids,
            operation_id="profile-publish-repeat", build_context={"profile": "repeat"},
            promote=False))
        assert not repeat.changed and repeat.snapshot_id == first.snapshot_id
        assert first_ids != repeat_ids
        after_repeat = {
            "raw_payload": objects(store, "raw/objects/*/payload.bin"),
            "canonical": objects(store, "canonical/**/*.parquet"),
            "snapshots": objects(store, "snapshots/*.json"),
        }
        assert after_repeat == initial_objects

        selected = measure("pit_read_one_security", lambda: read_events(
            store, first.snapshot_id, EventQuery(
                "financial_events", ("total_revenue",), (identities[codes[0]],),
                "2020-01-01", "2020-12-31", "2026-09-30T00:00:00Z",
                "operational_pit_v1", "report_period")))
        assert len(selected.frame) == 4
        rebuilt = measure("offline_rebuild_four_raw", lambda: rebuild_from_raw(
            store, base_snapshot=first.snapshot_id, raw_batch_ids=first_ids,
            domains=("financial_events",), operation_id="profile-rebuild",
            build_context={"profile": "rebuild"}, promote=False))
        assert store.load_snapshot(rebuilt.snapshot_id)["domains"]["financial_events"]["partitions"] == (
            store.load_snapshot(first.snapshot_id)["domains"]["financial_events"]["partitions"])
        final_objects = {
            "raw_payload": objects(store, "raw/objects/*/payload.bin"),
            "canonical": objects(store, "canonical/**/*.parquet"),
            "snapshots": objects(store, "snapshots/*.json"),
            "receipt_log": objects(store, "raw/fetches.jsonl"),
        }

    OUTPUT.write_text(json.dumps({
        "kind": "bounded synthetic processing and storage probe; no supplier network",
        "securities": COUNT, "quarters": len(PERIODS), "canonical_rows": COUNT * len(PERIODS),
        "mock_responses": 8, "unique_payloads": 4,
        "python": platform.python_version(), "platform": platform.platform(),
        "timing_limitations": [
            "MockClient returns prebuilt in-memory responses; supplier latency, rate limits, retries and transport are excluded.",
            "tracemalloc is active for every phase and substantially slows Python allocation paths; elapsed times are diagnostic, not production throughput.",
            "tracemalloc tracks Python allocations, not all Arrow/native allocations or process RSS.",
            "The 1,800-security universe and four quarters are bounded synthetic data, not twelve-year market evidence."
        ],
        "phases": phases, "objects_after_initial": initial_objects,
        "objects_after_same_fact_refresh": after_repeat,
        "objects_after_rebuild": final_objects,
        "same_fact_snapshot_reused": repeat.snapshot_id == first.snapshot_id,
    }, ensure_ascii=False, indent=2) + "\n")
    print(OUTPUT)


if __name__ == "__main__":
    main()
