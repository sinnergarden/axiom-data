"""Tushare market source adapter and canonical mapper."""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from importlib.resources import files
from pathlib import Path
from typing import Any

from axiom_data.artifacts import (
    ArtifactConflictError,
    ArtifactError,
    MarketDomainBuilder,
    RawBatch,
    RawBatchRef,
    _RAW_BATCH_WRITE_SCHEMA,
    _digest,
    _json_bytes,
    _json_copy,
    load_raw_batch,
    write_raw_batch,
)


_PROFILE_NAME = "tushare_phase1.v1.json"
_SYMBOL = re.compile(r"[0-9]{6}\.(SH|SZ)\Z")
_PROFILE_PIT_FIELDS = (
    "retrieved_at",
    "represented_session",
    "historical_availability",
    "revision_capability",
)
_PROFILE_ENDPOINT_FIELDS = (
    "source_profile_ref",
    "primary_key",
    "represented_session_field",
    "fields",
    "units",
    "missing_row",
    "terminal_history",
    "canonical_mapping",
)


def load_tushare_source_profile() -> dict[str, Any]:
    """Return the frozen endpoint semantics used by this adapter."""

    content = files("axiom_data.source_profiles").joinpath(_PROFILE_NAME).read_bytes()
    profile = json.loads(content)
    if not isinstance(profile, dict) or profile.get("profile_version") != "tushare_phase1.v1":
        raise ArtifactError("packaged Tushare source profile is invalid")
    return profile


def _normalized_source_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    pit = profile.get("pit_classification")
    endpoints = profile.get("endpoints")
    if not isinstance(pit, Mapping) or not isinstance(endpoints, Mapping):
        raise ArtifactError("Tushare source profile semantics are invalid")
    try:
        return _json_copy(
            {
                "profile_version": profile["profile_version"],
                "pit_classification": {name: pit[name] for name in _PROFILE_PIT_FIELDS},
                "endpoints": {
                    endpoint: {
                        name: definition[name] for name in _PROFILE_ENDPOINT_FIELDS
                    }
                    for endpoint, definition in endpoints.items()
                },
            }
        )
    except (KeyError, TypeError) as exc:
        raise ArtifactError("Tushare source profile semantics are incomplete") from exc


def tushare_source_profile_digest(
    profile: Mapping[str, Any] | None = None,
) -> str:
    """Digest only the fields that can change source interpretation."""

    selected = load_tushare_source_profile() if profile is None else profile
    return _digest(_json_bytes(_normalized_source_profile(selected)))


