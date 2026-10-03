"""Tushare dated index-weight membership, with explicit between-date semantics.

Only saved original ``index_weight`` responses contribute members. A successful
uncapped response is treated as the supplier's complete dated group; an empty
response supplies no new state. No exchange or index-provider fact is consulted.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, timedelta
from hashlib import sha256
from typing import Any, Mapping, Sequence

from .protocols import DataError
from .sources import _rows
from .storage import LocalStore, _json_bytes


SCHEMA = "tushare_csi1800_membership_source_v1"
INDEX_UNIVERSES = {"000300.SH": "csi300", "000905.SH": "csi500",
                   "000852.SH": "csi1000"}
CONTRACT = {
    "contract_id": "local.tushare_dated_membership.v1",
    "logical_key": ["membership_id"],
    "fields": {
        "membership_id": {"dtype": "string", "nullable": False},
        "security_id": {"dtype": "string", "nullable": False},
        "universe_id": {"dtype": "string", "nullable": False},
        "effective_from": {"dtype": "date", "nullable": False},
        "effective_to": {"dtype": "date", "nullable": True},
        "revision_id": {"dtype": "string", "nullable": False},
        "revision_sequence": {"dtype": "int64", "nullable": False},
        "first_observed_at": {"dtype": "timestamp", "nullable": False},
        "raw_batch_id": {"dtype": "string", "nullable": False},
        "source_available_at": {"dtype": "timestamp", "nullable": True},
        "evidence_ref": {"dtype": "string", "nullable": True},
    },
}
PROFILE = {
    "id": "tushare.index_weight.dated_membership.v1",
    "revision_order": "source_sequence_only",
    "availability": {"timezone": "Asia/Shanghai", "session_release_time": "18:00:00",
                     "basis": "assumed dated supplier snapshot release; not verified historical publication time"},
    "membership_semantics": "Tushare dated index_weight groups; explicit unknown or carry-forward between snapshots",
}


def _day(value: str) -> str:
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError
    except ValueError as exc:
        raise DataError(f"invalid vendor membership date {value!r}") from exc
    return value


def _source_day(value: str) -> str:
    try:
        if not isinstance(value, str):
            raise ValueError
        return datetime.strptime(value, "%Y%m%d").date().isoformat()
    except ValueError as exc:
        raise DataError(f"invalid index_weight trade_date {value!r}") from exc


def _next_day(value: str) -> str:
    return (date.fromisoformat(value) + timedelta(days=1)).isoformat()


def _plain(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: (value.isoformat() if isinstance(value, (date, datetime)) else value)
            for key, value in row.items()}


def _digest(value: Any) -> str:
    return sha256(_json_bytes(value)).hexdigest()


def _selected_groups(store: LocalStore, raw_ids: Sequence[str],
                     identity_map: Mapping[str, str]) -> dict[str, list[dict[str, Any]]]:
    """Validate source selectors, then retain each nonempty dated supplier group."""
    raw_records = store.get_raw_many(raw_ids)
    by_index: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for raw_id in raw_ids:
        raw = raw_records[raw_id]
        request = raw.get("request") or {}
        params = request.get("params") or {}
        code = params.get("index_code")
        if (raw.get("domain") != "reference_bootstrap" or request.get("endpoint") != "index_weight"
                or code not in INDEX_UNIVERSES or raw.get("status") not in {"success", "empty"}):
            raise DataError(f"{raw_id} is not an uncapped Tushare index_weight Raw observation")
        rows = _rows(store.read_raw_record(raw))
        if raw["status"] == "empty":
            if rows:
                raise DataError("empty index_weight Raw contains rows")
            continue
        if not rows:
            raise DataError("successful index_weight Raw has no rows")
        grouped: dict[str, set[str]] = defaultdict(set)
        for row in rows:
            if row.get("index_code") != code or not isinstance(row.get("con_code"), str):
                raise DataError("index_weight row differs from its source selector")
            source_date = row.get("trade_date")
            day = _source_day(source_date)
            if not params.get("start_date", "") <= source_date <= params.get("end_date", ""):
                raise DataError("index_weight trade_date lies outside requested window")
            symbol = row["con_code"]
            if symbol in grouped[day]:
                raise DataError("duplicate index_weight constituent in one dated group")
            grouped[day].add(symbol)
        for day, source_codes in grouped.items():
            members = {identity_map.get(symbol, symbol) for symbol in source_codes}
            candidate = {"day": day, "members": members, "raw_batch_id": raw_id,
                         "observed_at": raw["observed_at"],
                         "source_codes": sorted(source_codes)}
            previous = by_index[code].get(day)
            if previous is not None:
                if previous["source_codes"] == candidate["source_codes"]:
                    # Re-fetched identical supplier content does not move its
                    # first receipt or create another economic revision.
                    if previous["observed_at"] <= candidate["observed_at"]:
                        continue
                elif previous["observed_at"] >= candidate["observed_at"]:
                    # A later supplier response wins a same-date correction.
                    continue
            by_index[code][day] = candidate
    return {code: [by_index[code][day] for day in sorted(by_index[code])]
            for code in INDEX_UNIVERSES}


def _economic_states(groups: Mapping[str, list[dict[str, Any]]], *,
                     between_snapshots: str, verified_through: str) -> list[dict[str, Any]]:
    states: list[dict[str, Any]] = []
    for code, universe in INDEX_UNIVERSES.items():
        series = groups[code]
        for pos, group in enumerate(series):
            start = group["day"]
            if start > verified_through:
                continue
            stop = series[pos + 1]["day"] if pos + 1 < len(series) else None
            states.append({"state_id": f"vendor:{universe}:{start}",
                           "universe_id": universe, "effective_from": start,
                           "effective_to": stop, "members": group["members"],
                           "first_observed_at": group["observed_at"],
                           "raw_batch_id": group["raw_batch_id"],
                           "dependency_raw_batch_ids": [group["raw_batch_id"]],
                           "open_ended": stop is None})
    # CSI1800 is the supplier's union of the latest dated group from all three
    # indexes. It becomes known only after each has supplied an initial group.
    changes = sorted({group["day"] for series in groups.values() for group in series})
    latest: dict[str, dict[str, Any]] = {}
    by_day = {code: {group["day"]: group for group in groups[code]}
              for code in INDEX_UNIVERSES}
    for pos, day in enumerate(changes):
        for code in INDEX_UNIVERSES:
            if day in by_day[code]:
                latest[code] = by_day[code][day]
        if len(latest) != len(INDEX_UNIVERSES) or day > verified_through:
            continue
        dependencies = list(dict.fromkeys(latest[code]["raw_batch_id"] for code in INDEX_UNIVERSES))
        observed = max(latest[code]["observed_at"] for code in INDEX_UNIVERSES)
        members = set().union(*(latest[code]["members"] for code in INDEX_UNIVERSES))
        stop = changes[pos + 1] if pos + 1 < len(changes) else None
        states.append({"state_id": f"vendor:csi1800:{day}",
                       "universe_id": "csi1800", "effective_from": day,
                       "effective_to": stop, "members": members,
                       "first_observed_at": observed, "raw_batch_id": dependencies[0],
                       "dependency_raw_batch_ids": dependencies,
                       "open_ended": stop is None})
    return states


def _positive_rows(states: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    by_universe: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for state in states:
        by_universe[state["universe_id"]].append(state)
    for universe, series in by_universe.items():
        active: dict[str, tuple[str, str, str, str, list[str]]] = {}
        for state in sorted(series, key=lambda item: item["effective_from"]):
            start = state["effective_from"]
            current = state["members"]
            for symbol in sorted(active.keys() - current):
                since, observed, raw_id, opening, dependencies = active.pop(symbol)
                dependencies = list(dict.fromkeys([*dependencies, *state["dependency_raw_batch_ids"]]))
                rows.append(_row(universe, symbol, since, start, max(observed, state["first_observed_at"]),
                                 raw_id, opening, dependencies))
            for symbol in sorted(current - active.keys()):
                active[symbol] = (start, state["first_observed_at"], state["raw_batch_id"],
                                  state["state_id"], state["dependency_raw_batch_ids"])
            if state["effective_to"] is not None and state["effective_to"] != _next_start(series, state):
                for symbol, (since, observed, raw_id, opening, dependencies) in active.items():
                    rows.append(_row(universe, symbol, since, state["effective_to"],
                                     max(observed, state["first_observed_at"]), raw_id, opening,
                                     list(dict.fromkeys([*dependencies, *state["dependency_raw_batch_ids"]]))))
                active.clear()
        for symbol, (since, observed, raw_id, opening, dependencies) in active.items():
            rows.append(_row(universe, symbol, since, None, observed, raw_id, opening, dependencies))
    return rows


def _next_start(series: Sequence[Mapping[str, Any]], state: Mapping[str, Any]) -> str | None:
    for pos, item in enumerate(series):
        if item is state:
            return series[pos + 1]["effective_from"] if pos + 1 < len(series) else None
    return None


def _row(universe: str, symbol: str, start: str, stop: str | None,
         observed: str, raw_id: str, opening: str, dependencies: list[str]) -> dict[str, Any]:
    identity = f"{universe}:{symbol}:{opening}"
    return {"membership_id": identity, "security_id": symbol,
            "universe_id": universe, "effective_from": start, "effective_to": stop,
            "revision_id": "vendor:" + _digest([identity, start, stop, observed]),
            "revision_sequence": 1, "first_observed_at": observed,
            "raw_batch_id": raw_id, "source_available_at": None,
            "evidence_ref": None, "dependency_raw_batch_ids": dependencies}


def _versions(store: LocalStore, old: Mapping[str, Any] | None,
              rows: list[dict[str, Any]], states: list[dict[str, Any]],
              change_time: str, new_identity_bindings: Mapping[str, str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    if old is None or old.get("source_profile", {}).get("id") != PROFILE["id"]:
        return rows, states, True
    old_rows = [_plain(row) for part in old["partitions"] for row in store.read_partition(part).to_pylist()]
    for code, stable in new_identity_bindings.items():
        if code != stable and any(row["security_id"] == code for row in old_rows):
            raise DataError("new identity binding would retroactively remap existing membership")
    old_states = deepcopy(old["coverage"]["complete_states"])
    def merge(before, now, key, material, *, tombstone=False):
        latest = {item[key]: item for item in sorted(before, key=lambda r: r.get("revision_sequence", 1))}
        output = list(before)
        changed = False
        current_ids = {item[key] for item in now}
        for item in now:
            previous = latest.get(item[key])
            if previous is not None and material(previous) == material(item):
                continue
            if previous is not None:
                item["revision_sequence"] = previous["revision_sequence"] + 1
                item["first_observed_at"] = max(item["first_observed_at"], previous["first_observed_at"])
                item["revision_id"] = "vendor:" + _digest([item[key], material(item), item["first_observed_at"]])
            output.append(item)
            changed = True
        if tombstone:
            for identity, previous in latest.items():
                if identity in current_ids or previous["effective_from"] == previous["effective_to"]:
                    continue
                removed = dict(previous)
                removed["effective_to"] = removed["effective_from"]
                removed["revision_sequence"] = previous["revision_sequence"] + 1
                removed["first_observed_at"] = max(change_time, previous["first_observed_at"])
                removed["revision_id"] = "vendor:" + _digest([identity, "removed", removed["first_observed_at"]])
                output.append(removed)
                changed = True
        return output, changed
    row_material = lambda r: (r["effective_from"], r["effective_to"], r["security_id"], r["universe_id"])
    state_material = lambda s: (s["effective_from"], s["effective_to"], s["member_set_sha256"])
    all_rows, row_changed = merge(old_rows, rows, "membership_id", row_material, tombstone=True)
    all_states, state_changed = merge(old_states, states, "state_id", state_material)
    return all_rows, all_states, row_changed or state_changed


def build_vendor_membership_domain(store: LocalStore, *,
                                   index_weight_raw_batch_ids: Sequence[str],
                                   identity_map: Mapping[str, str] | None,
                                   verified_through: str, operation_id: str,
                                   between_snapshots: str = "carry_forward",
                                   old_domain: Mapping[str, Any] | None = None,
                                   old_snapshot_id: str | None = None,
                                   prior_source_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Build a typed member domain from frozen Tushare Raw; no network or publish.

    Each dated group applies until the next group or ``verified_through``;
    a later monthly snapshot never fills earlier days. Operational visibility
    starts at actual Raw receipt, never at the supplier trade date.
    """
    if between_snapshots != "carry_forward":
        raise DataError("vendor membership uses carry_forward between dated snapshots")
    through = _day(verified_through)
    ids = list(dict.fromkeys(index_weight_raw_batch_ids))
    if not ids:
        raise DataError("vendor membership requires saved index_weight Raw")
    identity = dict(identity_map or {})
    old = old_domain if old_domain and old_domain.get("source_profile", {}).get("id") == PROFILE["id"] else None
    if old is not None:
        previous_config = (dict(prior_source_config) if prior_source_config is not None else
                           vendor_membership_source_chain(store, old)[-1])
        if previous_config["between_snapshots"] != between_snapshots:
            raise DataError("vendor membership between-snapshot policy cannot change within a Snapshot chain")
        if any(identity.get(code) != stable for code, stable in previous_config["identity_map"].items()):
            raise DataError("vendor membership update cannot remap an existing security identity")
        if not set(previous_config["index_weight_raw_batch_ids"]) <= set(ids):
            raise DataError("vendor membership update cannot omit prior source Raw")
    groups = _selected_groups(store, ids, identity)
    states = _economic_states(groups, between_snapshots=between_snapshots,
                              verified_through=through)
    if not any(state["universe_id"] == "csi1800" for state in states):
        raise DataError("no dated Tushare group exists for every CSI1800 component")
    coverage_states = [{key: value for key, value in state.items()
                        if key not in {"members", "open_ended"}}
                       | {"complete": True, "member_set_source": "canonical_intervals_v1",
                          "source_snapshot_date": state["effective_from"],
                          "member_count": len(state["members"]),
                          "member_set_sha256": _digest(sorted(state["members"])),
                          "revision_sequence": 1,
                          "revision_id": "vendor:" + _digest([state["state_id"], sorted(state["members"])])}
                       for state in states]
    rows = _positive_rows(states)
    change_time = max(store.get_raw(raw_id)["observed_at"] for raw_id in ids)
    additions = ({code: stable for code, stable in identity.items()
                  if code not in previous_config["identity_map"]} if old is not None else {})
    rows, coverage_states, changed = _versions(store, old, rows, coverage_states,
                                               change_time, additions)
    previous_through = old.get("coverage", {}).get("verified_through") if old else None
    checkpoints = deepcopy(old.get("coverage", {}).get("verification_checkpoints", [])) if old else []
    if previous_through is not None and through < previous_through:
        raise DataError("vendor membership verified_through cannot move backward")
    union_states = sorted((state for state in states if state["universe_id"] == "csi1800"),
                          key=lambda state: state["effective_from"])
    if old is None:
        for state in union_states:
            stop = state["effective_to"]
            state_through = (min(through, (date.fromisoformat(stop) - timedelta(days=1)).isoformat())
                             if stop else through)
            checkpoints.append({"verified_through": state_through,
                                "first_observed_at": state["first_observed_at"],
                                "dependency_raw_batch_ids": state["dependency_raw_batch_ids"]})
    elif through > previous_through:
        latest = union_states[-1]
        checkpoints.append({"verified_through": through,
                            "first_observed_at": latest["first_observed_at"],
                            "dependency_raw_batch_ids": latest["dependency_raw_batch_ids"]})
    if old is not None and not changed and previous_through == through:
        return deepcopy(dict(old))
    parts = (deepcopy(old["partitions"]) if old is not None and not changed else
             [store.write_partition("universe_membership", "history", rows, CONTRACT)])
    if old is not None and not old_snapshot_id:
        raise DataError("vendor membership update needs parent Snapshot ID")
    config = {"schema_version": SCHEMA, "index_weight_raw_batch_ids": ids,
              "identity_map": identity, "verified_through": through,
              "between_snapshots": between_snapshots}
    context = ({"vendor_membership_source_base": config} if old is None else {
        "vendor_membership_source_parent_snapshot": old_snapshot_id,
        "vendor_membership_source_delta": {
            "index_weight_raw_batch_ids": [raw_id for raw_id in ids if raw_id not in previous_config["index_weight_raw_batch_ids"]],
            "identity_map": {code: stable for code, stable in identity.items()
                             if code not in previous_config["identity_map"]},
            "verified_through": through,
        }})
    expected = {"000300.SH": 300, "000905.SH": 500, "000852.SH": 1000}
    warnings = []
    for code, series in groups.items():
        for group in series:
            if len(group["source_codes"]) != expected[code]:
                warnings.append({"kind": "supplier_group_count", "index_code": code,
                                 "trade_date": group["day"], "received": len(group["source_codes"]),
                                 "usual_count": expected[code]})
    return {"contract": CONTRACT, "source_profile": PROFILE, "partitions": parts,
            "raw_batch_ids": list(dict.fromkeys([*(old["raw_batch_ids"] if old else []), *ids])),
            "coverage": {"basis": "tushare_dated_index_weight_snapshot_v1",
                         "between_snapshots": between_snapshots,
                         "verified_through": through,
                         "verification_checkpoints": checkpoints,
                         "complete_states": coverage_states,
                         "warnings": warnings,
                         "limitations": [
                             "Supplier index_weight was observed on dated snapshots only; no intramonth membership change is proven.",
                             "Best-effort uses an assumed 18:00 Asia/Shanghai dated release; strict policies use actual Raw receipt.",
                         ]},
            "build_context": {"operation_id": operation_id, **context}}


