"""Market domain contract access."""

from __future__ import annotations

from typing import Any

from axiom_data.contracts import MARKET_CONTRACT_VERSIONS, load_contract


MARKET_DOMAINS = ("trading_calendar", "security_master", "market_daily")


def market_contracts() -> dict[str, dict[str, Any]]:
    contracts = [load_contract(version) for version in MARKET_CONTRACT_VERSIONS]
    return {contract["domain"]: contract for contract in contracts}


__all__ = ["MARKET_CONTRACT_VERSIONS", "MARKET_DOMAINS", "market_contracts"]