def _endpoint_profile(
    endpoint: str, profile: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    selected = load_tushare_source_profile() if profile is None else profile
    endpoints = selected.get("endpoints")
    if not isinstance(endpoints, dict) or endpoint not in endpoints:
        raise ArtifactError(f"unsupported Tushare endpoint: {endpoint!r}")
    profile = endpoints[endpoint]
    if not isinstance(profile, dict):
        raise ArtifactError("Tushare endpoint profile is invalid")
    return profile


def _retrieved_at(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat()
    if not isinstance(value, str):
        raise ArtifactError("retrieved_at must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ArtifactError("retrieved_at must be an ISO-8601 string") from exc
    if parsed.tzinfo is None:
        raise ArtifactError("retrieved_at must include a UTC offset")
    return value


def _response_records(response: object) -> list[dict[str, Any]]:
    if isinstance(response, list):
        records = response
    elif hasattr(response, "to_json"):
        try:
            records = json.loads(
                response.to_json(  # type: ignore[union-attr]
                    orient="records", force_ascii=False, double_precision=15
                )
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ArtifactError("Tushare response cannot be serialized") from exc
    else:
        raise ArtifactError("Tushare client must return tabular records")
    if any(not isinstance(row, Mapping) for row in records):
        raise ArtifactError("Tushare response rows must be objects")
    return [_json_copy(dict(row)) for row in records]


class TushareCollector:
    """Collect one allow-listed response and immediately freeze it as RawBatch."""

    implementation_revision = "tushare-collector.v3"

    def __init__(self, data_root: str | Path, client: object | None = None) -> None:
        self.data_root = Path(data_root)
        self.client = client

    def _client(self) -> object:
        if self.client is not None:
            return self.client
        try:
            import tushare as ts
        except ImportError as exc:  # pragma: no cover - live-only optional dependency
            raise ArtifactError("live collection requires the optional tushare package") from exc
        token = os.environ.get("TUSHARE_TOKEN")
        return ts.pro_api(token) if token else ts.pro_api()

    def collect(
        self,
        endpoint: str,
        params: Mapping[str, Any],
        *,
        retrieved_at: str | None = None,
    ) -> RawBatchRef:
        source_profile = load_tushare_source_profile()
        profile = _endpoint_profile(endpoint, source_profile)
        profile_digest = tushare_source_profile_digest(source_profile)
        from axiom_data.source_completeness import source_profile_completeness_binding
        completeness = source_profile_completeness_binding(source_profile['profile_version'], profile_digest)
        if not isinstance(params, Mapping):
            raise ArtifactError("Tushare request params must be a mapping")
        request_params = _json_copy(dict(params))
        fields = profile.get("fields")
        if not isinstance(fields, list) or any(not isinstance(field, str) for field in fields):
            raise ArtifactError("Tushare endpoint fields are invalid")
        client = self._client()
        query = getattr(client, "query", None)
        if not callable(query):
            raise ArtifactError("Tushare client must provide query(endpoint, ...)")
        response = query(endpoint, fields=",".join(fields), **request_params)
        records = _response_records(response)
        payload = _json_bytes(records)
        observed_at = _retrieved_at(retrieved_at)
        request = {
            "endpoint": endpoint,
            "params": request_params,
            "fields": fields,
        }
        identity_seed = {
            "source_completeness": completeness,
            "source_profile_ref": profile["source_profile_ref"],
            "source_profile_version": source_profile["profile_version"],
            "source_profile_digest": profile_digest,
            "collector_code_ref": f"axiom-data.{self.implementation_revision}",
            "request": request,
            "retrieved_at": observed_at,
            "payload_digest": _digest(payload),
        }
        raw_batch_id = _tushare_raw_batch_id(
            endpoint, _RAW_BATCH_WRITE_SCHEMA, identity_seed
        )
        pit = source_profile["pit_classification"]
        return write_raw_batch(
            self.data_root,
            raw_batch_id,
            domain=_endpoint_domain(endpoint),
            source_profile=profile["source_profile_ref"],
            source_profile_version=source_profile["profile_version"],
            source_profile_digest=profile_digest,
            request=request,
            retrieved_at=observed_at,
            payload=payload,
            collector_code=f"axiom-data.{self.implementation_revision}",
            summary={
                "source_completeness": completeness,
                "rows": len(records),
                "response_fields": fields,
                "represented_session_field": profile["represented_session_field"],
                "historical_availability": pit["historical_availability"],
                "revision_capability": pit["revision_capability"],
            },
        )


def _tushare_raw_batch_id(
    endpoint: str,
    schema_version: str,
    identity_fields: Mapping[str, Any],
) -> str:
    identity_seed = {
        "artifact_type": "raw_batch",
        "schema_version": schema_version,
        **identity_fields,
    }
    return (
        f"tushare-{endpoint}-"
        f"{_digest(_json_bytes(identity_seed)).removeprefix('sha256:')}"
    )


def _endpoint_domain(endpoint: str) -> str:
    if endpoint == "trade_cal":
        return "trading_calendar"
    if endpoint == "stock_basic":
        return "security_master"
    if endpoint in {"daily", "adj_factor", "daily_basic", "stk_limit", "suspend_d"}:
        return "market_daily"
    raise ArtifactError(f"unsupported Tushare endpoint: {endpoint!r}")


def _source_date(value: object, *, nullable: bool = False) -> str | None:
    if value in (None, "") and nullable:
        return None
    if not isinstance(value, str):
        raise ArtifactError("Tushare date must be YYYYMMDD text")
    compact = value.replace("-", "")
    try:
        parsed = datetime.strptime(compact, "%Y%m%d").date()
    except ValueError as exc:
        raise ArtifactError(f"invalid Tushare date: {value!r}") from exc
    return parsed.isoformat()


def _source_symbol(value: object) -> str:
    if not isinstance(value, str) or not _SYMBOL.fullmatch(value):
        raise ArtifactError(f"invalid Tushare security code: {value!r}")
    return value


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ArtifactError("Tushare numeric field must be a number or null")
    number = float(value)
    if not math.isfinite(number):
        raise ArtifactError("Tushare numeric field must be finite")
    return number


def _scaled_float(value: object, multiplier: int) -> float | None:
    number = _optional_float(value)
    return None if number is None else float(Decimal(str(number)) * multiplier)


def _volume_shares(value: object) -> int:
    number = _optional_float(value)
    if number is None:
        raise ArtifactError("Tushare daily vol must be present")
    scaled = Decimal(str(number)) * 100
    if scaled != scaled.to_integral_value():
        raise ArtifactError("Tushare daily vol does not convert to whole shares")
    return int(scaled)


def _raw_endpoint_rows(raw_batches: Sequence[RawBatch]) -> dict[str, list[dict[str, Any]]]:
    source_profile = load_tushare_source_profile()
    profile_digest = tushare_source_profile_digest(source_profile)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for raw in raw_batches:
        if raw.manifest.get("schema_version") != "raw_batch.v2":
            raise ArtifactError(
                "Tushare builder requires profile-bound raw_batch.v2 input"
            )
        request = raw.manifest.get("request")
        endpoint = request.get("endpoint") if isinstance(request, dict) else None
        if not isinstance(endpoint, str):
            raise ArtifactError("Tushare RawBatch has no endpoint identity")
        profile = _endpoint_profile(endpoint, source_profile)
        if (
            raw.manifest.get("source_profile_ref") != profile.get("source_profile_ref")
            or raw.manifest.get("source_profile_version")
            != source_profile.get("profile_version")
            or raw.manifest.get("source_profile_digest") != profile_digest
        ):
            raise ArtifactError("Tushare RawBatch source profile mismatch")
        try:
            rows = json.loads(raw.payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArtifactError("Tushare RawBatch payload is not JSON") from exc
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ArtifactError("Tushare RawBatch payload must contain row objects")
        grouped.setdefault(endpoint, []).extend(rows)
    return grouped


def _source_table(
    endpoint: str,
    rows: Sequence[Mapping[str, Any]],
) -> dict[tuple[Any, ...], dict[str, Any]]:
    primary_key = _endpoint_profile(endpoint)["primary_key"]
    table: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        try:
            key = tuple(row[name] for name in primary_key)
            hash(key)
        except (KeyError, TypeError) as exc:
            raise ArtifactError(f"Tushare {endpoint} row has an invalid primary key") from exc
        copied = _json_copy(dict(row))
        if key in table and table[key] != copied:
            raise ArtifactConflictError(f"Tushare {endpoint} contains conflicting key {key!r}")
        table[key] = copied
    return table


def _merge_canonical(
    contract: Mapping[str, Any],
    parent_rows: Sequence[Mapping[str, Any]],
    new_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    key_fields = contract["primary_key"]
    rows: dict[tuple[Any, ...], dict[str, Any]] = {}
    for source, values in (("parent", parent_rows), ("Tushare", new_rows)):
        for row in values:
            copied = _json_copy(dict(row))
            key = tuple(copied[name] for name in key_fields)
            if key in rows and rows[key] != copied:
                raise ArtifactConflictError(f"{source} conflicts at canonical key {key!r}")
            rows[key] = copied
    return sorted(
        rows.values(), key=lambda row: tuple(row[name] for name in contract["sort_order"])
    )


class TushareMarketBuilder(MarketDomainBuilder):
    """Map the versioned market source profiles through the canonical publisher."""

    implementation_revision = "tushare-market-builder.v2"

    def __init__(
        self,
        data_root: str | Path,
        domain: str,
        *,
        builder_config: Mapping[str, Any] | None = None,
        commit_id: str | None = None,
        calendar_commit_id: str | None = None,
        security_master_commit_id: str | None = None,
        created_at: str | None = None,
    ) -> None:
        source_profile = load_tushare_source_profile()
        profile_version = source_profile.get("profile_version")
        config = dict(builder_config or {})
        if domain!='market_daily' and set(config)&{'session_suspension_policy','market_source_partitioning'}:
            raise ArtifactError('market-only source mapping configuration')
        from axiom_data.session_suspension import bind_profile
        bind_profile(config)
        configured_version = config.get("source_profile_version")
        if configured_version not in (None, profile_version):
            raise ArtifactError("Tushare builder source profile version mismatch")
        config["source_profile_version"] = profile_version
        config["source_profile_digest"] = tushare_source_profile_digest(source_profile)
        super().__init__(
            data_root,
            domain,
            builder_config=config,
            commit_id=commit_id,
            calendar_commit_id=calendar_commit_id,
            security_master_commit_id=security_master_commit_id,
            created_at=created_at,
        )

    def _scope(self) -> tuple[tuple[str, ...], str, str]:
        symbols = self.builder_config.get("symbols")
        start = self.builder_config.get("start_session")
        end = self.builder_config.get("end_session")
        if (
            not isinstance(symbols, list)
            or not symbols
            or len(symbols) != len(set(symbols))
            or any(
                not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol)
                for symbol in symbols
            )
            or not isinstance(start, str)
            or not isinstance(end, str)
        ):
            raise ArtifactError("Tushare builder requires explicit symbols and session bounds")
        start_date = _source_date(start)
        end_date = _source_date(end)
        assert start_date is not None and end_date is not None
        if start_date > end_date:
            raise ArtifactError("Tushare builder session bounds are reversed")
        return tuple(symbols), start_date, end_date

    def _build_rows(
        self,
        contract: Mapping[str, Any],
        parent_rows: Sequence[Mapping[str, Any]],
        raw_batches: Sequence[RawBatch],
    ) -> list[dict[str, Any]]:
        symbols, start, end = self._scope()
        if self.domain == "trading_calendar":
            previous = {}
            for row in parent_rows:
                if row['is_open'] and row['session'] < start:
                    previous[row['exchange']] = max(previous.get(row['exchange'], ''), row['session'])
            rows = self._calendar_rows(raw_batches, symbols, start, end, previous_open=previous)
        elif self.domain == "security_master":
            grouped = _raw_endpoint_rows(raw_batches)
            rows = self._security_rows(grouped, symbols)
        else:
            grouping=self.builder_config.get('market_source_partitioning')
            if grouping is None:
                chunks=[raw_batches]
            elif grouping=='security.v1':
                chunks_by_symbol={}
                for raw in raw_batches:
                    symbol=raw.manifest.get('request',{}).get('params',{}).get('ts_code')
                    if symbol not in symbols:raise ArtifactError('partitioned market build requires individual scoped security requests')
                    chunks_by_symbol.setdefault(symbol,[]).append(raw.ref.raw_batch_id)
                if set(chunks_by_symbol)!=set(symbols):raise ArtifactError('market source requests do not cover scoped securities')
                from axiom_data.artifacts import RawBatches
                chunks=[RawBatches(self.layout.root,chunks_by_symbol[s]) for s in sorted(chunks_by_symbol)]
            else:raise ArtifactError('unsupported market source partitioning')
            def mapped_rows():
                for chunk in chunks:
                    grouped = _raw_endpoint_rows(chunk)
                    if grouping is not None:
                        expected=chunk[0].manifest['request']['params']['ts_code']
                        if any(r.get('ts_code')!=expected for values in grouped.values() for r in values):
                            raise ArtifactError('market payload differs from individual security request')
                    yield from self._market_rows(grouped, set(symbols), start, end,
                                        partial_halts=self.builder_config.get('session_suspension_policy',False))
            rows=mapped_rows()
        return _merge_canonical(contract, parent_rows, rows)

    @staticmethod
    def _calendar_rows(
        raw_batches: Sequence[RawBatch],
        symbols: Sequence[str],
        start: str,
        end: str,
        *, previous_open: Mapping[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        exchanges = {"SSE" if symbol.endswith(".SH") else "SZSE" for symbol in symbols}
        first = date.fromisoformat(start)
        last = date.fromisoformat(end)
        expected_dates = {
            (first + timedelta(days=offset)).isoformat()
            for offset in range((last - first).days + 1)
        }
        requested_exchanges: set[str] = set()
        source: dict[tuple[str, str], Mapping[str, Any]] = {}
        for raw in raw_batches:
            grouped = _raw_endpoint_rows((raw,))
            if set(grouped) != {"trade_cal"}:
                raise ArtifactError("trading_calendar requires only trade_cal RawBatches")
            request = raw.manifest.get("request")
            if not isinstance(request, dict):
                raise ArtifactError("trade_cal RawBatch request metadata is invalid")
            params = request.get("params")
            if request.get("endpoint") != "trade_cal" or not isinstance(params, dict):
                raise ArtifactError("trade_cal RawBatch request metadata is invalid")
            exchange = params.get("exchange")
            request_start = _source_date(params.get("start_date"))
            request_end = _source_date(params.get("end_date"))
            if (
                exchange not in exchanges
                or request_start != start
                or request_end != end
                or exchange in requested_exchanges
            ):
                raise ArtifactError("trade_cal RawBatch request scope mismatch")
            requested_exchanges.add(exchange)
            actual_dates: set[str] = set()
            for row in grouped["trade_cal"]:
                payload_exchange = row.get("exchange")
                session = _source_date(row.get("cal_date"))
                if payload_exchange != exchange:
                    raise ArtifactError(
                        "trade_cal payload exchange does not match its RawBatch request"
                    )
                if session not in expected_dates:
                    raise ArtifactError(
                        "trade_cal payload is outside the declared request scope"
                    )
                key = (exchange, session)
                if key in source:
                    raise ArtifactError("trade_cal payload has duplicate calendar rows")
                source[key] = row
                actual_dates.add(session)
            if actual_dates != expected_dates:
                raise ArtifactError(
                    "trade_cal payload does not completely cover its RawBatch request scope"
                )
        if requested_exchanges != exchanges:
            raise ArtifactError("trade_cal RawBatches do not cover every scoped exchange")

        values: list[tuple[str, str, bool]] = []
        for row in source.values():
            exchange = row.get("exchange")
            session = _source_date(row.get("cal_date"))
            is_open = row.get("is_open")
            if is_open not in (0, 1, "0", "1"):
                raise ArtifactError("Tushare trade_cal is_open must be 0 or 1")
            values.append((exchange, session, str(is_open) == "1"))
        rows: list[dict[str, Any]] = []
        previous: dict[str, str] = dict(previous_open or {})
        for exchange, session, is_open in sorted(values, key=lambda item: (item[0], item[1])):
            rows.append(
                {
                    "exchange": exchange,
                    "session": session,
                    "is_open": is_open,
                    "previous_open_session": previous.get(exchange),
                }
            )
            if is_open:
                previous[exchange] = session
        return rows

    @staticmethod
    def _security_rows(
        grouped: Mapping[str, Sequence[Mapping[str, Any]]],
        symbols: Sequence[str],
    ) -> list[dict[str, Any]]:
        if set(grouped) != {"stock_basic"}:
            raise ArtifactError("security_master requires only stock_basic RawBatches")
        source = _source_table("stock_basic", grouped["stock_basic"])
        by_symbol = {row.get("ts_code"): row for row in source.values()}
        rows: list[dict[str, Any]] = []
        for symbol in symbols:
            row = by_symbol.get(symbol)
            if row is None:
                raise ArtifactError(f"stock_basic has no scoped identity for {symbol!r}")
            list_status = row.get("list_status")
            if not isinstance(list_status, str) or not list_status:
                raise ArtifactError("stock_basic list_status must be present")
            delist_session = _source_date(row.get("delist_date"), nullable=True)
            if delist_session is not None:
                raise ArtifactError(
                    "unverified Tushare delist_date boundary cannot be promoted"
                )
            rows.append(
                {
                    "symbol": _source_symbol(row.get("ts_code")),
                    "exchange": row.get("exchange"),
                    "list_session": _source_date(row.get("list_date"), nullable=True),
                    "delist_session": delist_session,
                    "status": f"source-current-{list_status}",
                }
            )
        return rows

    @staticmethod
    def _market_rows(
        grouped: Mapping[str, Sequence[Mapping[str, Any]]],
        symbols: set[str],
        start: str,
        end: str,
        *, partial_halts: bool = False,
    ) -> list[dict[str, Any]]:
        allowed = {"daily", "adj_factor", "daily_basic", "stk_limit", "suspend_d"}
        if set(grouped) != allowed:
            raise ArtifactError("market_daily requires all five frozen Tushare endpoints")
        tables = {name: _source_table(name, grouped[name]) for name in allowed}
        daily_evidence={(r['ts_code'],r['trade_date']):r for r in tables['daily'].values()} if partial_halts else {}

        def in_scope(row: Mapping[str, Any]) -> tuple[str, str] | None:
            symbol = _source_symbol(row.get("ts_code"))
            session = _source_date(row.get("trade_date"))
            assert session is not None
            return (symbol, session) if symbol in symbols and start <= session <= end else None

        keyed: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
        for endpoint, table in tables.items():
            keyed[endpoint] = {}
            for row in table.values():
                if endpoint == "suspend_d":
                    if partial_halts=='session_suspension.v3':
                        from axiom_data.session_suspension import qualified_daily_state
                        if qualified_daily_state(row,daily_evidence.get((row.get('ts_code'),row.get('trade_date'))))!='full_day_halt':
                            continue
                    else:
                        if partial_halts:
                            from axiom_data.session_suspension import qualified_partial_halt
                            if qualified_partial_halt(row,daily_evidence.get((row.get('ts_code'),row.get('trade_date'))),policy=partial_halts if isinstance(partial_halts,str) else 'session_suspension.v1'):
                                continue
                        if row.get('suspend_timing') not in (None,''):
                            raise ArtifactError("unsupported non-full-day suspend_timing")
                        if partial_halts=='session_suspension.v2':
                            from axiom_data.session_suspension import is_halt_event
                            if not is_halt_event(row):
                                continue
                key = in_scope(row)
                if key is not None:
                    if key in keyed[endpoint]:
                        raise ArtifactConflictError(
                            f"Tushare {endpoint} has duplicate canonical key {key!r}"
                        )
                    keyed[endpoint][key] = row

        rows: list[dict[str, Any]] = []
        for key, daily in keyed["daily"].items():
            basic = keyed["daily_basic"].get(key, {})
            factor = keyed["adj_factor"].get(key, {})
            limit = keyed["stk_limit"].get(key, {})
            rows.append(
                {
                    "session": key[1],
                    "symbol": key[0],
                    "open": _optional_float(daily.get("open")),
                    "high": _optional_float(daily.get("high")),
                    "low": _optional_float(daily.get("low")),
                    "close": _optional_float(daily.get("close")),
                    "pre_close": _optional_float(daily.get("pre_close")),
                    "volume_shares": _volume_shares(daily.get("vol")),
                    "amount_cny": _scaled_float(daily.get("amount"), 1000),
                    "adj_factor": _optional_float(factor.get("adj_factor")),
                    "up_limit": _optional_float(limit.get("up_limit")),
                    "down_limit": _optional_float(limit.get("down_limit")),
                    "is_suspended": False,
                    "turnover_rate": _optional_float(basic.get("turnover_rate")),
                    "total_market_cap_cny": _scaled_float(basic.get("total_mv"), 10000),
                    "circulating_market_cap_cny": _scaled_float(
                        basic.get("circ_mv"), 10000
                    ),
                }
            )

        for key, suspension in keyed["suspend_d"].items():
            if suspension.get("suspend_type") != "S":
                continue
            if key in keyed["daily"]:
                raise ArtifactConflictError(
                    f"daily and suspend_d both claim market state for {key!r}"
                )
            factor = keyed["adj_factor"].get(key, {})
            limit = keyed["stk_limit"].get(key, {})
            rows.append(
                {
                    "session": key[1],
                    "symbol": key[0],
                    "open": None,
                    "high": None,
                    "low": None,
                    "close": None,
                    "pre_close": None,
                    "volume_shares": 0,
                    "amount_cny": 0.0,
                    "adj_factor": _optional_float(factor.get("adj_factor")),
                    "up_limit": _optional_float(limit.get("up_limit")),
                    "down_limit": _optional_float(limit.get("down_limit")),
                    "is_suspended": True,
                    "turnover_rate": None,
                    "total_market_cap_cny": None,
                    "circulating_market_cap_cny": None,
                }
            )
        return rows


__all__ = [
    "TushareCollector",
    "TushareMarketBuilder",
    "load_tushare_source_profile",
    "tushare_source_profile_digest",
]
