"""Source-specific response admission, separate from immutable Raw storage.

Unknown caps are not completeness evidence. Packaged profiles remain immutable;
the indicator profile is the endpoint authority even for its legacy PR6 binding.
"""
import json
import re
from datetime import date, timedelta
from importlib.resources import files

from axiom_data.artifacts import ArtifactError, _decode_rows, _digest, _json_bytes


class SourceCompletenessError(ArtifactError):
    def __init__(self, message, *, raw_batch_id=None):
        super().__init__(message)
        self.raw_batch_id = raw_batch_id


def legacy_completeness_policy(profile_version, endpoint):
    result = {'status': 'unestablished', 'profile_version': profile_version,
              'endpoint': endpoint, 'limit': None, 'pagination': None,
              'policy_ref': None, 'policy_digest': None,
              'completeness_rule': 'no_declared_response_limit',
              'action': 'qualify_source_policy'}
    if not isinstance(profile_version, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+', profile_version):
        return result
    path = files('axiom_data.source_profiles').joinpath(profile_version + '.json')
    if not path.is_file():
        return result
    profile = json.loads(path.read_bytes())
    definition = profile.get('endpoints', {}).get(endpoint)
    if definition is None:
        return result
    authority = profile
    if endpoint == 'fina_indicator':
        authority_path = files('axiom_data.source_profiles').joinpath('tushare_fina_indicator.v1.json')
        if not authority_path.is_file():
            return result
        authority = json.loads(authority_path.read_bytes())
        definition = authority['endpoints'][endpoint]
    limit = definition.get('limit', definition.get('maximum_rows',
        authority.get('response_limit', authority.get('collection_policy', {}).get('maximum_rows'))))
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        return result
    paginated = (profile_version == 'tushare_industry_qualification.v1'
                 and endpoint in {'index_member_all', 'ci_index_member'}
                 and bool(profile.get('pagination')))
    return dict(result, status='established', limit=limit,
        authority_profile_version=authority['profile_version'],
        authority_profile_digest=_digest(_json_bytes(authority)),
        policy_ref=authority['profile_version'], policy_digest=_digest(_json_bytes(authority)),
        pagination='limit_offset' if paginated else None,
        completeness_rule='below_limit_or_complete_page_series' if paginated else 'strictly_below_limit',
        action='validate_complete_page_series' if paginated else 'split_date_scope')


def _extension():
    path = files('axiom_data.source_profiles').joinpath('source_completeness.v1.json')
    if not path.is_file():
        return {}
    return json.loads(path.read_bytes())


def source_profile_completeness_binding(profile_version, base_digest):
    """Bind the immutable mapping identity and its versioned admission extension."""
    extension = _extension()
    if profile_version not in extension.get('profile_versions', []):
        raise SourceCompletenessError('required SourceProfile completeness extension missing')
    return _binding_for_extension(profile_version, base_digest, extension)


def _binding_for_extension(profile_version, base_digest, extension):
    binding = {'extension_ref': extension['extension_version'],
               'extension_digest': _digest(_json_bytes(extension)),
               'base_source_profile_version': profile_version,
               'base_source_profile_digest': base_digest}
    return dict(binding, effective_source_profile_digest=_digest(_json_bytes(binding)))


def current_contract_binding():
    extension = _extension()
    if not re.fullmatch(r'source_completeness\.v[0-9]+', str(extension.get('extension_version'))):
        raise SourceCompletenessError('required SourceProfile completeness extension missing')
    return {'extension_ref': extension['extension_version'],
            'extension_digest': _digest(_json_bytes(extension))}


def completeness_policy(profile_version, endpoint):
    legacy = legacy_completeness_policy(profile_version, endpoint)
    extension = _extension()
    path = files('axiom_data.source_profiles').joinpath(str(profile_version) + '.json')
    if (profile_version not in extension.get('profile_versions', []) or not path.is_file()
            or endpoint not in json.loads(path.read_bytes()).get('endpoints', {})):
        return dict(legacy, status='unestablished', historical_completeness='unknown')
    base = json.loads(path.read_bytes())
    definition = extension.get('endpoints', {}).get(endpoint)
    if not isinstance(definition, dict):
        return dict(legacy, status='unestablished', historical_completeness='unknown')
    policy = {key: value for key, value in legacy.items()
              if key in {'profile_version', 'endpoint', 'authority_profile_version', 'authority_profile_digest'}}
    policy.update(limit=None, pagination=None)
    policy.update(definition)
    policy.update(status='established', policy_ref=extension['extension_version'] + '#' + endpoint,
                  policy_digest=_digest(_json_bytes(extension)),
                  historical_completeness=extension.get('historical_completeness'),
                  historical_limitations=extension.get('historical_limitations'))
    bound_field = base['endpoints'][endpoint].get('request_bound_field', base['endpoints'][endpoint].get('bound_field'))
    if bound_field: policy['date_field'] = bound_field
    # The pilot profile never declared offset support. A renamed profile cannot
    # acquire the qualification profile's pagination capabilities.
    if endpoint == 'index_member_all' and profile_version == 'tushare_sw_pilot.v1':
        policy.update(pagination=None, completeness_rule='strictly_below_limit',
                      action='fail_closed_unsplittable')
    return policy


def validate_policy_contract(profile_version, endpoint):
    """Readiness check for executable endpoint rules, without issuing requests."""
    policy = completeness_policy(profile_version, endpoint)
    reasons = _policy_issues(policy)
    return {'status': 'BLOCKED' if reasons else 'PASS', 'reasons': reasons, 'policy': policy}


def _policy_issues(policy):
    reasons = []
    rules = {'strictly_below_limit', 'below_limit_or_complete_page_series',
             'documented_security_history', 'documented_monthly_snapshot',
             'scoped_listing_snapshot', 'scoped_taxonomy_snapshot',
             'complete_civil_calendar', 'official_termination_document'}
    rule = policy.get('completeness_rule')
    if policy.get('status') != 'established' or rule not in rules:
        reasons.append('required executable completeness rule missing')
    if policy.get('historical_completeness') != 'request_complete_best_effort':
        reasons.append('historical completeness must be explicitly qualified')
    for key in ('policy_ref', 'policy_digest', 'documentation', 'evidence', 'historical_limitations'):
        if not policy.get(key): reasons.append('missing ' + key)
    if policy.get('coverage_semantics') not in {'bounded_date_observation', 'security_history_observation',
            'current_snapshot', 'taxonomy_snapshot', 'civil_calendar'}:
        reasons.append('missing coverage semantics')
    if policy.get('empty_result_semantics') not in {'unknown_observation', 'no_source_events_in_scope', 'invalid_empty'}:
        reasons.append('missing empty response semantics')
    if rule in {'strictly_below_limit', 'below_limit_or_complete_page_series', 'scoped_listing_snapshot'}:
        limit = policy.get('limit')
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            reasons.append('missing positive response limit')
    if rule == 'below_limit_or_complete_page_series' and (
            policy.get('pagination') != 'limit_offset'
            or policy.get('terminal_page_rule') != 'contiguous_offsets_then_short_page'
            or policy.get('action') != 'validate_complete_page_series'):
        reasons.append('missing complete page-series termination rule')
    if policy.get('action') == 'split_date_scope' and policy.get('date_field') not in {'ann_date', 'end_date', 'trade_date', 'cal_date'}:
        reasons.append('missing supported split date field')
    if policy.get('action') not in {'split_date_scope', 'validate_complete_page_series', 'fail_closed_unsplittable'}:
        reasons.append('missing executable cap action')
    if rule == 'documented_monthly_snapshot' and policy.get('max_scope_days') != 31:
        reasons.append('missing monthly request boundary')
    return reasons


def validate_payload_completeness(profile_version, endpoint, records, *,
                                  params=None, raw_batch_id=None, evidence=None):
    return _validate_payload_completeness(profile_version, endpoint, records,
        params=params, raw_batch_id=raw_batch_id, evidence=evidence)


def _validate_payload_completeness(profile_version, endpoint, records, *,
                                  params=None, raw_batch_id=None, evidence=None, legacy=False,
                                  verified_document=False):
    """Admit a response by its real policy; evidence contains verified Raw pages.

    A short response proves only that the declared cap was not reached, not
    historical coverage or PIT safety. No profile declares indicator pagination.
    """
    def reject(message):
        raise SourceCompletenessError(message, raw_batch_id=raw_batch_id)
    if not isinstance(records, list):
        reject('source payload must be rows')
    policy = (legacy_completeness_policy if legacy else completeness_policy)(profile_version, endpoint)
    def result(admission):
        value = dict(policy, admission=admission, qualification=admission,
                     complete=admission=='complete', rows=len(records), row_count=len(records))
        if not legacy: value['scope'] = dict(params or {})
        return value
    if policy['status'] != 'established':
        if not legacy and isinstance(profile_version, str) and re.fullmatch(r'[A-Za-z0-9_.-]+', profile_version) and files(
                'axiom_data.source_profiles').joinpath(profile_version + '.json').is_file():
            reject('required source completeness policy unavailable')
        if endpoint == 'fina_indicator' and profile_version in {'tushare_pr6.v1','tushare_fina_indicator.v1'}:
            reject('required indicator completeness policy unavailable')
        return result('unestablished')
    if not legacy:
        issues = _policy_issues(policy)
        if issues: reject('; '.join(issues))
        if policy['completeness_rule'] == 'official_termination_document' and not verified_document:
            reject('official completeness requires the original Raw document parser')
    if (not legacy or params is not None) and profile_version in {'tushare_sw_pilot.v1','tushare_industry_qualification.v1'}:
        from axiom_data.sw_source import validate_payload_scope
        try:
            validate_payload_scope(endpoint, params, records, profile_version=profile_version)
        except ArtifactError as exc:
            reject(str(exc))
    params = params or {}
    if not legacy:
        _validate_scoped_response(policy, records, params, reject)
    paging = set(params) & {'limit', 'offset'}
    if paging and policy['pagination'] is None:
        reject('SourceProfile does not support pagination')
    if paging and policy['pagination'] == 'limit_offset' and evidence is None:
        # Existing SW batch qualification validates complete immutable page
        # series. A single stored page must not assert completeness on its own.
        return result('page_series_required')
    if evidence is not None or paging:
        if policy['pagination'] != 'limit_offset' or not evidence:
            reject('complete immutable page evidence required by SourceProfile')
        offset = 0; terminal = False; seen = set(); matched = False
        for raw in evidence:
            _validate_raw_result(raw)
            if not legacy: _validate_raw_binding(raw)
            manifest = raw.manifest; request = manifest['request']; page_params = request['params']
            page = _industry_raw_scope(raw)
            limit = page_params.get('limit')
            if (not str(limit).isdigit() or not 0 < int(limit) <= policy['limit']
                or str(page_params.get('offset')) != str(offset) or terminal
                or manifest.get('source_profile_version') != profile_version
                or manifest.get('source_profile_digest') != policy['authority_profile_digest']
                or request.get('endpoint') != endpoint
                or {k:v for k,v in page_params.items() if k != 'offset'} !=
                   {k:v for k,v in params.items() if k != 'offset'}
                or not isinstance(page, list) or len(page) > int(limit)):
                reject('incomplete or misbound immutable page series')
            fingerprints = {_digest(_json_bytes(row)) for row in page}
            if len(fingerprints) != len(page) or seen & fingerprints:
                reject('duplicate records in immutable page series')
            seen.update(fingerprints)
            matched |= page_params == params and page == records and (
                raw_batch_id is None or raw.ref.raw_batch_id == raw_batch_id)
            terminal = len(page) < int(limit)
            offset += int(limit)
        if not terminal or not matched:
            reject('missing terminal page or response binding in immutable page series')
    elif policy['limit'] is not None and len(records) >= policy['limit']:
        reject('possibly truncated source payload; split bounded request')
    return result('complete')


def _validate_scoped_response(policy, records, params, reject):
    """Validate the response proof promised by this particular source contract."""
    endpoint = policy['endpoint']; rule = policy['completeness_rule']
    if not isinstance(params, dict): reject('explicit source scope required')
    if any(not isinstance(row, dict) for row in records): reject('source rows must be objects')
    if not records and policy['empty_result_semantics'] == 'invalid_empty':
        reject('empty response cannot prove complete source snapshot')
    if rule == 'official_termination_document':
        # Only the Raw entrypoint has the official original bytes needed for
        # this proof; it runs parse_termination before passing decoded rows.
        return
    if endpoint == 'dividend':
        if set(params) != {'ts_code'} or not params.get('ts_code'):
            reject('dividend completeness requires the formal security-history request')
    elif rule == 'scoped_listing_snapshot':
        if params.get('list_status') not in {'L', 'D', 'P'} or params.get('exchange') not in {'', 'SSE', 'SZSE', 'BSE'}:
            reject('listing snapshot requires explicit status and exchange')
        for row in records:
            if row.get('list_status') != params['list_status'] or (
                    params['exchange'] and row.get('exchange') != params['exchange']):
                reject('listing snapshot row outside request selectors')
    elif rule == 'scoped_taxonomy_snapshot':
        if params.get('src') != 'SW2021' or params.get('level') not in {'L1', 'L2', 'L3'}:
            reject('taxonomy snapshot requires SW2021 and one level')
        if any(row.get('src') != params['src'] or row.get('level') != params['level'] for row in records):
            reject('taxonomy snapshot row outside request selectors')
    elif endpoint not in {'index_member_all', 'ci_index_member'}:
        has_range = bool(params.get('start_date') and params.get('end_date'))
        if not (has_range or params.get('trade_date') or params.get('period')):
            reject('complete source response requires bounded dates or report period')
        if rule in {'documented_security_history', 'documented_monthly_snapshot'} and not (
                params.get('ts_code') or params.get('index_code')):
            reject('documented history completeness requires a security/index selector')
        if has_range:
            try:
                start = date.fromisoformat(params['start_date']); end = date.fromisoformat(params['end_date'])
            except (TypeError, ValueError): reject('invalid source date scope')
            if start > end: reject('reversed source date scope')
            if rule == 'documented_monthly_snapshot' and (end-start).days >= policy['max_scope_days']:
                reject('index_weight completeness requires at most 31 civil days')
        if rule == 'complete_civil_calendar':
            if not has_range or not params.get('exchange') or params.get('is_open') not in (None, ''):
                reject('calendar completeness requires exchange and unfiltered civil date bounds')
            expected = {(start + timedelta(days=i)).strftime('%Y%m%d') for i in range((end-start).days+1)}
            if len(records) != len(expected) or {row.get('cal_date') for row in records} != expected:
                reject('incomplete civil calendar date coverage')
    identity = 'index_code' if endpoint == 'index_classify' else 'ts_code' if endpoint == 'stock_basic' else None
    if identity and (any(not row.get(identity) for row in records)
            or len({row[identity] for row in records}) != len(records)):
        reject('duplicate or missing snapshot identity')
    for row in records:
        for key in ('ts_code', 'index_code', 'exchange'):
            if params.get(key) and (key not in row or row[key] not in str(params[key]).split(',')):
                reject('source row outside request selector: ' + key)
        field = policy.get('date_field')
        if field:
            if field not in row: reject('missing source scope date: ' + field)
            represented = str(row[field])[:10].replace('-', '')
            if params.get('trade_date') and represented != params['trade_date']:
                reject('source date outside request')
            if params.get('start_date') and not params['start_date'] <= represented <= params['end_date']:
                reject('source date outside request bounds')


def _validate_raw_result(raw):
    """Apply the same transport/partial-result guard to every evidence page."""
    manifest = raw.manifest
    if manifest.get('status') != 'success':
        raise SourceCompletenessError('partial or failed Raw cannot be admitted',
                                      raw_batch_id=raw.ref.raw_batch_id)
    summary = manifest.get('summary', {})
    if (any(summary.get(key) in (False, 'partial', 'PARTIAL', 'truncated', 'TRUNCATED')
            for key in ('complete', 'completeness', 'result_status'))
            or summary.get('truncated') is True or summary.get('partial') is True):
        raise SourceCompletenessError('partial or truncated Raw cannot be admitted',
                                      raw_batch_id=raw.ref.raw_batch_id)


def _industry_raw_scope(raw):
    from axiom_data.sw_source import validate_raw_scope
    try:
        return validate_raw_scope(raw)
    except ArtifactError as exc:
        raise SourceCompletenessError(str(exc), raw_batch_id=raw.ref.raw_batch_id) from exc


def _validate_raw_binding(raw):
    manifest = raw.manifest
    version = manifest.get('source_profile_version')
    extension = _extension()
    if version not in extension.get('profile_versions', []): return
    binding = manifest.get('summary', {}).get('source_completeness')
    if binding is not None:
        # Validate provenance against its immutable original extension. Current
        # admission then evaluates the current contract over the same Raw bytes.
        ref = binding.get('extension_ref') if isinstance(binding, dict) else None
        path = (files('axiom_data.source_profiles').joinpath(ref + '.json')
                if isinstance(ref, str) and re.fullmatch(r'source_completeness\.v[0-9]+', ref) else None)
        original = json.loads(path.read_bytes()) if path is not None and path.is_file() else {}
        if (version not in original.get('profile_versions', [])
                or binding != _binding_for_extension(version, manifest.get('source_profile_digest'), original)):
            raise SourceCompletenessError('Raw completeness extension binding mismatch',
                                          raw_batch_id=raw.ref.raw_batch_id)
    if version == 'exchange_security.v1': return  # Original-document dispatch below.
    request = manifest.get('request', {}); endpoint = request.get('endpoint')
    if version == 'tushare_phase1.v1':
        from axiom_data.tushare import load_tushare_source_profile, tushare_source_profile_digest
        base = load_tushare_source_profile()
        digest = tushare_source_profile_digest(base)
    elif version == 'tushare_dm1.v1':
        from axiom_data.reference_source import load_reference_source_profile, reference_source_profile_digest
        base = load_reference_source_profile()
        digest = reference_source_profile_digest(base)
    else:
        if version in {'tushare_sw_pilot.v1', 'tushare_industry_qualification.v1'}:
            from axiom_data.sw_source import load_profile
        elif version in {'tushare_pr7.v1', 'tushare_pr7_holder.v2', 'tushare_pr7_holder.v3'}:
            from axiom_data.event_source import load_event_source_profile as load_profile
        else:
            from axiom_data.fundamentals_source import load_fundamentals_source_profile as load_profile
        base = load_profile(version)
        digest = _digest(_json_bytes(base))
    definition = base['endpoints'].get(endpoint)
    if (definition is None or manifest.get('source_profile_digest') != digest
            or request.get('fields') != definition['fields']):
        raise SourceCompletenessError('Raw source profile/fields binding mismatch',
                                      raw_batch_id=raw.ref.raw_batch_id)
    if version in {'tushare_sw_pilot.v1', 'tushare_industry_qualification.v1'}: return
    if version == 'tushare_phase1.v1':
        from axiom_data.tushare import _endpoint_domain
        domains = [_endpoint_domain(endpoint)]
    else:
        domains = definition.get('canonical_domains', [definition.get('domain')])
    source_ref = definition.get('source_profile_ref')
    if source_ref is None:
        source_ref = ('tushare.pr7.' if version.startswith('tushare_pr7') else 'tushare.pr6.') + endpoint
    if manifest.get('domain') not in domains or manifest.get('source_profile_ref') != source_ref:
        raise SourceCompletenessError('Raw source profile/domain binding mismatch',
                                      raw_batch_id=raw.ref.raw_batch_id)


def validate_raw_completeness(raw, *, evidence=None):
    return _validate_raw_completeness(raw, evidence=evidence)


def page_evidence(raw_batches):
    """Validate page groups within one commit's explicit ordered Raw refs.

    Callers must keep commit boundaries: two observations of the same selector
    are distinct page plans, not a series to concatenate across history.
    """
    groups = {}
    for raw in raw_batches:
        manifest = raw.manifest
        request = manifest.get('request', {})
        params = request.get('params', {})
        if (manifest.get('source_profile_version') != 'tushare_industry_qualification.v1'
                or request.get('endpoint') not in {'index_member_all', 'ci_index_member'}
                or not isinstance(params, dict) or not set(params) & {'limit', 'offset'}):
            continue
        # This is the existing request/payload scope validator, including the
        # exact supported selectors and bounded numeric page identities.
        _industry_raw_scope(raw)
        key = _json_bytes({'profile_version': manifest['source_profile_version'],
                           'endpoint': request['endpoint'],
                           'params': {k: v for k, v in params.items() if k != 'offset'}})
        groups.setdefault(key, []).append(raw)
    result = {}
    for pages in groups.values():
        pages.sort(key=lambda raw: int(raw.manifest['request']['params']['offset']))
        validate_raw_completeness(pages[0], evidence=pages)
        result.update((raw.ref.raw_batch_id, pages) for raw in pages)
    return result


def validate_raw_completeness_legacy(raw, *, evidence=None):
    """Frozen source_observations.v1 projection; never current admission."""
    return _validate_raw_completeness(raw, evidence=evidence, legacy=True)


legacy_validate_raw_completeness = validate_raw_completeness_legacy


def _validate_raw_completeness(raw, *, evidence=None, legacy=False):
    """Shared pre-mapping guard. Raw loading remains an immutable byte operation."""
    _validate_raw_result(raw)
    manifest = raw.manifest
    request = manifest.get('request', {})
    if not legacy: _validate_raw_binding(raw)
    if manifest.get('source_profile_version') == 'exchange_security.v1':
        # Official exchange originals are a JSON envelope (SSE) or XLSX
        # workbook (SZSE). Preserve their source-bound parser and bytes.
        from axiom_data.exchange_security import parse_termination, profile
        authority = profile(); exchange = request.get('exchange')
        if (manifest.get('domain') != 'security_master'
                or exchange not in authority['endpoints']
                or request != authority['endpoints'][exchange]
                or manifest.get('source_profile_ref') != 'exchange.termination.' + exchange
                or manifest.get('source_profile_digest') != _digest(_json_bytes(authority))):
            raise SourceCompletenessError('exchange boundary source binding mismatch',
                                          raw_batch_id=raw.ref.raw_batch_id)
        rows = list(parse_termination(exchange, raw.payload).values())
        return _validate_payload_completeness('exchange_security.v1', exchange, rows,
            params=request.get('params'), raw_batch_id=raw.ref.raw_batch_id, legacy=legacy,
            verified_document=True)
    rows = (_industry_raw_scope(raw) if manifest.get('source_profile_version') in
            {'tushare_sw_pilot.v1','tushare_industry_qualification.v1'} else _decode_rows(raw))
    admission = _validate_payload_completeness(manifest.get('source_profile_version'),
        request.get('endpoint'), rows, params=request.get('params'),
        raw_batch_id=raw.ref.raw_batch_id, evidence=evidence, legacy=legacy)
    if not legacy:
        version = manifest.get('source_profile_version')
        try:
            if version in {'tushare_pr6.v1', 'tushare_pr6.v2', 'tushare_fina_indicator.v1'}:
                from axiom_data.fundamentals_source import _validate_payload_shape
                _validate_payload_shape(request.get('endpoint'), request.get('params'), rows, profile_version=version)
            elif version in {'tushare_pr7.v1', 'tushare_pr7_holder.v2', 'tushare_pr7_holder.v3'}:
                from axiom_data.event_source import validate_payload
                validate_payload(request.get('endpoint'), request.get('params'), rows, profile_version=version)
        except ArtifactError as exc:
            raise SourceCompletenessError(str(exc), raw_batch_id=raw.ref.raw_batch_id) from exc
    return admission


def requalify_sources(data_root, domain_commit_ids):
    """Current candidate admission is separate from frozen artifact replay."""
    from axiom_data.artifacts import _validated_domain_commit_with_raw_closure, RawBatches
    from pathlib import Path
    from axiom_data.artifacts import load_raw_batch
    from axiom_data.verification_cache import current_source_cache
    binding = _digest(_json_bytes(current_contract_binding()))
    pending = list(domain_commit_ids.items()); seen = set(); checked_raw = current_source_cache(data_root)
    while pending:
        domain, identity = pending.pop()
        if (domain, identity) in seen:
            continue
        seen.add((domain, identity))
        commit, _ = _validated_domain_commit_with_raw_closure(Path(data_root), domain, identity)
        refs = commit.manifest['ordered_raw_batch_refs']
        # One immutable commit's ordered inputs delimit its page observation.
        # Combining all ancestors would conflate distinct offset-zero revisions.
        pages = page_evidence(RawBatches(data_root, [ref['raw_batch_id'] for ref in refs
            if ref.get('source_profile_version') == 'tushare_industry_qualification.v1']))
        for ref in refs:
            raw_id = ref['raw_batch_id']
            key = (raw_id, ref['manifest_digest'], binding)
            if key in checked_raw:
                continue
            raw = load_raw_batch(data_root, raw_id)
            admitted = validate_raw_completeness(raw, evidence=pages.get(raw_id))
            if admitted.get('complete') is not True:
                raise ArtifactError('current candidate source completeness is unproven')
            checked_raw.add(key)
        parent = commit.manifest.get('parent_commit_ref')
        if parent is not None:
            pending.append((domain, parent['domain_commit_id']))