def vendor_membership_source_chain(store: LocalStore,
                                   domain: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Recover frozen vendor inputs through immutable parent Snapshots."""
    context = domain.get("build_context") or {}
    base = context.get("vendor_membership_source_base")
    if isinstance(base, Mapping):
        if base.get("schema_version") != SCHEMA:
            raise DataError("unsupported vendor membership source schema")
        return [deepcopy(dict(base))]
    parent_id = context.get("vendor_membership_source_parent_snapshot")
    delta = context.get("vendor_membership_source_delta")
    if not isinstance(parent_id, str) or not isinstance(delta, Mapping):
        return []
    parent = store.load_snapshot(parent_id)
    prior = parent["domains"].get("universe_membership")
    if prior is None:
        raise DataError("vendor membership source parent lacks domain")
    chain = vendor_membership_source_chain(store, prior)
    if not chain:
        raise DataError("vendor membership source parent lacks frozen inputs")
    previous = chain[-1]
    new = deepcopy(previous)
    new["index_weight_raw_batch_ids"].extend(delta.get("index_weight_raw_batch_ids") or [])
    new["identity_map"].update(delta.get("identity_map") or {})
    new["verified_through"] = delta["verified_through"]
    chain.append(new)
    return chain


def publish_vendor_membership(store: LocalStore, *,
                              index_weight_raw_batch_ids: Sequence[str],
                              identity_map: Mapping[str, str] | None,
                              verified_through: str, operation_id: str,
                              between_snapshots: str = "carry_forward",
                              base_snapshot: str | None, promote: bool = False) -> dict[str, Any]:
    """Publish Tushare-only member states; return the parent ID on no change."""
    parent = store.load_snapshot(base_snapshot) if base_snapshot else None
    old = parent["domains"].get("universe_membership") if parent else None
    domain = build_vendor_membership_domain(
        store, index_weight_raw_batch_ids=index_weight_raw_batch_ids,
        identity_map=identity_map, verified_through=verified_through,
        operation_id=operation_id, between_snapshots=between_snapshots,
        old_domain=old, old_snapshot_id=base_snapshot)
    if old == domain:
        return {"snapshot_id": base_snapshot, "changed": False,
                "complete_state_count": len(domain["coverage"]["complete_states"])}
    domains = deepcopy(parent["domains"]) if parent else {}
    domains["universe_membership"] = domain
    snapshot = store.publish_snapshot(domains, parent_snapshot=base_snapshot,
                                      build_context={"source": SCHEMA, "operation_id": operation_id},
                                      promote=promote)
    return {"snapshot_id": snapshot["snapshot_id"], "changed": True,
            "complete_state_count": len(domain["coverage"]["complete_states"])}
