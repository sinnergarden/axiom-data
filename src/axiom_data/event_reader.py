"""PIT selection of native economic events from an immutable local Snapshot.

Report-period queries prune immutable report-year partitions. Queries using
correctable dates scan all revisions before filtering. Reads never write or
contact a source.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd

from .protocols import DataBatch, EventQuery, QueryError
from .reader import READER_VERSION, _date_value, _instant, _revision_order


EVENT_READER_VERSION = "event_reader_v7"
_ECONOMIC_EVENT_READER_VERSION = "event_reader_v9"
_ACTION_PHASES = {"record_date": "record", "ex_date": "ex", "payment_date": "pay", "stock_listing_date": "listing"}
_ACTION_TERMS = ("report_period", "process_status", "implementation_announcement_date", "record_date", "ex_date",
                 "cash_dividend_before_tax_per_share", "bonus_shares_per_share", "capital_transfer_shares_per_share",
                 "stock_distribution_shares_per_share", "payment_date", "stock_listing_date")
_POLICIES = {"operational_pit_v1", "market_pit_safe_v1", "best_effort_vendor_v1"}
_PURPOSES = {"decision_facts", "historical_exploration", "research_label", "label_outcomes", "market_replay"}
_STATUSES = {"value", "not_provided", "retracted", "source_missing", "parse_error"}
_VERSION = ("revision_id", "revision_sequence", "first_observed_at", "raw_batch_id",
            "source_available_at", "evidence_ref", "declared_available_at")


def _date_string(value: Any, name: str) -> str:
    return _date_value(value, name).isoformat()


def _status_column(field: str, spec: Mapping[str, Any], declared: Mapping[str, Any]) -> str | None:
    explicit = spec.get("status_field")
    if explicit is not None:
        if not isinstance(explicit, str) or explicit not in declared:
            raise QueryError(f"invalid status_field for {field}")
        return explicit
    for candidate in (f"{field}__status", f"{field}_state"):
        if candidate in declared:
            return candidate
    return None


def _not_before_date_fields(profile: Mapping[str, Any]) -> tuple[str, ...]:
    """A terminal dividend implementation cannot precede its implementation notice.

    Both dates remain supplier assumptions. This clock lower bound applies to
    the entire selected implementation row, including future record/ex dates;
    it neither invents an earlier proposal nor certifies a public timestamp.
    """
    return (("implementation_announcement_date",)
            if profile.get("endpoint") in {"dividend", "fund_div"} else ())


def _best_effort_time(row: Mapping[str, Any], profile: Mapping[str, Any], *,
                      cutoff: datetime | None = None) -> datetime | None:
    """Use a declared vendor rule; optionally skip provably post-cutoff dates."""
    availability = profile.get("availability")
    if not isinstance(availability, Mapping):
        raise QueryError("best_effort_vendor_v1 requires source_profile.availability")
    instant_field = availability.get("declared_available_at_field")
    if instant_field is not None:
        if not isinstance(instant_field, str) or row.get(instant_field) is None:
            raise QueryError("declared_available_at_field has no value on event revision")
        return _instant(row[instant_field], instant_field)
    if row.get("declared_available_at") is not None and availability.get("basis") == "declared_available_at":
        return _instant(row["declared_available_at"], "declared_available_at")
    date_field = availability.get("date_field")
    rule = availability.get("date_rule")
    tz_name = availability.get("timezone")
    clock = availability.get("session_release_time")
    if not all(isinstance(x, str) and x for x in (date_field, rule, tz_name, clock)):
        raise QueryError("best_effort_vendor_v1 requires date_field, date_rule, timezone and session_release_time")
    vendor_date = _date_string(row.get(date_field), date_field)
    for lower_field in _not_before_date_fields(profile):
        if row.get(lower_field) is not None:
            vendor_date = max(vendor_date, _date_string(row[lower_field], lower_field))
    try:
        zone = ZoneInfo(tz_name)
        local_clock = time.fromisoformat(clock)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise QueryError("invalid vendor availability timezone or release time") from exc
    if local_clock.tzinfo is not None:
        raise QueryError("session_release_time must be local wall-clock time")
    if cutoff is not None:
        cutoff_day = cutoff.astimezone(zone).date().isoformat()
        if rule in {"same_day_release", "next_open"} and (
                vendor_date > cutoff_day or (rule == "next_open" and vendor_date == cutoff_day)):
            # A future candidate cannot affect this cutoff. Do not require its
            # future calendar mapping merely to bound a historical warning.
            return None
    if rule == "same_day_release":
        usable_date = vendor_date
    elif rule == "next_open":
        calendar = availability.get("next_open_session_by_date")
        if not isinstance(calendar, Mapping) or vendor_date not in calendar:
            raise QueryError(f"next_open rule lacks calendar mapping for {vendor_date}")
        usable_date = _date_string(calendar[vendor_date], "next_open session")
        if usable_date <= vendor_date:
            raise QueryError("next_open session must follow vendor date")
    else:
        raise QueryError(f"unsupported vendor date_rule {rule!r}")
    return datetime.combine(date.fromisoformat(usable_date), local_clock, zone)


def _availability(row: Mapping[str, Any], policy: str, profile: Mapping[str, Any]) -> tuple[datetime, str]:
    if policy == "best_effort_vendor_v1":
        return _best_effort_time(row, profile), "declared_vendor_assumption"
    observed = row.get("first_observed_at")
    if observed is None:
        raise QueryError("strict event PIT requires first_observed_at on each revision")
    observed_at = _instant(observed, "first_observed_at")
    if policy == "market_pit_safe_v1" and row.get("source_available_at") is not None and row.get("evidence_ref"):
        return _instant(row["source_available_at"], "source_available_at"), "revision_bound_source_evidence"
    return observed_at, "first_observed_at"


def _validate(query: EventQuery, snapshot: Mapping[str, Any]) -> tuple[Mapping[str, Any], tuple[str, ...], dict[str, Mapping[str, Any]], datetime, str, str]:
    if not isinstance(query, EventQuery):
        raise QueryError("events requires EventQuery")
    if query.pit_policy not in _POLICIES or query.purpose not in _PURPOSES:
        raise QueryError("unsupported event PIT policy or purpose")
    if any(not values or len(set(values)) != len(values) or any(not isinstance(x, str) or not x for x in values)
           for values in (query.fields, query.symbols)):
        raise QueryError("event fields and symbols must be nonempty unique strings")
    start, end = _date_string(query.start, "start"), _date_string(query.end, "end")
    if start > end:
        raise QueryError("event start exceeds end")
    cutoff = _instant(query.cutoff, "event cutoff")
    domain = (snapshot.get("domains") or {}).get(query.domain)
    if not isinstance(domain, Mapping):
        raise QueryError(f"required event domain {query.domain!r} is absent")
    contract = domain.get("contract") or {}
    declared = contract.get("fields")
    keys = tuple(contract.get("logical_key") or ())
    if not isinstance(declared, Mapping) or not keys or "security_id" not in keys:
        raise QueryError("event contract requires declared fields and security_id logical key")
    if any(not isinstance(k, str) or not k for k in keys):
        raise QueryError("invalid event logical key")
    if query.time_field not in declared and query.time_field not in keys:
        raise QueryError("event time_field must be declared")
    if any(field not in declared or field in keys for field in query.fields):
        raise QueryError("event query contains undeclared or implicit key field")
    if not isinstance(query.filters, Mapping) or any(k not in declared and k not in keys for k in query.filters):
        raise QueryError("event filters must use declared fields or keys")
    for field in query.fields:
        if not isinstance(declared[field], Mapping):
            raise QueryError(f"invalid contract definition for {field}")
        _status_column(field, declared[field], declared)
    return domain, keys, dict(declared), cutoff, start, end


def _cell_status(row: Mapping[str, Any], field: str, spec: Mapping[str, Any], declared: Mapping[str, Any]) -> str:
    status_column = _status_column(field, spec, declared)
    value = row.get(field)
    status = row.get(status_column) if status_column else None
    if status is None:
        status = "value" if value is not None else "not_provided"
    if status not in _STATUSES:
        raise QueryError(f"unknown field status {status!r} for {field}")
    if (status == "value") != (value is not None):
        raise QueryError(f"field status/value mismatch for {field}")
    return status


def _ambiguous_action_candidates(store, row, *, _budget=None):
    """Read candidate dates from the retained native group without choosing an action."""
    from contextlib import ExitStack
    with ExitStack() as stack:
        if _budget is not None:
            for owner in ('raw-record','raw-payload','raw-parsing'):
                stack.enter_context(_budget.scope(owner))
        return _action_candidates(store,row,_budget=_budget)


def _action_candidates(store,row,*,_budget=None):
    from .sources import _rows
    import sys
    raw = store.get_raw(row['raw_batch_id'])
    profile = raw['source_profile']
    code = next((code for code, stable in profile['identity_map'].items()
                 if stable == row['security_id']), None)
    fields = profile['field_map']
    def source_day(value):
        try:
            return datetime.strptime(value, '%Y%m%d').date().isoformat()
        except (TypeError, ValueError) as exc:
            raise QueryError('ambiguous action Raw has an invalid source date') from exc
    payload=store.read_raw_record(raw)
    if _budget is not None: _budget.reserve('raw-parsing',32*len(payload)+4096)
    originals=_rows(payload)
    if _budget is not None:
        # Copies borrow source-row scalars (owned by source), but dictionaries,
        # parsed dates, candidate-time lists and their pointers remain live.
        _budget.add('candidates',len(originals)*(4*sys.getsizeof(row)+4096)+4096)
    candidates = []
    for original in originals:
        if original.get('ts_code') != code or original.get('div_proc') != row['process_status']:
            continue
        if any(source_day(original.get(source)) != _date_string(row[field], field)
               for field, source in (('report_period', 'end_date'), ('announcement_date', 'ann_date'))):
            continue
        candidate = dict(row)
        for field in ('implementation_announcement_date', 'record_date', 'ex_date', 'payment_date', 'stock_listing_date'):
            if field not in fields:
                continue
            value = original.get(fields[field])
            candidate[field] = None if value in (None, '') else source_day(value)
        candidates.append(candidate)
    if not candidates:
        raise QueryError('ambiguous action lacks its complete referenced Raw group')
    return candidates


def _action_candidate_scope(candidates, *, field, start, end, candidate_times=None):
    """Clip visible uncertain dates and their clock to the requested range."""
    def included(candidate):
        return candidate.get(field) is None or start <= _date_string(candidate[field], field) <= end
    dates = list(dict.fromkeys(None if candidate.get(field) is None else _date_string(candidate[field], field)
                              for candidate in candidates if included(candidate)))
    clock = None
    if dates and candidate_times is not None:
        clock = min(usable for candidate, usable in candidate_times if included(candidate)), "declared_vendor_assumption"
    return dates, clock


def _phase_projection(selected, *, query, keys, declared, profile, start, end, store, action_candidates,
                      hidden_action_summaries, budget):
    """Fold only visible, strictly matching aliases; unknown phases remain markers."""
    from .event_sources import _action_identity_id
    bindings = {(profile["identity_map"][item["ts_code"]],
                 datetime.strptime(item["end_date"], "%Y%m%d").date().isoformat(),
                 datetime.strptime(item["ann_date"], "%Y%m%d").date().isoformat(), item["div_proc"]): item["round"]
                for item in profile["economic_identity"]["rounds"]}
    groups = {}
    for row, clock in selected:
        native_key = tuple(str(row[k]) for k in keys)
        if row.get("source_issue") == "ambiguous_economic_alias_terms_or_dates" and native_key in bindings:
            # Source summaries retain all returned aliases, including later
            # notices. Recheck their frozen round after cutoff selection;
            # a not-yet-visible alias cannot invalidate this phase projection.
            row = dict(row)
            row["economic_event_id"] = _action_identity_id([
                "tushare.dividend", row["security_id"], str(row["report_period"]).replace("-", ""),
                "round", bindings[native_key]])
            row["economic_event_id__status"] = "value"
            row["source_issue"] = None
        identity = row.get("economic_event_id")
        status = _cell_status(row, "economic_event_id", declared["economic_event_id"], declared)
        if not str(identity).startswith("ca:native:") and native_key in bindings:
            # A visible uncertain revision still belongs to its frozen round.
            # Association is not a resolved ID: it only propagates uncertainty
            # to the other currently visible members of that declared round.
            association = _action_identity_id([
                "tushare.dividend", row["security_id"], str(row["report_period"]).replace("-", ""),
                "round", bindings[native_key]])
            key = ("economic", association)
        else:
            key = ("economic", identity) if status == "value" else ("native", *(row[k] for k in keys))
        groups.setdefault(key, []).append((row, clock))
    projected, scopes, aliases = [], [], {}
    for group in groups.values():
        group.sort(key=lambda item: tuple(str(item[0][k]) for k in keys))
        consistent = all(all((row.get(f), _cell_status(row, f, declared[f], declared)) ==
                             (group[0][0].get(f), _cell_status(group[0][0], f, declared[f], declared))
                             for f in _ACTION_TERMS) for row, _ in group)
        complete = all(row.get("stock_distribution_shares_per_share") is not None
                       and row.get("cash_dividend_before_tax_per_share") is not None
                       and all(row.get(f) is not None for f in ("implementation_announcement_date", "record_date", "ex_date"))
                       and (row["cash_dividend_before_tax_per_share"] == 0 or row.get("payment_date") is not None)
                       and (row["stock_distribution_shares_per_share"] == 0 or row.get("stock_listing_date") is not None)
                       for row, _ in group)
        native_identity = str(group[0][0].get("economic_event_id", "")).startswith("ca:native:")
        linked = len(group) == 1 or (consistent and (complete or native_identity))
        provenance = [{"native_key": {k: str(row[k]) for k in keys}, "revision_id": row["revision_id"],
                       "raw_batch_id": row["raw_batch_id"], "first_observed_at": _instant(row["first_observed_at"], "first_observed_at").isoformat(),
                       "usable_from": clock[0].isoformat(), "availability_basis": clock[1]} for row, clock in group]
        candidates = group[:1] if linked else group
        for original, clock in candidates:
            row = original
            if not linked:
                row = dict(original)
                if native_identity:
                    if original is not group[0][0]:
                        continue
                    # A native vendor key is still known; conflicting terms
                    # have no declared revision order and cannot be applied.
                    for field in _ACTION_TERMS:
                        if field not in keys:
                            row[field] = None
                            row[f"{field}__status"] = "source_missing"
                else:
                    row["economic_event_id"] = None
                    row["economic_event_id__status"] = "source_missing"
                row["source_issue"] = original.get("source_issue") or "ambiguous_economic_alias_terms_or_dates"
                # This marker requires every conflicting visible member;
                # an older alias cannot claim the later uncertainty earlier.
                clock = max((member_clock for _, member_clock in group), key=lambda item: item[0])
            uncertain = row.get("source_issue") in {
                "ambiguous_action_identity_or_revision", "ambiguous_economic_alias_terms_or_dates",
                "economic_round_not_declared_or_native_key_ambiguous"}
            if any(row.get(f) != value for f, value in query.filters.items() if not uncertain or f in keys):
                continue
            day = row.get(query.time_field)
            candidate_dates = [None if day is None else _date_string(day, query.time_field)]
            if row.get("source_issue") == "ambiguous_action_identity_or_revision":
                visible = action_candidates.get(id(original))
                retained = [r for r, _ in visible] if visible is not None else _ambiguous_action_candidates(store, original, _budget=budget)
                candidate_dates, candidate_clock = _action_candidate_scope(
                    retained, field=query.time_field, start=start, end=end, candidate_times=visible)
                if candidate_clock is not None:
                    clock = max(clock, candidate_clock, key=lambda item: item[0])
            elif not linked and native_identity:
                candidate_dates = list(dict.fromkeys(None if r.get(query.time_field) is None else
                                      _date_string(r[query.time_field], query.time_field) for r, _ in group))
            candidate_dates = [d for d in candidate_dates if d is None or start <= d <= end]
            if not candidate_dates:
                continue
            if day is not None and not start <= _date_string(day, query.time_field) <= end:
                continue
            if day is None or uncertain:
                scopes.append({"security_id": row["security_id"], "native_key": {k: str(row[k]) for k in keys},
                               "economic_event_id": row.get("economic_event_id"), "phase": _ACTION_PHASES[query.time_field],
                               "time_field": query.time_field,
                               "candidate_dates": candidate_dates,
                               "source_issue": row.get("source_issue") or "phase_date_not_provided",
                               "raw_batch_id": row["raw_batch_id"]})
            aliases[tuple(str(row[k]) for k in keys)] = provenance
            if id(original) in hidden_action_summaries:
                hidden_action_summaries.add(id(row))
            projected.append((row, clock))
    return projected, scopes, aliases


def read_events(store: Any, snapshot_id: str, query: EventQuery, *,
                _snapshot=None, _sink=None, _source_symbols=None, _evidence=None) -> DataBatch:
    """Read PIT-selected events by native key from a concrete Snapshot.

    The inclusive economic date range and filters apply only after selecting a
    visible revision for every complete logical event key. An ambiguous whole
    action with a missing economic date returns a missing-date marker when its
    retained candidates intersect the range or cannot bound it. The context
    records the affected event scope; this is not an absence or a zero. Bad contracts,
    ambiguous revisions and unknown statuses raise QueryError.
    """
    if not isinstance(snapshot_id, str) or not snapshot_id or snapshot_id in {"current", "latest"}:
        raise QueryError("resolve a concrete snapshot before events")
    snapshot = store.load_snapshot(snapshot_id) if _snapshot is None else _snapshot
    if snapshot.get("snapshot_id") != snapshot_id:
        raise QueryError("loaded Snapshot ID does not match requested ID")
    domain, keys, declared, cutoff, start, end = _validate(query, snapshot)
    contract = domain["contract"]
    profile = domain.get("source_profile") or {}
    phase = (query.domain == "corporate_actions" and contract.get("contract_id") == "local.corporate_actions.tushare.v3"
             and query.time_field in _ACTION_PHASES)
    if query.pit_policy == "best_effort_vendor_v1" and not isinstance(profile.get("availability"), Mapping):
        raise QueryError("best_effort_vendor_v1 requires source_profile.availability")
    status_columns = [c for f in query.fields if (c := _status_column(f, declared[f], declared))]
    date_field = (profile.get("availability") or {}).get("date_field")
    instant_field = (profile.get("availability") or {}).get("declared_available_at_field")
    order_fields = ("actual_announcement_date",) if profile.get("revision_order") == "announcement_day_then_terminal_v1" else ()
    columns = list(dict.fromkeys((*keys, query.time_field, *query.filters, *query.fields, *order_fields,
                                  *_not_before_date_fields(profile),
                                  *status_columns, *[c for c in (date_field, instant_field) if c],
                                  "group_completeness", "holders", "source_issue", *_VERSION)))
    if phase:
        terms = (*_ACTION_TERMS, "economic_event_id")
        columns = list(dict.fromkeys((*columns, *terms, *[c for f in terms if (c := _status_column(f, declared[f], declared))])))
    from .public_evidence import apply_evidence, evidence_index
    evidence = evidence_index(store, snapshot, query.domain) if _evidence is None else _evidence
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    encounters = {}
    source_symbols = query.symbols if _source_symbols is None else _source_symbols
    for part_number, part in enumerate(domain.get("partitions") or ()):
        # Report period is immutable only when included in the logical key.
        # Announcement/effective-date queries cannot use this optimization:
        # a correction may move the selected date across their query boundary.
        label = part.get("partition", "")
        if (query.time_field == "report_period" and "report_period" in keys
                and len(label) == 11 and label.startswith("period-")
                and label[7:].isdigit() and not start[:4] <= label[7:] <= end[:4]):
            continue
        from contextlib import closing, nullcontext
        native=getattr(store,'_native_chunks',None)
        if native is None:
            table=store.read_partition(part,columns=columns,symbols=source_symbols)
            source_rows=table.to_pylist()
            chunks=iter([(source_rows,range(table.num_rows))])
        else:
            chunks=native(part,owner='source',columns=columns,symbols=source_symbols,positions=True)
        source_rows=ordinals=None
        with closing(chunks) if native is not None else nullcontext(chunks):
            for source_rows,ordinals in chunks:
                apply_evidence(source_rows,index=evidence,key_fields=keys)
                for physical_ordinal,row in zip(ordinals,source_rows):
                    if row.get('security_id') not in query.symbols: continue
                    key=tuple(row.get(k) for k in keys)
                    if any(v is None for v in key): raise QueryError('event revision lacks a logical key')
                    grouped.setdefault(key,[]).append(row)
                    if _sink is not None:
                        encounters.setdefault(tuple(str(v) for v in key),(part_number,physical_ordinal))
        if native is not None: del source_rows,ordinals
    budget=None if _sink is None else _sink.writer
    if budget is not None:
        budget.reserve('event-selection',2*budget.retained.get('source',0)+4096*len(grouped))
    selected: list[tuple[dict[str, Any], tuple[datetime, str]]] = []
    unavailable_actions = []
    action_candidates = {}
    hidden_action_summaries = set()
    fallback_count = 0
    for key, revisions in grouped.items():
        visible = []
        provenance: dict[int, tuple[datetime, str]] = {}
        for row in revisions:
            if (query.pit_policy == 'best_effort_vendor_v1' and query.domain == 'corporate_actions' and
                    row.get('source_issue') == 'ambiguous_action_identity_or_revision'):
                candidates = _ambiguous_action_candidates(store, row,_budget=budget)
                candidate_times = [(candidate, _best_effort_time(candidate, profile, cutoff=cutoff))
                                   for candidate in candidates]
                eligible = [(candidate, usable) for candidate, usable in candidate_times
                            if usable is not None and usable <= cutoff]
                if not eligible:
                    continue
                # Missing scope can be visible before the entire group. This
                # never makes a candidate amount eligible or chooses a winner.
                action_candidates[id(row)] = eligible
                if len(eligible) < len(candidates):
                    hidden_action_summaries.add(id(row))
                usable, basis = min(usable for _, usable in eligible), 'declared_vendor_assumption'
            else:
                usable, basis = _availability(row, query.pit_policy, profile)
            if query.pit_policy == "market_pit_safe_v1" and basis == "first_observed_at":
                fallback_count += 1
            if usable <= cutoff:
                visible.append(row)
                provenance[id(row)] = usable, basis
        if not visible:
            continue
        chosen = _revision_order(visible, repr(key), profile=profile)
        if phase:
            # Every native revision is selected before any phase date/filter,
            # so an alias outside the requested range cannot hide a conflict.
            selected.append((chosen, provenance[id(chosen)]))
            continue
        ambiguous_action = (query.domain == 'corporate_actions' and
                            chosen.get('source_issue') == 'ambiguous_action_identity_or_revision')
        if any(chosen.get(field) != expected for field, expected in query.filters.items()
               if not ambiguous_action or field in keys):
            continue
        candidates = None
        if chosen.get(query.time_field) is None and declared[query.time_field].get("nullable", True):
            if not ambiguous_action:
                # Ordinary absent dates do not fall back to an older revision.
                continue
            candidate_times = action_candidates.get(id(chosen))
            eligible = ([candidate for candidate, _ in candidate_times] if candidate_times is not None
                        else _ambiguous_action_candidates(store, chosen,_budget=budget))
            candidates, candidate_clock = _action_candidate_scope(
                eligible, field=query.time_field, start=start, end=end, candidate_times=candidate_times)
            if not candidates:
                continue
            if candidate_clock is not None:
                # Report when this requested uncertain range becomes usable,
                # rather than an unrelated earlier candidate's notice time.
                provenance[id(chosen)] = candidate_clock
        else:
            event_date = _date_string(chosen.get(query.time_field), query.time_field)
            if not start <= event_date <= end:
                continue
        if ambiguous_action:
            unavailable_actions.append({'security_id': chosen['security_id'],
                'native_key': {field: str(chosen[field]) for field in keys},
                'time_field': query.time_field, 'candidate_dates': candidates,
                'source_issue': chosen['source_issue'], 'raw_batch_id': chosen['raw_batch_id']})
        selected.append((chosen, provenance[id(chosen)]))
    phase_aliases = {}
    if phase:
        selected, unavailable_actions, phase_aliases = _phase_projection(
            selected, query=query, keys=keys, declared=declared, profile=profile, start=start, end=end,
            store=store, action_candidates=action_candidates,
            hidden_action_summaries=hidden_action_summaries, budget=budget)
    positions = {symbol: i for i, symbol in enumerate(query.symbols)}
    selected.sort(key=lambda item: (positions[item[0]["security_id"]],
                                    (_date_string(item[0][query.time_field], query.time_field)
                                     if item[0].get(query.time_field) is not None else ''),
                                    tuple(str(item[0][k]) for k in keys)))
    records = [] if _sink is None else _sink.records
    field_meta = {field: {"dtype": declared[field].get("dtype"), "unit": declared[field].get("unit"),
                          "basis": declared[field].get("basis"),
                          "by_key": [] if _sink is None else _sink.metadata(field)} for field in query.fields}
    for row, (usable, basis) in selected:
        if _sink is not None: _sink.begin_row()
        native = {k: _date_string(row[k], k) if isinstance(row[k], date) and not isinstance(row[k], datetime)
                  else row[k] for k in keys}
        record = dict(native)
        if query.time_field not in record:
            record[query.time_field] = (_date_string(row[query.time_field], query.time_field)
                                       if row.get(query.time_field) is not None else None)
        if query.domain == "top_holders_reports":
            record["group_completeness"] = row.get("group_completeness")
        for field in query.fields:
            status = _cell_status(row, field, declared[field], declared)
            if id(row) in hidden_action_summaries and field in {'source_candidate_count', 'candidate_economic_dates'}:
                # The retained full-group diagnostics include future candidates.
                # Do not project their dates or number at an earlier cutoff.
                status = 'source_missing'
            if query.domain == "top_holders_reports" and field in {"top10_ratio", "top10_ratio_pct"}:
                completeness = row.get("group_completeness")
                if completeness not in {"complete", "incomplete", "supplier_report_complete",
                                        "partial_supplier_report", "ambiguous_duplicate_holder"}:
                    raise QueryError("holder report requires explicit group_completeness")
                if completeness not in {"complete", "supplier_report_complete"} and status == "value":
                    status = "source_missing"
            value = row.get(field) if status == "value" else None
            record[field] = value
            missing_reason = None if status == "value" else status
            if status == "source_missing" and row.get("source_issue"):
                missing_reason = row["source_issue"]
            field_meta[field]["by_key"].append({
                **native, "status": status, "missing_reason": missing_reason,
                "revision_id": row.get("revision_id"), "revision_sequence": row.get("revision_sequence"),
                "raw_batch_id": row.get("raw_batch_id"), "usable_from": usable.isoformat(),
                "first_observed_at": (_instant(row["first_observed_at"], "first_observed_at").isoformat()
                                      if row.get("first_observed_at") is not None else None),
                "availability_basis": basis, "evidence_ref": row.get("evidence_ref"),
                "group_completeness": row.get("group_completeness") if query.domain == "top_holders_reports" else None,
                **({"economic_aliases": phase_aliases[tuple(str(row[k]) for k in keys)]} if phase else {}),
            })
        records.append(record)
    identity_unavailable = sum(scope.get("source_issue") != "phase_date_not_provided" for scope in unavailable_actions)
    limitations = _event_limitations(query, profile, fallback_count, identity_unavailable,
        phase_date_missing=phase and any(scope.get("source_issue") == "phase_date_not_provided"
                                        for scope in unavailable_actions))
    context = {
        "contract_version": "data_batch_v1", "snapshot_id": snapshot_id,
        "domain": query.domain, "contract_id": contract.get("contract_id"),
        "source_profile_id": profile.get("id"), "reader_version": READER_VERSION,
        "event_reader_version": (_ECONOMIC_EVENT_READER_VERSION if contract.get("contract_id") == "local.corporate_actions.tushare.v3"
                                 else EVENT_READER_VERSION), "logical_key": list(keys),
        "query": {"fields": list(query.fields), "symbols": list(query.symbols),
                  "start": start, "end": end, "cutoff": cutoff.isoformat(),
                  "pit_policy": query.pit_policy, "time_field": query.time_field,
                  "filters": deepcopy(dict(query.filters)), "purpose": query.purpose},
        "coverage": deepcopy(domain.get("coverage")) if _sink is None else domain.get('coverage'),
        "limitations": limitations,
        **({"event_phase": _ACTION_PHASES[query.time_field],
            "economic_identity_rule": {k: (profile.get("economic_identity") or {}).get(k)
                                       for k in ("rule", "alias_rule", "native_key_field")},
            "vendor_assumption_id": (profile.get("availability") or {}).get("assumption_id")} if phase else {}),
        **({"unavailable_event_scope": unavailable_actions} if unavailable_actions else {}),
    }
    if _sink is not None:
        scopes = [(encounters[tuple(scope['native_key'][k] for k in keys)], scope)
                  for scope in unavailable_actions]
        return context, fallback_count, scopes
    output_columns = list(dict.fromkeys((*keys, query.time_field,
                                          *(("group_completeness",) if query.domain == "top_holders_reports" else ()),
                                          *query.fields)))
    frame = pd.DataFrame.from_records(records, columns=output_columns)
    nullable_types = {"int": "Int64", "integer": "Int64", "int64": "Int64",
                      "int32": "Int32", "bool": "boolean", "boolean": "boolean",
                      "float": "Float64", "double": "Float64", "float64": "Float64",
                      "float32": "Float32", "string": "string", "str": "string", "utf8": "string"}
    for field in query.fields:
        dtype = nullable_types.get(str(declared[field].get("dtype")).lower())
        if dtype:
            # Construct from the original Arrow scalars. An inferred float
            # intermediate silently rounds large int64 facts beside nulls.
            frame[field] = pd.array([record[field] for record in records], dtype=dtype)
    return DataBatch(frame, field_meta, context)


def _event_limitations(query, profile, fallback_count, unavailable_count, *, phase_date_missing=False):
    limitations = []
    if unavailable_count:
        limitations.append(f'{unavailable_count} whole corporate actions are unavailable: '
            'supplier rows do not distinguish action identity from revision. Missing economic dates '
            'are retained as markers for the affected range; mutable-field filters cannot prove absence. '
            'Do not apply unanimous zero amounts or treat these markers as no company action.')
    if query.pit_policy == "best_effort_vendor_v1":
        limitations.append("vendor event availability is a declared assumption, not revision-bound historical public evidence")
        if unavailable_count:
            limitations.append("unavailable action scope is retrospective uncertainty under retained terminal source content; only cutoff-visible candidate ranges are shown")
    if query.pit_policy == "market_pit_safe_v1" and fallback_count:
        limitations.append(f"{fallback_count} event revisions lacked revision-bound public evidence; first_observed_at was used")
    if profile.get("revision_order") == "terminal_observation_v1":
        limitations.append("terminal states ordered by system observation under declared source policy; vendor revision/publication order is unknown")
    elif profile.get("revision_order") == "announcement_day_then_terminal_v1":
        limitations.append("report versions use declared supplier announcement days; same-day corrections use actual observation order, not a verified publication sequence")
    if phase_date_missing:
        limitations.append("requested phase dates not provided by the source remain missing-date markers; EX dates are not substituted")
    return limitations
