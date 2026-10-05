"""Explicit, immutable Qlib daily projections of the existing PIT reader.

Qlib's float32 files are a consumer format, not a new fact store. Numeric fields
keep their declared units; no price normalization, filling or feature calculation
is implicit. Rich per-key provenance remains in the referenced source Snapshot.
"""
from __future__ import annotations

from dataclasses import fields, replace
from hashlib import sha256
from itertools import groupby
import json
import math
from pathlib import Path
import re
import shutil
import tempfile

import numpy as np
import pandas as pd

from .protocols import DataError, QuerySpec, _json_safe
from .reader import READER_VERSION, _instant, _policy_for, _row_availability, _revision_order
from .portable import _publish_new_directory

EXPORTER_VERSION = "qlib_daily_export_v2"
SCHEMA = "axiom_qlib_view_v1"
MANIFEST = "axiom-qlib.json"
_FIELD = re.compile(r"^[a-z][a-z0-9_]*$")
_INSTRUMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_NUMERIC = {"float", "double", "float32", "float64", "int", "integer",
            "int32", "int64", "bool", "boolean"}


def _encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode()


def _digest(value):
    return sha256(_encoded(value)).hexdigest()


def _query_dict(query):
    return _json_safe({f.name: getattr(query, f.name) for f in fields(query)})


def _chunks(query):
    for _, days in groupby(query.sessions, key=lambda s: s[:7]):
        sessions = tuple(days)
        yield replace(query, sessions=sessions,
                      cutoff_by_session={s: query.cutoff_by_session[s] for s in sessions},
                      policy_by_session=({s: query.policy_by_session[s] for s in sessions}
                                         if query.policy_by_session is not None else None))


def _matrix(batch, symbols, sessions, field):
    index = pd.MultiIndex.from_product([symbols, sessions], names=["security_id", "session"])
    selected = batch.frame.set_index(["security_id", "session"])
    if selected.index.has_duplicates:
        raise DataError("Qlib export requires unique security/session keys")
    return selected[field].reindex(index).to_numpy(dtype=np.float64, na_value=np.nan).reshape(
        len(symbols), len(sessions))


def _calendar(data, domain, query):
    if not domain:
        raise DataError("Qlib export requires a fixed trading_calendar")
    sessions = query.sessions
    profile = domain["source_profile"]
    keys = domain["contract"]["logical_key"]
    grouped = {}
    for part in domain["partitions"]:
        data.store.verify_partition(part)
        for row in data.store.read_partition(part).to_pylist():
            if sessions[0] <= str(row["session"]) <= sessions[-1]:
                grouped.setdefault(tuple(str(row[k]) for k in keys), []).append(row)
    days = set()
    known = set()
    from bisect import bisect_right
    for key, rows in grouped.items():
        day = str(rows[0]["session"])
        anchor = day if day in query.cutoff_by_session else sessions[bisect_right(sessions, day)-1]
        policy = _policy_for(query, anchor)
        # Omitted dates never borrow a later strict information set. Vendor
        # history assumes release on the calendar date, so use the next explicit
        # session cutoff for that assumption (still bounded by the query).
        if day not in query.cutoff_by_session and policy == "best_effort_vendor_v1":
            anchor = sessions[bisect_right(sessions, day)]
        cutoff = _instant(query.cutoff_by_session[anchor], "calendar cutoff")
        visible = [r for r in rows if _row_availability(r, policy, profile, day)[0] <= cutoff]
        if not visible:
            raise DataError(f"Qlib calendar is unknown at its requested cutoff: {day}")
        chosen = _revision_order(visible, repr(key), profile=profile)
        known.add(day)
        if chosen.get("is_open"):
            days.add(day)
    if not set(sessions) <= known:
        raise DataError("Qlib requested session is absent from its fixed calendar")
    if tuple(sorted(days)) != sessions:
        raise DataError("Qlib sessions must cover every open session in the requested range; include lookback")


