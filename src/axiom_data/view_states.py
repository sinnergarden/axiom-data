"""Sparse physical rows for financial and event Views.

Each leaf owns ordered half-open state intervals.  Daily cutoff and target
session are query coordinates, so they are restored when a logical row is read.
"""

from __future__ import annotations

import gzip
import heapq
import json
from bisect import bisect_left, bisect_right
from collections.abc import Iterator, Sequence
from copy import deepcopy
from datetime import date, timedelta
from typing import Any

from axiom_data.artifacts import ArtifactError, _json_bytes
from axiom_data.pit import instant


def session_cutoff(cutoff: str, session: str) -> str:
    return min(instant(cutoff), instant(session + "T23:59:59+08:00")).isoformat()


def encode_states(rows, *, sessions, symbol_sessions, fields, kind, cutoff):
    """Encode the already admitted daily projection, one leaf change at a time."""
    if kind not in {"financial", "event"}:
        raise ArtifactError("unknown sparse View kind")
    by_key = {(row["symbol"], row["session"]): row for row in rows}
    if len(by_key) != len(rows):
        raise ArtifactError("duplicate daily View row")
    states = {}
    for symbol, days in symbol_sessions.items():
        if not days or days != sorted(set(days)):
            raise ArtifactError("sparse View sessions are not ordered")
        states[symbol] = {}
        for field in fields:
            spans = []
            previous = object()
            for day in days:
                row = by_key.get((symbol, day))
                if row is None:
                    raise ArtifactError("sparse View is missing a projected day")
                fact = dict(row["facts"][field])
                if fact.pop("knowledge_cutoff") != session_cutoff(cutoff, day):
                    raise ArtifactError("daily View cutoff differs from its session")
                if kind == "event" and fact.pop("target_session") != day:
                    raise ArtifactError("event target session differs from its row")
                state = {"fact": fact}
                if kind == "financial" and field in row["provenance"]:
                    state["provenance"] = row["provenance"][field]
                if state != previous:
                    if spans:
                        spans[-1]["effective_to"] = day
                    spans.append({"effective_from": day, "effective_to": None, "state": state})
                    previous = state
            spans[-1]["effective_to"] = (date.fromisoformat(days[-1]) + timedelta(days=1)).isoformat()
            states[symbol][field] = spans
    if set(by_key) != {(symbol, day) for symbol, days in symbol_sessions.items() for day in days}:
        raise ArtifactError("sparse View has rows outside its calendar")
    return {"sessions": sessions, "symbol_sessions": symbol_sessions, "states": states}


def packed_states(payload) -> bytes:
    return gzip.compress(_json_bytes(payload), mtime=0)


def unpacked_states(content: bytes, uncompressed_bytes: int):
    if type(uncompressed_bytes) is not int or uncompressed_bytes < 0:
        raise ArtifactError("sparse View size is invalid")
    try:
        raw = gzip.decompress(content)
        if len(raw) != uncompressed_bytes:
            raise ArtifactError("sparse View decoded size differs from manifest")
        payload = json.loads(raw)
        if raw != _json_bytes(payload):
            raise ArtifactError("sparse View state JSON is not canonical")
        return payload
    except (OSError, EOFError, ValueError, UnicodeDecodeError) as exc:
        raise ArtifactError("sparse View state file is invalid") from exc


