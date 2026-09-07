"""Canonical market v1 domain contracts."""

from .market import (
    MARKET_DOMAINS,
    MarketContractError,
    market_contracts,
    market_daily_observation_state,
    security_identity_state,
    validate_market_daily_rows,
    validate_security_master_rows,
    validate_trading_calendar_rows,
)
from .dm1 import (
    ALL_CANONICAL_DOMAINS,
    DM1_REFERENCE_DOMAINS,
    DM1_SNAPSHOT_DOMAINS,
    PIT_QUALIFICATIONS,
    validate_dm1_snapshot_rows,
)

__all__ = [
    "ALL_CANONICAL_DOMAINS",
    "DM1_REFERENCE_DOMAINS",
    "DM1_SNAPSHOT_DOMAINS",
    "MARKET_DOMAINS",
    "MarketContractError",
    "PIT_QUALIFICATIONS",
    "market_contracts",
    "market_daily_observation_state",
    "security_identity_state",
    "validate_market_daily_rows",
    "validate_security_master_rows",
    "validate_trading_calendar_rows",
    "validate_dm1_snapshot_rows",
]

from .pr6 import PR6_DOMAINS
PR6_SNAPSHOT_DOMAINS = DM1_SNAPSHOT_DOMAINS + PR6_DOMAINS
ALL_CANONICAL_DOMAINS = PR6_SNAPSHOT_DOMAINS
