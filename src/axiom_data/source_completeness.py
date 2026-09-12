"""Source-specific response admission, separate from immutable Raw storage.

Unknown caps are not completeness evidence. Packaged profiles remain immutable;
the indicator profile is the endpoint authority even for its legacy PR6 binding.
"""
import json
import re
from importlib.resources import files

from axiom_data.artifacts import ArtifactError, _decode_rows, _digest, _json_bytes


class SourceCompletenessError(ArtifactError):
    def __init__(self, message, *, raw_batch_id=None):
        super().__init__(message)
        self.raw_batch_id = raw_batch_id


def completeness_policy(profile_version, endpoint):
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


def validate_payload_completeness(profile_version, endpoint, records, *,
                                  params=None, raw_batch_id=None, evidence=None):
    """Admit a response by its real policy; evidence contains verified Raw pages.

    A short response proves only that the declared cap was not reached, not
    historical coverage or PIT safety. No profile declares indicator pagination.
    """
    def reject(message):
        raise SourceCompletenessError(message, raw_batch_id=raw_batch_id)
    if not isinstance(records, list):
        reject('source payload must be rows')
    policy = completeness_policy(profile_version, endpoint)
    def result(admission):
        return dict(policy, admission=admission, qualification=admission,
                    complete=admission=='complete', rows=len(records), row_count=len(records))
    if policy['status'] != 'established':
        if endpoint == 'fina_indicator' and profile_version in {'tushare_pr6.v1','tushare_fina_indicator.v1'}:
            reject('required indicator completeness policy unavailable')
        return result('unestablished')
    params = params or {}
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
            manifest = raw.manifest; request = manifest['request']; page_params = request['params']
            page = _decode_rows(raw)
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
    elif len(records) >= policy['limit']:
        reject('possibly truncated source payload; split bounded request')
    return result('complete')


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


def validate_raw_completeness(raw, *, evidence=None):
    """Shared pre-mapping guard. Raw loading remains an immutable byte operation."""
    _validate_raw_result(raw)
    manifest = raw.manifest
    request = manifest.get('request', {})
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
        return validate_payload_completeness('exchange_security.v1', exchange, rows,
            params=request.get('params'), raw_batch_id=raw.ref.raw_batch_id)
    return validate_payload_completeness(manifest.get('source_profile_version'),
        request.get('endpoint'), _decode_rows(raw), params=request.get('params'),
        raw_batch_id=raw.ref.raw_batch_id, evidence=evidence)