class SparseDailyRows(Sequence):
    """Validated state intervals with ordered range and single-day sweeps."""

    def __init__(self, payload: dict[str, Any], *, symbols, fields, kind, cutoff):
        if kind not in {"financial", "event"}:
            raise ArtifactError("unknown sparse View kind")
        if not isinstance(payload, dict) or set(payload) != {"sessions", "symbol_sessions", "states"}:
            raise ArtifactError("sparse View payload is invalid")
        sessions = payload["sessions"]
        by_symbol = payload["symbol_sessions"]
        states = payload["states"]
        if (not isinstance(sessions, list) or not sessions or sessions != sorted(set(sessions))
                or not isinstance(by_symbol, dict) or set(by_symbol) != set(symbols)
                or not isinstance(states, dict) or set(states) != set(symbols)):
            raise ArtifactError("sparse View session or symbol scope is invalid")
        for day in sessions:
            date.fromisoformat(day)
        self.sessions = tuple(sessions)
        self.symbols = tuple(symbols)
        self.symbol_sessions = {}
        self.fields = tuple(fields)
        self.states = states
        self.starts = {}
        self.kind = kind
        self.cutoff = cutoff
        from axiom_data.consumption import feature_bytes
        if kind == "event":
            from axiom_data.domains.events import NUMERIC_FIELDS
            numeric_fields = set(NUMERIC_FIELDS)
        else:
            numeric_fields = set(fields)
        for symbol in symbols:
            days = by_symbol[symbol]
            if (not isinstance(days, list) or not days or days != sorted(set(days))
                    or not set(days) <= set(sessions) or not isinstance(states[symbol], dict)
                    or set(states[symbol]) != set(fields)):
                raise ArtifactError("sparse View symbol calendar is invalid")
            self.symbol_sessions[symbol] = tuple(days)
            self.starts[symbol] = {}
            for field in fields:
                spans = states[symbol][field]
                if not isinstance(spans, list) or not spans:
                    raise ArtifactError("sparse View field has no state")
                previous_end = None
                previous_state = None
                starts = []
                for span in spans:
                    if (not isinstance(span, dict) or set(span) != {"effective_from", "effective_to", "state"}
                            or not isinstance(span["state"], dict) or "fact" not in span["state"]
                            or not isinstance(span["state"]["fact"], dict)
                            or "value" not in span["state"]["fact"]):
                        raise ArtifactError("sparse View state is invalid")
                    first, last = span["effective_from"], span["effective_to"]
                    date.fromisoformat(first)
                    date.fromisoformat(last)
                    if first >= last or (previous_end is not None and
                            (first != previous_end or span["state"] == previous_state)):
                        raise ArtifactError("sparse View intervals overlap, gap or repeat")
                    starts.append(first)
                    previous_end, previous_state = last, span["state"]
                if starts[0] != days[0] or previous_end != (date.fromisoformat(days[-1]) + timedelta(days=1)).isoformat():
                    raise ArtifactError("sparse View intervals do not close their calendar")
                if field in numeric_fields:
                    feature_bytes(0, [span["state"]["fact"]["value"] for span in spans])
                self.starts[symbol][field] = tuple(starts)
        self._length = sum(map(len, self.symbol_sessions.values()))

    def __len__(self) -> int:
        return self._length

    def __getitem__(self, item):
        if isinstance(item, int) and len(self.symbol_sessions) == 1:
            symbol, days = next(iter(self.symbol_sessions.items()))
            day = days[item]
            return next(self.iter_range(day, day, {symbol}))
        return tuple(self)[item]

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return self.iter_range()

    def iter_range(self, start=None, end=None, symbols=None) -> Iterator[dict[str, Any]]:
        requested = self.symbols if symbols is None else tuple(symbols)
        selected = []
        seen = set()
        for symbol in requested:
            if symbol in self.symbol_sessions and symbol not in seen:
                selected.append((symbol, self.symbol_sessions[symbol]))
                seen.add(symbol)
        position = {symbol: index for index, (symbol, _) in enumerate(selected)}
        # Each field enters the requested interval once, then advances only at
        # its own change points. Merge symbols without buffering all daily rows.
        def sweep(symbol, calendar):
            days = calendar[bisect_left(calendar, start) if start else 0:
                            bisect_right(calendar, end) if end else len(calendar)]
            if not days:
                return
            positions = {field: max(0, bisect_right(self.starts[symbol][field], days[0]) - 1)
                         for field in self.fields}
            for day in days:
                facts, provenance = {}, {}
                cutoff = session_cutoff(self.cutoff, day)
                for field in self.fields:
                    spans = self.states[symbol][field]
                    index = positions[field]
                    while day >= spans[index]["effective_to"]:
                        index += 1
                    positions[field] = index
                    state = spans[index]["state"]
                    fact = deepcopy(state["fact"])
                    fact["knowledge_cutoff"] = cutoff
                    if self.kind == "event":
                        fact["target_session"] = day
                    facts[field] = fact
                    if "provenance" in state:
                        provenance[field] = state["provenance"]
                if self.kind == "financial":
                    yield {"session": day, "symbol": symbol,
                           "values": {field: facts[field]["value"] for field in self.fields},
                           "provenance": provenance, "knowledge_cutoff": cutoff, "facts": facts}
                else:
                    from axiom_data.domains.events import NUMERIC_FIELDS
                    yield {"symbol": symbol, "session": day,
                           "values": {field: facts[field]["value"] for field in NUMERIC_FIELDS},
                           "facts": facts}
        yield from heapq.merge(*(sweep(symbol, calendar) for symbol, calendar in selected),
                               key=lambda row: (row["session"], position[row["symbol"]]))

    def range(self, start=None, end=None, symbols=None) -> tuple[dict[str, Any], ...]:
        return tuple(self.iter_range(start, end, symbols))
