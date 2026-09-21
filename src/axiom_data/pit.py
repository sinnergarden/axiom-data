"""Revision selection and stable financial arithmetic, without I/O or implicit clocks."""

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
                     knowledge_cutoff: str, group_states=None, _financial_leaves=False) -> tuple[dict[str, Any], ...]:
    if group_states is not None:
        return _group_members(rows,group_states,policy,knowledge_cutoff)
    cutoff = instant(knowledge_cutoff)
    if policy not in POLICIES:
        raise MarketContractError('unsupported PIT policy')
    if rows and 'observations' in rows[0]:
        return _select_observations(rows, policy, knowledge_cutoff, _financial_leaves)
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
            if _financial_leaves:
                selected.append(_financial_tie(winners))
                continue
            raise MarketContractError(f'ambiguous simultaneous revisions: {key}')
        selected.append(winners[0])
    return tuple(selected)


def _select_observations(rows, policy, knowledge_cutoff, financial_leaves=False):
    if any('group_state_ref' in o for r in rows for o in r['observations']):
        raise MarketContractError('universe group_states required; member rows alone are incomplete')
    cutoff = instant(knowledge_cutoff)
    groups = defaultdict(list)
    universe = any(o.get('state_id') for r in rows for o in r['observations'])
    for row in rows:
        for observation in row['observations']:
            candidate = dict(row, first_observed_at=observation['observed_at'],
                             vendor_available_at=observation['vendor_available_at'])
            usable = usable_from(candidate, policy)
            # A reconstructed universe state is a best-effort terminal history;
            # effective bounds, never observation freshness, constrain membership.
            if universe and policy == 'best_effort_vendor_v1':
                usable = min(o['vendor_available_at'] for r in rows for o in r['observations']
                             if o['state_id']==observation['state_id'])
            if usable is None or instant(usable)>cutoff: continue
            group = row['group_id'] if universe else row['logical_event_key']
            order = ((instant(observation['observed_at']),) if universe else
                     (instant(usable),instant(observation['observed_at'])))
            selected = dict({k:v for k,v in row.items() if k!='observations'}, usable_from=usable, observation_ref=observation,
                            source_ref=observation['source_ref'])
            groups[group].append((order, selected))
    result=[]
    for key, candidates in sorted(groups.items()):
        newest=max(order for order,_ in candidates)
        winners=[r for order,r in candidates if order==newest]
        if universe:
            if len({r['observation_ref']['state_id'] for r in winners})!=1:
                raise MarketContractError('ambiguous simultaneous membership states')
            result.extend(winners)
        else:
            if len({r['revision_id'] for r in winners})!=1:
                if financial_leaves:
                    result.append(_financial_tie(winners))
                    continue
                raise MarketContractError(f'ambiguous simultaneous revisions: {key}')
            result.append(min(winners,key=lambda r:r['observation_ref']['observation_id']))
    return tuple(sorted(result,key=lambda r:(r['logical_event_key'],r['revision_id'])))


AMBIGUOUS_SOURCE_REVISION = 'AMBIGUOUS_SOURCE_REVISION'


def _financial_tie(winners):
    """A consumption result over all tied sources, never a canonical winner."""
    if any(r.get('endpoint') not in {'income','balancesheet','cashflow','fina_indicator'} for r in winners):
        raise MarketContractError('financial leaf resolution requires financial records')
    common = {k:v for k,v in winners[0].items()
              if all(k in r and r[k] == v for r in winners)}
    refs = [{ 'logical_event_key':r['logical_event_key'], 'revision_id':r['revision_id'],
              'source_ref':r['source_ref'], 'observation_ref':r.get('observation_ref'),
              'report_period':r['report_period'], 'usable_from':r['usable_from']}
            for r in winners]
    refs = sorted({fingerprint(r):r for r in refs}.values(), key=fingerprint)
    values={};reasons={};conflicts=[]
    for field in sorted(set().union(*(r['values'] for r in winners))):
        candidates=[r['values'].get(field) for r in winners]
        if any(v != candidates[0] for v in candidates):
            values[field]=None;reasons[field]=AMBIGUOUS_SOURCE_REVISION;conflicts.append(field)
        else:
            values[field]=candidates[0]
            if candidates[0] is None:
                missing={r.get('missing_reasons',{}).get(field,'source_value_missing') for r in winners}
                reasons[field]=next(iter(missing)) if len(missing)==1 else 'source_value_missing'
    common.update(values=values, missing_reasons=reasons, revision_id=None,
                  source_ref=None, observation_ref=None, component_revisions=refs,
                  ambiguous_fields=conflicts, resolution_policy='financial_leaf_resolution.v1',
                  pit_qualification='best_effort' if any(r['pit_qualification']=='best_effort' for r in winners) else 'observed')
    common['resolution_id']=fingerprint({'values':values,'missing_reasons':reasons,'components':refs})
    return common


