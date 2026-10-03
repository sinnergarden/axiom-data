"""Small public contracts for the local, immutable data path."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from math import isfinite
from types import MappingProxyType
from typing import Any, Mapping


class DataError(Exception):
    """A local data operation failed without publishing a new snapshot."""


class QueryError(DataError):
    """A query is invalid or cannot be answered by this reader."""


class CoverageError(DataError):
    """Required source or domain coverage is unavailable."""


class ConflictError(DataError):
    """An operation conflicts with already persisted immutable state."""


@dataclass(frozen=True)
class QuerySpec:
    """Explicit daily scope and knowledge boundary for a concrete Snapshot.

    Sessions use ISO dates; every session has a timezone-aware cutoff. Fields
    and symbols keep the caller's output order. The Snapshot is supplied to
    Data.read separately. Membership additionally requires universe_id.
    """
    domain: str
    fields: tuple[str, ...]
    symbols: tuple[str, ...]
    sessions: tuple[str, ...]
    pit_policy: str
    cutoff_by_session: Mapping[str, datetime | str]
    purpose: str = "decision_facts"
    price_basis: str = "unadjusted"
    adjustment_anchor: str | None = None
    universe_id: str | None = None
    policy_by_session: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        for name in ("fields", "symbols", "sessions"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        object.__setattr__(self, "cutoff_by_session", MappingProxyType(dict(self.cutoff_by_session)))
        if self.policy_by_session is not None:
            object.__setattr__(self, "policy_by_session", MappingProxyType(dict(self.policy_by_session)))


@dataclass(frozen=True)
class EventQuery:
    """PIT-selected native events in inclusive economic date range [start, end].

    time_field declares the economic date (e.g. report_period/effective_date),
    not publication time. Select revisions before range/filter projection.
    filters binds explicit endpoint/report_type or other contract fields.
    """
    domain: str
    fields: tuple[str, ...]
    symbols: tuple[str, ...]
    start: str
    end: str
    cutoff: datetime | str
    pit_policy: str
    time_field: str
    filters: Mapping[str, Any] = field(default_factory=dict)
    purpose: str = "decision_facts"

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", tuple(self.fields))
        object.__setattr__(self, "symbols", tuple(self.symbols))
        object.__setattr__(self, "filters", MappingProxyType(dict(self.filters)))


def _json_safe(value: Any) -> Any:
    """Convert common DataFrame scalars to strict JSON values."""
    if value is None:
        return None
    if type(value).__module__.startswith(("pandas", "numpy")) and str(value) in ("NaT", "nan", "<NA>"):
        return None
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value) if value.is_finite() else None
    if isinstance(value, float):
        return value if isfinite(value) else None
    # pandas and numpy expose scalar conversion without requiring either here.
    if type(value).__module__.startswith(("pandas", "numpy")):
        if hasattr(value, "item"):
            return _json_safe(value.item())
        if hasattr(value, "isoformat"):
            return value.isoformat()
    return value


@dataclass(frozen=True)
class DataBatch:
    """A keyed table plus field provenance and the actual query identity.

    Consumers retain context when saving an experiment or producing UI JSON.
    Missing facts are null with a reason, never synthetic zero prices.
    """
    frame: Any
    field_meta: Mapping[str, Any]
    context: Mapping[str, Any]

    def to_json(self) -> dict[str, Any]:
        """Return records and metadata suitable for strict JSON serialization."""
        records = self.frame.to_dict(orient="records")
        return {
            "records": _json_safe(records),
            "field_meta": _json_safe(self.field_meta),
            "context": _json_safe(self.context),
        }

    def to_response(self) -> dict[str, Any]:
        """Serialize a fresh API response with an ephemeral generation clock.

        `to_json` preserves reproducible semantic content for saved references;
        this envelope adds response time without changing cache/query identity,
        source freshness or any PIT timestamp. UI may add its own envelope clock.
        """
        response = self.to_json()
        response["context"]["generated_at"] = datetime.now(timezone.utc).isoformat()
        return response


@dataclass(frozen=True)
class IngestBatch:
    """One source response and the exact contract used to interpret its bytes.

    observed_at is actual receipt time, not a vendor publication assumption.
    update persists the payload before attempting canonical normalization.
    """
    domain: str
    payload: bytes
    request: Mapping[str, Any]
    contract: Mapping[str, Any]
    source_profile: Mapping[str, Any]
    observed_at: datetime | str
    normalizer: str = "records_v1"


@dataclass(frozen=True)
class UpdateRequest:
    """An idempotent operation whose ID binds its full input and configuration.

    Use a new operation_id for a new fetch, even when source bytes repeat.
    promote=False builds a branch Snapshot without replacing current.
    """
    batches: tuple[IngestBatch, ...]
    operation_id: str
    build_context: Mapping[str, Any]
    promote: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "batches", tuple(self.batches))


@dataclass(frozen=True)
class OperationResult:
    """Successful operation receipt; failures raise without publishing success."""
    snapshot_id: str
    changed: bool
    operation_id: str
    issues: tuple[Any, ...] = field(default_factory=tuple)
    status: str = "success"
