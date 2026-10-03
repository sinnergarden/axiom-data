"""Repeatable small synthetic read timing; not a full backtest benchmark."""

from datetime import date, timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from axiom_data import Data, IngestBatch, QuerySpec, UpdateRequest
from axiom_data.reader import SnapshotQueryReader


def run():
    symbols = tuple(f"synthetic-{i:03d}" for i in range(300))
    # These are synthetic dates, not a claim about the Chinese exchange calendar.
    sessions = tuple((date(2020, 1, 1) + timedelta(days=i)).isoformat() for i in range(20))
    rows = [{"security_id": symbol, "session": session, "close": float(index + 1),
             "volume": 1000 + index} for session in sessions for index, symbol in enumerate(symbols)]
    contract = {"contract_id": "benchmark.daily.v1", "logical_key": ["security_id", "session"],
                "fields": {"security_id": {"dtype": "string"}, "session": {"dtype": "date"},
                           "close": {"dtype": "float64", "unit": "CNY/share"},
                           "volume": {"dtype": "int64", "unit": "shares"}}}
    profile = {"id": "synthetic.benchmark.v1", "field_map": {name: name for name in contract["fields"]},
               "source_units": {"close": "CNY/share", "volume": "shares"}}
    ingest = IngestBatch("market_daily", json.dumps(rows).encode(), {}, contract, profile,
                         "2020-02-01T00:00:00Z")
    query = QuerySpec("market_daily", ("close", "volume"), symbols, sessions,
                      "operational_pit_v1", {s: "2020-02-02T00:00:00Z" for s in sessions})
    with TemporaryDirectory(prefix="axiom-benchmark-") as directory:
        data = Data(directory)
        snapshot = data.update(base_snapshot=None, request=UpdateRequest((ingest,), "benchmark", {"synthetic": True})).snapshot_id
        reader = SnapshotQueryReader(data.store, snapshot)
        measurements = []
        for label in ("first_read", "same_query_again"):
            before = reader.partition_reads
            started = perf_counter()
            result = reader.read(query)
            elapsed = perf_counter() - started
            assert len(result.frame) == 6000 and result.frame["close"].iloc[299] == 300
            measurements.append({"case": label, "seconds": round(elapsed, 6),
                                 "additional_partition_reads": reader.partition_reads - before})
        assert reader.partition_reads == 1 and reader.cache_hits == 1
        return {"scope": "synthetic 300 securities × 20 dates × 2 fields",
                "os_page_cache": "uncontrolled; first read is not cold-disk evidence",
                "measurements": measurements, "result_cache_hits": reader.cache_hits,
                "persisted_views": (Path(directory) / "views").exists()}


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
