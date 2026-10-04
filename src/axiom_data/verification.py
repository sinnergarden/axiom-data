"""Read-only, source-relative acceptance for a pinned Data Snapshot.

Verify referenced bytes and declared market/event conversions in partitions.
Report gaps against the saved calendar/listing facts without inventing prices,
normal trading states or vendor completeness. No source calls or repairs occur.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from functools import lru_cache
import json
import math
import time
from typing import Any, Mapping

from .protocols import DataError
from .sources import _rows
from .storage import LocalStore


_EVENT_FIELDS = {
    'financial_events': {
        'report_period': 'end_date', 'announcement_date': 'ann_date',
        'actual_announcement_date': 'f_ann_date', 'report_type': 'report_type',
        'total_revenue': 'total_revenue', 'parent_net_income': 'n_income_attr_p',
    },
    'balance_sheet_events': {
        'report_period': 'end_date', 'announcement_date': 'ann_date',
        'actual_announcement_date': 'f_ann_date', 'report_type': 'report_type',
        'total_assets': 'total_assets', 'total_liabilities': 'total_liab',
        'parent_equity': 'total_hldr_eqy_exc_min_int',
    },
    'cash_flow_events': {
        'report_period': 'end_date', 'announcement_date': 'ann_date',
        'actual_announcement_date': 'f_ann_date', 'report_type': 'report_type',
        'operating_cash_flow': 'n_cashflow_act',
        'investing_cash_flow': 'n_cashflow_inv_act',
        'financing_cash_flow': 'n_cash_flows_fnc_act',
    },
    'financial_indicator_events': {
        'report_period': 'end_date', 'announcement_date': 'ann_date',
        'actual_announcement_date': 'ann_date',
        'roe': 'roe', 'weighted_roe': 'roe_waa',
        'debt_to_assets': 'debt_to_assets',
    },
    'corporate_actions': {
        'report_period': 'end_date', 'announcement_date': 'ann_date',
        'process_status': 'div_proc',
        'cash_dividend_before_tax_per_share': 'cash_div_tax',
        'bonus_shares_per_share': 'stk_bo_rate',
        'capital_transfer_shares_per_share': 'stk_co_rate',
        'implementation_announcement_date': 'imp_ann_date',
        'record_date': 'record_date', 'ex_date': 'ex_date',
    },
    'top_holders_reports': {
        'report_period': 'end_date', 'announcement_date': 'ann_date',
        'actual_announcement_date': 'ann_date',
    },
    'price_limits': {'session': 'trade_date', 'up_limit': 'up_limit',
                     'down_limit': 'down_limit'},
}
_EVENT_ENDPOINTS = {'financial_events': {'income', 'income_vip'},
                    'balance_sheet_events': {'balancesheet', 'balancesheet_vip'},
                    'cash_flow_events': {'cashflow', 'cashflow_vip'},
                    'financial_indicator_events': {'fina_indicator', 'fina_indicator_vip'},
                    'corporate_actions': {'dividend'},
                    'top_holders_reports': {'top10_holders'},
                    'price_limits': {'stk_limit'}}
_EVENT_DATE_FIELDS = {'report_period', 'announcement_date', 'actual_announcement_date',
                      'implementation_announcement_date', 'record_date', 'ex_date', 'session'}
_EVENT_NUMBER_FIELDS = {'total_revenue', 'parent_net_income',
                        'total_assets', 'total_liabilities', 'parent_equity',
                        'operating_cash_flow', 'investing_cash_flow',
                        'financing_cash_flow', 'roe', 'weighted_roe',
                        'debt_to_assets',
                        'cash_dividend_before_tax_per_share', 'bonus_shares_per_share',
                        'capital_transfer_shares_per_share', 'top10_ratio',
                        'up_limit', 'down_limit'}
_EVENT_UNITS = {
    'financial_events': {'total_revenue': 'CNY', 'parent_net_income': 'CNY'},
    'balance_sheet_events': {'total_assets': 'CNY', 'total_liabilities': 'CNY',
                             'parent_equity': 'CNY'},
    'cash_flow_events': {'operating_cash_flow': 'CNY', 'investing_cash_flow': 'CNY',
                         'financing_cash_flow': 'CNY'},
    'financial_indicator_events': {'roe': 'percent', 'weighted_roe': 'percent',
                                   'debt_to_assets': 'percent'},
    'corporate_actions': {'cash_dividend_before_tax_per_share': 'CNY/share',
                          'bonus_shares_per_share': 'shares/share',
                          'capital_transfer_shares_per_share': 'shares/share'},
    'price_limits': {'up_limit': 'CNY/share', 'down_limit': 'CNY/share'},
}
_EVENT_BASES = {
    'financial_events': {'total_revenue': 'cumulative_ytd',
                         'parent_net_income': 'cumulative_ytd'},
    'balance_sheet_events': {'total_assets': 'period_end_stock',
                             'total_liabilities': 'period_end_stock',
                             'parent_equity': 'period_end_stock'},
    'cash_flow_events': {'operating_cash_flow': 'cumulative_ytd',
                         'investing_cash_flow': 'cumulative_ytd',
                         'financing_cash_flow': 'cumulative_ytd'},
}


@lru_cache(maxsize=32768)
def _source_date8(value: str) -> str:
    try:
        return date(int(value[:4]), int(value[4:6]), int(value[6:8])).isoformat()
    except ValueError as exc:
        raise DataError(f'event Raw has an invalid YYYYMMDD date: {value!r}') from exc


def _source_date(value: Any) -> str | None:
    if value in (None, ''):
        return None
    if not isinstance(value, str) or len(value) != 8 or not value.isascii() or not value.isdigit():
        raise DataError(f'event Raw has an invalid YYYYMMDD date: {value!r}')
    return _source_date8(value)


def _canonical_date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        raise DataError('canonical event date contains a timestamp')
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise DataError(f'canonical event date is invalid: {value!r}') from exc
        if parsed.isoformat() == value:
            return value
    raise DataError(f'canonical event date is invalid: {value!r}')


def _equal_event_value(field: str, actual: Any, original: Any) -> bool:
    if field in _EVENT_DATE_FIELDS:
        return _canonical_date(actual) == _source_date(original)
    if original is None:
        return actual is None
    if field in _EVENT_NUMBER_FIELDS:
        return actual is not None and math.isclose(float(actual), float(Decimal(str(original))),
                                                     rel_tol=1e-12, abs_tol=1e-8)
    return actual == original


def _audit_raw_observation(row: Mapping[str, Any], raw: Mapping[str, Any]) -> None:
    observed = row.get('first_observed_at')
    if isinstance(observed, str):
        try:
            observed = datetime.fromisoformat(observed.replace('Z', '+00:00'))
        except ValueError as exc:
            raise DataError('canonical first observation timestamp is invalid') from exc
    try:
        receipt = datetime.fromisoformat(raw['observed_at'].replace('Z', '+00:00'))
    except (KeyError, AttributeError, ValueError) as exc:
        raise DataError('referenced Raw receipt timestamp is invalid') from exc
    if (not isinstance(observed, datetime) or observed.tzinfo is None or
            observed.utcoffset() is None or receipt.tzinfo is None or
            receipt.utcoffset() is None or
            observed.astimezone(timezone.utc) != receipt.astimezone(timezone.utc)):
        raise DataError('canonical first observation differs from referenced Raw receipt')


def _event_key(name: str, row: Mapping[str, Any], *, source: bool) -> tuple[Any, ...]:
    if source:
        code = row.get('ts_code')
        if name in {'financial_events', 'balance_sheet_events', 'cash_flow_events'}:
            return code, row.get('report_type'), _source_date(row.get('end_date')), _source_date(row.get('f_ann_date'))
        if name == 'financial_indicator_events':
            return code, _source_date(row.get('end_date')), _source_date(row.get('ann_date'))
        if name == 'corporate_actions':
            return code, _source_date(row.get('end_date')), _source_date(row.get('ann_date')), row.get('div_proc')
        if name == 'top_holders_reports':
            return code, _source_date(row.get('end_date')), _source_date(row.get('ann_date'))
        return code, _source_date(row.get('trade_date'))
    code = row['source_code']
    if name in {'financial_events', 'balance_sheet_events', 'cash_flow_events'}:
        return code, row.get('report_type'), _canonical_date(row.get('report_period')), _canonical_date(row.get('actual_announcement_date'))
    if name == 'financial_indicator_events':
        return code, _canonical_date(row.get('report_period')), _canonical_date(row.get('actual_announcement_date'))
    if name == 'corporate_actions':
        return code, _canonical_date(row.get('report_period')), _canonical_date(row.get('announcement_date')), row.get('process_status')
    if name == 'top_holders_reports':
        return code, _canonical_date(row.get('report_period')), _canonical_date(row.get('actual_announcement_date'))
    return code, _canonical_date(row.get('session'))


def _audit_holder_group(row: Mapping[str, Any], originals: list[Mapping[str, Any]]) -> None:
    """Compare one atomic report to its complete retained supplier row group."""
    def same_source_scalar(actual: Any, original: Any) -> bool:
        if type(original) in (int, float) and math.isfinite(original):
            return type(actual) in (int, float) and math.isclose(
                float(actual), float(original), rel_tol=1e-12, abs_tol=1e-8)
        return actual == original

    names = [item.get('holder_name') for item in originals]
    if any(not isinstance(name, str) for name in names):
        raise DataError('top_holders_reports Raw has an invalid holder name')
    if len(set(names)) != len(names):
        flag, issue = 'ambiguous_duplicate_holder', 'duplicate_holder'
    elif len(originals) == 10 and all(
            item['holder_name'] and type(item.get('hold_amount')) in (int, float) and
            math.isfinite(item['hold_amount']) and type(item.get('hold_ratio')) in (int, float) and
            math.isfinite(item['hold_ratio']) for item in originals):
        flag, issue = 'supplier_report_complete', None
    else:
        flag, issue = 'partial_supplier_report', 'row_count_or_missing_fields'
    try:
        holders = json.loads(row.get('holders'))
    except (TypeError, ValueError) as exc:
        raise DataError('top_holders_reports holders is not valid JSON') from exc
    if not isinstance(holders, list) or len(holders) != len(originals):
        raise DataError('top_holders_reports holders list differs from Raw group')
    for actual, original in zip(holders, sorted(originals, key=lambda item: item['holder_name'])):
        if not isinstance(actual, dict) or set(actual) != {
                'holder_name', 'hold_amount_shares', 'hold_ratio_percent'}:
            raise DataError('top_holders_reports holder fields differ from Raw group')
        if (actual['holder_name'] != original['holder_name'] or
                not same_source_scalar(actual['hold_amount_shares'],
                                       original.get('hold_amount')) or
                not same_source_scalar(actual['hold_ratio_percent'],
                                       original.get('hold_ratio'))):
            raise DataError('top_holders_reports holder amounts or names differ from Raw group')
    expected_ratio = sum(item['hold_ratio'] for item in originals) if issue is None else None
    if (row.get('holder_count') != len(originals) or
            row.get('group_completeness') != flag or row.get('group_issue') != issue or
            not _equal_event_value('top10_ratio', row.get('top10_ratio'), expected_ratio)):
        raise DataError('top_holders_reports completeness or ratio differs from Raw group')


def _audit_event_raw(store: LocalStore, name: str, domain: Mapping[str, Any],
                     raw_id: str, canonical: list[dict[str, Any]]) -> int:
    raw = store.get_raw(raw_id)
    original_id = raw.get('request', {}).get('revalidated_from_batch_id')
    if original_id:
        original = store.get_raw(original_id)
        original_request = dict(raw['request'])
        original_request.pop('revalidated_from_batch_id')
        # Reinterpretation may change contract/profile/operation, never the
        # original request, response bytes or receipt clock.
        for field in ('endpoint', 'params', 'fields', 'canonical_symbols'):
            if original_request.get(field) != original.get('request', {}).get(field):
                raise DataError(f'{name} revalidation changed its original source selector')
        if any(raw.get(field) != original.get(field) for field in
               ('payload_sha256', 'payload_uri', 'observed_at', 'domain')):
            raise DataError(f'{name} revalidation changed original bytes or receipt')
    endpoint = raw.get('request', {}).get('endpoint')
    if (raw_id not in domain['raw_batch_ids'] or raw.get('domain') != name or
            raw.get('status') not in {'success', 'empty'} or
            (canonical and raw.get('status') != 'success') or
            raw.get('normalizer') != 'event_records_v1' or
            endpoint not in _EVENT_ENDPOINTS[name] or raw.get('contract') != domain['contract']):
        raise DataError(f'{name} references incompatible event Raw')
    profile = raw.get('source_profile') or {}
    source_map = profile.get('field_map') or {}
    if source_map.get('security_id') != 'ts_code' or any(
            source_map.get(field) != source for field, source in _EVENT_FIELDS[name].items()):
        raise DataError(f'{name} source field map differs from audited contract')
    for field, unit in _EVENT_UNITS.get(name, {}).items():
        source = _EVENT_FIELDS[name][field]
        if (domain['contract']['fields'].get(field, {}).get('unit') != unit or
                profile.get('source_units', {}).get(source) != unit):
            raise DataError(f'{name}.{field} source/contract unit differs from audited unit')
    for field, basis in _EVENT_BASES.get(name, {}).items():
        if domain['contract']['fields'].get(field, {}).get('basis') != basis:
            raise DataError(f'{name}.{field} basis differs from audited financial basis')
    if name in {'financial_events', 'balance_sheet_events', 'cash_flow_events',
                'financial_indicator_events'} and source_map.get('endpoint') != '__endpoint':
        raise DataError(f'{name} declared endpoint mapping differs from audit')
    if name == 'financial_events' and (
            source_map.get('basis') != 'report_type' or
            profile.get('value_maps', {}).get('basis', {}).get('1') != 'cumulative_ytd'):
        raise DataError('financial_events declared report-type mapping differs from audit')
    if name == 'top_holders_reports' and any(
            source_map.get(field) != source for field, source in {
                'holders': '__holders_json', 'holder_count': '__holder_count',
                'group_completeness': '__group_completeness',
                'group_issue': '__group_issue', 'top10_ratio': '__top10_ratio',
            }.items()):
        raise DataError('top_holders_reports declared group mapping differs from audit')
    if name == 'top_holders_reports' and (
            domain['contract']['fields'].get('top10_ratio', {}).get('unit') != 'percent of total shares' or
            profile.get('source_units', {}).get('__top10_ratio') != 'percent of total shares' or
            profile.get('source_units', {}).get('hold_amount') != 'shares' or
            profile.get('source_units', {}).get('hold_ratio') != 'percent of total shares'):
        raise DataError('top_holders_reports source/contract units differ from audited units')
    identities = profile.get('identity_map') or {}
    inverse = {identity: code for code, identity in identities.items()}
    if len(inverse) != len(identities):
        raise DataError(f'{name} Raw identity map is not one-to-one')
    originals = _rows(store.read_raw_record(raw))
    financial = name in {'financial_events', 'balance_sheet_events', 'cash_flow_events',
                          'financial_indicator_events'}
    if financial:
        chosen = set(raw.get('request', {}).get('canonical_symbols') or ())
        originals = [item for item in originals if item.get('ts_code') in chosen]
    source_rows = defaultdict(list)
    for original in originals:
        source_rows[_event_key(name, original, source=True)].append(original)
    checked = 0
    for row in canonical:
        _audit_raw_observation(row, raw)
        code = inverse.get(row.get('security_id'))
        if code is None:
            raise DataError(f'{name} canonical security lacks its Raw stable identity')
        key = _event_key(name, {**row, 'source_code': code}, source=False)
        candidates = source_rows.get(key)
        if not candidates or any(item is None for item in key):
            raise DataError(f'{name} canonical economic key absent from referenced Raw')
        if financial:
            candidates = [_audit_financial_group(name, row, candidates, domain['contract'], source_map)]
        elif name == 'corporate_actions':
            candidates = [_audit_dividend_group(row, candidates, domain['contract'], source_map)]
        for original in candidates:
            for field, source in _EVENT_FIELDS[name].items():
                if not _equal_event_value(field, row.get(field), original.get(source)):
                    raise DataError(f'{name}.{field} differs from referenced Raw')
            if name in {'financial_events', 'balance_sheet_events', 'cash_flow_events',
                        'financial_indicator_events'}:
                if row.get('endpoint') != endpoint.removesuffix('_vip'):
                    raise DataError(f'{name} endpoint differs from its Raw selector')
                if name != 'financial_indicator_events' and original.get('report_type') != '1':
                    raise DataError(f'{name} report type differs from supported Raw contract')
                if name == 'financial_events' and row.get('basis') != 'cumulative_ytd':
                    raise DataError('financial_events YTD basis differs from Raw contract')
        if name == 'top_holders_reports':
            _audit_holder_group(row, candidates)
        checked += 1
    return checked


def _audit_dividend_group(row: Mapping[str, Any], originals: list[dict[str, Any]],
                          contract: Mapping[str, Any], source_map: Mapping[str, str]) -> dict[str, Any]:
    """Check whole-action ambiguity independently of the normalization resolver."""
    fields = _EVENT_FIELDS['corporate_actions']
    unique = []
    for original in originals:
        candidate = {source: original.get(source) for source in fields.values()}
        for source in ('imp_ann_date', 'record_date', 'ex_date'):
            if candidate[source] == '':
                candidate[source] = None
        if candidate not in unique:
            unique.append(candidate)
    ambiguous = len(unique) > 1
    if ambiguous:
        from .sources import _typed
        for original in unique:
            for field, source in fields.items():
                if field not in contract['logical_key']:
                    _typed(original[source], 'date' if field in _EVENT_DATE_FIELDS else 'float64', source, 'YYYYMMDD')
    modern = 'source_issue' in contract['fields']
    if ambiguous and not modern:
        raise DataError('corporate_actions ambiguous source group needs an explicit action rebuild')
    expected = dict(unique[0])
    if modern:
        if (source_map.get('source_issue') != '__source_issue' or
                source_map.get('source_candidate_count') != '__source_candidate_count' or
                source_map.get('candidate_economic_dates') != '__candidate_economic_dates' or
                row.get('source_candidate_count') != len(unique) or
                row.get('source_issue') != ('ambiguous_action_identity_or_revision' if ambiguous else None)):
            raise DataError('corporate_actions source group evidence differs from Raw')
        dates = None
        if ambiguous:
            dates = json.dumps({source: sorted({item[source] for item in unique}, key=lambda value: value or '')
                                for source in ('imp_ann_date', 'record_date', 'ex_date')},
                               sort_keys=True, separators=(',', ':'))
        if row.get('candidate_economic_dates') != dates:
            raise DataError('corporate_actions candidate economic dates differ from its complete Raw group')
        for field, source in fields.items():
            if field in contract['logical_key']:
                continue
            status_field = contract['fields'][field].get('status_field')
            if (status_field != f'{field}__status' or
                    source_map.get(status_field) != f'__status__{source}' or
                    row.get(status_field) != ('source_missing' if ambiguous else None)):
                raise DataError(f'corporate_actions.{field} status differs from its whole source group')
            if ambiguous:
                expected[source] = None
            if not _equal_event_value(field, row.get(field), expected.get(source)):
                raise DataError(f'corporate_actions.{field} differs from its whole source group')
    return expected


def _audit_financial_group(name: str, row: Mapping[str, Any],
                           originals: list[dict[str, Any]], contract: Mapping[str, Any],
                           source_map: Mapping[str, str]) -> dict[str, Any]:
    """Independently check a disclosure against every retained source row.

    Audit does not call the normalizer's resolver: a supplied value must come
    from a returned dominating report or be unanimous across the whole group.
    """
    fields = dict(_EVENT_FIELDS[name])
    modern = 'source_issue' in contract['fields']
    if modern and name != 'financial_indicator_events':
        fields.update(company_type='comp_type', report_end_type='end_type', update_flag='update_flag')
    if modern:
        if source_map.get('source_issue') != '__source_issue' or any(
                source_map.get(field) != source for field, source in fields.items()):
            raise DataError(f'{name} financial context mapping differs from audit')
    sources = set(fields.values())
    dominating = next((item for item in originals if all(
        other.get(source) is None or other.get(source) == item.get(source)
        for other in originals for source in sources)), None)
    ambiguous = set()
    if dominating is not None:
        expected = dominating
    else:
        ambiguous = {source for source in sources
                     if any(item.get(source) != originals[0].get(source) for item in originals)}
        if ambiguous & {'comp_type', 'end_type'}:
            ambiguous.update(fields[field] for field in _EVENT_UNITS[name])
        expected = {source: None if source in ambiguous else originals[0].get(source)
                    for source in sources}
        if not modern:
            raise DataError(f'{name} ambiguous source group needs an explicit financial rebuild')
    if modern:
        issue = 'ambiguous_same_disclosure' if dominating is None else None
        if row.get('source_issue') != issue:
            raise DataError(f'{name}.source_issue differs from referenced Raw group')
        for field, source in fields.items():
            if not _equal_event_value(field, row.get(field), expected.get(source)):
                raise DataError(f'{name}.{field} differs from referenced Raw')
            spec = contract['fields'][field]
            if status_field := spec.get('status_field'):
                if (status_field != f'{field}__status' or
                        source_map.get(status_field) != f'__status__{source}'):
                    raise DataError(f'{name}.{field} status mapping differs from audit')
                status = 'source_missing' if source in ambiguous else None
                if row.get(status_field) != status:
                    raise DataError(f'{name}.{field} status differs from referenced Raw group')
    return expected


def _audit_listing_raw(store: LocalStore, domain: Mapping[str, Any], raw_id: str,
                       canonical: list[dict[str, Any]]) -> int:
    raw = store.get_raw(raw_id)
    if (raw_id not in domain['raw_batch_ids'] or
            raw.get('domain') != 'reference_bootstrap' or
            raw.get('request', {}).get('endpoint') != 'stock_basic' or
            raw.get('status') not in {'success', 'empty'}):
        raise DataError('listing_events references incompatible stock_basic Raw')
    source = {item['ts_code']: item for item in _rows(store.read_raw_record(raw))}
    from .vendor_listing import vendor_listing_source_chain
    identity = vendor_listing_source_chain(store, domain)[-1]['identity_map']
    for row in canonical:
        # A disappeared supplier event is an explicit revision, not a new
        # invented source date; its original version retains the Raw link.
        if row.get('event_state') == 'source_missing':
            continue
        _audit_raw_observation(row, raw)
        original = source.get(row.get('source_code'))
        if original is None:
            raise DataError('listing_events canonical identity absent from stock_basic Raw')
        if identity.get(row.get('source_code')) != row.get('security_id'):
            raise DataError('listing_events stable identity differs from frozen binding')
        kind = row.get('event_type')
        expected = original.get('list_date' if kind == 'listing' else 'delist_date')
        expected_end = original.get('delist_date') if kind == 'delisting' else None
        if (kind not in {'listing', 'delisting'} or
                row.get('exchange') != original['exchange'] or
                row.get('vendor_list_status') != original['list_status'] or
                _canonical_date(row.get('event_date')) != _source_date(expected) or
                _canonical_date(row.get('listing_date')) != _source_date(original['list_date']) or
                _canonical_date(row.get('delisting_date')) != _source_date(expected_end)):
            raise DataError('listing_events date/boundary differs from stock_basic Raw')
    return len(canonical)


def audit_snapshot(store: LocalStore, *, snapshot_id: str, plan=None,
                   base_snapshot: str | None = None) -> dict:
    """Audit all referenced partitions; memory is bounded by one partition.

    A supplied frozen job plan additionally binds the exact securities/date
    scope. ``failed`` denotes a byte/mapping/structural error. ``limited`` means
    market cells remain missing relative to the saved source calendar and
    listing dates; the report lists known suspension evidence separately.
    Neither status certifies supplier historical truth or strict public PIT.
    """
    started = time.monotonic()
    snapshot = store.load_snapshot(snapshot_id)
    domains = snapshot['domains']
    prior_domains = store.load_snapshot(base_snapshot)['domains'] if base_snapshot else {}
    report = {'snapshot_id': snapshot_id, 'status': 'passed', 'row_counts': {},
              'source_mapping_checks': {}, 'issues': [], 'missing_market_cells': 0,
              'missing_market_samples': [], 'known_suspension_missing_cells': 0,
              'limitations': ['Checks are relative to retained supplier responses, not independent historical completeness.',
                              'Terminal observations do not prove historical public vintages.']}
    calendar = defaultdict(set)
    securities = {}
    suspended = set()
    latest_reference = {}
    for name in ('trading_calendar', 'security_master', 'security_status'):
        for part in domains.get(name, {}).get('partitions', []):
            rows = store.read_partition(part).to_pylist()
            report['row_counts'][name] = report['row_counts'].get(name, 0) + len(rows)
            for row in rows:
                key = (name, row.get('security_id', row.get('exchange')), str(row.get('session', '')))
                previous = latest_reference.get(key)
                if previous is None or str(row.get('first_observed_at', '')) >= str(previous.get('first_observed_at', '')):
                    latest_reference[key] = row
    for (name, _, _), row in latest_reference.items():
        if name == 'trading_calendar' and row['is_open']:
            calendar[row['exchange']].add(str(row['session']))
        elif name == 'security_master':
            securities[row['security_id']] = (row['exchange'], str(row['listing_date']),
                str(row['delisting_date']) if row.get('delisting_date') else None)
        elif name == 'security_status' and row.get('is_suspended') is True:
            suspended.add((row['security_id'], str(row['session'])))
    # Use the same accepted vendor endpoint boundary as state diagnostics.
    # No exchange table or outside proof is required for this coverage report.
    listing_latest = {}
    for part in domains.get('listing_events', {}).get('partitions', []):
        for row in store.read_partition(part).to_pylist():
            if row.get('event_type') != 'delisting':
                continue
            key = row['security_id']
            previous = listing_latest.get(key)
            if previous is None or row['revision_sequence'] > previous['revision_sequence']:
                listing_latest[key] = row
    for security, event in listing_latest.items():
        if security in securities and event.get('event_state', 'value') == 'value':
            exchange, first, _ = securities[security]
            if first == str(event['listing_date']):
                securities[security] = exchange, first, str(event['delisting_date'])
    wanted = set(dict(plan.identity_map)[code] for code in plan.symbols) if plan else set(securities)
    report['selected_securities'] = len(wanted)
    inverse = {identity: code for code, identity in dict(plan.identity_map).items()} if plan else {}
    mapping = {
        'market_daily': {'open': ('open', 1), 'high': ('high', 1), 'low': ('low', 1),
                         'close': ('close', 1), 'pre_close': ('pre_close', 1),
                         'volume_shares': ('vol', 100), 'amount_cny': ('amount', 1000)},
        'adjustment_factors': {'factor': ('adj_factor', 1)},
        'benchmark_daily': {'close': ('close', 1)},
    }
    for name, domain in domains.items():
        if name in ('trading_calendar', 'security_master', 'security_status'):
            continue
        report['row_counts'][name] = 0
        inverse.update({identity: code for code, identity in domain['source_profile'].get('identity_map', {}).items()})
        event_domain = name in _EVENT_FIELDS or name == 'listing_events'
        profile_id = str(domain.get('source_profile', {}).get('id', ''))
        supported_event = (profile_id.startswith('tushare.local.') if name in _EVENT_FIELDS
                           else profile_id == 'tushare.stock_basic.listing_events.v1') if event_domain else False
        audited_raw_ids = set()
        if event_domain:
            report['source_mapping_checks'][name] = 0
            if not supported_event:
                report['issues'].append({'reason': 'unsupported_event_source_profile',
                                         'domain': name, 'source_profile_id': profile_id})
            elif name == 'listing_events':
                report['limitations'].append(
                    'Listing event dates are accepted Tushare stock_basic fields under the declared boundary policy; historical public vintage is not established.')
        for part in domain['partitions']:
            rows = store.read_partition(part).to_pylist()
            report['row_counts'][name] += len(rows)
            if name == 'corporate_actions':
                report['unavailable_corporate_action_revisions'] = report.get('unavailable_corporate_action_revisions', 0) + sum(
                    row.get('source_issue') == 'ambiguous_action_identity_or_revision' for row in rows)
            if event_domain:
                if not supported_event:
                    continue
                grouped = defaultdict(list)
                for row in rows:
                    grouped[row.get('raw_batch_id')].append(row)
                for raw_id, canonical in grouped.items():
                    audited_raw_ids.add(raw_id)
                    checked = (_audit_event_raw(store, name, domain, raw_id, canonical)
                               if name in _EVENT_FIELDS else
                               _audit_listing_raw(store, domain, raw_id, canonical))
                    report['source_mapping_checks'][name] += checked
                continue
            if name not in mapping:
                continue
            grouped = defaultdict(list)
            prior_part = next((candidate for candidate in
                               prior_domains.get(name, {}).get('partitions', [])
                               if candidate['partition'] == part['partition']), None)
            prior_outside = None
            if plan and name != 'benchmark_daily' and prior_part is not None and prior_part != part:
                prior_outside = {json.dumps(row, sort_keys=True, default=str)
                                 for row in store.read_partition(prior_part).to_pylist()
                                 if row.get('security_id') not in wanted}
            for row in rows:
                if plan and name != 'benchmark_daily' and row['security_id'] not in wanted:
                    unchanged_part = prior_part == part
                    unchanged_row = (prior_outside is not None and
                                     json.dumps(row, sort_keys=True, default=str) in prior_outside)
                    if not unchanged_part and not unchanged_row:
                        raise DataError(f'{name} contains a new security fact outside the frozen plan')
                grouped[row['raw_batch_id']].append(row)
            for raw_id, canonical in grouped.items():
                raw = store.get_raw(raw_id)
                source = {(row['ts_code'], row['trade_date']): row
                          for row in _rows(store.read_raw_record(raw))}
                for row in canonical:
                    code = row['security_id'] if name == 'benchmark_daily' else inverse[row['security_id']]
                    key = (code, str(row['session']).replace('-', ''))
                    original = source.get(key)
                    if original is None:
                        raise DataError(f'{name} canonical key absent from referenced Raw')
                    for field, (source_field, multiplier) in mapping[name].items():
                        value = original.get(source_field)
                        expected = None if value is None else Decimal(str(value)) * multiplier
                        actual = row[field]
                        equal = (actual is None if expected is None else
                                 actual is not None and
                                 (Decimal(str(actual)) == expected if field == 'volume_shares' else
                                  math.isclose(float(actual), float(expected), rel_tol=1e-12, abs_tol=1e-8)))
                        if not equal:
                            raise DataError(f'{name}.{field} differs from source or declared unit conversion')
                    report['source_mapping_checks'][name] = report['source_mapping_checks'].get(name, 0) + 1
            if name == 'market_daily' and calendar and wanted:
                present = {(row['security_id'], str(row['session'])) for row in rows if row.get('close') is not None}
                month = part['partition']
                for security in wanted:
                    listing = securities.get(security)
                    if listing is None:
                        continue
                    exchange, first, end = listing
                    for day in calendar[exchange]:
                        if not day.startswith(month) or day < first or (end and day >= end):
                            continue
                        if plan and not plan.start_session <= day <= plan.end_session:
                            continue
                        if (security, day) not in present:
                            report['missing_market_cells'] += 1
                            known = (security, day) in suspended
                            report['known_suspension_missing_cells'] += int(known)
                            if len(report['missing_market_samples']) < 30:
                                report['missing_market_samples'].append({'security_id': security, 'session': day,
                                    'reason': 'explicit_suspension' if known else 'source_gap_or_unresolved_state'})
        if supported_event:
            # Raw contributing no surviving canonical row still belongs to the
            # domain closure and must have valid original bytes and scope.
            for raw_id in domain['raw_batch_ids']:
                if raw_id not in audited_raw_ids:
                    if name in _EVENT_FIELDS:
                        _audit_event_raw(store, name, domain, raw_id, [])
                    else:
                        _audit_listing_raw(store, domain, raw_id, [])
    if plan:
        report['requested_start'] = plan.start_session
        report['requested_end'] = plan.end_session
        missing_identity = sorted(wanted - securities.keys())
        if missing_identity:
            report['issues'].append({'reason': 'missing_security_master', 'count': len(missing_identity),
                                     'sample': missing_identity[:10]})
        # An absent entire month must not disappear from the gap report.
        market_months = {p['partition'] for p in domains.get('market_daily', {}).get('partitions', [])}
        for security in wanted:
            if security not in securities:
                continue
            exchange, first, end = securities[security]
            for day in calendar[exchange]:
                if (plan.start_session <= day <= plan.end_session and day >= first and
                        (not end or day < end) and day[:7] not in market_months):
                    report['missing_market_cells'] += 1
                    if len(report['missing_market_samples']) < 30:
                        report['missing_market_samples'].append({'security_id': security, 'session': day,
                                                                'reason': 'missing_month_partition'})
    if not calendar or not securities:
        report['issues'].append({'reason': 'calendar_or_identity_unavailable'})
    if report.get('unavailable_corporate_action_revisions'):
        report['issues'].append({'reason': 'ambiguous_action_identity_or_revision',
                                 'count': report['unavailable_corporate_action_revisions']})
        report['limitations'].append(
            'Ambiguous supplier corporate-action groups are unavailable as whole economic events; '
            'candidate identity/revision order is unknown, including any unanimous zero amounts.')
    if report['issues'] or report['missing_market_cells']:
        report['status'] = 'limited'
    report['elapsed_seconds'] = round(time.monotonic() - started, 3)
    return report
