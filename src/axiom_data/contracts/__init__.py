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
_CONTRACT_FILES['corporate_actions.v2']='corporate_actions.v2.json'
_CONTRACT_DOMAINS['corporate_actions.v2']='corporate_actions'
_CONTRACT_FILES['security_capital.v2']='security_capital.v2.json'
_CONTRACT_DOMAINS['security_capital.v2']='security_capital'


PR6_CONTRACT_VERSIONS = tuple(name + ".v1" for name in ("financial_events", "valuation_daily", "universe_membership", "industry_membership"))
PR6_CONTRACT_VERSIONS += tuple(v.replace(".v1", ".v2") for v in PR6_CONTRACT_VERSIONS)
PR6_CONTRACT_VERSIONS += ("universe_membership.v3", "industry_membership.v3", "financial_events.v3")
_CONTRACT_FILES.update({v: v + ".json" for v in PR6_CONTRACT_VERSIONS})
_CONTRACT_DOMAINS.update({v: v[:-3] for v in PR6_CONTRACT_VERSIONS})

PR7_CONTRACT_VERSIONS = tuple(d + '.v1' for d in ('holder_count_events','top_holders_reports','margin_daily','moneyflow_daily','forecast_observations'))
_CONTRACT_FILES.update({v:v+'.json' for v in PR7_CONTRACT_VERSIONS})
_CONTRACT_DOMAINS.update({v:v[:-3] for v in PR7_CONTRACT_VERSIONS})
_CONTRACT_FILES['forecast_observations.v2']='forecast_observations.v2.json'
_CONTRACT_DOMAINS['forecast_observations.v2']='forecast_observations'

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


def writable_contracts() -> dict[str, Any]:
    """Explicit publication policy; registered historical contracts stay readable."""
    policy = json.loads(files(__package__).joinpath('writable_contracts.v1.json').read_bytes())
    if policy.get('schema_version') != 'writable_contracts.v1' or set(policy['domains']) != set(_CONTRACT_DOMAINS.values()):
        raise ValueError('writable contract registry is incomplete')
    for domain, versions in policy['domains'].items():
        accepted = {versions['current'], *versions['legacy_read_only']}
        if (versions['current'] in versions['legacy_read_only'] or
                accepted != {v for v, d in _CONTRACT_DOMAINS.items() if d == domain}):
            raise ValueError('writable contract policy does not classify all registered versions')
    return policy


def require_writable_contract(domain: str, contract_version: str) -> None:
    current = writable_contracts()['domains'].get(domain, {}).get('current')
    if contract_version != current:
        raise ValueError(f'LEGACY_CONTRACT_READ_ONLY: {contract_version}; current writable contract is {current}')


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
