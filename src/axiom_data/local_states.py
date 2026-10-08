"""Snapshot-bound market-state diagnostics and historical research scope.

These helpers read only immutable Parquet referenced by a concrete Snapshot.
Reference domains use explicit rows: trading_calendar has (exchange, session,
is_open), security_master has (security_id, listing_date, delisting_date,
exchange), and security_status has (security_id, session, is_suspended).
``delisting_date`` is exclusive. An absent row or nullable status is unknown,
not proof of normal trading. Market coverage is complete only when the domain
manifest explicitly declares ``complete_cells`` or ``complete_ranges``.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime
from typing import Any, Mapping, Sequence

import pandas as pd

from .protocols import DataBatch, QueryError, QuerySpec
from .reader import (
    SnapshotQueryReader, _POLICIES, _date_value, _instant, _revision_order,
    _row_availability, _session,
)


def _domain_rows(store: Any, manifest: Mapping[str, Any], name: str,
                 columns: Sequence[str], *, sessions: Sequence[str] | None = None,
                 symbols: Sequence[str] | None = None) -> list[dict[str, Any]]:
    domain = manifest.get("domains", {}).get(name)
    if not domain:
        return []
    months = {s[:7] for s in sessions} if sessions is not None else None
    rows: list[dict[str, Any]] = []
    for part in domain["partitions"]:
        partition = str(part["partition"])
        if months is not None and len(partition) == 7 and partition[4] == "-" and partition not in months:
            continue
        table = store.read_partition(part, columns=list(columns), symbols=symbols, sessions=sessions)
        rows.extend(table.to_pylist())
    from .public_evidence import apply_evidence, evidence_index
    return apply_evidence(rows, index=evidence_index(store, manifest, name),
                          key_fields=domain["contract"]["logical_key"])


def _date_text(value: Any) -> str | None:
    if value is None:
        return None
    return _date_value(value, "reference date").isoformat()


def _selected(rows: Sequence[Mapping[str, Any]], *, policy: str, cutoff: datetime,
              profile: Mapping[str, Any], session: str, key: str) -> tuple[dict[str, Any] | None, str | None]:
    visible: list[dict[str, Any]] = []
    for row in rows:
        usable, _ = _row_availability(row, policy, profile, session)
        if usable <= cutoff:
            visible.append(dict(row))
    if not visible:
        return None, "not_visible_at_cutoff" if rows else "source_missing"
    return _revision_order(visible, key, profile=profile), None


def _coverage_complete(coverage: Mapping[str, Any], symbol: str, session: str) -> bool:
    """Require explicit symbol/date complete evidence, not a mere fetched request."""
    for cell in coverage.get("complete_cells", ()):
        if cell.get("security_id") == symbol and _date_text(cell.get("session")) == session and cell.get("complete") is True:
            return True
    for span in coverage.get("complete_ranges", ()):
        start, end = _date_text(span.get("start")), _date_text(span.get("end"))
        if (span.get("complete") is True and symbol in span.get("symbols", ())
                and start is not None and end is not None and start <= session <= end):
            return True
    return False


def _reference_data(store: Any, manifest: Mapping[str, Any], query: QuerySpec) -> tuple[dict, dict, dict]:
    calendar: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in _domain_rows(store, manifest, "trading_calendar", (
            "exchange", "session", "is_open", "revision_id", "revision_sequence",
            "first_observed_at", "source_available_at", "evidence_ref", "raw_batch_id"),
            sessions=query.sessions):
        session = _date_text(row.get("session"))
        if row.get("exchange") and session:
            calendar[(row["exchange"], session)].append(row)
    identities: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for row in _domain_rows(store, manifest, "security_master", (
            "security_id", "exchange", "listing_date", "delisting_date",
            "vendor_delist_date", "list_status",
            "revision_id", "revision_sequence", "first_observed_at",
            "source_available_at", "evidence_ref", "raw_batch_id"), symbols=query.symbols):
        if row.get("security_id") in query.symbols and row.get("listing_date") is not None:
            identities[row["security_id"]][_date_text(row["listing_date"])].append(row)
    statuses: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in _domain_rows(store, manifest, "security_status", (
            "security_id", "session", "is_suspended", "status_reason", "suspend_timing", "revision_id",
            "revision_sequence", "first_observed_at", "source_available_at",
            "evidence_ref", "raw_batch_id"), sessions=query.sessions, symbols=query.symbols):
        session = _date_text(row.get("session"))
        if row.get("security_id") in query.symbols and session:
            statuses[(row["security_id"], session)].append(row)
    return calendar, identities, statuses


def read_states(store: Any, snapshot_id: str, query: QuerySpec, *, _reader=None,
                _market=None) -> DataBatch:
    """Diagnose requested symbol/dates with PIT-selected reference and market facts.

    ``query.domain`` must be market_daily; fields choose actual price/volume
    columns to show beside a diagnostic ``market_state``. No source call, write,
    or inferred zero occurs. Missing optional reference domains yield
    ``unknown_status``; invalid query or conflicting revisions raise QueryError.
    The DataBatch is a diagnostic grid, not canonical market rows.
    """
    if not isinstance(query, QuerySpec) or query.domain != "market_daily":
        raise QueryError("states requires a market_daily QuerySpec")
    reader = SnapshotQueryReader(store, snapshot_id) if _reader is None else _reader
    market = reader.read(query) if _market is None else _market
    manifest = reader.snapshot
    domains = manifest["domains"]
    calendar, identities, statuses = _reference_data(store, manifest, query)
    listing_events = defaultdict(list)
    for event in _domain_rows(store, manifest, "listing_events", (
            "security_id", "exchange", "listing_date", "delisting_date",
            "last_trade_date", "event_type", "event_date", "event_state", "revision_id", "revision_sequence",
            "first_observed_at", "source_available_at", "evidence_ref", "raw_batch_id"),
            symbols=query.symbols):
        listing_events[(event["security_id"], _date_text(event["listing_date"]))].append(event)
    listing_profile = manifest["domains"].get("listing_events", {}).get("source_profile", {})
    calendar_profile = (domains.get("trading_calendar") or {}).get("source_profile") or {}
    identity_profile = (domains.get("security_master") or {}).get("source_profile") or {}
    status_profile = (domains.get("security_status") or {}).get("source_profile") or {}
    coverage = (domains["market_daily"].get("coverage") or {})
    market_by_key = {(r.security_id, r.session): r for r in market.frame.itertuples(index=False)}
    meta_by_field = {
        field: {(m["security_id"], m["session"]): m for m in meta["by_key"]}
        for field, meta in market.field_meta.items()
    }
    records: list[dict[str, Any]] = []
    state_meta: list[dict[str, Any]] = []
    for session in query.sessions:
        cutoff = _instant(query.cutoff_by_session[session], f"cutoff for {session}")
        policy = (query.policy_by_session or {}).get(session, query.pit_policy)
        for symbol in query.symbols:
            identity_rows = identities.get(symbol, {})
            selected_identities = []
            invisible_identity = False
            for listing, revisions in identity_rows.items():
                chosen, reason = _selected(revisions, policy=policy, cutoff=cutoff,
                                           profile=identity_profile, session=listing,
                                           key=f"security_master/{symbol}/{listing}")
                if chosen:
                    # The supplier event controls the economic boundary;
                    # its selected revision still must be visible at cutoff.
                    candidates = listing_events.get((symbol, listing), ())
                    eligible = []
                    for event in candidates:
                        if event.get("event_type") != "delisting":
                            continue
                        event_day = _date_text(event.get("event_date") or event.get("delisting_date"))
                        usable, _ = _row_availability(event, policy, listing_profile, event_day)
                        if usable <= cutoff:
                            eligible.append(event)
                    if eligible:
                        event = _revision_order(eligible, f"listing_events/{symbol}/{listing}",
                                                profile=listing_profile)
                        if event.get("event_state", "value") == "value":
                            chosen["delisting_date"] = event["delisting_date"]
                            chosen["_listing_event"] = {**event, "_evidence_domain": "listing_events"}
                    selected_identities.append(chosen)
                elif reason == "not_visible_at_cutoff":
                    invisible_identity = True
            selected_identities.sort(key=lambda row: _date_text(row["listing_date"]))
            active = [row for row in selected_identities if
                      _date_text(row["listing_date"]) <= session and
                      (row.get("delisting_date") is None or session < _date_text(row["delisting_date"]))]
            if len(active) > 1:
                raise QueryError(f"overlapping security identity intervals for {symbol}/{session}")
            identity = active[0] if active else None
            exchange = identity.get("exchange") if identity else (
                selected_identities[0].get("exchange") if selected_identities else None)
            cal_rows = calendar.get((exchange, session), []) if exchange else []
            # A market-wide holiday can be proven even when identity is not visible.
            if not cal_rows and exchange is None:
                same_day = [rows for (ex, day), rows in calendar.items() if day == session]
                if len(same_day) == 1:
                    cal_rows = same_day[0]
            cal, cal_reason = _selected(cal_rows, policy=policy, cutoff=cutoff,
                                        profile=calendar_profile, session=session,
                                        key=f"trading_calendar/{exchange}/{session}")
            state, reason = "unknown_status", None
            evidence: dict[str, Any] | None = None
            if cal is None or cal.get("is_open") is None:
                reason = "calendar_" + (cal_reason or "unknown")
            elif cal["is_open"] is False:
                state, evidence = "calendar_closed", cal
            elif not selected_identities:
                reason = "identity_not_visible_at_cutoff" if invisible_identity else "identity_missing"
            elif identity is None:
                if session < _date_text(selected_identities[0]["listing_date"]):
                    state, evidence = "not_listed", selected_identities[0]
                elif all(row.get("delisting_date") is not None and
                         session >= _date_text(row["delisting_date"]) for row in selected_identities):
                    state, evidence = "delisted", selected_identities[-1]
                    if evidence.get("_listing_event"):
                        evidence = evidence["_listing_event"]
                else:
                    reason = "identity_interval_unknown"
            else:
                status, status_reason = _selected(statuses.get((symbol, session), ()),
                                                  policy=policy, cutoff=cutoff,
                                                  profile=status_profile, session=session,
                                                  key=f"security_status/{symbol}/{session}")
                if status is not None and status.get("status_reason") == "partial_session_suspension":
                    reason, evidence = "partial_session_suspension", status
                elif status is None or status.get("is_suspended") is None:
                    reason = "status_" + (status_reason or "unknown")
                elif status["is_suspended"] is True:
                    state, evidence = "suspended", status
                else:
                    price_meta = [meta_by_field[field][(symbol, session)] for field in query.fields]
                    missing = [m for m in price_meta if m.get("missing_reason")]
                    if not missing:
                        state, evidence = "normal_trading", status
                    elif any(m["missing_reason"] == "not_visible_at_cutoff" for m in missing):
                        reason = "market_not_visible_at_cutoff"
                    elif _coverage_complete(coverage, symbol, session):
                        state, reason, evidence = "source_gap", "market_fact_missing", status
                    else:
                        reason = "market_coverage_unknown"
            item = {"security_id": symbol, "session": session, "market_state": state}
            source = market_by_key[(symbol, session)]
            for field in query.fields:
                # Closed/nonlisted dates are diagnostics only, never price rows.
                item[field] = getattr(source, field) if state not in {"calendar_closed", "not_listed", "delisted"} else None
            records.append(item)
            state_meta.append({"security_id": symbol, "session": session,
                               "missing_reason": reason, "evidence_domain": (
                                   "trading_calendar" if state == "calendar_closed" else
                                   (evidence or {}).get("_evidence_domain", "security_master") if state in {"not_listed", "delisted"} else
                                   "security_status" if state in {"suspended", "normal_trading", "source_gap"} or reason == "partial_session_suspension" else None),
                               "revision_id": evidence.get("revision_id") if evidence else None,
                               "raw_batch_id": evidence.get("raw_batch_id") if evidence else None})
    frame = pd.DataFrame.from_records(records, columns=["security_id", "session", "market_state", *query.fields])
    fields = deepcopy(market.field_meta)
    state_by_key = {(record["security_id"], record["session"]): record["market_state"] for record in records}
    for field in query.fields:
        for meta in fields[field]["by_key"]:
            key = (meta["security_id"], meta["session"])
            state = state_by_key[key]
            if state in {"calendar_closed", "not_listed", "delisted"}:
                meta["missing_reason"] = state
    fields["market_state"] = {"dtype": "string", "unit": None, "by_key": state_meta}
    context = dict(market.context)
    context["domain"] = "market_state_diagnostics"
    context["reader_version"] = "local_states_v3"
    context["diagnostic_only"] = True
    context["limitations"] = [*context.get("limitations", []),
                              "coverage gap classification describes this Snapshot's observations, not proof the gap was known at a historical decision"]
    context["state_dependencies"] = ["trading_calendar", "security_master", "security_status", "market_daily"]
    return DataBatch(frame, fields, context)


def plan_research_scope(store: Any, snapshot_id: str, *, universe_id: str,
                        output_sessions: Sequence[str], cutoff_by_session: Mapping[str, datetime | str],
                        pit_policy: str, lookback_sessions: int,
                        held_symbols: Sequence[str] = (), pending_symbols: Sequence[str] = (),
                        exchange: str | None = None,
                        policy_by_session: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Plan an index study from PIT membership and prior open calendar sessions.

    Read and tracking sets include held/pending symbols outside the index.
    Missing calendar or membership evidence is returned explicitly; callers
    must decide whether that scope is usable. This function is read-only and
    does not collect or impute source data.
    """
    sessions = tuple(_session(s) for s in output_sessions)
    if not sessions or tuple(sorted(set(sessions))) != sessions:
        raise QueryError("output_sessions must be nonempty, sorted, unique dates")
    if type(lookback_sessions) is not int or lookback_sessions < 0:
        raise QueryError("lookback_sessions must be a nonnegative integer")
    if set(cutoff_by_session) != set(sessions):
        raise QueryError("cutoff_by_session must match output_sessions")
    if not universe_id or not isinstance(universe_id, str):
        raise QueryError("universe_id is required")
    if pit_policy == "bootstrap_hybrid_v1":
        if policy_by_session is None or set(policy_by_session) != set(sessions):
            raise QueryError("bootstrap_hybrid_v1 requires policy_by_session for each output session")
        if any(policy not in _POLICIES for policy in policy_by_session.values()):
            raise QueryError("unsupported segmented PIT policy")
    elif pit_policy not in _POLICIES:
        raise QueryError(f"unsupported PIT policy {pit_policy!r}")
    elif policy_by_session is not None:
        raise QueryError("policy_by_session is only valid with bootstrap_hybrid_v1")
    reader = SnapshotQueryReader(store, snapshot_id)
    manifest = reader.snapshot
    if "universe_membership" not in manifest["domains"]:
        raise QueryError("universe_membership is absent from Snapshot")
    calendar_domain = manifest["domains"].get("trading_calendar")
    first = sessions[0]
    first_policy = (policy_by_session or {}).get(first, pit_policy)
    cutoff = _instant(cutoff_by_session[first], f"cutoff for {first}")
    calendar_rows = _domain_rows(store, manifest, "trading_calendar", (
        "exchange", "session", "is_open", "revision_id", "revision_sequence",
        "first_observed_at", "source_available_at", "evidence_ref"))
    by_calendar: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in calendar_rows:
        day = _date_text(row.get("session"))
        if day and day <= sessions[-1] and (exchange is None or row.get("exchange") == exchange):
            by_calendar[(row.get("exchange"), day)].append(row)
    def proven_open(day: str, boundary: datetime) -> bool:
        for (ex, candidate), rows in by_calendar.items():
            if candidate != day:
                continue
            selected, _ = _selected(rows, policy=(policy_by_session or {}).get(day, first_policy), cutoff=boundary,
                                    profile=(calendar_domain or {}).get("source_profile") or {},
                                    session=day, key=f"calendar/{ex}/{day}")
            if selected and selected.get("is_open") is True:
                return True
        return False

    previous = sorted(day for day in {key[1] for key in by_calendar} if day < first and proven_open(day, cutoff))
    warmup = tuple(previous[-lookback_sessions:]) if lookback_sessions else ()
    missing: list[dict[str, Any]] = []
    if len(warmup) < lookback_sessions:
        missing.append({"domain": "trading_calendar", "reason": "insufficient_open_session_history",
                        "required": lookback_sessions, "available": len(warmup)})
    for day in sessions:
        if not proven_open(day, _instant(cutoff_by_session[day], f"cutoff for {day}")):
            missing.append({"domain": "trading_calendar", "session": day,
                            "reason": "output_session_not_proven_open"})
    membership_domain = manifest["domains"]["universe_membership"]
    membership_rows = _domain_rows(store, manifest, "universe_membership", (
        "security_id", "universe_id", "membership_id", "logical_event_key",
        "effective_from", "effective_to", "revision_id", "revision_sequence",
        "first_observed_at", "source_available_at", "evidence_ref"))
    candidates = {r["security_id"] for r in membership_rows
                  if r.get("universe_id") == universe_id and r.get("security_id")}
    for state in (membership_domain.get("coverage") or {}).get("complete_states", ()):
        if state.get("universe_id") == universe_id:
            candidates.update(state.get("members", ()))
    held, pending = tuple(dict.fromkeys(held_symbols)), tuple(dict.fromkeys(pending_symbols))
    candidates.update(held)
    candidates.update(pending)
    if not candidates:
        raise QueryError("membership has no candidate securities for requested universe")
    selected_sessions = (*warmup, *sessions)
    cutoffs = {day: cutoff_by_session.get(day, cutoff) for day in selected_sessions}
    segmented = ({day: (policy_by_session or {}).get(day, first_policy) for day in selected_sessions}
                 if pit_policy == "bootstrap_hybrid_v1" else None)
    member_query = QuerySpec("universe_membership", ("is_member",), tuple(sorted(candidates)),
                             selected_sessions, pit_policy, cutoffs, universe_id=universe_id,
                             policy_by_session=segmented)
    membership = reader.read(member_query)
    by_day: dict[str, list[str]] = {day: [] for day in selected_sessions}
    for row in membership.frame.itertuples(index=False):
        if row.is_member is True or (pd.notna(row.is_member) and bool(row.is_member)):
            by_day[row.session].append(row.security_id)
    for meta in membership.field_meta["is_member"]["by_key"]:
        if meta["missing_reason"]:
            missing.append({"domain": "universe_membership", "session": meta["session"],
                            "security_id": meta["security_id"], "reason": meta["missing_reason"]})
    historical = sorted({symbol for symbols in by_day.values() for symbol in symbols})
    tracking = sorted(set(held) | set(pending))
    return {
        "snapshot_id": snapshot_id, "universe_id": universe_id,
        "output_sessions": list(sessions), "warmup_sessions": list(warmup),
        "read_sessions": list(selected_sessions),
        "read_symbols": sorted(set(historical) | set(tracking)),
        "historical_union_symbols": historical,
        "decision_membership_by_session": {day: sorted(by_day[day]) for day in sessions},
        "held_symbols": list(held), "pending_symbols": list(pending),
        "tracking_symbols": tracking, "missing_reasons": missing,
        "pit_policy": pit_policy,
        "policy_by_session": segmented,
        "cutoff_by_session": {day: _instant(cutoffs[day], f"cutoff for {day}").isoformat()
                              for day in selected_sessions},
    }
