"""Versioned, packaged data contracts."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any


TRADING_CALENDAR_CONTRACT_VERSION = "trading_calendar.v1"
SECURITY_MASTER_CONTRACT_VERSION = "security_master.v1"
MARKET_DAILY_CONTRACT_VERSION = "market_daily.v1"
SECURITY_STATUS_CONTRACT_VERSION = "security_status.v1"
PRICE_LIMITS_CONTRACT_VERSION = "price_limits.v1"
CORPORATE_ACTIONS_CONTRACT_VERSION = "corporate_actions.v1"
ADJUSTMENT_FACTORS_CONTRACT_VERSION = "adjustment_factors.v1"
BENCHMARK_DAILY_CONTRACT_VERSION = "benchmark_daily.v1"
SECURITY_CAPITAL_CONTRACT_VERSION = "security_capital.v1"
MARKET_CONTRACT_VERSIONS = (
    TRADING_CALENDAR_CONTRACT_VERSION,
    SECURITY_MASTER_CONTRACT_VERSION,
    MARKET_DAILY_CONTRACT_VERSION,
)
DM1_CONTRACT_VERSIONS = MARKET_CONTRACT_VERSIONS + (
    SECURITY_STATUS_CONTRACT_VERSION,
    PRICE_LIMITS_CONTRACT_VERSION,
    CORPORATE_ACTIONS_CONTRACT_VERSION,
    ADJUSTMENT_FACTORS_CONTRACT_VERSION,
    BENCHMARK_DAILY_CONTRACT_VERSION,
    SECURITY_CAPITAL_CONTRACT_VERSION,
)
_CONTRACT_FILES = {version: f"{version}.json" for version in DM1_CONTRACT_VERSIONS}
_CONTRACT_DOMAINS = {
    TRADING_CALENDAR_CONTRACT_VERSION: "trading_calendar",
    SECURITY_MASTER_CONTRACT_VERSION: "security_master",
    MARKET_DAILY_CONTRACT_VERSION: "market_daily",
    SECURITY_STATUS_CONTRACT_VERSION: "security_status",
    PRICE_LIMITS_CONTRACT_VERSION: "price_limits",
    CORPORATE_ACTIONS_CONTRACT_VERSION: "corporate_actions",
    ADJUSTMENT_FACTORS_CONTRACT_VERSION: "adjustment_factors",
    BENCHMARK_DAILY_CONTRACT_VERSION: "benchmark_daily",
    SECURITY_CAPITAL_CONTRACT_VERSION: "security_capital",
}


def load_contract(contract_version: str) -> dict[str, Any]:
    """Load one explicit contract version; aliases and fallback are forbidden."""

    try:
        filename = _CONTRACT_FILES[contract_version]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"unknown contract version: {contract_version!r}") from exc
    contract = json.loads(files(__package__).joinpath(filename).read_text(encoding="utf-8"))
    if contract.get("contract_version") != contract_version:
        raise ValueError(f"packaged contract identity mismatch: {contract_version}")
    if contract.get("domain") != _CONTRACT_DOMAINS[contract_version]:
        raise ValueError(f"packaged contract domain mismatch: {contract_version}")
    return contract


__all__ = [
    "ADJUSTMENT_FACTORS_CONTRACT_VERSION",
    "BENCHMARK_DAILY_CONTRACT_VERSION",
    "CORPORATE_ACTIONS_CONTRACT_VERSION",
    "DM1_CONTRACT_VERSIONS",
    "MARKET_CONTRACT_VERSIONS",
    "MARKET_DAILY_CONTRACT_VERSION",
    "PRICE_LIMITS_CONTRACT_VERSION",
    "SECURITY_CAPITAL_CONTRACT_VERSION",
    "SECURITY_MASTER_CONTRACT_VERSION",
    "SECURITY_STATUS_CONTRACT_VERSION",
    "TRADING_CALENDAR_CONTRACT_VERSION",
    "load_contract",
]
