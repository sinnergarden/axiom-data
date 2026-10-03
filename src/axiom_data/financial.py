"""Pure stable financial derivations over PIT-selected event DataBatch inputs."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation
from math import isfinite
from typing import Any, Mapping, Sequence

import pandas as pd

from .protocols import DataBatch, QueryError


FINANCIAL_RECIPE_VERSION = "financial_stable_local_v1"
_STATUSES = {"value", "not_provided", "retracted", "source_missing", "parse_error"}
_MISSING_PRIORITY = ("retracted", "parse_error", "source_missing", "not_provided")


def _period(value: Any) -> str:
    if isinstance(value, date) and not isinstance(value, str):
        value = value.isoformat()
    if not isinstance(value, str):
        raise QueryError("financial report_period must be an ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise QueryError("financial report_period must be an ISO date") from exc
    if parsed.isoformat() != value or (parsed.month, parsed.day) not in {(3, 31), (6, 30), (9, 30), (12, 31)}:
        raise QueryError("financial report_period must be a calendar quarter end")
    return value


def _prior(period: str) -> str:
    year, month = int(period[:4]), int(period[5:7])
    return {3: f"{year-1}-12-31", 6: f"{year}-03-31",
            9: f"{year}-06-30", 12: f"{year}-09-30"}[month]


def _number(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None or pd.isna(value):
        raise QueryError("financial value status conflicts with nonnumeric value")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise QueryError("financial value must be finite numeric") from exc
    if not number.is_finite():
        raise QueryError("financial value must be finite numeric")
    return number


def _as_scalar(number: Decimal, dtype: str | None) -> int | float:
    if str(dtype).lower() in {"int", "integer", "int64", "int32"}:
        if number != number.to_integral_value():
            raise QueryError("integer financial inputs produced fractional result")
        result = int(number)
        bits = 32 if str(dtype).lower() == "int32" else 64
        if not -(2**(bits-1)) <= result < 2**(bits-1):
            raise QueryError(f"derived financial integer exceeds int{bits} range")
        return result
    result = float(number)
    if not isfinite(result):
        raise QueryError("derived financial value exceeds float64 range")
    return result


def _prepare(batch: DataBatch, *, field: str, endpoint: str, report_type: str,
             basis: str, unit: str) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[tuple[str, str], dict[str, Any]], Mapping[str, Any]]:
    if not isinstance(batch, DataBatch) or not isinstance(batch.frame, pd.DataFrame):
        raise QueryError("financial transform requires selected event DataBatch")
    context = batch.context
    if (not isinstance(context, Mapping) or
            context.get("domain") not in {"financial_events", "cash_flow_events"} or
            not context.get("snapshot_id") or not context.get("event_reader_version")):
        raise QueryError("financial transform requires PIT-selected income or cash-flow events")
    query = context.get("query")
    if not isinstance(query, Mapping) or field not in (query.get("fields") or ()) or not query.get("cutoff"):
        raise QueryError("financial source field and cutoff must be explicit")
    if not all(isinstance(value, str) and value for value in (field, endpoint, report_type, basis, unit)):
        raise QueryError("financial field, endpoint, report_type, basis and unit are required")
    if basis != "cumulative_ytd":
        raise QueryError("single-quarter and TTM recipes require cumulative_ytd basis")
    definition = batch.field_meta.get(field) if isinstance(batch.field_meta, Mapping) else None
    if not isinstance(definition, Mapping) or definition.get("unit") != unit or definition.get("basis") != basis:
        raise QueryError("financial unit or basis differs from declared input field")
    columns = {"security_id", "endpoint", "report_type", "report_period", field}
    if not columns.issubset(batch.frame.columns):
        raise QueryError("financial events lack required native keys")
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for source in batch.frame.to_dict(orient="records"):
        if source.get("endpoint") != endpoint or source.get("report_type") != report_type:
            continue
        if "basis" in source and source["basis"] != basis:
            raise QueryError("financial event basis differs from requested basis")
        symbol = source.get("security_id")
        if not isinstance(symbol, str) or not symbol:
            raise QueryError("financial event lacks security_id")
        key = (symbol, _period(source["report_period"]))
        if key in rows:
            raise QueryError("duplicate financial report period in selected event batch")
        rows[key] = source
    if not isinstance(definition.get("by_key"), list):
        raise QueryError("financial field lacks per-event provenance")
    provenance: dict[tuple[str, str], dict[str, Any]] = {}
    for item in definition["by_key"]:
        if not isinstance(item, Mapping):
            raise QueryError("invalid financial field provenance")
        if item.get("endpoint") != endpoint or item.get("report_type") != report_type:
            continue
        key = (item.get("security_id"), _period(item.get("report_period")))
        if key in provenance:
            raise QueryError("duplicate financial field provenance")
        status = item.get("status")
        if status not in _STATUSES:
            raise QueryError("financial transform requires explicit valid field status")
        provenance[key] = dict(item)
    if set(rows) != set(provenance):
        raise QueryError("financial values and provenance keys differ")
    for key, source in rows.items():
        status = provenance[key]["status"]
        if (status == "value") != (source[field] is not None and not pd.isna(source[field])):
            raise QueryError("financial field value/status mismatch")
    return rows, provenance, definition


def _quarter(key: tuple[str, str], rows: Mapping[tuple[str, str], Mapping[str, Any]],
             meta: Mapping[tuple[str, str], Mapping[str, Any]], field: str,
             dtype: str | None) -> tuple[int | float | None, str, list[dict[str, Any]]]:
    symbol, period = key
    needed = [key]
    if period[5:7] != "03":
        needed.append((symbol, _prior(period)))
    components = []
    states = []
    for component_key in needed:
        source = rows.get(component_key)
        provenance = meta.get(component_key)
        status = provenance["status"] if provenance else "source_missing"
        components.append({"security_id": component_key[0], "report_period": component_key[1],
                           "status": status, "provenance": deepcopy(provenance) if provenance else None})
        states.append(status)
        if source is not None and status == "value":
            _number(source[field])
    if any(state != "value" for state in states):
        return None, next(state for state in _MISSING_PRIORITY if state in states), components
    amount = _number(rows[needed[0]][field])
    if len(needed) == 2:
        amount -= _number(rows[needed[1]][field])
    return _as_scalar(amount, dtype), "value", components


def _derive(batch: DataBatch, *, field: str, endpoint: str, report_type: str,
            basis: str, unit: str, recipe: str,
            periods: Sequence[str] | None = None) -> DataBatch:
    rows, provenance, definition = _prepare(batch, field=field, endpoint=endpoint,
                                             report_type=report_type, basis=basis, unit=unit)
    if periods is not None:
        chosen = tuple(_period(p) for p in periods)
        if not chosen or len(set(chosen)) != len(chosen):
            raise QueryError("periods must be unique quarter ends")
        output_keys = [(symbol, period) for symbol in dict.fromkeys(batch.context["query"]["symbols"])
                       for period in chosen]
    else:
        output_keys = sorted(rows, key=lambda x: (x[0], x[1]))
    records = []
    output_basis = "single_quarter" if recipe == "single_quarter" else "trailing_twelve_months"
    output_meta = {field: {"dtype": definition.get("dtype"), "unit": unit,
                           "basis": output_basis, "source_basis": basis,
                           "recipe_version": FINANCIAL_RECIPE_VERSION, "by_key": []}}
    for key in output_keys:
        if recipe == "single_quarter":
            value, status, components = _quarter(key, rows, provenance, field, definition.get("dtype"))
        else:
            quarter_values = []
            components = []
            statuses = []
            cursor = key
            for _ in range(4):
                quarter_value, quarter_status, quarter_components = _quarter(cursor, rows, provenance, field, definition.get("dtype"))
                quarter_values.append(quarter_value)
                statuses.append(quarter_status)
                components.extend(quarter_components)
                cursor = (cursor[0], _prior(cursor[1]))
            if all(status == "value" for status in statuses):
                value = _as_scalar(sum((_number(x) for x in quarter_values), Decimal(0)), definition.get("dtype"))
                status = "value"
            else:
                value = None
                status = next(state for state in _MISSING_PRIORITY if state in statuses)
        records.append({"security_id": key[0], "endpoint": endpoint, "report_type": report_type,
                        "report_period": key[1], field: value})
        output_meta[field]["by_key"].append({
            "security_id": key[0], "endpoint": endpoint, "report_type": report_type,
            "report_period": key[1], "status": status,
            "missing_reason": None if status == "value" else (
                "missing_quarter" if status == "source_missing" else status),
            "components": components,
        })
    context = deepcopy(dict(batch.context))
    context["query"] = deepcopy(dict(batch.context["query"]))
    context["query"]["fields"] = [field]
    context["query"]["value_basis"] = output_basis
    context["value_basis"] = output_basis
    context["derivation"] = {
        "recipe_version": FINANCIAL_RECIPE_VERSION, "recipe": recipe,
        "field": field, "endpoint": endpoint, "report_type": report_type,
        "basis": output_basis, "source_basis": basis, "unit": unit,
        "periods": list(periods) if periods is not None else None,
        "input_snapshot_id": batch.context["snapshot_id"],
        "input_query": deepcopy(dict(batch.context["query"])),
        "input_reader_version": batch.context["event_reader_version"],
    }
    frame = pd.DataFrame.from_records(records, columns=["security_id", "endpoint", "report_type", "report_period", field])
    nullable_types = {"int": "Int64", "integer": "Int64", "int64": "Int64",
                      "int32": "Int32", "float": "Float64", "double": "Float64",
                      "float64": "Float64", "float32": "Float32"}
    dtype = nullable_types.get(str(definition.get("dtype")).lower())
    if dtype:
        frame[field] = pd.array([record[field] for record in records], dtype=dtype)
    return DataBatch(frame, output_meta, context)


def single_quarter(batch: DataBatch, *, field: str, endpoint: str, report_type: str,
                   basis: str, unit: str, periods: Sequence[str] | None = None) -> DataBatch:
    """Derive individual fiscal quarters from cumulative YTD income or cash-flow events.

    Missing earlier quarters and explicit retractions propagate as nulls with
    component provenance. ``periods`` optionally requests missing target keys.
    No source access, revision selection, or cache publication occurs.
    """
    return _derive(batch, field=field, endpoint=endpoint, report_type=report_type,
                   basis=basis, unit=unit, recipe="single_quarter", periods=periods)


def ttm(batch: DataBatch, *, field: str, endpoint: str, report_type: str,
        basis: str, unit: str, periods: Sequence[str] | None = None) -> DataBatch:
    """Sum four contiguous selected income or cash-flow quarters at each report end.

    Each quarter requires its YTD source and, outside Q1, its preceding YTD
    source. A missing or retracted component makes the TTM output null.
    """
    return _derive(batch, field=field, endpoint=endpoint, report_type=report_type,
                   basis=basis, unit=unit, recipe="ttm", periods=periods)