def _membership_lines(data, snapshot, query, mapping):
    """Convert only known PIT member flags to inclusive, gap-preserving intervals."""
    flags = np.empty((len(query.symbols), len(query.sessions)), dtype=bool)
    positions = {s:i for i,s in enumerate(query.sessions)}
    for chunk in _chunks(query):
        values = _matrix(data.members(snapshot=snapshot, query=chunk), query.symbols, chunk.sessions, "is_member")
        if np.isnan(values).any():
            raise DataError("unknown PIT membership cannot be represented as a Qlib instrument interval")
        start = positions[chunk.sessions[0]]; flags[:, start:start+len(chunk.sessions)] = values.astype(bool)
    lines = []
    for i, symbol in enumerate(query.symbols):
        padded = np.r_[False, flags[i], False].astype(np.int8)
        starts = np.flatnonzero(np.diff(padded)==1); ends = np.flatnonzero(np.diff(padded)==-1)-1
        lines.extend(f"{mapping[symbol]}\t{query.sessions[a]}\t{query.sessions[b]}\n" for a,b in zip(starts, ends))
    return "".join(lines)


def export_qlib(data, *, snapshot: str, queries, destination,
                instrument_map=None, field_aliases=None, universe_query=None,
                universe_name="universe", rtol=1e-6, atol=1e-8):
    """Export aligned numeric daily QuerySpecs, batched by month, without network.

    All queries share explicit stable symbols and a complete chronological open
    calendar. field_aliases maps 'domain.field' to a lowercase Qlib field name.
    Optional membership produces inclusive Qlib intervals from PIT-selected
    True values; unknown membership raises rather than being converted to False.
    'all' means requested candidate scope, not daily membership or tradability.
    Prices remain unadjusted; factors and volume retain their native source units.

    Destination is immutable: identical specs reuse a verified existing view;
    a different spec or failed build never replaces it or changes data.current.
    Float32 rounding uses explicit rtol/atol, and nulls stay NaN. Financial/event
    objects are queried separately through Data.events, not implicitly daily-filled.
    Calendar uses each query's PIT/revision rules and explicit session cutoffs.
    An omitted date uses the preceding strict cutoff; best-effort uses the next
    supplied cutoff with its declared dated-release assumption. Unknown calendar
    states fail. A later observation never enters an earlier strict projection.
    """
    queries = tuple(queries)
    if snapshot in {"current", "latest"} or not queries or not all(isinstance(q, QuerySpec) for q in queries):
        raise DataError("Qlib export needs a concrete Snapshot and QuerySpecs")
    if not all(math.isfinite(x) and x >= 0 for x in (rtol, atol)):
        raise DataError("Qlib numeric tolerances must be finite and nonnegative")
    first = queries[0]
    symbols, sessions = first.symbols, first.sessions
    if not symbols or not sessions or len(set(symbols)) != len(symbols) or tuple(sorted(set(sessions))) != sessions:
        raise DataError("Qlib export needs unique symbols and chronological sessions")
    for q in queries + ((universe_query,) if universe_query is not None else ()):
        if not isinstance(q, QuerySpec) or q.symbols != symbols or q.sessions != sessions:
            raise DataError("Qlib queries must use the same ordered symbols and sessions")
        if set(q.cutoff_by_session) != set(sessions):
            raise DataError("Qlib query cutoffs must cover exactly its sessions")
    mapping = dict(instrument_map or {s: s.upper() for s in symbols})
    if (set(mapping) != set(symbols) or not all(isinstance(v, str) and _INSTRUMENT.fullmatch(v) for v in mapping.values())
            or len({v.upper() for v in mapping.values()}) != len(symbols)):
        raise DataError("instrument_map must bind each stable identity to a unique safe Qlib code")
    mapping = {s: mapping[s].upper() for s in symbols}
    aliases = dict(field_aliases or {})
    # Each export rechecks the source manifest, including a warm Data instance.
    # Subsequent data.read/members calls reuse this freshly verified Reader.
    manifest = data._reader(snapshot, refresh=True).snapshot
    exports = {}
    for q in queries:
        domain = manifest["domains"].get(q.domain) or {}
        contract = domain.get("contract") or {}
        if set(contract.get("logical_key") or ()) - {"security_id", "session"}:
            raise DataError("Qlib native export requires daily security/session fields; use Data.events for events")
        for name in q.fields:
            meta = (contract.get("fields") or {}).get(name) or {}
            alias = aliases.get(f"{q.domain}.{name}", name)
            if str(meta.get("dtype")).lower() not in _NUMERIC or not _FIELD.fullmatch(alias):
                raise DataError(f"Qlib field must be declared numeric with a safe alias: {q.domain}.{name}")
            if alias in exports:
                raise DataError(f"duplicate Qlib output field: {alias}")
            exports[alias] = {"domain": q.domain, "field": name, "source_dtype": meta["dtype"],
                              "unit": meta.get("unit"), "price_basis": q.price_basis}
    if set(aliases) - {f'{x["domain"]}.{x["field"]}' for x in exports.values()}:
        raise DataError("field_aliases contains a field outside the exported queries")
    if universe_query is not None and (universe_query.domain != "universe_membership"
            or universe_query.fields != ("is_member",) or not _FIELD.fullmatch(universe_name) or universe_name == "all"):
        raise DataError("Qlib universe needs an explicit is_member query and a safe non-all name")
    spec = {"schema_version": SCHEMA, "exporter_version": EXPORTER_VERSION,
            "reader_version": READER_VERSION, "snapshot_id": snapshot,
            "queries": [_query_dict(q) for q in queries], "instrument_map": mapping,
            "fields": exports, "calendar": list(sessions),
            "universe_query": _query_dict(universe_query) if universe_query else None,
            "universe_name": universe_name if universe_query else None,
            "numeric_format": {"dtype": "little-endian float32", "rtol": rtol, "atol": atol}}
    spec_id = _digest(spec)
    destination = Path(destination).resolve()
    if destination.exists():
        existing = verify_qlib_export(destination)
        if existing["spec_id"] != spec_id:
            raise DataError("Qlib destination belongs to a different immutable query")
        return existing
    for q in queries + ((universe_query,) if universe_query is not None else ()):
        _calendar(data, manifest["domains"].get("trading_calendar"), q)
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".qlib-", dir=destination.parent))
    try:
        (stage/"calendars").mkdir(); (stage/"instruments").mkdir()
        (stage/"calendars/day.txt").write_text("\n".join(sessions)+"\n")
        (stage/"instruments/all.txt").write_text("".join(
            f"{mapping[s]}\t{sessions[0]}\t{sessions[-1]}\n" for s in symbols))
        # One scratch matrix per field avoids reopening every instrument file
        # in every month. OS-paged buffers bound memory; each final bin is written
        # once, and scratch bytes are removed before publication.
        buffers = {}
        for alias in exports:
            matrix = np.memmap(stage/f".{alias}.scratch", mode="w+", dtype="<f4",
                               shape=(len(symbols), len(sessions)))
            matrix[:] = np.nan; buffers[alias] = matrix
        contexts = []; rounding = {}; positions = {s: i for i, s in enumerate(sessions)}
        for q in queries:
            limitations = set(); context = None
            for chunk in _chunks(q):
                batch = data.read(snapshot=snapshot, query=chunk)
                context = batch.context
                limitations.update(batch.context.get("limitations") or [])
                start = positions[chunk.sessions[0]]
                for alias, meta in exports.items():
                    if meta["domain"] != q.domain or meta["field"] not in q.fields:
                        continue
                    values = _matrix(batch, symbols, chunk.sessions, meta["field"])
                    converted = values.astype("<f4")
                    if not np.allclose(values, converted, rtol=rtol, atol=atol, equal_nan=True):
                        raise DataError(f"float32 conversion exceeds declared tolerance: {alias}")
                    finite = np.isfinite(values)
                    error = float(np.max(np.abs(values[finite]-converted[finite]))) if finite.any() else 0.0
                    rounding[alias] = max(rounding.get(alias, 0.0), error)
                    buffers[alias][:, start:start+len(chunk.sessions)] = converted
            contexts.append({"domain": q.domain, "contract_id": context["contract_id"],
                             "source_profile_id": context["source_profile_id"], "limitations": sorted(limitations)})
        if universe_query is not None:
            (stage/"instruments"/f"{universe_name}.txt").write_text(
                _membership_lines(data, snapshot, universe_query, mapping))
        for alias, matrix in buffers.items():
            for i, symbol in enumerate(symbols):
                directory = stage/"features"/mapping[symbol].lower(); directory.mkdir(parents=True, exist_ok=True)
                with (directory/f"{alias}.day.bin").open("wb") as stream:
                    stream.write(np.array([0], dtype="<f4").tobytes())
                    stream.write(matrix[i].tobytes())
            matrix._mmap.close()
            (stage/f".{alias}.scratch").unlink()
        result = {**spec, "spec_id": spec_id, "contexts": contexts,
                  "max_absolute_float32_error": rounding,
                  "limitations": ["Qlib consumes the frozen projection; it does not re-evaluate PIT or revisions.",
                    "Native unadjusted prices and source volume are not Qlib's implicitly normalized training convention.",
                    "all.txt denotes candidate scope; named universe intervals denote known daily membership.",
                    "Per-key provenance is recovered from source Snapshot + QuerySpec, not reconstructed from float32 files."],
                  "files": {str(p.relative_to(stage)): {"sha256": sha256(p.read_bytes()).hexdigest(), "size": p.stat().st_size}
                            for p in sorted(stage.rglob("*")) if p.is_file()}}
        result["view_id"] = _digest(result)
        (stage/MANIFEST).write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)+"\n")
        _publish_new_directory(stage, destination)
        return result
    finally:
        if stage.exists(): shutil.rmtree(stage)


