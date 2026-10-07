"""Pure, common-anchor transformations of already selected local Data batches.

This module does not choose revisions or infer action availability.  Both inputs
must have been read from the same fixed Snapshot at one decision cutoff.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
from math import isfinite
from typing import Any, Mapping

import pandas as pd

from .protocols import DataBatch, QueryError


PRICE_ADJUSTMENT_VERSION = "common_anchor_price_v1"


def _session(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise QueryError(f"{label} must be a YYYY-MM-DD session")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise QueryError(f"{label} must be a YYYY-MM-DD session") from exc
    if parsed.isoformat() != value:
        raise QueryError(f"{label} must be a YYYY-MM-DD session")
    return value


def _instant(value: Any, label: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise QueryError(f"{label} must be timezone-aware") from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise QueryError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _query(batch: DataBatch, label: str, *, instant_parser=None) -> tuple[Mapping[str, Any], tuple[str, ...], tuple[str, ...], datetime]:
    if not isinstance(batch, DataBatch) or not isinstance(batch.context, Mapping):
        raise QueryError(f"{label} must be a reader DataBatch")
    if batch.context.get("contract_version") != "data_batch_v1" or not batch.context.get("reader_version"):
        raise QueryError(f"{label} must retain reader batch identity")
    query = batch.context.get("query")
    if not isinstance(query, Mapping):
        raise QueryError(f"{label} requires reader query context")
    symbols, sessions = query.get("symbols"), query.get("sessions")
    if not isinstance(symbols, (tuple, list)) or not symbols or len(set(symbols)) != len(symbols):
        raise QueryError(f"{label} requires unique query symbols")
    if not isinstance(sessions, (tuple, list)) or not sessions or len(set(sessions)) != len(sessions):
        raise QueryError(f"{label} requires unique query sessions")
    if any(not isinstance(symbol, str) or not symbol for symbol in symbols):
        raise QueryError(f"{label} requires nonempty security identities")
    sessions = tuple(_session(value, f"{label} query session") for value in sessions)
    cutoffs = query.get("cutoff_by_session")
    if not isinstance(cutoffs, Mapping) or set(cutoffs) != set(sessions):
        raise QueryError(f"{label} cutoff must cover exactly its sessions")
    parse = _instant if instant_parser is None else instant_parser
    instants = {parse(cutoffs[session], f"{label} cutoff") for session in sessions}
    if len(instants) != 1:
        raise QueryError("common-anchor adjustment requires a single decision cutoff across the whole window and anchor")
    if query.get("price_basis") != "unadjusted" or query.get("adjustment_anchor") is not None:
        raise QueryError(f"{label} must contain unadjusted reader facts")
    return query, tuple(symbols), sessions, instants.pop()


def _index(batch: DataBatch, symbols: tuple[str, ...], sessions: tuple[str, ...], label: str) -> dict[tuple[str, str], Mapping[str, Any]]:
    frame = batch.frame
    if not isinstance(frame, pd.DataFrame) or not {"security_id", "session"}.issubset(frame.columns):
        raise QueryError(f"{label} must have a keyed DataFrame")
    expected = {(symbol, session) for session in sessions for symbol in symbols}
    found: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in frame.to_dict(orient="records"):
        key = (row["security_id"], row["session"])
        if key in found or key not in expected:
            raise QueryError(f"{label} contains a duplicate or unexpected security/session key")
        found[key] = row
    if set(found) != expected:
        raise QueryError(f"{label} is missing requested security/session keys")
    return found


def _meta(batch: DataBatch, field: str, keys: set[tuple[str, str]], label: str) -> tuple[Mapping[str, Any], dict[tuple[str, str], Mapping[str, Any]]]:
    definition = batch.field_meta.get(field) if isinstance(batch.field_meta, Mapping) else None
    if not isinstance(definition, Mapping) or not isinstance(definition.get("by_key"), list):
        raise QueryError(f"{label} lacks per-key provenance for {field}")
    indexed: dict[tuple[str, str], Mapping[str, Any]] = {}
    for item in definition["by_key"]:
        if not isinstance(item, Mapping):
            raise QueryError(f"{label} has invalid per-key provenance for {field}")
        key = (item.get("security_id"), item.get("session"))
        if key in indexed or key not in keys:
            raise QueryError(f"{label} has duplicate or unexpected provenance for {field}")
        indexed[key] = item
    if set(indexed) != keys:
        raise QueryError(f"{label} is missing provenance for {field}")
    return definition, indexed


def _number(value: Any) -> tuple[float | None, str | None]:
    if value is None or pd.isna(value):
        return None, "missing"
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None, "invalid"
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return None, "invalid"
    return (number, None) if isfinite(number) else (None, "invalid")


def _copy_provenance(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """Detach each leaf; only plain dictionaries of exact JSON scalars are shallow-safe."""
    scalar_types = (type(None), bool, int, float, str)
    if type(value) is dict and all(type(key) in scalar_types and type(item) in scalar_types
                                  for key, item in value.items()):
        return value.copy()
    return deepcopy(value)


def adjust_prices(
    prices: DataBatch,
    factors: DataBatch,
    *,
    fields: tuple[str, ...],
    anchor_session: str,
    factor_field: str = "adj_factor",
    decision_session: str | None = None,
) -> DataBatch:
    """Return prices on one explicit anchor using ``price_t * factor_t / factor_anchor``.

    Inputs are read-only, already PIT-selected DataBatch objects.  Their fixed
    Snapshot IDs, PIT policies, purposes, symbols and decision cutoffs must match.
    Factor sessions must be exactly the price window plus the anchor.  By default
    the latest price session is the decision session; an explicit decision session
    must be in the price window.  A future anchor is rejected.  Missing prices or
    factors, and nonpositive/nonfinite factors, yield nulls with per-key reasons.
    No I/O, Raw replay, revision selection, or timestamp inference occurs here.
    Source vintage limitations in either input remain limitations of the result.
    Within one call, at most 128 distinct timestamp strings and each security's
    immutable native anchor number are reused. Other numeric types keep their
    original parsing path, and every provenance/cutoff check runs.
    Nothing is retained between calls, and output lineage remains detached.
    """
    parsed_instants: dict[str, datetime] = {}

    def parse_instant(value: Any, label: str) -> datetime:
        if type(value) is str and value in parsed_instants:
            return parsed_instants[value]
        result = _instant(value, label)
        if type(value) is str and len(parsed_instants) < 128:
            parsed_instants[value] = result
        return result

    price_query, price_symbols, price_sessions, price_cutoff = _query(prices, "prices", instant_parser=parse_instant)
    factor_query, factor_symbols, factor_sessions, factor_cutoff = _query(factors, "factors", instant_parser=parse_instant)
    if not prices.context.get("snapshot_id") or prices.context["snapshot_id"] != factors.context.get("snapshot_id"):
        raise QueryError("price and factor Snapshot IDs must match")
    if price_query.get("pit_policy") != factor_query.get("pit_policy"):
        raise QueryError("price and factor PIT policies must match")
    if price_query.get("pit_policy") == "bootstrap_hybrid_v1":
        price_policies = price_query.get("policy_by_session") or {}
        factor_policies = factor_query.get("policy_by_session") or {}
        if any(price_policies.get(session) != factor_policies.get(session)
               for session in price_sessions):
            raise QueryError("price and factor per-session PIT policies must match")
    if price_query.get("purpose") != factor_query.get("purpose"):
        raise QueryError("price and factor query purposes must match")
    if price_query.get("purpose") == "market_replay":
        raise QueryError("market replay must use unadjusted prices")
    if price_cutoff != factor_cutoff:
        raise QueryError("price and factor batches require the same decision cutoff")
    if set(price_symbols) != set(factor_symbols):
        raise QueryError("price and factor security identities must match")
    anchor = _session(anchor_session, "anchor_session")
    decision = _session(decision_session, "decision_session") if decision_session is not None else max(price_sessions)
    if decision not in price_sessions or decision != max(price_sessions):
        raise QueryError("decision_session must be the latest price session")
    if anchor > decision:
        raise QueryError("future adjustment anchor exceeds decision session")
    if set(factor_sessions) != set(price_sessions) | {anchor}:
        raise QueryError("factor sessions must cover exactly the price window and explicit anchor")
    if not isinstance(fields, (tuple, list)) or not fields or len(set(fields)) != len(fields):
        raise QueryError("fields must be nonempty and unique")
    if any(not isinstance(field, str) or field not in prices.frame.columns for field in fields):
        raise QueryError("adjusted fields must be present in prices")
    if not isinstance(factor_field, str) or factor_field not in factors.frame.columns:
        raise QueryError("factor_field must be present in factors")
    if any(field not in (price_query.get("fields") or ()) for field in fields):
        raise QueryError("adjusted fields must be declared by the price query")
    if factor_field not in (factor_query.get("fields") or ()):
        raise QueryError("factor_field must be declared by the factor query")

    price_rows = _index(prices, price_symbols, price_sessions, "prices")
    factor_rows = _index(factors, factor_symbols, factor_sessions, "factors")
    price_keys, factor_keys = set(price_rows), set(factor_rows)
    _, factor_meta = _meta(factors, factor_field, factor_keys, "factors")
    price_definitions = {field: _meta(prices, field, price_keys, "prices") for field in fields}
    # A provenance timestamp is only checked, never filled in or backdated.
    for label, definitions in (("factors", (factor_meta,)), ("prices", tuple(value[1] for value in price_definitions.values()))):
        for indexed in definitions:
            for item in indexed.values():
                if item.get("usable_from") is not None and parse_instant(item["usable_from"], f"{label} usable_from") > price_cutoff:
                    raise QueryError(f"{label} provenance is later than the decision cutoff")

    records: list[dict[str, Any]] = []
    output_meta: dict[str, Any] = {
        field: {"dtype": "float64", "unit": deepcopy(price_definitions[field][0].get("unit")),
                "recipe_version": PRICE_ADJUSTMENT_VERSION, "by_key": []}
        for field in fields
    }
    anchor_numbers: dict[str, tuple[float | None, str | None]] = {}
    for session in price_sessions:
        for symbol in price_symbols:
            key, anchor_key = (symbol, session), (symbol, anchor)
            factor, factor_state = _number(factor_rows[key][factor_field])
            anchor_value = factor_rows[anchor_key][factor_field]
            if type(anchor_value) in (type(None), bool, int, float, Decimal):
                if symbol not in anchor_numbers:
                    anchor_numbers[symbol] = _number(anchor_value)
                anchor_factor, anchor_state = anchor_numbers[symbol]
            else:
                anchor_factor, anchor_state = _number(anchor_value)
            record: dict[str, Any] = {"security_id": symbol, "session": session}
            for field in fields:
                price, price_state = _number(price_rows[key][field])
                if price_state:
                    reason = (price_definitions[field][1][key].get("missing_reason") or "price_missing") if price_state == "missing" else "invalid_price"
                elif anchor_state:
                    reason = "missing_anchor_factor" if anchor_state == "missing" else "invalid_anchor_factor"
                elif anchor_factor <= 0:
                    reason = "invalid_anchor_factor"
                elif factor_state:
                    reason = "missing_factor" if factor_state == "missing" else "invalid_factor"
                elif factor <= 0:
                    reason = "invalid_factor"
                else:
                    reason = None
                adjusted = None if reason else price * factor / anchor_factor
                if adjusted is not None and not isfinite(adjusted):
                    reason, adjusted = "invalid_adjusted_value", None
                record[field] = adjusted
                output_meta[field]["by_key"].append({
                    "security_id": symbol, "session": session,
                    "missing_reason": reason,
                    "price_provenance": _copy_provenance(price_definitions[field][1][key]),
                    "factor_provenance": _copy_provenance(factor_meta[key]),
                    "anchor_factor_provenance": _copy_provenance(factor_meta[anchor_key]),
                })
            records.append(record)

    # Preserve query's position and detach it independently below, without
    # first copying the query graph that would immediately be overwritten.
    context = deepcopy({key: None if key == "query" else value
                        for key, value in prices.context.items()})
    context["query"] = deepcopy(dict(price_query))
    context["query"].update({"fields": list(fields), "price_basis": "common_anchor_adjusted_v1", "adjustment_anchor": anchor})
    context["derivation"] = {
        "recipe_version": PRICE_ADJUSTMENT_VERSION,
        "formula": "price_t * factor_t / factor_anchor",
        "factor_field": factor_field,
        "anchor_session": anchor,
        "decision_session": decision,
        "decision_cutoff": price_cutoff.isoformat(),
        "factor_domain": factors.context.get("domain"),
        "factor_contract_id": factors.context.get("contract_id"),
        "factor_source_profile_id": factors.context.get("source_profile_id"),
        "factor_reader_version": factors.context.get("reader_version"),
        "price_query": deepcopy(dict(price_query)),
        "factor_query": deepcopy(dict(factor_query)),
    }
    limitations = list(context.get("limitations") or ())
    for limitation in factors.context.get("limitations") or ():
        if limitation not in limitations:
            limitations.append(limitation)
    limitations.append("Factor availability and any terminal-vintage assumptions are inherited from the PIT-selected input; this recipe adds no historical evidence")
    context["limitations"] = limitations
    frame = pd.DataFrame.from_records(records, columns=["security_id", "session", *fields])
    return DataBatch(frame, output_meta, context)
