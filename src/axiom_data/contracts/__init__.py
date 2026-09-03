"""Versioned, packaged data contracts."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any


TRADING_CALENDAR_CONTRACT_VERSION = "trading_calendar.v1"
SECURITY_MASTER_CONTRACT_VERSION = "security_master.v1"
MARKET_DAILY_CONTRACT_VERSION = "market_daily.v1"
MARKET_CONTRACT_VERSIONS = (
    TRADING_CALENDAR_CONTRACT_VERSION,
    SECURITY_MASTER_CONTRACT_VERSION,
    MARKET_DAILY_CONTRACT_VERSION,
)
_CONTRACT_FILES = {version: f"{version}.json" for version in MARKET_CONTRACT_VERSIONS}


def load_contract(contract_version: str) -> dict[str, Any]:
    """Load one explicit contract version; aliases and fallback are forbidden."""

    try:
        filename = _CONTRACT_FILES[contract_version]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"unknown contract version: {contract_version!r}") from exc
    contract = json.loads(files(__package__).joinpath(filename).read_text(encoding="utf-8"))
    if contract.get("contract_version") != contract_version:
        raise ValueError(f"packaged contract identity mismatch: {contract_version}")
    return contract


__all__ = [
    "MARKET_CONTRACT_VERSIONS",
    "MARKET_DAILY_CONTRACT_VERSION",
    "SECURITY_MASTER_CONTRACT_VERSION",
    "TRADING_CALENDAR_CONTRACT_VERSION",
    "load_contract",
]
