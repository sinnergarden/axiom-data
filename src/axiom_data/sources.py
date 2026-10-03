"""Pure Raw-to-canonical conversion and an explicitly invoked daily source adapter.

``records_v1`` uses a contract's ``fields`` and a SourceProfile ``field_map``
(canonical name -> source name). A profile must declare each mapped source unit;
non-identity conversions must also declare their numeric factor. Raw bytes remain
on :class:`IngestBatch`; this module never opens a connection or writes data.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from typing import Any

from axiom_data.protocols import DataError, IngestBatch


_DATE8 = re.compile(r"[0-9]{8}\Z")
_TS_CODE = re.compile(r"[0-9]{6}\.(?:SH|SZ)\Z")
_INDEX_CODE = re.compile(r"[0-9]{6}\.(?:SH|SZ|CSI)\Z")
_META = {"revision_id", "revision_sequence", "first_observed_at", "raw_batch_id",
         "source_available_at", "evidence_ref"}
_DAILY_FIELDS = ("ts_code", "trade_date", "open", "high", "low", "close",
                 "pre_close", "change", "pct_chg", "vol", "amount")


TUSHARE_DAILY_CONTRACT: dict[str, Any] = {
    "contract_id": "local.market_daily.v1",
    "logical_key": ["security_id", "session"],
    "fields": {
        "security_id": {"dtype": "string", "nullable": False},
        "session": {"dtype": "date", "nullable": False},
        "open": {"dtype": "float64", "unit": "CNY/share", "nullable": True},
        "high": {"dtype": "float64", "unit": "CNY/share", "nullable": True},
        "low": {"dtype": "float64", "unit": "CNY/share", "nullable": True},
        "close": {"dtype": "float64", "unit": "CNY/share", "nullable": True},
        "pre_close": {"dtype": "float64", "unit": "CNY/share", "nullable": True},
        "volume_shares": {"dtype": "int64", "unit": "shares", "nullable": True},
        "amount_cny": {"dtype": "float64", "unit": "CNY", "nullable": True},
        "revision_id": {"dtype": "string", "nullable": False},
        "revision_sequence": {"dtype": "int64", "nullable": True},
        "first_observed_at": {"dtype": "timestamp", "nullable": False},
        "raw_batch_id": {"dtype": "string", "nullable": False},
        "source_available_at": {"dtype": "timestamp", "nullable": True},
        "evidence_ref": {"dtype": "string", "nullable": True},
    },
}


def tushare_daily_profile(identity_map: Mapping[str, str]) -> dict[str, Any]:
    """Freeze all source assumptions and the caller's stable identity mapping."""
    if not isinstance(identity_map, Mapping) or not identity_map:
        raise DataError("Tushare daily requires a nonempty explicit identity_map")
    identities = dict(identity_map)
    for code, security_id in identities.items():
        if not isinstance(code, str) or not _TS_CODE.fullmatch(code):
            raise DataError(f"invalid Tushare identity key: {code!r}")
        if not isinstance(security_id, str) or not security_id.strip():
            raise DataError(f"invalid stable security_id for {code!r}")
    if len(set(identities.values())) != len(identities):
        raise DataError("identity_map maps multiple source codes to one security_id")
    return {
        "id": "tushare.daily.local.v1",
        "endpoint": "daily",
        "raw_serialization": "json_source_records_or_sdk_table_v1",
        "field_map": {
            "security_id": "ts_code", "session": "trade_date",
            "open": "open", "high": "high", "low": "low", "close": "close",
            "pre_close": "pre_close", "volume_shares": "vol", "amount_cny": "amount",
        },
        "identity_map": identities,
        "source_units": {
            "open": "CNY/share", "high": "CNY/share", "low": "CNY/share",
            "close": "CNY/share", "pre_close": "CNY/share",
            "vol": "hundred-share lots", "amount": "thousand CNY",
        },
        "unit_conversions": {
            "volume_shares": {"from": "hundred-share lots", "to": "shares", "factor": "100"},
            "amount_cny": {"from": "thousand CNY", "to": "CNY", "factor": "1000"},
        },
        "date_formats": {"session": "YYYYMMDD"},
        "availability": {
            "timezone": "Asia/Shanghai", "session_release_time": "20:00:00",
            "basis": "best_effort_assumption; not revision-bound publication evidence",
        },
        "revision_order": "source_sequence_only",
        "revision_capability": "terminal history; earlier vintages and publication times unknown",
        "missing_row": "unknown; never inferred to be suspended",
        "response_limit": 6000,
        "response_limit_rule": "reject response at or above limit; no offset pagination declared",
        "coverage_claim": "bounded response below cap; no independently verified historical completeness",
    }


