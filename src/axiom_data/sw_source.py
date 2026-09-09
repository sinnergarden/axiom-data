"""SW2021 qualification observations, retained without inferred interval semantics."""
import json
import re
from importlib.resources import files
from axiom_data.artifacts import ArtifactError, _digest, _json_bytes, write_raw_batch
from axiom_data.tushare import TushareCollector, _response_records, _retrieved_at
from axiom_data.domains.market import _symbol


def load_profile():
    return json.loads(files('axiom_data.source_profiles').joinpath('tushare_sw_pilot.v1.json').read_bytes())


def profile_digest():
    return _digest(_json_bytes(load_profile()))


def validate_request(endpoint, params):
    if not isinstance(params, dict):
        raise ArtifactError('SW request parameters must be explicit')
    if endpoint == 'index_classify':
        if set(params) != {'src', 'level'} or params['src'] != 'SW2021' or params['level'] not in {'L1','L2','L3'}:
            raise ArtifactError('SW taxonomy requires explicit SW2021 and level')
    elif endpoint == 'index_member_all':
        selectors = set(params) - {'is_new'}
        if len(selectors) != 1 or not selectors <= {'ts_code','l1_code','l2_code','l3_code'}:
            raise ArtifactError('SW membership requires one bounded selector')
        key = next(iter(selectors))
        value = params[key]
        if key == 'ts_code':
            _symbol(value)
        elif not isinstance(value, str) or not re.fullmatch(r'[0-9]{6}\.SI', value):
            raise ArtifactError('invalid SW industry code')
        if 'is_new' in params and params['is_new'] not in {'Y','N'}:
            raise ArtifactError('invalid SW observation mode')
    elif endpoint == 'stock_basic':
        if set(params) != {'list_status','exchange'} or params['list_status'] not in {'L','D','P'} or params['exchange'] not in {'','SSE','SZSE'}:
            raise ArtifactError('stock comparison request scope required')
    else:
        raise ArtifactError('unsupported SW qualification endpoint')


def payload_issues(endpoint, params, records):
    definition = load_profile()['endpoints'][endpoint]
    issues = []
    if len(records) >= definition.get('maximum_rows', 10000):
        issues.append('possible_truncation')
    for row in records:
        if set(row) != set(definition['fields']):
            issues.append('response_field_mismatch')
        if endpoint == 'index_member_all':
            for key in ('ts_code','l1_code','l2_code','l3_code','is_new'):
                if key in params and row.get(key) != params[key]:
                    issues.append('request_scope_mismatch:' + key)
        if endpoint == 'index_classify':
            for key in ('level','src'):
                if row.get(key) != params[key]:
                    issues.append('request_scope_mismatch:' + key)
        if endpoint == 'stock_basic' and row.get('list_status') != params['list_status']:
            issues.append('request_scope_mismatch:list_status')
    return sorted(set(issues))


class SwQualificationCollector(TushareCollector):
    """Publish supplier observations even when qualification finds source anomalies.

    Raw collection success does not imply payload qualification or canonical readiness.
    """
    implementation_revision = 'tushare-sw-qualification-collector.v1'

    def collect(self, endpoint, params, *, retrieved_at=None):
        validate_request(endpoint, params)
        profile = load_profile()
        definition = profile['endpoints'][endpoint]
        rows = _response_records(self._client().query(endpoint, fields=','.join(definition['fields']), **params))
        payload = _json_bytes(rows)
        observed = _retrieved_at(retrieved_at)
        request = {'endpoint': endpoint, 'params': params, 'fields': definition['fields']}
        digest = profile_digest()
        code = self.implementation_revision + '-' + _digest(files('axiom_data').joinpath('sw_source.py').read_bytes())[7:]
        seed = {'profile': digest, 'collector_code': code, 'request': request,
                'retrieved_at': observed, 'payload_digest': _digest(payload)}
        issues = payload_issues(endpoint, params, rows)
        return write_raw_batch(self.data_root, 'sw-pilot-' + _digest(_json_bytes(seed))[7:],
            domain=definition['domain'], source_profile='tushare.sw-pilot.' + endpoint,
            source_profile_version=profile['profile_version'], source_profile_digest=digest,
            collector_code=code, request=request, retrieved_at=observed, payload=payload,
            summary={'rows': len(rows), 'qualification_issues': issues,
                     'qualification': 'BLOCKED' if issues else 'STRUCTURAL_CHECKS_ONLY',
                     'canonical_admission': 'NOT_ASSESSED', 'historical_availability': 'best_effort'})
