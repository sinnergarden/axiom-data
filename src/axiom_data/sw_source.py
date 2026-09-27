"""SW2021 qualification observations, retained without inferred interval semantics."""
from axiom_data.deprecated.resources import resource_file, profile_generation, historical_profile, source_reference
import json
import re
from importlib.resources import files
from axiom_data.artifacts import ArtifactError, _decode_rows, _digest, _json_bytes, write_raw_batch
from axiom_data.tushare import TushareCollector, _response_records, _retrieved_at
from axiom_data.domains.market import _symbol


def load_profile(version='tushare_sw_pilot.v1'):
    if version not in {'tushare_sw_pilot.v1','tushare_industry_qualification.v1'}:
        raise ArtifactError('unsupported industry SourceProfile')
    return json.loads(resource_file('source_profiles', version+'.json').read_bytes())


def profile_digest(version='tushare_sw_pilot.v1'):
    return _digest(_json_bytes(load_profile(version)))


def validate_request(endpoint, params, *, profile_version='tushare_sw_pilot.v1'):
    if not isinstance(params, dict):
        raise ArtifactError('SW request parameters must be explicit')
    if endpoint not in load_profile(profile_version)['endpoints']:
        raise ArtifactError('endpoint outside industry profile')
    if endpoint == 'index_classify':
        if set(params) != {'src', 'level'} or params['src'] != 'SW2021' or params['level'] not in {'L1','L2','L3'}:
            raise ArtifactError('SW taxonomy requires explicit SW2021 and level')
    elif endpoint in {'index_member_all','ci_index_member'}:
        page = set(params) & {'limit','offset'}
        if page:
            if profile_version != 'tushare_industry_qualification.v1' or page != {'limit','offset'}:
                raise ArtifactError('explicit complete page parameters required')
            maximum=load_profile(profile_version)['endpoints'][endpoint]['maximum_rows']
            if any(not str(params[k]).isdigit() for k in page) or not 1<=int(params['limit'])<=maximum or not 0<=int(params['offset'])<=1000000:
                raise ArtifactError('invalid bounded page')
        selectors = set(params) - {'is_new','limit','offset'}
        if len(selectors)>1 or (not selectors and not page) or not selectors <= {'ts_code','l1_code','l2_code','l3_code'}:
            raise ArtifactError('SW membership requires one bounded selector')
        key = next(iter(selectors),None)
        value = params.get(key)
        if key == 'ts_code':
            _symbol(value)
        elif key is not None and (not isinstance(value, str) or not re.fullmatch(r'[0-9]{6}\.SI' if endpoint=='index_member_all' else r'CI[0-9]{6}\.CI', value)):
            raise ArtifactError('invalid SW industry code')
        if 'is_new' in params and params['is_new'] not in {'Y','N'}:
            raise ArtifactError('invalid SW observation mode')
    elif endpoint == 'stock_basic':
        if set(params) != {'list_status','exchange'} or params['list_status'] not in {'L','D','P'} or params['exchange'] not in {'','SSE','SZSE'}:
            raise ArtifactError('stock comparison request scope required')
    else:
        raise ArtifactError('unsupported SW qualification endpoint')


def payload_issues(endpoint, params, records, *, profile_version='tushare_sw_pilot.v1'):
    definition = load_profile(profile_version)['endpoints'][endpoint]
    issues = []
    if len(records) >= definition.get('maximum_rows', 10000):
        issues.append('possible_truncation')
    for row in records:
        if set(row) != set(definition['fields']):
            issues.append('response_field_mismatch')
        if endpoint in {'index_member_all','ci_index_member'}:
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


def validate_payload_scope(endpoint, params, records, *, profile_version):
    """Use supplier request/row rules; response caps are a separate admission."""
    validate_request(endpoint, params, profile_version=profile_version)
    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
        raise ArtifactError('industry payload must be row objects')
    issues = [issue for issue in payload_issues(endpoint, params, records,
        profile_version=profile_version) if issue != 'possible_truncation']
    if issues:
        raise ArtifactError('industry qualification payload: ' + ','.join(issues))


def validate_raw_scope(raw):
    """Shared profile/field/selector admission for mapping and page evidence."""
    manifest = raw.manifest; version = manifest.get('source_profile_version')
    profile = load_profile(version); request = manifest.get('request', {})
    endpoint = request.get('endpoint'); definition = profile['endpoints'].get(endpoint)
    if (definition is None or manifest.get('schema_version') != 'raw_batch.v2'
            or manifest.get('domain') != definition['domain']
            or manifest.get('source_profile_digest') != profile_digest(version)
            or manifest.get('source_profile_ref') != 'tushare.sw-pilot.' + endpoint
            or set(request) != {'endpoint','params','fields'}
            or request.get('fields') != definition['fields']):
        raise ArtifactError('industry qualification source binding mismatch')
    rows = _decode_rows(raw)
    validate_payload_scope(endpoint, request['params'], rows, profile_version=version)
    return rows


class SwQualificationCollector(TushareCollector):
    """Publish supplier observations even when qualification finds source anomalies.

    Raw collection success does not imply payload qualification or canonical readiness.
    """
    implementation_revision = 'tushare-sw-qualification-collector.v1'
    profile_version = 'tushare_sw_pilot.v1'

    def collect(self, endpoint, params, *, retrieved_at=None):
        validate_request(endpoint, params, profile_version=self.profile_version)
        profile = load_profile(self.profile_version)
        definition = profile['endpoints'][endpoint]
        rows = _response_records(self._client().query(endpoint, fields=','.join(definition['fields']), **params))
        payload = _json_bytes(rows)
        observed = _retrieved_at(retrieved_at)
        request = {'endpoint': endpoint, 'params': params, 'fields': definition['fields']}
        digest = profile_digest(self.profile_version)
        from axiom_data.source_completeness import source_profile_completeness_binding
        completeness = source_profile_completeness_binding(self.profile_version, digest)
        code = self.implementation_revision + '-' + _digest(files('axiom_data').joinpath('sw_source.py').read_bytes())[7:]
        seed = {'profile': digest, 'collector_code': code, 'request': request,
                'source_completeness': completeness,
                'retrieved_at': observed, 'payload_digest': _digest(payload)}
        issues = payload_issues(endpoint, params, rows, profile_version=self.profile_version)
        return write_raw_batch(self.data_root, 'sw-pilot-' + _digest(_json_bytes(seed))[7:],
            domain=definition['domain'], source_profile='tushare.sw-pilot.' + endpoint,
            source_profile_version=profile['profile_version'], source_profile_digest=digest,
            collector_code=code, request=request, retrieved_at=observed, payload=payload,
            summary={'rows': len(rows), 'qualification_issues': issues,
                     'source_completeness': completeness,
                     'qualification': 'BLOCKED' if issues else 'STRUCTURAL_CHECKS_ONLY',
                     'canonical_admission': 'NOT_ASSESSED', 'historical_availability': 'best_effort'})


class IndustryQualificationCollector(SwQualificationCollector):
    profile_version = 'tushare_industry_qualification.v1'
    implementation_revision = 'tushare-industry-qualification-collector.v1'
