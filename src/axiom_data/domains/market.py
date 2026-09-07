"""Market v1 contract access and pure row-level contract checks."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from axiom_data.contracts import MARKET_CONTRACT_VERSIONS, load_contract


MARKET_DOMAINS = ("trading_calendar", "security_master", "market_daily")
_EXCHANGES = frozenset({"SSE", "SZSE"})
_SYMBOL = re.compile(r"(?P<code>[0-9]{6})\.(?P<suffix>SH|SZ)\Z")
_SUFFIX_EXCHANGE = {"SH": "SSE", "SZ": "SZSE"}


class MarketContractError(ValueError):
    """Raised when an in-memory row contradicts a market v1 contract."""


def market_contracts() -> dict[str, dict[str, Any]]:
    contracts = [load_contract(version) for version in MARKET_CONTRACT_VERSIONS]
    return {contract["domain"]: contract for contract in contracts}


def _rows(domain: str, rows: object, version: str = "v1") -> tuple[Mapping[str, object], ...]:
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
        raise MarketContractError(f"{domain} rows must be a sequence")
    try:
        contract = load_contract(f"{domain}.{version}")
    except ValueError as exc:
        raise MarketContractError(f"unknown canonical domain {domain!r}") from exc
    expected_fields = tuple(field["name"] for field in contract["fields"])
    expected_set = set(expected_fields)
    frozen: list[Mapping[str, object]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise MarketContractError(f"{domain} row {index} must be a mapping")
        try:
            actual_fields = set(row)
        except TypeError as exc:
            raise MarketContractError(f"{domain} row {index} has invalid field names") from exc
        if actual_fields != expected_set:
            raise MarketContractError(f"{domain} row {index} must match the exact v1 schema")
        frozen.append(row)

    return tuple(frozen)


def _validate_keys(domain: str, rows: Sequence[Mapping[str, object]], version: str = "v1") -> None:
    """Check identity and ordering after domain key types have been validated."""

    try:
        contract = load_contract(f"{domain}.{version}")
    except ValueError as exc:
        raise MarketContractError(f"unknown canonical domain {domain!r}") from exc
    primary_key = contract["primary_key"]
    keys = [tuple(row[name] for name in primary_key) for row in rows]
    try:
        unique_keys = set(keys)
    except TypeError as exc:
        raise MarketContractError(f"{domain} primary key values have invalid types") from exc
    if len(keys) != len(unique_keys):
        raise MarketContractError(f"{domain} primary keys must be unique")
    sort_order = contract["sort_order"]
    ordered_keys = [tuple(row[name] for name in sort_order) for row in rows]
    try:
        sorted_keys = sorted(ordered_keys)
    except TypeError as exc:
        raise MarketContractError(f"{domain} sort key values have invalid types") from exc
    if ordered_keys != sorted_keys:
        raise MarketContractError(f"{domain} rows must follow the declared sort order")


def _date(name: str, value: object, *, nullable: bool = False) -> date | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise MarketContractError(f"{name} must be an ISO-8601 date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise MarketContractError(f"{name} must be an ISO-8601 date") from exc
    if parsed.isoformat() != value:
        raise MarketContractError(f"{name} must use canonical ISO-8601 form")
    return parsed


def _exchange(value: object) -> str:
    if not isinstance(value, str) or value not in _EXCHANGES:
        raise MarketContractError("exchange must be SSE or SZSE")
    return value


def _symbol(value: object, exchange: object | None = None) -> str:
    if not isinstance(value, str):
        raise MarketContractError("symbol must be a canonical string")
    match = _SYMBOL.fullmatch(value)
    if match is None:
        raise MarketContractError("symbol must be six digits followed by .SH or .SZ")
    if exchange is not None and _SUFFIX_EXCHANGE[match.group("suffix")] != _exchange(exchange):
        raise MarketContractError("symbol suffix must match exchange")
    return value


def _number(name: str, value: object, *, integer: bool = False) -> int | float | None:
    if value is None:
        return None
    expected = int if integer else (int, float)
    if isinstance(value, bool) or not isinstance(value, expected):
        raise MarketContractError(f"{name} has the wrong numeric type")
    if not math.isfinite(value) or value < 0:
        raise MarketContractError(f"{name} must be finite and non-negative")
    return value


def validate_trading_calendar_rows(rows: object) -> None:
    """Validate explicit open/closed dates and nearest-open predecessors."""

    frozen = _rows("trading_calendar", rows)
    parsed_rows: list[tuple[str, date, bool, date | None]] = []
    for index, row in enumerate(frozen):
        exchange = _exchange(row["exchange"])
        session = _date(f"row {index} session", row["session"])
        assert session is not None
        is_open = row["is_open"]
        if not isinstance(is_open, bool):
            raise MarketContractError("is_open must be boolean")
        predecessor = _date(
            f"row {index} previous_open_session",
            row["previous_open_session"],
            nullable=True,
        )
        parsed_rows.append((exchange, session, is_open, predecessor))

    _validate_keys("trading_calendar", frozen)

    sessions_by_exchange: dict[str, list[date]] = {}
    for exchange, session, _, _ in parsed_rows:
        sessions_by_exchange.setdefault(exchange, []).append(session)
    for exchange, sessions in sessions_by_exchange.items():
        first = min(sessions)
        last = max(sessions)
        expected = [
            first + timedelta(days=offset)
            for offset in range((last - first).days + 1)
        ]
        if sorted(sessions) != expected:
            raise MarketContractError(
                f"trading_calendar coverage for {exchange} must contain every calendar date "
                "between its artifact boundaries"
            )

    previous_open: dict[str, date] = {}
    for exchange, session, is_open, predecessor in parsed_rows:
        if predecessor != previous_open.get(exchange):
            raise MarketContractError(
                "previous_open_session must be the nearest earlier open session "
                "for the same exchange, or null at the artifact left boundary"
            )
        if is_open:
            previous_open[exchange] = session


def validate_security_master_rows(rows: object) -> None:
    """Validate canonical identity syntax and its half-open date bounds."""

    frozen = _rows("security_master", rows)
    for index, row in enumerate(frozen):
        _symbol(row["symbol"], row["exchange"])
        list_session = _date(
            f"row {index} list_session", row["list_session"], nullable=True
        )
        delist_session = _date(
            f"row {index} delist_session", row["delist_session"], nullable=True
        )
        if list_session is not None and delist_session is not None:
            if delist_session < list_session:
                raise MarketContractError("delist_session must not precede list_session")
        if not isinstance(row["status"], str) or not row["status"]:
            raise MarketContractError("status must be a non-empty source/current observation")
    _validate_keys("security_master", frozen)


def security_identity_state(row: Mapping[str, object], session: str) -> str:
    """Interpret identity bounds only; this is not historical PIT eligibility."""

    validate_security_master_rows([row])
    target = _date("session", session)
    start = _date("list_session", row["list_session"], nullable=True)
    end = _date("delist_session", row["delist_session"], nullable=True)
    assert target is not None
    if start is None:
        return "unknown"
    if target < start:
        return "not_yet_listed"
    if end is not None and target >= end:
        return "delisted"
    return "within_identity_interval"


def validate_market_daily_rows(rows: object) -> None:
    """Validate exact normal-trading and confirmed-suspension row shapes."""

    numeric_fields = (
        "open",
        "high",
        "low",
        "close",
        "pre_close",
        "volume_shares",
        "amount_cny",
        "adj_factor",
        "up_limit",
        "down_limit",
        "turnover_rate",
        "total_market_cap_cny",
        "circulating_market_cap_cny",
    )
    frozen = _rows("market_daily", rows)
    for index, row in enumerate(frozen):
        _date(f"row {index} session", row["session"])
        _symbol(row["symbol"])
        for name in numeric_fields:
            _number(name, row[name], integer=name == "volume_shares")
        if row["adj_factor"] is not None and row["adj_factor"] <= 0:  # type: ignore[operator]
            raise MarketContractError("adj_factor must be strictly positive")
        suspended = row["is_suspended"]
        if not isinstance(suspended, bool):
            raise MarketContractError("is_suspended must be boolean")

        ohlc = tuple(row[name] for name in ("open", "high", "low", "close"))
        if suspended:
            if any(value is not None for value in ohlc):
                raise MarketContractError("confirmed suspended rows must have null OHLC")
            if row["volume_shares"] != 0 or row["amount_cny"] != 0:
                raise MarketContractError(
                    "confirmed suspended rows must have zero volume_shares and amount_cny"
                )
            if row["turnover_rate"] not in (None, 0, 0.0):
                raise MarketContractError("confirmed suspended turnover_rate must be zero or null")
            continue

        required = ohlc + (row["pre_close"], row["volume_shares"], row["amount_cny"])
        if any(value is None for value in required):
            raise MarketContractError(
                "normal trading rows require OHLC, pre_close, volume_shares, and amount_cny"
            )
        open_price, high, low, close = ohlc
        if high < max(open_price, low, close):  # type: ignore[type-var]
            raise MarketContractError("high is below another OHLC value")
        if low > min(open_price, high, close):  # type: ignore[type-var]
            raise MarketContractError("low is above another OHLC value")
    _validate_keys("market_daily", frozen)


def market_daily_observation_state(
    row: Mapping[str, object] | None,
    *,
    identity_state: str = "unknown",
) -> str:
    """Classify one explicit row or absence without guessing from null prices."""

    if row is None:
        if identity_state in {"not_yet_listed", "delisted"}:
            return identity_state
        return "unknown"
    validate_market_daily_rows([row])
    return "confirmed_suspended" if row["is_suspended"] else "normal_trading"


__all__ = [
    "MARKET_CONTRACT_VERSIONS",
    "MARKET_DOMAINS",
    "MarketContractError",
    "market_contracts",
    "market_daily_observation_state",
    "security_identity_state",
    "validate_market_daily_rows",
    "validate_security_master_rows",
    "validate_trading_calendar_rows",
]
