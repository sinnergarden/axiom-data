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
from .reference import (
    ALL_CANONICAL_DOMAINS,
    REFERENCE_DOMAINS,
    REFERENCE_SNAPSHOT_DOMAINS,
    PIT_QUALIFICATIONS,
    validate_reference_snapshot_rows,
)

__all__ = [
    "ALL_CANONICAL_DOMAINS",
    "REFERENCE_DOMAINS",
    "REFERENCE_SNAPSHOT_DOMAINS",
    "MARKET_DOMAINS",
    "MarketContractError",
    "PIT_QUALIFICATIONS",
    "market_contracts",
    "market_daily_observation_state",
    "security_identity_state",
    "validate_market_daily_rows",
    "validate_security_master_rows",
    "validate_trading_calendar_rows",
    "validate_reference_snapshot_rows",
]

from .fundamentals import FUNDAMENTAL_DOMAINS
FUNDAMENTAL_SNAPSHOT_DOMAINS = REFERENCE_SNAPSHOT_DOMAINS + FUNDAMENTAL_DOMAINS
ALL_CANONICAL_DOMAINS = FUNDAMENTAL_SNAPSHOT_DOMAINS

from .events import EVENT_DOMAINS
EVENT_SNAPSHOT_DOMAINS = FUNDAMENTAL_SNAPSHOT_DOMAINS + EVENT_DOMAINS
ALL_CANONICAL_DOMAINS = EVENT_SNAPSHOT_DOMAINS


# Compatibility exports for historical callers.
