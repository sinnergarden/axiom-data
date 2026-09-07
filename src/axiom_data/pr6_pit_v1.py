"""Frozen published PR6 v1 PIT semantics; only for versioned artifact validation."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from axiom_data.domains.market import MarketContractError

POLICIES = frozenset({'market_pit_safe_v1', 'operational_pit_v1', 'best_effort_vendor_v1'})


def instant(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (AttributeError, TypeError, ValueError) as exc:
        raise MarketContractError('knowledge time requires an ISO timestamp') from exc
    if result.tzinfo is None:
        raise MarketContractError('knowledge time requires a UTC offset')
    return result.astimezone(timezone.utc)


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def usable_from(row: Mapping[str, Any], policy: str) -> str | None:
    if policy not in POLICIES:
        raise MarketContractError('unsupported PIT policy')
    qualification = row['pit_qualification']
    if qualification == 'verified':
        raise MarketContractError('VERIFIED_EVIDENCE_UNAVAILABLE')
    if qualification not in {'observed', 'best_effort', 'unknown'}:
        raise MarketContractError('invalid PIT qualification')
    observed = instant(row['first_observed_at'])
    if row['source_available_at'] is not None:
        raise MarketContractError('untyped source availability cannot prove a revision')
    if qualification == 'unknown':
        return None
    if policy == 'best_effort_vendor_v1':
        vendor = row.get('vendor_available_at')
        return instant(vendor).isoformat() if vendor else observed.isoformat()
    # A bootstrap row becomes operationally readable at its actual observation,
    # without upgrading its historical evidence qualification.
    return observed.isoformat()


def select_revisions(rows: Sequence[Mapping[str, Any]], *, policy: str,
                     knowledge_cutoff: str) -> tuple[dict[str, Any], ...]:
    cutoff = instant(knowledge_cutoff)
    if policy not in POLICIES:
        raise MarketContractError('unsupported PIT policy')
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for original in rows:
        usable = usable_from(original, policy)
        if usable is None or instant(usable) > cutoff:
            continue
        row = dict(original, usable_from=usable)
        groups[row['logical_event_key']].append(row)
    selected = []
    for key, versions in sorted(groups.items()):
        newest = max(instant(r['usable_from']) for r in versions)
        winners = [r for r in versions if instant(r['usable_from']) == newest]
        if len({r['revision_id'] for r in winners}) != 1:
            raise MarketContractError(f'ambiguous simultaneous revisions: {key}')
        selected.append(winners[0])
    return tuple(selected)


def members(rows: Sequence[Mapping[str, Any]], *, target_session: str,
            knowledge_cutoff: str, policy: str, group_id: str) -> tuple[dict[str, Any], ...]:
    selected = select_revisions(rows, policy=policy, knowledge_cutoff=knowledge_cutoff)
    active = [r for r in selected if r['group_id'] == group_id
              and r['effective_from'] <= target_session
              and (r['effective_to'] is None or target_session < r['effective_to'])]
    symbols = [r['symbol'] for r in active]
    if len(symbols) != len(set(symbols)):
        raise MarketContractError('overlapping or conflicting PIT membership intervals')
    return tuple(sorted(active, key=lambda r: r['symbol']))


def historical_union(rows: Sequence[Mapping[str, Any]], *, group_id: str,
                     start_session: str, end_session: str, lookback_start: str,
                     knowledge_cutoff: str, policy: str) -> tuple[str, ...]:
    if not lookback_start <= start_session <= end_session:
        raise MarketContractError('invalid historical read interval')
    selected = select_revisions(rows, policy=policy, knowledge_cutoff=knowledge_cutoff)
    return tuple(sorted({r['symbol'] for r in selected if r['group_id'] == group_id
                         and r['effective_from'] <= end_session
                         and (r['effective_to'] is None or r['effective_to'] > lookback_start)}))


def _quarter(period: str) -> int:
    suffixes = {'03-31': 0, '06-30': 1, '09-30': 2, '12-31': 3}
    if len(period) != 10 or period[5:] not in suffixes:
        raise MarketContractError('stable financial derivation requires calendar fiscal quarters')
    return int(period[:4]) * 4 + suffixes[period[5:]]


def _value(row: Mapping[str, Any], field: str) -> float | None:
    value = row['values'].get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise MarketContractError('financial value must be finite or null')
    return value


def financial_derived(rows: Sequence[Mapping[str, Any]], *, policy: str,
                      knowledge_cutoff: str) -> tuple[dict[str, Any], ...]:
    """Derive only after as-of selection. Missing components retain an explicit reason."""
    selected = select_revisions(rows, policy=policy, knowledge_cutoff=knowledge_cutoff)
    income = [r for r in selected if r['endpoint'] == 'income']
    by_series: dict[tuple[str, str], dict[int, dict[str, Any]]] = defaultdict(dict)
    for r in income:
        quarter = _quarter(r['report_period'])
        series = by_series[(r['symbol'], r['report_type'])]
        if quarter in series:
            raise MarketContractError('multiple logical events for one financial period/type')
        series[quarter] = r
    result = []
    for (symbol, report_type), series in sorted(by_series.items()):
        quarters: dict[tuple[int, str], dict[str, Any]] = {}
        for q, current in sorted(series.items()):
            for field in ('revenue', 'oper_cost', 'net_income'):
                components = [current]
                reason = None
                value = _value(current, field)
                if report_type != '1':
                    reason = 'incompatible_report_type'
                elif q % 4:
                    previous = series.get(q - 1)
                    if previous is None:
                        reason = 'missing_previous_cumulative_quarter'
                    else:
                        components.append(previous)
                        prior_value = _value(previous, field)
                        value = value - prior_value if value is not None and prior_value is not None else None
                if value is None and reason is None:
                    reason = 'missing_source_field'
                if reason:
                    value = None
                derived = _derived_row(symbol, current['report_period'], report_type,
                                       'single_quarter_' + field, value, reason,
                                       components, policy, knowledge_cutoff)
                quarters[(q, field)] = derived
                # Net income single-quarter is an internal TTM component, not a new leaf.
                if field != 'net_income':
                    result.append(derived)
            for field in ('revenue', 'net_income'):
                parts = [quarters.get((i, field)) for i in range(q - 3, q + 1)]
                reason = None
                if any(p is None for p in parts):
                    reason = 'missing_quarter'
                elif any(p['value'] is None for p in parts):
                    reason = 'invalid_component_quarter'
                value = None if reason else sum(p['value'] for p in parts)
                components = [p for p in parts if p is not None]
                ttm = _derived_row(symbol, current['report_period'], report_type,
                                   'ttm_' + field, value, reason, components,
                                   policy, knowledge_cutoff)
                ttm['quarter_components'] = [
                    {'report_period': p['report_period'], 'derived_id': p['derived_id'],
                     'component_revisions': p['component_revisions'], 'value': p['value'],
                     'usable_from': p['usable_from']} for p in components]
                ttm['derived_id'] = fingerprint({k:v for k,v in ttm.items() if k != 'derived_id'})
                result.append(ttm)
    return tuple(result)


def _derived_row(symbol: str, period: str, report_type: str, field: str,
                 value: float | None, reason: str | None, components: Sequence[Mapping[str, Any]],
                 policy: str, cutoff: str) -> dict[str, Any]:
    refs = []
    for row in components:
        refs.extend(row.get('component_revisions', [{
            'logical_event_key': row.get('logical_event_key'),
            'revision_id': row.get('revision_id'), 'source_ref': row.get('source_ref'),
            'report_period': row['report_period'], 'usable_from': row['usable_from']}]))
    refs = sorted({fingerprint(r): r for r in refs}.values(), key=fingerprint)
    usable = max((r['usable_from'] for r in components), key=instant, default=None)
    output = {'symbol': symbol, 'report_period': period, 'report_type': report_type,
              'field': field, 'value': value, 'missing_reason': reason,
              'usable_from': usable, 'component_revisions': refs,
              'pit_policy': policy, 'knowledge_cutoff': instant(cutoff).isoformat(),
              'contract_version': 'financial_stable.v1', 'unit': 'CNY'}
    output['derived_id'] = fingerprint(output)
    return output