def verify_qlib_export(view, *, data=None):
    """Verify manifest/file identity; optionally compare all values to the reader.

    Does not initialize Qlib, write files, contact suppliers or mutate current.
    Actual Qlib loading/equivalence is exercised by the Research consumer adapter.
    """
    root = Path(view).resolve(); result = json.loads((root/MANIFEST).read_text())
    body = {k:v for k,v in result.items() if k != "view_id"}
    if result.get("schema_version") != SCHEMA or _digest(body) != result.get("view_id"):
        raise DataError("Qlib manifest identity mismatch")
    actual = {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}
    if actual != set(result["files"]) | {MANIFEST}:
        raise DataError("Qlib directory contains missing or untracked files")
    for relative, expected in result["files"].items():
        path = root/relative
        if path.is_symlink() or root not in path.resolve().parents:
            raise DataError("Qlib file escapes the immutable view")
        if path.stat().st_size != expected["size"] or sha256(path.read_bytes()).hexdigest() != expected["sha256"]:
            raise DataError(f"Qlib file integrity mismatch: {relative}")
    if data is not None:
        mapping = result["instrument_map"]; positions = {s:i for i,s in enumerate(result["calendar"])}
        for query in result["queries"]:
            for chunk in _chunks(QuerySpec(**query)):
                batch = data.read(snapshot=result["snapshot_id"], query=chunk)
                for alias, meta in result["fields"].items():
                    if meta["domain"] != chunk.domain or meta["field"] not in chunk.fields: continue
                    expected = _matrix(batch, chunk.symbols, chunk.sessions, meta["field"])
                    observed = np.array([np.fromfile(root/"features"/mapping[s].lower()/f"{alias}.day.bin",
                        dtype="<f4", count=len(chunk.sessions), offset=4*(positions[chunk.sessions[0]]+1)) for s in chunk.symbols])
                    if not np.allclose(expected, observed, equal_nan=True,
                        rtol=result["numeric_format"]["rtol"], atol=result["numeric_format"]["atol"]):
                        raise DataError(f"Qlib/reader mismatch for {alias}")
        if result["universe_query"] is not None:
            expected = _membership_lines(data, result["snapshot_id"], QuerySpec(**result["universe_query"]), mapping)
            if (root/"instruments"/f'{result["universe_name"]}.txt').read_text() != expected:
                raise DataError("Qlib/reader membership interval mismatch")
    return result