def select_financial_revisions(rows, *, policy, knowledge_cutoff):
    return select_revisions(rows, policy=policy, knowledge_cutoff=knowledge_cutoff,
                            _financial_leaves=True)


def financial_ambiguities(rows, *, policy, knowledge_cutoff):
    """Complete visible tie intervals; future observations do not close old intervals."""
    cutoff=instant(knowledge_cutoff);groups=defaultdict(list);result=[]
    for row in rows:
        groups[row['logical_event_key']].append(row)
    for key, versions in sorted(groups.items()):
        if len({r['revision_id'] for r in versions})<2:continue
        boundaries=set()
        for row in versions:
            for observation in row.get('observations',[None]):
                candidate=(dict(row,first_observed_at=observation['observed_at'],
                                vendor_available_at=observation['vendor_available_at']) if observation else row)
                usable=usable_from(candidate,policy)
                if usable is not None and instant(usable)<=cutoff:boundaries.add(instant(usable))
        times=sorted(boundaries)
        for i, start in enumerate(times):
            selected=select_financial_revisions(versions,policy=policy,knowledge_cutoff=start.isoformat())
            for row in selected:
                if 'resolution_id' not in row:continue
                result.append({'logical_event_key':key,'symbol':row['symbol'],
                    'report_period':row['report_period'],'endpoint':row['endpoint'],
                    'from':start.isoformat(),'to_exclusive':times[i+1].isoformat() if i+1<len(times) else None,
                    'ambiguous_fields':row['ambiguous_fields'],'resolution_id':row['resolution_id'],
                    'component_revisions':row['component_revisions']})
    return result


def select_group_states(states, *, policy, knowledge_cutoff):
    if policy not in POLICIES:raise MarketContractError('unsupported PIT policy')
    cutoff=instant(knowledge_cutoff);groups=defaultdict(list)
    for state in states:
        usable=usable_from(state,policy)
        if usable is not None and instant(usable)<=cutoff:
            groups[state['universe_id']].append(dict(state,usable_from=usable))
    selected=[]
    for group,versions in sorted(groups.items()):
        newest=max(instant(s['first_observed_at']) for s in versions)
        winners=[s for s in versions if instant(s['first_observed_at'])==newest]
        if len({s['state_id'] for s in winners})!=1:
            raise MarketContractError('ambiguous simultaneous universe group states')
        selected.append(winners[0])
    return tuple(selected)


def _group_members(rows,states,policy,cutoff):
    selected=[]
    for state in select_group_states(states,policy=policy,knowledge_cutoff=cutoff):
        found=[]
        for row in rows:
            if row['group_id']!=state['universe_id']:continue
            for observation in row['observations']:
                if observation['state_id']==state['state_id']:
                    found.append(dict({k:v for k,v in row.items() if k!='observations'},
                        observation_ref=observation,usable_from=state['usable_from'],
                        source_ref=observation['source_ref']))
        if sorted(r['revision_id'] for r in found)!=state['member_revision_refs']:
            raise MarketContractError('universe state member closure mismatch')
        selected.extend(found)
    return tuple(sorted(selected,key=lambda row:(row['logical_event_key'],row['revision_id'])))


def members(rows: Sequence[Mapping[str, Any]], *, target_session: str,
            knowledge_cutoff: str, policy: str, group_id: str, group_states=None) -> tuple[dict[str, Any], ...]:
    selected = select_revisions(rows, policy=policy, knowledge_cutoff=knowledge_cutoff, group_states=group_states)
    active = [r for r in selected if r['group_id'] == group_id
              and r['effective_from'] <= target_session
              and (r['effective_to'] is None or target_session < r['effective_to'])]
    symbols = [r['symbol'] for r in active]
    if len(symbols) != len(set(symbols)):
        raise MarketContractError('overlapping or conflicting PIT membership intervals')
    return tuple(sorted(active, key=lambda r: r['symbol']))