def _aware(value: Any, label: str) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise DataError(f"{label} must be an ISO timestamp with timezone") from exc
    else:
        raise DataError(f"{label} must be an ISO timestamp with timezone")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DataError(f"{label} must include a timezone")
    return parsed.isoformat()


def _rows(payload: bytes) -> list[dict[str, Any]]:
    if not isinstance(payload, bytes):
        raise DataError("Raw payload must be bytes")
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, ValueError) as exc:
        raise DataError("Raw payload must be valid JSON") from exc
    if isinstance(document, list):
        records = document
    elif isinstance(document, dict) and set(document) == {"fields", "rows"}:
        fields, records = document["fields"], document["rows"]
        if (not isinstance(fields, list) or not fields or
                any(not isinstance(f, str) or not f for f in fields) or
                len(fields) != len(set(fields)) or not isinstance(records, list)):
            raise DataError("Raw table fields/rows are invalid")
        if all(isinstance(row, list) for row in records):
            if any(len(row) != len(fields) for row in records):
                raise DataError("Raw table row width differs from fields")
            records = [dict(zip(fields, row)) for row in records]
        elif any(not isinstance(row, dict) for row in records):
            raise DataError("Raw table rows must be consistently arrays or objects")
    else:
        raise DataError("Raw payload must be a list of objects or {fields,rows}")
    if any(not isinstance(row, dict) for row in records):
        raise DataError("Raw rows must be objects")
    return records


