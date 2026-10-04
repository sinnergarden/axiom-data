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


EVENT_READER_VERSION = "event_reader_v6"
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


def _best_effort_time(row: Mapping[str, Any], profile: Mapping[str, Any]) -> datetime:
    """Use only a declared vendor rule; never turn a date into public evidence."""
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
    try:
        zone = ZoneInfo(tz_name)
        local_clock = time.fromisoformat(clock)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise QueryError("invalid vendor availability timezone or release time") from exc
    if local_clock.tzinfo is not None:
        raise QueryError("session_release_time must be local wall-clock time")
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


def _ambiguous_action_dates(store, row, time_field, start, end):
    """Bound a missing action date using its complete retained source group.

    Return evidence only, never choose one candidate date as the event's date.
    Any absent candidate date leaves the requested range uncertain. Known dates
    outside the range do not invalidate unrelated dates for this security.
    """
    from .sources import _rows
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
    candidates = []
    for original in _rows(store.read_raw_record(raw)):
        if original.get('ts_code') != code or original.get('div_proc') != row['process_status']:
            continue
        if any(source_day(original.get(source)) != _date_string(row[field], field)
               for field, source in (('report_period', 'end_date'), ('announcement_date', 'ann_date'))):
            continue
        value = original.get(fields[time_field])
        candidate = None if value in (None, '') else source_day(value)
        if candidate not in candidates:
            candidates.append(candidate)
    if not candidates:
        raise QueryError('ambiguous action lacks its complete referenced Raw group')
    return any(day is None or start <= day <= end for day in candidates), candidates


def read_events(store: Any, snapshot_id: str, query: EventQuery) -> DataBatch:
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
    snapshot = store.load_snapshot(snapshot_id)
    if snapshot.get("snapshot_id") != snapshot_id:
        raise QueryError("loaded Snapshot ID does not match requested ID")
    domain, keys, declared, cutoff, start, end = _validate(query, snapshot)
    contract = domain["contract"]
    profile = domain.get("source_profile") or {}
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
    from .public_evidence import apply_evidence, evidence_index
    evidence = evidence_index(store, snapshot, query.domain)
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for part in domain.get("partitions") or ():
        # Report period is immutable only when included in the logical key.
        # Announcement/effective-date queries cannot use this optimization:
        # a correction may move the selected date across their query boundary.
        label = part.get("partition", "")
        if (query.time_field == "report_period" and "report_period" in keys
                and len(label) == 11 and label.startswith("period-")
                and label[7:].isdigit() and not start[:4] <= label[7:] <= end[:4]):
            continue
        table = store.read_partition(part, columns=columns, symbols=query.symbols)
        for row in apply_evidence(table.to_pylist(), index=evidence, key_fields=keys):
            if row.get("security_id") not in query.symbols:
                continue
            key = tuple(row.get(k) for k in keys)
            if any(v is None for v in key):
                raise QueryError("event revision lacks a logical key")
            grouped.setdefault(key, []).append(row)
    selected: list[tuple[dict[str, Any], tuple[datetime, str]]] = []
    unavailable_actions = []
    fallback_count = 0
    for key, revisions in grouped.items():
        visible = []
        provenance: dict[int, tuple[datetime, str]] = {}
        for row in revisions:
            timing_row = row
            if (query.pit_policy == 'best_effort_vendor_v1' and query.domain == 'corporate_actions' and
                    row.get('source_issue') == 'ambiguous_action_identity_or_revision'):
                _, implementation_dates = _ambiguous_action_dates(
                    store, row, 'implementation_announcement_date', '0001-01-01', '9999-12-31')
                known_dates = [day for day in implementation_dates if day is not None]
                if known_dates:
                    # Lower-bound the whole group's visibility by every known
                    # implementation notice; this does not select an action.
                    timing_row = {**row, 'implementation_announcement_date': max(known_dates)}
            usable, basis = _availability(timing_row, query.pit_policy, profile)
            if query.pit_policy == "market_pit_safe_v1" and basis == "first_observed_at":
                fallback_count += 1
            if usable <= cutoff:
                visible.append(row)
                provenance[id(row)] = usable, basis
        if not visible:
            continue
        chosen = _revision_order(visible, repr(key), profile=profile)
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
            in_scope, candidates = _ambiguous_action_dates(store, chosen, query.time_field, start, end)
            if not in_scope:
                continue
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
    positions = {symbol: i for i, symbol in enumerate(query.symbols)}
    selected.sort(key=lambda item: (positions[item[0]["security_id"]],
                                    (_date_string(item[0][query.time_field], query.time_field)
                                     if item[0].get(query.time_field) is not None else ''),
                                    tuple(str(item[0][k]) for k in keys)))
    records: list[dict[str, Any]] = []
    field_meta = {field: {"dtype": declared[field].get("dtype"), "unit": declared[field].get("unit"),
                          "basis": declared[field].get("basis"), "by_key": []} for field in query.fields}
    for row, (usable, basis) in selected:
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
            })
        records.append(record)
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
    limitations = []
    if unavailable_actions:
        limitations.append(f'{len(unavailable_actions)} whole corporate actions are unavailable: '
            'supplier rows do not distinguish action identity from revision. Missing economic dates '
            'are retained as markers for the affected range; mutable-field filters cannot prove absence. '
            'Do not apply unanimous zero amounts or treat these markers as no company action.')
    if query.pit_policy == "best_effort_vendor_v1":
        limitations.append("vendor event availability is a declared assumption, not revision-bound historical public evidence")
    if query.pit_policy == "market_pit_safe_v1" and fallback_count:
        limitations.append(f"{fallback_count} event revisions lacked revision-bound public evidence; first_observed_at was used")
    if profile.get("revision_order") == "terminal_observation_v1":
        limitations.append("terminal states ordered by system observation under declared source policy; vendor revision/publication order is unknown")
    elif profile.get("revision_order") == "announcement_day_then_terminal_v1":
        limitations.append("report versions use declared supplier announcement days; same-day corrections use actual observation order, not a verified publication sequence")
    context = {
        "contract_version": "data_batch_v1", "snapshot_id": snapshot_id,
        "domain": query.domain, "contract_id": contract.get("contract_id"),
        "source_profile_id": profile.get("id"), "reader_version": READER_VERSION,
        "event_reader_version": EVENT_READER_VERSION, "logical_key": list(keys),
        "query": {"fields": list(query.fields), "symbols": list(query.symbols),
                  "start": start, "end": end, "cutoff": cutoff.isoformat(),
                  "pit_policy": query.pit_policy, "time_field": query.time_field,
                  "filters": deepcopy(dict(query.filters)), "purpose": query.purpose},
        "coverage": deepcopy(domain.get("coverage")),
        "limitations": limitations,
        **({"unavailable_event_scope": unavailable_actions} if unavailable_actions else {}),
    }
    return DataBatch(frame, field_meta, context)
