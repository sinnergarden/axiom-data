"""Reference-domain contracts and explicit cross-domain checks."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .market import (
    MARKET_DOMAINS,
    MarketContractError,
    _date,
    _number,
    _rows,
    _symbol,
    _validate_keys,
    _checked_security_identity_state,
    validate_security_master_rows,
    validate_market_daily_rows,
)


REFERENCE_DOMAINS = (
    "security_status",
    "price_limits",
    "corporate_actions",
    "adjustment_factors",
    "benchmark_daily",
    "security_capital",
)
REFERENCE_SNAPSHOT_DOMAINS = MARKET_DOMAINS + REFERENCE_DOMAINS
ALL_CANONICAL_DOMAINS = REFERENCE_SNAPSHOT_DOMAINS
PIT_QUALIFICATIONS = frozenset({"verified", "observed", "best_effort", "unknown"})
_PIT_STRENGTH = {"unknown": 0, "best_effort": 1, "observed": 2, "verified": 3}
REVISION_SPECIFIC_PUBLIC_EVIDENCE = "revision_specific_public_evidence"
FIRST_OBSERVATION_EVIDENCE = "first_observation"
TERMINAL_HISTORY_EVIDENCE = "terminal_history_observed"
_INDEX = re.compile(r"[0-9]{6}\.(SH|SZ)\Z")
_STATUS_REASON = {
    "normal_active": "daily_observation",
    "suspended": "explicit_full_day_suspension",
    "not_yet_listed": "identity_not_effective",
    "delisted": "delisting_effective",
    "unknown_source_gap": "source_gap",
}


def _text(name: str, value: object, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value:
        raise MarketContractError(f"{name} must be non-empty text")
    return value


def _timestamp(name: str, value: object, *, nullable: bool = False) -> str | None:
    text = _text(name, value, nullable=nullable)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketContractError(f"{name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise MarketContractError(f"{name} must include a UTC offset")
    return text


def _provenance(row: Mapping[str, object]) -> None:
    source_available = _timestamp(
        "source_available_at", row["source_available_at"], nullable=True
    )
    _timestamp("first_observed_at", row["first_observed_at"])
    basis = _text("availability_basis", row["availability_basis"])
    qualification = _text("pit_qualification", row["pit_qualification"])
    if qualification not in PIT_QUALIFICATIONS:
        raise MarketContractError("pit_qualification is not canonical")
    if qualification == "verified":
        raise MarketContractError(
            "VERIFIED_EVIDENCE_UNAVAILABLE: reference has no typed SourceEvidence artifact"
        )
    if qualification == "observed" and (
        source_available is not None or basis != FIRST_OBSERVATION_EVIDENCE
    ):
        raise MarketContractError(
            "observed PIT must be supported only by first observation"
        )
    if qualification == "best_effort" and (
        source_available is not None or basis != TERMINAL_HISTORY_EVIDENCE
    ):
        raise MarketContractError(
            "best_effort PIT requires explicit terminal-history observation semantics"
        )
    _text("source_ref", row["source_ref"])


def weakest_pit_qualification(rows: Sequence[Mapping[str, Any]]) -> str:
    """Return the weakest qualification actually present in consumed rows."""

    qualifications = [row.get("pit_qualification") for row in rows]
    if not qualifications:
        return "unknown"
    if any(value not in PIT_QUALIFICATIONS for value in qualifications):
        raise MarketContractError("consumed rows have invalid PIT qualification")
    return min(qualifications, key=_PIT_STRENGTH.__getitem__)  # type: ignore[arg-type]


def validate_strict_decision_time(
    rows: Sequence[Mapping[str, Any]], cutoff_session: str
) -> str:
    """Prove availability through the inclusive Asia/Shanghai cutoff day.

    This date-only contract does not grant intraday decision eligibility.
    """

    cutoff = _date("decision cutoff", cutoff_session)
    for row in rows:
        _provenance(row)
        qualification = row.get("pit_qualification")
        if qualification in {"best_effort", "unknown"}:
            raise MarketContractError(
                "best-effort or unknown historical facts cannot support strict decision time"
            )
        timestamp_name = "first_observed_at"
        timestamp = _timestamp(timestamp_name, row.get(timestamp_name))
        assert timestamp is not None
        if datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(ZoneInfo("Asia/Shanghai")).date() > cutoff:
            raise MarketContractError(
                f"{timestamp_name} is later than the strict decision cutoff"
            )
    return weakest_pit_qualification(rows)


def validate_security_status_rows(rows: object) -> None:
    frozen = _rows("security_status", rows)
    for index, row in enumerate(frozen):
        _date(f"row {index} session", row["session"])
        _symbol(row["symbol"])
        status = _text("status", row["status"])
        if status not in _STATUS_REASON or row["reason"] != _STATUS_REASON[status]:
            raise MarketContractError("security status/reason combination is invalid")
        _provenance(row)
    _validate_keys("security_status", frozen)


def validate_price_limits_rows(rows: object) -> None:
    frozen = _rows("price_limits", rows)
    for index, row in enumerate(frozen):
        _date(f"row {index} session", row["session"])
        _symbol(row["symbol"])
        state = _text("limit_state", row["limit_state"])
        upper = _number("upper_limit", row["upper_limit"])
        lower = _number("lower_limit", row["lower_limit"])
        if state == "limited":
            if upper is None or lower is None or lower <= 0 or upper <= lower:
                raise MarketContractError("limited rows require positive ordered bounds")
        elif state in {"no_limit", "unknown"}:
            if upper is not None or lower is not None:
                raise MarketContractError("no_limit/unknown rows require null bounds")
        else:
            raise MarketContractError("limit_state is not canonical")
        _text("rule_ref", row["rule_ref"])
        if row['rule_ref'] == 'zero_limit_pair.v1' and state != 'unknown':
            raise MarketContractError('zero source limits are unknown, not tradable bounds or no-limit evidence')
        _provenance(row)
    _validate_keys("price_limits", frozen)


def validate_adjustment_factor_rows(rows: object) -> None:
    frozen = _rows("adjustment_factors", rows)
    for index, row in enumerate(frozen):
        _date(f"row {index} session", row["session"])
        _symbol(row["symbol"])
        factor = _number("factor", row["factor"])
        if factor is None or factor <= 0:
            raise MarketContractError("adjustment factor must be strictly positive")
        _provenance(row)
    _validate_keys("adjustment_factors", frozen)


def validate_benchmark_daily_rows(rows: object) -> None:
    frozen = _rows("benchmark_daily", rows)
    for index, row in enumerate(frozen):
        _date(f"row {index} session", row["session"])
        benchmark = row["benchmark"]
        if not isinstance(benchmark, str) or _INDEX.fullmatch(benchmark) is None:
            raise MarketContractError("benchmark must be a canonical exchange-qualified code")
        close = _number("close", row["close"])
        if close is None:
            raise MarketContractError("benchmark close must be present")
        _provenance(row)
    _validate_keys("benchmark_daily", frozen)


def validate_security_capital_rows(rows: object, version=None) -> None:
    version = version or ('v2' if isinstance(rows, Sequence) and any(isinstance(row, Mapping) and 'source_conflict' in row for row in rows) else 'v1')
    frozen = _rows("security_capital", rows, version)
    for index, row in enumerate(frozen):
        _date(f"row {index} session", row["session"])
        _symbol(row["symbol"])
        total = _number("total_shares", row["total_shares"])
        circulating = _number("circulating_shares", row["circulating_shares"])
        if version == 'v2':
            conflict = row['source_conflict']
            if row['missing_reason'] == 'source_capital_conflict':
                if total is not None or circulating is not None or not isinstance(conflict, Mapping) or set(conflict) != {'profile', 'total_share', 'float_share'} or conflict['profile'] != 'capital_conflict.v1':
                    raise MarketContractError('capital conflict requires null values and source evidence')
                source_total = _number('source total_share', conflict['total_share'])
                source_float = _number('source float_share', conflict['float_share'])
                if source_total is None or source_float is None or not 0 < source_total < source_float:
                    raise MarketContractError('capital conflict evidence is not inconsistent')
                _provenance(row)
                continue
            if row['missing_reason'] is not None or conflict is not None:
                raise MarketContractError('unexpected capital qualification')
        if total is None or circulating is None or total <= 0 or circulating <= 0:
            raise MarketContractError("share counts must be strictly positive")
        if circulating > total:
            raise MarketContractError("circulating shares must not exceed total shares")
        _provenance(row)
    _validate_keys("security_capital", frozen, version)


def validate_corporate_action_rows(rows: object, version=None) -> None:
    version=version or ('v2' if isinstance(rows,Sequence) and any(isinstance(row,Mapping) and 'observation_state' in row for row in rows) else 'v1')
    frozen = _rows("corporate_actions", rows,version)
    for index, row in enumerate(frozen):
        _symbol(row["symbol"])
        _text("action_id", row["action_id"])
        _text("action_version", row["action_version"])
        action_type = _text("action_type", row["action_type"])
        for name in (
            "announcement_date",
            "record_date",
            "ex_date",
            "payment_date",
            "share_available_date",
        ):
            _date(f"row {index} {name}", row[name], nullable=True)
        _date(f"row {index} effective_date", row["effective_date"],nullable=version in {'v2','v3'})
        terms = {
            "cash_dividend": "cash_per_share",
            "stock_dividend": "stock_ratio",
            "capital_transfer": "transfer_ratio",
            "split": "split_ratio",
            "consolidation": "split_ratio",
        }
        unresolved=version in {'v2','v3'} and action_type=='unresolved'
        if action_type not in terms and not unresolved:
            raise MarketContractError("corporate action type is unsupported")
        values = {
            name: _number(name, row[name])
            for name in ("cash_per_share", "stock_ratio", "transfer_ratio", "split_ratio")
        }
        if version in {'v2','v3'}:
            expected='unresolved_terms' if unresolved else 'undated_action' if row['effective_date'] is None else 'dated_action'
            if row['observation_state']!=expected or row['ex_date']!=row['effective_date']:
                raise MarketContractError('corporate action observation state mismatch')
        if unresolved:
            if any(value is not None for value in values.values()):
                raise MarketContractError('unresolved action must not invent economic terms')
            _provenance(row)
            continue
        selected = terms[action_type]
        if values[selected] is None or values[selected] <= 0:  # type: ignore[operator]
            raise MarketContractError("corporate action term must be positive")
        if any(value is not None for name, value in values.items() if name != selected):
            raise MarketContractError("corporate action row must contain one economic term")
        ratio = values["split_ratio"]
        if action_type == "split" and ratio is not None and ratio <= 1:
            raise MarketContractError("split ratio must exceed one")
        if action_type == "consolidation" and ratio is not None and ratio >= 1:
            raise MarketContractError("consolidation ratio must be below one")
        _provenance(row)
    _validate_keys("corporate_actions", frozen,version)


def validate_reference_snapshot_rows(
    commits: Mapping[str, Any],
) -> None:
    """Validate reference relationships without inventing a generic rule engine."""

    validate_security_master_rows(commits['security_master'].rows)

    calendar = {
        (row["exchange"], row["session"]): row
        for row in commits["trading_calendar"].rows
    }
    securities = {row["symbol"]: row for row in commits["security_master"].rows}
    market = {
        (row["session"], row["symbol"]): row for row in commits["market_daily"].rows
    }

    def checked_session_symbol(row: Mapping[str, Any], *, open_only: bool = True) -> None:
        symbol = row["symbol"]
        identity = securities.get(symbol)
        if identity is None:
            raise MarketContractError(f"{symbol!r} has no security identity")
        exchange = identity["exchange"]
        cal = calendar.get((exchange, row["session"]))
        if cal is None or (open_only and cal["is_open"] is not True):
            raise MarketContractError("reference fact refers to an invalid calendar session")
        state = _checked_security_identity_state(identity, row["session"])
        if state != "within_identity_interval":
            raise MarketContractError("reference fact is outside its security identity interval")

    for row in commits["security_status"].rows:
        symbol = row["symbol"]
        identity = securities.get(symbol)
        if identity is None:
            raise MarketContractError("security status has no security identity")
        cal = calendar.get((identity["exchange"], row["session"]))
        if cal is None or cal["is_open"] is not True:
            raise MarketContractError("security status refers to an invalid calendar session")
        identity_state = _checked_security_identity_state(identity, row["session"])
        expected_lifecycle = {
            "not_yet_listed": "not_yet_listed",
            "delisted": "delisted",
        }
        if row["status"] in expected_lifecycle:
            if identity_state != expected_lifecycle[row["status"]]:
                raise MarketContractError("security lifecycle status conflicts with identity")
        elif identity_state != "within_identity_interval":
            raise MarketContractError("active security status is outside identity interval")
        observed = market.get((row["session"], row["symbol"]))
        if row["status"] == "normal_active" and (
            observed is None or observed["is_suspended"] is not False
        ):
            raise MarketContractError("normal status has no normal market evidence")
        if row["status"] == "suspended" and (
            observed is None or observed["is_suspended"] is not True
        ):
            raise MarketContractError("suspended status has no explicit suspended market row")
        if row["status"] == "unknown_source_gap" and observed is not None:
            raise MarketContractError("unknown source gap conflicts with a market row")

    for row in commits["price_limits"].rows:
        checked_session_symbol(row)
        legacy = market.get((row["session"], row["symbol"]))
        if row["limit_state"] == "limited" and legacy is not None and (
            legacy["up_limit"] != row["upper_limit"]
            or legacy["down_limit"] != row["lower_limit"]
        ):
            raise MarketContractError("formal price limits conflict with market_daily evidence")

    for row in commits["adjustment_factors"].rows:
        checked_session_symbol(row)
        legacy = market.get((row["session"], row["symbol"]))
        if legacy is not None and legacy["adj_factor"] != row["factor"]:
            raise MarketContractError("formal adjustment factor conflicts with market_daily")

    for row in commits["security_capital"].rows:
        checked_session_symbol(row)

    for row in commits["corporate_actions"].rows:
        identity = securities.get(row["symbol"])
        if identity is None:
            raise MarketContractError("corporate action has no security identity")
        if row.get('observation_state','dated_action')!='dated_action':
            continue
        exchange_dates = sorted(
            session for exchange, session in calendar if exchange == identity["exchange"]
        )
        for name in ("announcement_date", "record_date", "effective_date", "ex_date"):
            value = row[name]
            if value is None:
                continue
            state = _checked_security_identity_state(identity, value)
            if state not in {"within_identity_interval", "unknown"}:
                raise MarketContractError("corporate action date is outside security lifecycle")
            if (
                exchange_dates
                and exchange_dates[0] <= value <= exchange_dates[-1]
                and (identity["exchange"], value) not in calendar
            ):
                raise MarketContractError("corporate action date is outside calendar coverage")

    for row in commits["benchmark_daily"].rows:
        exchange = "SSE" if row["benchmark"].endswith(".SH") else "SZSE"
        cal = calendar.get((exchange, row["session"]))
        if cal is None or cal["is_open"] is not True:
            raise MarketContractError("benchmark row is not on an open calendar session")


DOMAIN_VALIDATORS = {
    "security_status": validate_security_status_rows,
    "price_limits": validate_price_limits_rows,
    "corporate_actions": validate_corporate_action_rows,
    "adjustment_factors": validate_adjustment_factor_rows,
    "benchmark_daily": validate_benchmark_daily_rows,
    "security_capital": validate_security_capital_rows,
}


__all__ = [
    "ALL_CANONICAL_DOMAINS",
    "REFERENCE_DOMAINS",
    "REFERENCE_SNAPSHOT_DOMAINS",
    "DOMAIN_VALIDATORS",
    "PIT_QUALIFICATIONS",
    "FIRST_OBSERVATION_EVIDENCE",
    "REVISION_SPECIFIC_PUBLIC_EVIDENCE",
    "TERMINAL_HISTORY_EVIDENCE",
    "validate_reference_snapshot_rows",
    "validate_strict_decision_time",
    "weakest_pit_qualification",
]


# Compatibility exports for historical callers.