def historical_union(rows: Sequence[Mapping[str, Any]], *, group_id: str,
                     start_session: str, end_session: str, lookback_start: str,
                     knowledge_cutoff: str, policy: str, group_states=None) -> tuple[str, ...]:
    if not lookback_start <= start_session <= end_session:
        raise MarketContractError('invalid historical read interval')
    selected = select_revisions(rows, policy=policy, knowledge_cutoff=knowledge_cutoff, group_states=group_states)
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
                      knowledge_cutoff: str, resolve_ambiguity=True) -> tuple[dict[str, Any], ...]:
    """Derive only after as-of selection. Missing components retain an explicit reason."""
    selector=select_financial_revisions if resolve_ambiguity else select_revisions
    selected = selector(rows, policy=policy, knowledge_cutoff=knowledge_cutoff)
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
                        reason = 'missing_quarter'
                    else:
                        components.append(previous)
                        prior_value = _value(previous, field)
                        value = value - prior_value if value is not None and prior_value is not None else None
                if any(r.get('missing_reasons',{}).get(field)==AMBIGUOUS_SOURCE_REVISION for r in components):
                    reason=AMBIGUOUS_SOURCE_REVISION
                if value is None and reason is None:
                    reason = 'source_value_missing'
                if reason:
                    value = None
                derived = _derived_row(symbol, current['report_period'], report_type,
                                       'single_quarter_' + field, value, reason,
                                       components, policy, knowledge_cutoff,
                                       contract_version='financial_stable.v3' if resolve_ambiguity else 'financial_stable.v2')
                quarters[(q, field)] = derived
                # Net income single-quarter is an internal TTM component, not a new leaf.
                if field != 'net_income':
                    result.append(derived)
            for field in ('revenue', 'net_income'):
                parts = [quarters.get((i, field)) for i in range(q - 3, q + 1)]
                reason = None
                if any(p is not None and p['missing_reason']==AMBIGUOUS_SOURCE_REVISION for p in parts):
                    reason=AMBIGUOUS_SOURCE_REVISION
                elif report_type != '1':
                    reason = 'incompatible_report_type'
                elif any(p is None for p in parts):
                    reason = 'missing_quarter'
                elif any(p['value'] is None for p in parts):
                    reason = next((p['missing_reason'] for p in parts if p['missing_reason']), 'invalid_component')
                value = None if reason else sum(p['value'] for p in parts)
                components = [p for p in parts if p is not None]
                ttm = _derived_row(symbol, current['report_period'], report_type,
                                   'ttm_' + field, value, reason, components,
                                   policy, knowledge_cutoff,
                                   contract_version='financial_stable.v3' if resolve_ambiguity else 'financial_stable.v2')
                ttm['expected_quarters'] = [str(i//4)+'-'+('03-31','06-30','09-30','12-31')[i%4] for i in range(q-3,q+1)]
                ttm['quarter_components'] = [
                    {'report_period': p['report_period'], 'derived_id': p['derived_id'],
                     'component_revisions': p['component_revisions'], 'value': p['value'],
                     'missing_reason':p['missing_reason'],
                     'usable_from': p['usable_from']} for p in components]
                ttm['derived_id'] = fingerprint({k:v for k,v in ttm.items() if k != 'derived_id'})
                result.append(ttm)
    return tuple(result)


def _derived_row(symbol: str, period: str, report_type: str, field: str,
                 value: float | None, reason: str | None, components: Sequence[Mapping[str, Any]],
                 policy: str, cutoff: str, *, contract_version: str = 'financial_stable.v2') -> dict[str, Any]:
    refs = []
    for row in components:
        refs.extend(row.get('component_revisions', [{
            'logical_event_key': row.get('logical_event_key'),
            'revision_id': row.get('revision_id'), 'source_ref': row.get('source_ref'),
            'observation_ref': row.get('observation_ref'),
            'report_period': row['report_period'], 'usable_from': row['usable_from']}]))
    refs = sorted({fingerprint(r): r for r in refs}.values(), key=fingerprint)
    usable = max((r['usable_from'] for r in components), key=instant, default=None)
    output = {'symbol': symbol, 'report_period': period, 'report_type': report_type,
              'field': field, 'value': value, 'missing_reason': reason,
              'usable_from': usable, 'component_revisions': refs,
              'pit_policy': policy, 'knowledge_cutoff': instant(cutoff).isoformat(),
              'contract_version': contract_version, 'unit': 'CNY',
              'validity': 'unavailable' if reason == AMBIGUOUS_SOURCE_REVISION else 'missing' if value is None else 'valid',
              'quality_state': 'BLOCKED' if reason else 'PASS',
              'pit_qualification': 'best_effort' if any(r.get('pit_qualification')=='best_effort' for r in components) else 'observed'}
    output['derived_id'] = fingerprint(output)
    return output