def _field_specs(contract: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    fields = contract.get("fields")
    if not isinstance(fields, Mapping) or not fields:
        raise DataError("contract.fields must be a nonempty field mapping")
    if any(not isinstance(name, str) or not isinstance(spec, Mapping)
           for name, spec in fields.items()):
        raise DataError("invalid contract field specification")
    return dict(fields)


@lru_cache(maxsize=32768)
def _date8_iso(value: str) -> str:
    return date(int(value[:4]), int(value[4:6]), int(value[6:8])).isoformat()


def _date(value: Any, date_format: str | None, name: str) -> str:
    if isinstance(value, datetime):
        raise DataError(f"{name} must be a date without time")
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        raise DataError(f"{name} must be a date string")
    try:
        if date_format == "YYYYMMDD":
            if not _DATE8.fullmatch(value):
                raise ValueError
            return _date8_iso(value)
        if date_format in (None, "ISO"):
            return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise DataError(f"{name} has invalid date value {value!r}") from exc
    raise DataError(f"unsupported declared date format: {date_format!r}")


def _typed(value: Any, dtype: str, name: str, date_format: str | None) -> Any:
    if value is None:
        return None
    if dtype in ("string", "utf8"):
        if not isinstance(value, str):
            raise DataError(f"{name} must be a string")
        return value
    if dtype == "date":
        return _date(value, date_format, name)
    if dtype in ("timestamp", "datetime"):
        return _aware(value, name)
    if dtype == "bool":
        if not isinstance(value, bool):
            raise DataError(f"{name} must be a boolean")
        return value
    if dtype in ("int64", "float64"):
        if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
            raise DataError(f"{name} must be numeric")
        try:
            number = Decimal(str(value))
        except InvalidOperation as exc:
            raise DataError(f"{name} must be numeric") from exc
        if not number.is_finite():
            raise DataError(f"{name} must be finite")
        if dtype == "int64":
            if number != number.to_integral_value():
                raise DataError(f"{name} must be an integer after unit conversion")
            integer = int(number)
            if not -(2**63) <= integer < 2**63:
                raise DataError(f"{name} exceeds int64 range")
            return integer
        result = float(number)
        if not math.isfinite(result):
            raise DataError(f"{name} exceeds float64 range")
        return result
    raise DataError(f"unsupported dtype for {name}: {dtype!r}")


def _canonical_value(name: str, raw: Mapping[str, Any], spec: Mapping[str, Any],
                     profile: Mapping[str, Any]) -> Any:
    field_map = profile.get("field_map")
    if not isinstance(field_map, Mapping):
        raise DataError("source profile requires explicit field_map")
    source = field_map.get(name)
    if source is None:
        if not spec.get("nullable", True):
            raise DataError(f"required field {name} has no source mapping")
        return None
    if not isinstance(source, str) or not source:
        raise DataError(f"invalid mapping for {name}")
    if source not in raw:
        if not spec.get("nullable", True):
            raise DataError(f"required source field {source} is absent")
        return None
    value = raw[source]
    null_values = profile.get("null_values", {})
    if not isinstance(null_values, Mapping):
        raise DataError("null_values must be a mapping")
    if name in null_values:
        sentinels = null_values[name]
        if not isinstance(sentinels, (list, tuple)):
            raise DataError(f"invalid declared null values for {name}")
        if value in sentinels:
            value = None
    value_maps = profile.get("value_maps", {})
    if not isinstance(value_maps, Mapping):
        raise DataError("value_maps must be a mapping")
    if name in value_maps and value is not None:
        mapping = value_maps[name]
        if not isinstance(mapping, Mapping) or not isinstance(value, (str, int, bool)):
            raise DataError(f"invalid declared value map for {name}")
        lookup = str(value)
        if lookup not in mapping:
            raise DataError(f"unmapped source value for {name}: {value!r}")
        value = mapping[lookup]
    if name == "security_id" and source != "security_id":
        if (profile.get("id") == "tushare.local.index_daily.v1" and
                profile.get("identity_policy") == "qualified_index_code"):
            if not isinstance(value, str) or not _INDEX_CODE.fullmatch(value):
                raise DataError(f"invalid qualified index identity {value!r}")
        else:
            identities = profile.get("identity_map")
            if not isinstance(identities, Mapping) or value not in identities:
                raise DataError(f"unmapped source security identity {value!r}")
            value = identities[value]
    elif name == "security_id" and source == "security_id":
        # A canonical ID supplied directly is allowed; source ticker equivalence is not.
        if not isinstance(value, str) or not value.strip():
            raise DataError("security_id must be a stable nonempty identifier")
    if spec.get("unit") is not None:
        source_units = profile.get("source_units")
        if not isinstance(source_units, Mapping) or source not in source_units:
            raise DataError(f"source unit for {source} is undeclared")
        original_unit = source_units[source]
        target_unit = spec["unit"]
        if original_unit != target_unit:
            conversions = profile.get("unit_conversions")
            conversion = conversions.get(name) if isinstance(conversions, Mapping) else None
            if (not isinstance(conversion, Mapping) or conversion.get("from") != original_unit
                    or conversion.get("to") != target_unit):
                raise DataError(f"no declared unit conversion for {name}")
            try:
                factor = Decimal(str(conversion["factor"]))
                if value is not None:
                    value = Decimal(str(value)) * factor
            except (KeyError, InvalidOperation, TypeError) as exc:
                raise DataError(f"invalid unit conversion for {name}") from exc
            if not factor.is_finite() or factor <= 0:
                raise DataError(f"invalid unit factor for {name}")
    date_formats = profile.get("date_formats", {})
    if not isinstance(date_formats, Mapping):
        raise DataError("date_formats must be a mapping")
    converted = _typed(value, str(spec.get("dtype")), name, date_formats.get(name))
    if converted is None and not spec.get("nullable", True):
        raise DataError(f"required canonical field {name} is null")
    return converted


def normalize_batch(batch: IngestBatch, raw_record: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize one persisted Raw observation without source I/O.

    ``raw_record`` is the result of ``LocalStore.write_raw`` and supplies its
    immutable ``batch_id``. Source public time is copied only with an explicit
    revision-bound ``evidence_ref``; the profile release assumption is separate.
    """
    if batch.normalizer not in ("records_v1", "tushare_daily_v1", "tushare_index_weight_v1",
                                "tushare_suspend_d_v1", "event_records_v1", "public_document_v1", "etf_records_v1"):
        raise DataError(f"unsupported normalizer: {batch.normalizer!r}")
    if (not isinstance(raw_record, dict) or not isinstance(raw_record.get("batch_id"), str)
            or not raw_record["batch_id"]):
        raise DataError("normalization requires a persisted Raw batch_id")
    if not isinstance(batch.source_profile, Mapping):
        raise DataError("source_profile must be a mapping")
    if batch.normalizer == "tushare_daily_v1" and batch.source_profile.get("id") != "tushare.daily.local.v1":
        raise DataError("tushare_daily_v1 requires its declared source profile")
    observed_at = _aware(batch.observed_at, "observed_at")
    if raw_record.get("observed_at") is not None:
        raw_observed = _aware(raw_record["observed_at"], "Raw observed_at")
        if datetime.fromisoformat(raw_observed) != datetime.fromisoformat(observed_at):
            raise DataError("Raw observation time differs from IngestBatch")
    specs = _field_specs(batch.contract)
    key = batch.contract.get("logical_key")
    if (not isinstance(key, (tuple, list)) or not key or
            any(not isinstance(k, str) or k not in specs for k in key)):
        raise DataError("contract.logical_key is invalid")
    field_map = batch.source_profile.get("field_map")
    if not isinstance(field_map, Mapping):
        raise DataError("source profile requires field_map")
    if batch.normalizer == "public_document_v1":
        from .public_evidence import document_assertions
        raw_rows = document_assertions(batch.payload)
    elif batch.normalizer == "etf_records_v1":
        from .provider_etf import prepare_source_rows
        raw_rows = prepare_source_rows(batch)
    elif batch.normalizer == "event_records_v1":
        from .event_sources import prepare_event_rows
        raw_rows = prepare_event_rows(batch)
    elif "canonical_symbols" in batch.request or batch.request.get("request_strategy") == "trading_day_market_v2":
        from .provider_local import prepare_source_rows
        raw_rows = prepare_source_rows(batch)
    elif batch.normalizer == "tushare_index_weight_v1":
        from .provider_local import normalize_membership_payload
        raw_rows = _rows(normalize_membership_payload(batch.payload))
    elif batch.normalizer == "tushare_suspend_d_v1":
        from .provider_local import normalize_suspension_payload
        raw_rows = _rows(normalize_suspension_payload(batch.payload))
    else:
        raw_rows = _rows(batch.payload)
    result: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for raw in raw_rows:
        row = {name: _canonical_value(name, raw, spec, batch.source_profile)
               for name, spec in specs.items() if name not in _META}
        if any(row.get(k) is None for k in key):
            raise DataError("canonical logical key cannot contain null")
        revision_source = field_map.get("revision_id")
        if revision_source is not None and revision_source in raw:
            revision_id = _typed(raw[revision_source], "string", "revision_id", None)
            if not revision_id:
                raise DataError("revision_id cannot be blank")
        else:
            material = {"key": [row[k] for k in key], "values": row,
                        "source_profile_id": batch.source_profile.get("id")}
            encoded = json.dumps(material, sort_keys=True, ensure_ascii=False,
                                 separators=(",", ":"), allow_nan=False).encode("utf-8")
            revision_id = "sha256:" + hashlib.sha256(encoded).hexdigest()
        row["revision_id"] = revision_id
        sequence_source = field_map.get("revision_sequence")
        row["revision_sequence"] = (_typed(raw[sequence_source], "int64", "revision_sequence", None)
                                     if sequence_source is not None and sequence_source in raw else None)
        row["first_observed_at"] = observed_at
        row["raw_batch_id"] = raw_record["batch_id"]
        available_source = field_map.get("source_available_at")
        evidence_source = field_map.get("evidence_ref")
        available = raw.get(available_source) if available_source else None
        evidence = raw.get(evidence_source) if evidence_source else None
        if (available is None) != (evidence is None):
            raise DataError("source public time requires revision-bound evidence_ref")
        row["source_available_at"] = _aware(available, "source_available_at") if available is not None else None
        row["evidence_ref"] = _typed(evidence, "string", "evidence_ref", None) if evidence is not None else None
        if row["evidence_ref"] == "":
            raise DataError("evidence_ref cannot be blank")
        identity = tuple(row[k] for k in key) + (revision_id,)
        if identity in seen:
            raise DataError("duplicate logical key and revision in one Raw batch")
        seen.add(identity)
        result.append(row)
    return result


def _response_table(response: Any) -> tuple[list[str], list[list[Any]]]:
    if isinstance(response, Mapping) and set(response) == {"fields", "rows"}:
        fields = response["fields"]
        rows = response["rows"]
    elif isinstance(response, list):
        if any(not isinstance(row, Mapping) for row in response):
            raise DataError("Tushare response rows must be objects")
        fields = list(dict.fromkeys(k for row in response for k in row)) or list(_DAILY_FIELDS)
        rows = [[row.get(field) for field in fields] for row in response]
    elif hasattr(response, "to_json"):
        # A DataFrame is already an SDK-decoded object; record that serialization
        # in the profile and retain every returned column without canonical edits.
        try:
            split = json.loads(response.to_json(orient="split", force_ascii=False,
                                                date_format="iso", double_precision=15))
            fields, rows = split["columns"], split["data"]
        except (TypeError, ValueError, KeyError, AttributeError) as exc:
            raise DataError("Tushare response cannot be serialized") from exc
    else:
        raise DataError("Tushare client must return a table or list of records")
    if (not isinstance(fields, list) or any(not isinstance(f, str) or not f for f in fields)
            or len(fields) != len(set(fields)) or not isinstance(rows, list)
            or any(not isinstance(row, list) or len(row) != len(fields) for row in rows)):
        raise DataError("Tushare response table is malformed")
    return fields, rows


def collect_tushare_daily(client: Any, *, request: Mapping[str, Any],
                          identity_map: Mapping[str, str], observed_at: datetime) -> IngestBatch:
    """Call an injected client once and return its still-raw daily response.

    A response below the declared 6,000-row cap establishes only that this
    response did not hit the cap, not that the supplier returned every fact.
    At the cap we fail closed; this endpoint has no declared offset pagination.
    """
    if not isinstance(request, Mapping):
        raise DataError("Tushare daily request must be a mapping")
    if "params" in request:
        if request.get("endpoint", "daily") != "daily" or not isinstance(request["params"], Mapping):
            raise DataError("Tushare daily request endpoint/params are invalid")
        params = dict(request["params"])
    else:
        params = dict(request)
    if not params or ("trade_date" not in params and
                      not ("start_date" in params and "end_date" in params)):
        raise DataError("Tushare daily request requires trade_date or bounded start/end dates")
    for name in ("trade_date", "start_date", "end_date"):
        if name in params:
            _date(params[name], "YYYYMMDD", name)
    if "start_date" in params and "end_date" in params and params["start_date"] > params["end_date"]:
        raise DataError("start_date must not exceed end_date")
    if any(key in params for key in ("fields", "offset", "limit")):
        raise DataError("fields and pagination parameters are fixed by daily profile")
    profile = tushare_daily_profile(identity_map)
    query = getattr(client, "query", None)
    if not callable(query):
        raise DataError("injected client must provide query(endpoint, fields, **params)")
    observed = _aware(observed_at, "observed_at")
    response = query("daily", fields=",".join(_DAILY_FIELDS), **params)
    fields, rows = _response_table(response)
    if not set(profile["field_map"].values()).issubset(fields):
        raise DataError("Tushare daily response lacks required mapped columns")
    if len(rows) >= profile["response_limit"]:
        raise DataError("Tushare daily response reached 6000-row cap; split the request")
    index = {field: fields.index(field) for field in ("ts_code", "trade_date")}
    for row in rows:
        code, session = row[index["ts_code"]], row[index["trade_date"]]
        if code not in profile["identity_map"]:
            raise DataError(f"Tushare response contains unmapped identity {code!r}")
        if "ts_code" in params and code != params["ts_code"]:
            raise DataError("Tushare response lies outside requested security")
        if "trade_date" in params and session != params["trade_date"]:
            raise DataError("Tushare response lies outside requested session")
        if "start_date" in params and session < params["start_date"]:
            raise DataError("Tushare response precedes requested start_date")
        if "end_date" in params and session > params["end_date"]:
            raise DataError("Tushare response follows requested end_date")
    # List/dict responses keep their original row shape, fields, and nulls. SDK
    # DataFrames have no wire bytes, so retain all decoded columns and values.
    source_document = (response if isinstance(response, (list, Mapping))
                       else {"fields": fields, "rows": rows})
    try:
        payload = json.dumps(source_document, ensure_ascii=False,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise DataError("Tushare response is not strict JSON") from exc
    frozen_request = {
        "endpoint": "daily", "params": params, "fields": list(_DAILY_FIELDS),
        "returned_rows": len(rows), "response_below_cap": True,
        "coverage_status": "unverified_bounded_observation",
    }
    return IngestBatch("market_daily", payload, frozen_request,
                       TUSHARE_DAILY_CONTRACT, profile,
                       datetime.fromisoformat(observed), "tushare_daily_v1")
