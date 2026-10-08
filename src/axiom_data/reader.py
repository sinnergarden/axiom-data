"""Read a fixed local Snapshot without materializing views or mutating storage.

The reader only interprets the inline domain contract and referenced Parquet
objects.  It never resolves ``current`` after construction or replays Raw input.
"""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from datetime import date, datetime, time
from hashlib import sha256
from pathlib import Path
import json
import re
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Any, Mapping

import pandas as pd

from .protocols import DataBatch, QueryError, QuerySpec


READER_VERSION = "local_reader_v5"
_POLICIES = {"operational_pit_v1", "market_pit_safe_v1", "best_effort_vendor_v1"}
_PURPOSES = {
    "decision_facts", "market_replay", "research_label", "label_outcomes",
    "historical_exploration",
}
_VERSION_COLUMNS = (
    "revision_id", "revision_sequence", "first_observed_at", "raw_batch_id",
    "source_available_at", "evidence_ref",
)
_MONTH = re.compile(r"^\d{4}-\d{2}$")
# Charge cache keys, entry tuples and LRU nodes conservatively per entry.
_ENTRY_BYTES = 1024
_INDEX_CHUNK_ROWS = 64


def _instant(value: Any, name: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise QueryError(f"{name} must be an ISO timestamp with timezone") from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise QueryError(f"{name} must be a timezone-aware timestamp")
    return value


def _session(value: Any) -> str:
    if not isinstance(value, str):
        raise QueryError("sessions must use YYYY-MM-DD strings")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise QueryError(f"invalid session: {value!r}") from exc
    if parsed.isoformat() != value:
        raise QueryError(f"session must use YYYY-MM-DD: {value!r}")
    return value


def _date_value(value: Any, name: str) -> date:
    if isinstance(value, datetime):
        raise QueryError(f"{name} must be a session date, not timestamp")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise QueryError(f"{name} must be an ISO session date") from exc
        if parsed.isoformat() == value:
            return parsed
    raise QueryError(f"{name} must be an ISO session date")


def _declared_fields(contract: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    fields = contract.get("fields")
    if not isinstance(fields, Mapping):
        raise QueryError("domain contract requires a fields mapping")
    return dict(fields)


def _release_time(profile: Mapping[str, Any], session: str) -> datetime:
    availability = profile.get("availability")
    if not isinstance(availability, Mapping):
        raise QueryError("best_effort_vendor_v1 requires declared source_profile.availability")
    tz_name = availability.get("timezone")
    clock = availability.get("session_release_time")
    if not isinstance(tz_name, str) or not isinstance(clock, str):
        raise QueryError("best_effort_vendor_v1 requires timezone and session_release_time")
    try:
        zone = ZoneInfo(tz_name)
        local_clock = time.fromisoformat(clock)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise QueryError("invalid source availability timezone or session_release_time") from exc
    if local_clock.tzinfo is not None:
        raise QueryError("session_release_time must be a local wall-clock time")
    return datetime.combine(date.fromisoformat(session), local_clock, zone)


def _row_availability(row: Mapping[str, Any], policy: str, profile: Mapping[str, Any],
                      session: str, vendor_releases: dict[str, datetime] | None = None) -> tuple[datetime, str]:
    observed = row.get("first_observed_at")
    if policy == "best_effort_vendor_v1":
        if vendor_releases is None:
            return _release_time(profile, session), "declared_vendor_assumption"
        release = vendor_releases.get(session)
        if release is None:
            release = _release_time(profile, session)
            vendor_releases[session] = release
        return release, "declared_vendor_assumption"
    if observed is None:
        raise QueryError("strict PIT requires first_observed_at on each revision")
    observed_at = _instant(observed, "first_observed_at")
    if policy == "market_pit_safe_v1" and row.get("source_available_at") is not None and row.get("evidence_ref"):
        return _instant(row["source_available_at"], "source_available_at"), "revision_bound_source_evidence"
    return observed_at, "first_observed_at"


def _revision_order(rows: list[dict[str, Any]], key: str, profile=None) -> dict[str, Any]:
    """Select source sequence, never observation/file order, after PIT filtering."""
    if len(rows) == 1:
        return rows[0]
    if (profile or {}).get("revision_order") == "announcement_day_then_terminal_v1":
        # Different supplier announcement days identify declared report versions.
        # Corrections within one day retain the actual terminal observation order;
        # neither operation turns the supplier date into verified public evidence.
        dated = [(_date_value(row.get("actual_announcement_date"), "actual_announcement_date"), row)
                 for row in rows]
        latest_day = max(day for day, _ in dated)
        return _revision_order([row for day, row in dated if day == latest_day], key,
                               profile={"revision_order": "terminal_observation_v1"})
    if (profile or {}).get("revision_order") == "terminal_observation_v1":
        # Explicit source policy: order observed terminal states, not vendor
        # publication versions. Updates retain A→B→A as distinct occurrences.
        latest = max(_instant(row["first_observed_at"], "first_observed_at") for row in rows)
        winners = [row for row in rows if _instant(row["first_observed_at"], "first_observed_at") == latest]
        if len({row.get("revision_id") for row in winners}) != 1:
            raise QueryError(f"ambiguous terminal observations at identical time for {key}")
        return winners[0]
    sequences: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        seq = row.get("revision_sequence")
        if isinstance(seq, bool) or not isinstance(seq, int):
            raise QueryError(f"multiple visible revisions for {key} require integer revision_sequence")
        sequences.append((seq, row))
    highest = max(seq for seq, _ in sequences)
    winners = [row for seq, row in sequences if seq == highest]
    if len(winners) > 1:
        first = json.dumps(winners[0], sort_keys=True, default=str)
        if any(json.dumps(row, sort_keys=True, default=str) != first for row in winners[1:]):
            raise QueryError(f"conflicting source revisions with equal sequence for {key}")
    return winners[0]


def _policy_for(query: QuerySpec, session: str) -> str:
    return query.policy_by_session[session] if query.pit_policy == "bootstrap_hybrid_v1" else query.pit_policy


def _policy_limitations(query: QuerySpec, profile, fallback_count=0):
    policies = {_policy_for(query, session) for session in query.sessions}
    limitations = []
    if "best_effort_vendor_v1" in policies:
        limitations.append("vendor release time is an assumption, not historical vintage evidence")
    if query.pit_policy == "bootstrap_hybrid_v1":
        limitations.append("explicit mixed-policy segments; this batch is not wholly strict PIT")
    if "market_pit_safe_v1" in policies and fallback_count:
        limitations.append(f"{fallback_count} queried revisions lacked revision-bound public evidence; first_observed_at was used")
    if profile.get("revision_order") == "terminal_observation_v1":
        limitations.append("terminal states ordered by system observation under declared source policy; vendor revision/publication order is unknown")
    return limitations


def _object_size(value: Any, seen: set[int] | None = None) -> int:
    """Count Python containers/scalars once per entry, including shared rows.

    Cross-entry sharing is deliberately charged again. This is an object-size
    bound, not a promise about process RSS or the allocator's retained arenas.
    """
    if seen is None:
        seen = set()
    identity = id(value)
    if identity in seen:
        return 0
    seen.add(identity)
    size = sys.getsizeof(value)
    if isinstance(value, Mapping):
        size += sum(_object_size(k, seen) + _object_size(v, seen) for k, v in value.items())
    elif isinstance(value, (list, tuple, set, frozenset)):
        size += sum(_object_size(item, seen) for item in value)
    return size


def _cache_size(batch: DataBatch, *, limit: int | None = None) -> int:
    frame_size = int(batch.frame.memory_usage(deep=True).sum())
    if limit is not None and frame_size > limit:
        return frame_size
    # Conservatively charge pandas managers, array wrappers and index caches
    # in addition to its reported deep cell storage; metadata uses real objects.
    return (frame_size + 4096 + 1024 * len(batch.frame.columns)
            + sys.getsizeof(batch) + sys.getsizeof(batch.frame.columns)
            + _object_size((batch.field_meta, batch.context)))


class SnapshotQueryReader:
    """Query a fixed Snapshot; results and unselected indexes share one LRU budget.

    Indexes bind partition bytes, projections and evidence, never a cutoff's
    selected answer. Each request selects revisions and membership states anew.
    File verification remains mandatory on hits; no cache files are written.
    """

    def __init__(self, store: Any, snapshot_id: str, *, cache_bytes: int = 67_108_864):
        if not isinstance(cache_bytes, int) or cache_bytes < 0:
            raise ValueError("cache_bytes must be a nonnegative integer")
        if not snapshot_id or snapshot_id == "current":
            raise QueryError("SnapshotQueryReader requires a resolved snapshot ID")
        self.store = store
        self.snapshot_id = snapshot_id
        self._snapshot_path = Path(store.root)/'snapshots'/(snapshot_id+'.json') if hasattr(store,'root') else None
        def stamp():
            if self._snapshot_path is None or not self._snapshot_path.exists(): return None
            s=self._snapshot_path.stat()
            return (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        self._snapshot_mark=stamp()
        self.snapshot = store.load_snapshot(snapshot_id)
        if stamp()!=self._snapshot_mark:
            raise QueryError('Snapshot changed while loading')
        if self.snapshot.get("snapshot_id") != snapshot_id:
            raise QueryError("loaded Snapshot ID does not match requested ID")
        self.cache_bytes = cache_bytes
        self._cache: OrderedDict[str, tuple[Any, int]] = OrderedDict()
        self._cached_bytes = 0
        self.partition_reads = 0
        self.cache_hits = 0
        self.index_cache_hits = 0
        self.index_build_peak_bytes = 0

    def _validate(self, query: QuerySpec) -> tuple[dict[str, Any], dict[str, Mapping[str, Any]], dict[str, datetime]]:
        if not isinstance(query, QuerySpec):
            raise QueryError("read requires a QuerySpec")
        domains = self.snapshot.get("domains") or {}
        if query.domain not in domains:
            raise QueryError(f"required domain {query.domain!r} is absent from Snapshot {self.snapshot_id}")
        domain = domains[query.domain]
        if not isinstance(domain, Mapping):
            raise QueryError("invalid domain manifest")
        if query.pit_policy == "bootstrap_hybrid_v1":
            if not query.policy_by_session or set(query.policy_by_session) != set(query.sessions) or any(p not in _POLICIES for p in query.policy_by_session.values()):
                raise QueryError("bootstrap_hybrid_v1 requires one explicit concrete policy per session")
        elif query.pit_policy not in _POLICIES or query.policy_by_session is not None:
            raise QueryError("unsupported policy or unexpected policy_by_session on a non-hybrid query")
        if query.purpose not in _PURPOSES:
            raise QueryError(f"unsupported query purpose {query.purpose!r}")
        if query.purpose == "market_replay" and query.price_basis != "unadjusted":
            raise QueryError("market replay requires unadjusted prices")
        if query.price_basis != "unadjusted":
            raise QueryError("adjusted prices require a common anchor and revision-bound action/factor-time evidence; this reader currently supports unadjusted prices only")
        if query.adjustment_anchor is not None:
            raise QueryError("adjustment_anchor is only meaningful for adjusted price_basis")
        for label, values in (("fields", query.fields), ("symbols", query.symbols), ("sessions", query.sessions)):
            if not values or len(set(values)) != len(values) or any(not isinstance(v, str) or not v for v in values):
                raise QueryError(f"{label} must be nonempty unique strings")
        sessions = tuple(_session(s) for s in query.sessions)
        if set(query.cutoff_by_session) != set(sessions):
            raise QueryError("cutoff_by_session must provide exactly one cutoff for each requested session")
        cutoffs = {s: _instant(query.cutoff_by_session[s], f"cutoff for {s}") for s in sessions}
        fields = _declared_fields(domain.get("contract") or {})
        logical_key = tuple((domain.get("contract") or {}).get("logical_key") or ())
        if query.domain == "universe_membership":
            if not query.universe_id:
                raise QueryError("universe_membership requires explicit universe_id")
            if query.fields != ("is_member",):
                raise QueryError("universe_membership currently supports only the is_member projection")
            fields["is_member"] = {"dtype": "bool", "unit": None}
        elif query.universe_id is not None:
            raise QueryError("universe_id is only supported for universe_membership")
        elif logical_key != ("security_id", "session"):
            raise QueryError(f"daily reader requires logical_key security_id/session for {query.domain}")
        unknown = [field for field in query.fields if field not in fields]
        if unknown:
            raise QueryError(f"undeclared fields in {query.domain}: {unknown}")
        if any(field in {"security_id", "session"} for field in query.fields):
            raise QueryError("security_id and session are implicit keys; fields selects value columns")
        if any(_policy_for(query, s) == "best_effort_vendor_v1" for s in sessions):
            _release_time(domain.get("source_profile") or {}, sessions[0])
        return dict(domain), fields, cutoffs

    def _cache_key(self, query: QuerySpec, cutoffs: Mapping[str, datetime]) -> str:
        identity = {
            "snapshot_id": self.snapshot_id,
            "domain": query.domain,
            "fields": query.fields,
            "symbols": query.symbols,
            "sessions": query.sessions,
            "pit_policy": query.pit_policy,
            "cutoff_by_session": {s: cutoffs[s].isoformat() for s in query.sessions},
            "purpose": query.purpose,
            "price_basis": query.price_basis,
            "adjustment_anchor": query.adjustment_anchor,
            "universe_id": query.universe_id,
                "policy_by_session": dict(query.policy_by_session) if query.policy_by_session is not None else None,
        }
        return sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()

    def _parts_for_query(self, domain: Mapping[str, Any], query: QuerySpec) -> list[Mapping[str, Any]]:
        wanted_months = {s[:7] for s in query.sessions}
        return [
            part for part in domain.get("partitions") or []
            if query.domain == "universe_membership"
            or not _MONTH.fullmatch(str(part.get("partition", "")))
            or part["partition"] in wanted_months
        ]

    def _columns(self, domain: Mapping[str, Any], query: QuerySpec) -> list[str]:
        membership = query.domain == "universe_membership"
        logical_key = tuple((domain.get("contract") or {}).get("logical_key") or ())
        return list(dict.fromkeys((
            "security_id", "universe_id", "effective_from", "effective_to",
            "membership_id", "logical_event_key", "dependency_raw_batch_ids",
            *logical_key, *_VERSION_COLUMNS,
        ) if membership else ("security_id", "session", *query.fields, *_VERSION_COLUMNS)))

    def _evidence_parts(self, query: QuerySpec) -> list[Mapping[str, Any]]:
        if query.domain == "public_evidence":
            return []
        return (self.snapshot.get("domains", {}).get("public_evidence") or {}).get("partitions") or []

    def _make_room(self, size: int) -> bool:
        """Reserve charged cache/construction space by evicting least-used entries."""
        if size > self.cache_bytes:
            return False
        while self._cache and self._cached_bytes + size > self.cache_bytes:
            _, (_, evicted_size) = self._cache.popitem(last=False)
            self._cached_bytes -= evicted_size
        return True

    def _put_entry(self, key: str, value: Any, size: int) -> bool:
        size += _ENTRY_BYTES
        if not self.cache_bytes or size > self.cache_bytes:
            return False
        previous = self._cache.pop(key, None)
        if previous is not None:
            self._cached_bytes -= previous[1]
        self._make_room(size)
        self._cache[key] = (value, size)
        self._cached_bytes += size
        return True

    def _evidence(self, query: QuerySpec) -> dict:
        """Reuse the original evidence join index; verify its files on every hit."""
        from .public_evidence import evidence_index
        native=getattr(self.store,'_native_evidence_index',None)
        if native is not None:
            return native(query.domain)
        parts = self._evidence_parts(query)
        if not parts or not self.cache_bytes:
            return evidence_index(self.store, self.snapshot, query.domain)
        key = "evidence:" + sha256(json.dumps(
            (self.snapshot_id, query.domain, parts), sort_keys=True).encode()).hexdigest()
        if key in self._cache:
            for part in parts:
                self.store.verify_partition(part)
            self._cache.move_to_end(key)
            return self._cache[key][0]
        index = evidence_index(self.store, self.snapshot, query.domain)
        # The miss is the original, required query join, not an additional
        # preload. Only retain it if its real Python objects fit the shared LRU.
        try:
            self._put_entry(key, index, _object_size(index))
        except (MemoryError, RecursionError):
            pass
        return index

    def _read_rows(self, domain: Mapping[str, Any], query: QuerySpec,
                   parts: list[Mapping[str, Any]] | None = None, *, enrich: bool = True) -> list[dict[str, Any]]:
        """Original session-filter path, also used for inadmissible index candidates."""
        membership = query.domain == "universe_membership"
        columns = self._columns(domain, query)
        rows: list[dict[str, Any]] = []
        selected_parts = self._parts_for_query(domain, query) if parts is None else parts
        for part in selected_parts:
            table = self.store.read_partition(
                part, columns=columns, symbols=query.symbols,
                sessions=None if membership else query.sessions,
            )
            self.partition_reads += 1
            for row in table.to_pylist():
                if not membership and isinstance(row.get("session"), date):
                    row["session"] = row["session"].isoformat()
                rows.append(row)
        if not enrich:
            return rows
        from .public_evidence import apply_evidence
        return apply_evidence(rows, index=self._evidence(query),
                              key_fields=(domain.get("contract") or {}).get("logical_key") or ())

    def _group_key(self, row: Mapping[str, Any], domain: Mapping[str, Any], query: QuerySpec,
                   *, unselected: bool = False):
        if query.domain != "universe_membership":
            return row.get("security_id"), row.get("session")
        universe = row.get("universe_id")
        if not unselected and universe != query.universe_id:
            return None
        event_id = row.get("membership_id") or row.get("logical_event_key")
        if event_id is None:
            key_fields = (domain.get("contract") or {}).get("logical_key") or ()
            if not key_fields or any(row.get(name) is None for name in key_fields):
                if unselected:
                    # Do not introduce an event-key failure before the original
                    # all-universe evidence join has had a chance to fail.
                    return row["security_id"], universe, None
                raise QueryError("membership intervals require stable logical_key or membership_id")
            event_id = tuple(row[name] for name in key_fields)
        if unselected:
            return row["security_id"], universe, str(event_id)
        return row["security_id"], str(event_id)

    def _group_rows(self, rows, domain, query, *, unselected: bool = False) -> dict:
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        symbols, sessions = set(query.symbols), set(query.sessions)
        for row in rows:
            if row.get("security_id") not in symbols:
                continue
            if query.domain != "universe_membership" and row.get("session") not in sessions:
                continue
            key = self._group_key(row, domain, query, unselected=unselected)
            if key is not None:
                groups.setdefault(key, []).append(row)
        return groups

    def _build_index(self, part, domain, query, columns) -> dict | None:
        """Build unselected groups in small chunks, or discard and fall back.

        No Arrow buffers are retained in an admitted index. During construction
        charge all decoded buffers plus four times the current Python graph
        (including object-counting's temporary identity set), and five times a
        conservative chunk conversion bound. The extra factors cover both the
        converted chunk and growth/counting before its actual size is known.
        The identity set is also charged explicitly at its current allocation.
        This bounds optional index construction; required Parquet decoding,
        copied query groups and the evidence join remain query working memory,
        outside cache residency. It does not bound total process RSS.
        """
        table = self.store.read_partition(part, columns=columns, symbols=query.symbols, sessions=None)
        self.partition_reads += 1
        arrow_bytes = table.get_total_buffer_size() + sys.getsizeof(table)
        groups: dict = {}
        size = dict_bytes = sys.getsizeof(groups)
        if not table.num_rows:
            return groups
        seen: set[int] = set()
        symbols = set(query.symbols)
        for offset in range(0, table.num_rows, _INDEX_CHUNK_ROWS):
            chunk = table.slice(offset, _INDEX_CHUNK_ROWS)
            # Supported scalar projections: 32x logical Arrow bytes also covers
            # strings/containers, while per-cell slack covers small scalars.
            chunk_bound = 32 * chunk.nbytes + chunk.num_rows * (1024 + 256 * len(columns)) + 4096
            seen_bytes = sys.getsizeof(seen) + len(seen) * sys.getsizeof((1 << 64) - 1)
            peak = arrow_bytes + max(4 * size, size + seen_bytes) + 5 * chunk_bound + _ENTRY_BYTES
            if not self._make_room(peak):
                return None
            self.index_build_peak_bytes = max(self.index_build_peak_bytes, self._cached_bytes + peak)
            for row in chunk.to_pylist():
                if row.get("security_id") not in symbols:
                    continue
                if query.domain != "universe_membership":
                    if row.get("session") is None:
                        return None  # Preserve the original session-column/filter validation.
                    if isinstance(row["session"], date):
                        row["session"] = row["session"].isoformat()
                key = self._group_key(row, domain, query, unselected=True)
                if key is not None:
                    if key not in groups:
                        groups[key] = []
                        size += _object_size(key, seen) + sys.getsizeof(groups[key])
                    # Count each retained row/key once. Container growth is
                    # charged separately, without rescanning earlier chunks.
                    size += _object_size(row, seen)
                    previous_list_bytes = sys.getsizeof(groups[key])
                    groups[key].append(row)
                    size += sys.getsizeof(groups[key]) - previous_list_bytes
            new_dict_bytes = sys.getsizeof(groups)
            size += new_dict_bytes - dict_bytes
            dict_bytes = new_dict_bytes
        return groups

    def _read_groups(self, domain: Mapping[str, Any], query: QuerySpec) -> dict:
        """Reuse unselected per-partition revisions, then join evidence on query rows only."""
        native=getattr(self.store,'_native_read_groups',None)
        if native is not None:
            return native(domain,query)
        if self.cache_bytes < 4096:
            return self._group_rows(self._read_rows(domain, query), domain, query)
        from .public_evidence import apply_evidence
        columns = self._columns(domain, query)
        groups: dict = {}
        membership = query.domain == "universe_membership"
        for part in self._parts_for_query(domain, query):
            identity = (self.snapshot_id, query.domain, domain.get("contract"), part,
                        sorted(columns), sorted(query.symbols), query.universe_id,
                        self._evidence_parts(query))
            key = "rows:" + sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
            raw = None
            if key in self._cache:
                self.store.verify_partition(part)
                self._cache.move_to_end(key)
                raw = self._cache[key][0]
                if raw is not None:
                    self.index_cache_hits += 1
            elif query.domain == "universe_membership" or _MONTH.fullmatch(str(part.get("partition", ""))):
                try:
                    raw = self._build_index(part, domain, query, columns)
                    self._put_entry(key, raw, _object_size(raw))
                except (MemoryError, RecursionError, TypeError, ValueError, QueryError):
                    # A preload error (including an irrelevant row) must not
                    # add a failure to the original narrow query.
                    raw = None
            if raw is None:
                selected = self._group_rows(self._read_rows(domain, query, [part], enrich=False),
                                           domain, query, unselected=membership)
            else:
                selected = {}
                wanted_keys = (raw if membership else
                               ((symbol, session) for session in query.sessions for symbol in query.symbols))
                for group_key in wanted_keys:
                    revisions = raw.get(group_key)
                    if revisions is None:
                        continue
                    selected[group_key] = [dict(row) for row in revisions]
            for group_key, revisions in selected.items():
                groups.setdefault(group_key, []).extend(revisions)
            # A membership wanted_keys mapping otherwise keeps the entire
            # previous index alive while the next one is built, even after LRU
            # eviction. Only the copied query working set survives this part.
            wanted_keys = ()
            raw = selected = revisions = None
        # Preserve the original order: read/verify every fact partition, then
        # join evidence on its full queried scope, then validate/filter event
        # keys. Membership's original scope includes all queried universes for
        # the selected securities, not just query.universe_id.
        evidence = self._evidence(query)
        for revisions in groups.values():
            apply_evidence(revisions, index=evidence,
                           key_fields=(domain.get("contract") or {}).get("logical_key") or ())
        if membership:
            selected = {}
            for (symbol, universe, event_id), revisions in groups.items():
                if universe != query.universe_id:
                    continue
                if event_id is None:
                    self._group_key(revisions[0], domain, query)  # Original deferred validation error.
                selected[(symbol, event_id)] = revisions
            return selected
        return groups

    def _put_cache(self, key: str, batch: DataBatch) -> DataBatch:
        if not self.cache_bytes:
            return batch
        try:
            size = _cache_size(batch, limit=self.cache_bytes)
        except (MemoryError, RecursionError):
            return batch
        if not self._put_entry(key, batch, size):
            return batch
        return DataBatch(batch.frame.copy(deep=True), deepcopy(batch.field_meta), deepcopy(batch.context))

    def _read_membership(
        self, domain: Mapping[str, Any], declared_fields: Mapping[str, Any],
        cutoffs: Mapping[str, datetime], query: QuerySpec, cache_key: str, *, sink=None,
    ) -> DataBatch:
        """Project versioned positive membership intervals onto requested sessions.

        A negative answer needs a visible, explicit complete group state; missing
        positive intervals alone never prove that a security was not a member.
        """
        profile = domain.get("source_profile") or {}
        contract = domain.get("contract") or {}
        grouped = self._read_groups(domain, query)
        if sink is not None:
            sink.writer.reserve('membership-selection',2*sink.writer.retained.get('source',0))
            complete=(domain.get('coverage') or {}).get('complete_states') or []
            sink.writer.reserve('membership-state',2*_object_size(domain.get('coverage') or {})+len(complete)*4096+
                sum(len(s.get('members') or ())*128 for s in complete if isinstance(s,Mapping))+
                1024*len(query.sessions))
        events: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for (symbol, event_id), revisions in grouped.items():
            events.setdefault(symbol, {})[event_id] = revisions

        complete_states = (domain.get("coverage") or {}).get("complete_states") or []
        if not isinstance(complete_states, list):
            raise QueryError("coverage.complete_states must be a list")
        # Complete groups are shared by all requested securities. Parse their
        # bounds and materialize membership sets once, rather than once per cell.
        states = []
        for state in complete_states:
            if not isinstance(state, Mapping) or state.get("universe_id") != query.universe_id or state.get("complete") is not True:
                continue
            if isinstance(state.get("members"), list):
                members = set(state["members"])
            elif (state.get("member_set_source") == "canonical_intervals_v1"
                  and type(state.get("member_count")) is int and state["member_count"] >= 0):
                # The source builder has closed the full chain and validated
                # its positive intervals. Completeness metadata can refer to
                # those immutable rows without copying every member list into
                # every Snapshot. Requested rows still undergo PIT selection.
                members = None
            else:
                raise QueryError("complete membership state requires explicit members list or declared canonical intervals")
            start = _date_value(state.get("effective_from"), "complete state effective_from")
            stop = state.get("effective_to")
            end = _date_value(stop, "complete state effective_to") if stop is not None else None
            states.append((state, start, end, members))
        state_groups: dict[str, list[tuple]] = {}
        for ordinal, item in enumerate(states):
            state = item[0]
            # Stable source state identity survives corrections to economic
            # dates. Anonymous groups are older explicit disjoint fixtures.
            identity = state.get("state_id") or f"anonymous:{ordinal}"
            state_groups.setdefault(identity, []).append(item)
        fallback_count = sum(
            1 for revisions in grouped.values() for row in revisions
            if not (row.get("source_available_at") is not None and row.get("evidence_ref"))
        ) + sum(
            1 for state in complete_states if isinstance(state, Mapping)
            and state.get("universe_id") == query.universe_id
            and not (state.get("source_available_at") is not None and state.get("evidence_ref"))
        )
        records = [] if sink is None else sink.records
        meta_rows = [] if sink is None else sink.metadata("is_member")
        vendor_releases: dict[str, datetime] = {}
        coverage = domain.get("coverage") or {}
        vendor_carry = profile.get("id") == "tushare.index_weight.dated_membership.v1"
        verified_through = coverage.get("verified_through")
        checkpoints = [
            (_date_value(item["verified_through"], "verified_through"),
             _instant(item["first_observed_at"], "coverage first_observed_at"),
             item.get("dependency_raw_batch_ids") or [])
            for item in coverage.get("verification_checkpoints", [])
        ] if verified_through is not None else []
        dependency_lists: dict[tuple[int, ...], list[str]] = {}
        vendor_interval_cache: dict[tuple[str, str, str, datetime, date], tuple[Any, bool]] = {}

        def dependencies(source, complete, coverage_proof):
            # Thousands of rows often share the same source state. Keep one
            # list for that combination instead of copying its Raw closure
            # for every security and session.
            parts = (
                (source or {}).get("dependency_raw_batch_ids") or (),
                (complete[0] if complete else {}).get("dependency_raw_batch_ids") or (),
                coverage_proof[1] if coverage_proof else (),
            )
            identity = tuple(id(part) for part in parts)
            if identity not in dependency_lists:
                dependency_lists[identity] = list(dict.fromkeys(
                    raw_id for part in parts for raw_id in part))
            return dependency_lists[identity]

        for session in query.sessions:
            session_date = date.fromisoformat(session)
            policy = _policy_for(query, session)
            coverage_proof = None
            if verified_through is not None and not vendor_carry:
                # An unchanged open interval needs no daily fact revision.
                # The archived source window still limits the dates we can
                # answer, and its receipt limits when that coverage was known.
                through = _date_value(verified_through, "verified_through")
                if session_date <= through:
                    if policy == "best_effort_vendor_v1":
                        release = _release_time(profile, session)
                        if release <= cutoffs[session]:
                            coverage_proof = (release, ())
                    else:
                        eligible = [item for item in checkpoints
                                    if session_date <= item[0] and item[1] <= cutoffs[session]]
                        if eligible:
                            selected_proof = min(eligible, key=lambda item: item[1])
                            coverage_proof = (selected_proof[1], selected_proof[2])
            complete = None
            invisible_complete = False
            if vendor_carry:
                # Each supplier state becomes usable at its own dated release
                # or actual receipt. A not-yet-visible new month must leave the
                # prior snapshot in force, even though the final economic
                # interval now has an end at the new snapshot date.
                through = _date_value(verified_through, "verified_through")
                candidates = []
                if session_date <= through:
                    for state_id, revisions in state_groups.items():
                        visible = []
                        availability = {}
                        for item in revisions:
                            state, _, _, members = item
                            snapshot_day = _date_value(state.get("source_snapshot_date"),
                                                       "source_snapshot_date")
                            if snapshot_day > session_date:
                                continue
                            usable, basis = _row_availability(
                                state, policy, profile, snapshot_day.isoformat(), vendor_releases)
                            if usable <= cutoffs[session]:
                                visible.append(state)
                                availability[id(state)] = (item, usable, basis, snapshot_day)
                            else:
                                invisible_complete = True
                        if not visible:
                            continue
                        chosen = _revision_order(visible, f"membership-state/{state_id}", profile=profile)
                        item, usable, basis, snapshot_day = availability[id(chosen)]
                        candidates.append((snapshot_day, item[0], usable, basis, item[3]))
                if candidates:
                    newest = max(item[0] for item in candidates)
                    winners = [item for item in candidates if item[0] == newest]
                    if len(winners) != 1:
                        raise QueryError(f"duplicate vendor membership snapshots for {query.universe_id}/{session}")
                    _, state, usable, basis, members = winners[0]
                    complete = (state, usable, basis, members)
                    coverage_proof = (usable, state.get("dependency_raw_batch_ids") or ())
                evaluation_date = newest if complete else session_date
            else:
                for state_id, revisions in state_groups.items():
                    if not any(start <= session_date and (stop is None or session_date < stop)
                               for _, start, stop, _ in revisions):
                        continue
                    visible = []
                    availability = {}
                    for item in revisions:
                        state, start, stop, members = item
                        usable, basis = _row_availability(state, policy, profile,
                                                          start.isoformat(), vendor_releases)
                        if usable <= cutoffs[session]:
                            visible.append(state)
                            availability[id(state)] = (item, usable, basis)
                        elif start <= session_date and (stop is None or session_date < stop):
                            invisible_complete = True
                    if not visible:
                        continue
                    chosen = _revision_order(visible, f"membership-state/{state_id}", profile=profile)
                    (state, start, stop, members), usable, basis = availability[id(chosen)]
                    if not (start <= session_date and (stop is None or session_date < stop)):
                        continue
                    if complete is not None:
                        raise QueryError(f"overlapping complete membership states for {query.universe_id}/{session}")
                    complete = (state, usable, basis, members)
                evaluation_date = session_date
            for symbol in query.symbols:
                # Interval selection depends on this session's knowledge time
                # and economic evaluation date, even under one complete state.
                interval_key = ((symbol, complete[0]["revision_id"], policy,
                                 cutoffs[session], evaluation_date)
                                if vendor_carry and complete else None)
                if interval_key is not None and interval_key in vendor_interval_cache:
                    selected, invisible_intervals = vendor_interval_cache[interval_key]
                else:
                    selected_intervals: list[tuple[dict[str, Any], datetime, str]] = []
                    invisible_intervals = False
                    for revisions in events.get(symbol, {}).values():
                        visible: list[dict[str, Any]] = []
                        availability: dict[int, tuple[datetime, str]] = {}
                        for row in revisions:
                            # An interval is one economic event. The vendor's
                            # declared release assumption attaches to that event's
                            # start, rather than restarting every queried day.
                            event_session = _date_value(row.get("effective_from"), "effective_from").isoformat()
                            usable, basis = _row_availability(row, policy, profile,
                                                              event_session, vendor_releases)
                            if usable <= cutoffs[session]:
                                visible.append(row)
                                availability[id(row)] = usable, basis
                            else:
                                invisible_intervals = True
                        if not visible:
                            continue
                        chosen = _revision_order(visible, f"membership/{query.universe_id}/{symbol}", profile=profile)
                        start = _date_value(chosen.get("effective_from"), "effective_from")
                        stop = chosen.get("effective_to")
                        if start <= evaluation_date and (stop is None or evaluation_date < _date_value(stop, "effective_to")):
                            selected_intervals.append((chosen, *availability[id(chosen)]))
                    if len(selected_intervals) > 1:
                        raise QueryError(f"overlapping visible membership intervals for {query.universe_id}/{symbol}/{session}")
                    selected = selected_intervals[0] if selected_intervals else None
                    if interval_key is not None:
                        vendor_interval_cache[interval_key] = (selected, invisible_intervals)
                if verified_through is not None and (coverage_proof is None or complete is None):
                    selected = None
                    complete = None
                    if session_date <= through and coverage_proof is None:
                        invisible_complete = True
                if selected and complete and complete[3] is not None and symbol not in complete[3]:
                    raise QueryError(f"membership interval conflicts with complete state for {query.universe_id}/{symbol}/{session}")
                if selected:
                    value: bool | None = True
                    source, usable, basis = selected
                    missing_reason = None
                elif complete:
                    source, usable, basis, members = complete
                    value = symbol in members if members is not None else False
                    missing_reason = None
                else:
                    source, usable, basis = None, None, None
                    value = None
                    missing_reason = "not_visible_at_cutoff" if invisible_intervals or invisible_complete else "source_missing"
                if source is not None and coverage_proof is not None:
                    usable = max(usable, coverage_proof[0], complete[1])
                    if policy != "best_effort_vendor_v1":
                        basis = ("first_observed_at_and_vendor_snapshot_receipt" if vendor_carry else
                                 "first_observed_at_and_source_coverage")
                if sink is not None: sink.begin_row()
                records.append({"security_id": symbol, "session": session, "is_member": value})
                snapshot_date = (complete[0].get("source_snapshot_date") if complete else None)
                meta_rows.append({
                    "security_id": symbol, "session": session,
                    "source_snapshot_date": (
                        _date_value(snapshot_date, "source_snapshot_date").isoformat()
                        if snapshot_date is not None else None
                    ),
                    "revision_id": source.get("revision_id") if source else None,
                    "revision_sequence": source.get("revision_sequence") if source else None,
                    "raw_batch_id": source.get("raw_batch_id") if source else None,
                    "usable_from": usable.isoformat() if usable else None,
                    "first_observed_at": (
                        _instant(source["first_observed_at"], "first_observed_at").isoformat()
                        if source and source.get("first_observed_at") is not None else None
                    ),
                    "availability_basis": basis,
                    "evidence_ref": source.get("evidence_ref") if source else None,
                    "dependency_raw_batch_ids": dependencies(source, complete, coverage_proof),
                    "missing_reason": missing_reason,
                })
        field_meta = {"is_member": {"dtype": "bool", "unit": None, "by_key": meta_rows}}
        context = {
            "contract_version": "data_batch_v1", "snapshot_id": self.snapshot_id,
            "domain": query.domain, "contract_id": contract.get("contract_id"),
            "source_profile_id": profile.get("id"), "reader_version": READER_VERSION,
            "query": {
                "fields": ["is_member"], "symbols": list(query.symbols),
                "sessions": list(query.sessions), "pit_policy": query.pit_policy,
                "cutoff_by_session": {s: cutoffs[s].isoformat() for s in query.sessions},
                "purpose": query.purpose, "price_basis": query.price_basis,
                "adjustment_anchor": query.adjustment_anchor, "universe_id": query.universe_id,
                "policy_by_session": dict(query.policy_by_session) if query.policy_by_session is not None else None,
            },
            "coverage": deepcopy(domain.get("coverage")) if sink is None else domain.get("coverage"),
            "limitations": list(dict.fromkeys([
                *_policy_limitations(query, profile, fallback_count),
                *(domain.get("coverage") or {}).get("limitations", []),
            ])),
        }
        if sink is not None:
            return sink.finish(context, field_meta)
        frame = pd.DataFrame.from_records(records, columns=["security_id", "session", "is_member"])
        frame["is_member"] = frame["is_member"].astype("boolean")
        return self._put_cache(cache_key, DataBatch(frame, field_meta, context))

    def read(self, query: QuerySpec) -> DataBatch:
        return self._read(query)

    def _read(self, query: QuerySpec, *, sink=None, groups=None, copy_coverage=True, _on_fallback=None):
        """Shared selection; a private save sink never builds the whole result."""
        domain, declared_fields, cutoffs = self._validate(query)
        key = self._cache_key(query, cutoffs)
        if sink is None and copy_coverage and key in self._cache:
            for part in [*self._parts_for_query(domain, query), *self._evidence_parts(query)]:
                self.store.verify_partition(part)
            self.cache_hits += 1
            cached, _ = self._cache[key]
            self._cache.move_to_end(key)
            return DataBatch(cached.frame.copy(deep=True), deepcopy(cached.field_meta), deepcopy(cached.context))

        if query.domain == "universe_membership":
            return self._read_membership(domain, declared_fields, cutoffs, query, key, sink=sink)

        profile = domain.get("source_profile") or {}
        grouped = self._read_groups(domain, query) if groups is None else groups
        fallback_count = 0

        records = [] if sink is None else sink.records
        field_meta: dict[str, Any] = {
            field: {"dtype": declared_fields[field].get("dtype"),
                    "unit": declared_fields[field].get("unit"),
                    "by_key": [] if sink is None else sink.metadata(field)}
            for field in query.fields
        }
        vendor_releases: dict[str, datetime] = {}
        for session in query.sessions:
            policy = _policy_for(query, session)
            for symbol in query.symbols:
                matching = grouped.get((symbol, session), [])
                if sink is not None: sink.begin_row(2*sink.writer.retained.get('source',0))
                visible: list[dict[str, Any]] = []
                provenance: dict[int, tuple[datetime, str]] = {}
                for row in matching:
                    if query.pit_policy=='market_pit_safe_v1' and not (
                            row.get('source_available_at') is not None and row.get('evidence_ref')):
                        fallback_count+=1
                    usable, basis = _row_availability(row, policy, profile, session,
                                                      vendor_releases)
                    if usable <= cutoffs[session]:
                        visible.append(row)
                        provenance[id(row)] = usable, basis
                selected = _revision_order(visible, f"{symbol}/{session}", profile=profile) if visible else None
                record = {"security_id": symbol, "session": session}
                usable, basis = provenance[id(selected)] if selected else (None, None)
                observed_at = (
                    _instant(selected["first_observed_at"], "first_observed_at").isoformat()
                    if selected and selected.get("first_observed_at") is not None else None
                )
                for field in query.fields:
                    value = selected.get(field) if selected else None
                    record[field] = value
                    missing_reason = (
                        "source_missing" if not matching else "not_visible_at_cutoff"
                    ) if selected is None else ("not_provided" if value is None else None)
                    field_meta[field]["by_key"].append({
                        "security_id": symbol, "session": session,
                        "revision_id": selected.get("revision_id") if selected else None,
                        "revision_sequence": selected.get("revision_sequence") if selected else None,
                        "raw_batch_id": selected.get("raw_batch_id") if selected else None,
                        "usable_from": usable.isoformat() if usable else None,
                        "first_observed_at": observed_at,
                        "availability_basis": basis,
                        "evidence_ref": selected.get("evidence_ref") if selected else None,
                        "missing_reason": missing_reason,
                    })
                records.append(record)
                if sink is not None or groups is not None:
                    # A next-month load may evict its predecessor. Do not keep
                    # a previous cell's borrowed revisions in loop locals.
                    matching=visible=();provenance={};selected=row=record=None

        if _on_fallback is not None: _on_fallback(fallback_count)
        context = {
            "contract_version": "data_batch_v1",
            "snapshot_id": self.snapshot_id,
            "domain": query.domain,
            "contract_id": (domain.get("contract") or {}).get("contract_id"),
            "source_profile_id": profile.get("id"),
            "reader_version": READER_VERSION,
            "query": {
                "fields": list(query.fields), "symbols": list(query.symbols),
                "sessions": list(query.sessions), "pit_policy": query.pit_policy,
                "cutoff_by_session": {s: cutoffs[s].isoformat() for s in query.sessions},
                "purpose": query.purpose, "price_basis": query.price_basis,
                "adjustment_anchor": query.adjustment_anchor,
                "universe_id": query.universe_id,
                "policy_by_session": dict(query.policy_by_session) if query.policy_by_session is not None else None,
            },
            "coverage": deepcopy(domain.get("coverage")) if sink is None and copy_coverage else domain.get("coverage"),
            "limitations": _policy_limitations(query, profile, fallback_count),
        }
        if sink is not None:
            return sink.finish(context, field_meta)
        frame = pd.DataFrame.from_records(records, columns=["security_id", "session", *query.fields])
        nullable_types = {"int": "Int64", "integer": "Int64", "int64": "Int64",
                          "int32": "Int32", "bool": "boolean", "boolean": "boolean",
                          "float": "Float64", "double": "Float64", "float64": "Float64",
                          "float32": "Float32", "string": "string", "str": "string", "utf8": "string"}
        for field in query.fields:
            dtype = nullable_types.get(str(declared_fields[field].get("dtype")).lower())
            if dtype:
                # Construct from source scalars, not a float-inferred intermediate:
                # int64 values above 2**53 must survive alongside missing keys.
                frame[field] = pd.array([record[field] for record in records], dtype=dtype)
        batch = DataBatch(frame, field_meta, context)
        return self._put_cache(key, batch) if copy_coverage else batch
