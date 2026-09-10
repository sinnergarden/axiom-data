"""Version applicability is independent of the date through which data was checked."""
from collections import defaultdict
from axiom_data.artifacts import ArtifactError, _digest, _json_bytes, load_domain_commit

RULESET = 'v1_operational_admission.v1'


def validation_signature(reader, *, semantic_scope, ruleset_digest):
    """Describe required semantics; this function does not itself certify correctness."""
    contracts, builders, profiles = {}, defaultdict(dict), defaultdict(dict)
    pending = list(reader.commits.values())
    seen = set()
    while pending:
        commit = pending.pop()
        key = (commit.ref.domain, commit.ref.commit_id)
        if key in seen:
            continue
        seen.add(key)
        domain = commit.ref.domain
        contracts[domain] = {'version': commit.ref.contract_version,
                             'digest': commit.manifest['contract_digest']}
        config = commit.manifest['builder_config']
        implementation = {'ref': commit.manifest['builder_implementation_ref'],
            'semantic_code': {name: config[name] for name in (
                'implementation_content', 'storage_implementation', 'source_profile_digest',
                'storage_policy', 'no_change_policy', 'industry_source_profile',
                'sw_mapping_profile', 'sw_implementation_content',
                'boundary_profile_digest', 'boundary_mapping_code',
                'session_suspension_profile_digest','session_suspension_code') if name in config}}
        builders[domain][_digest(_json_bytes(implementation))] = implementation
        for raw in commit.manifest['ordered_raw_batch_refs']:
            if raw.get('schema_version') != 'raw_batch.v2':
                raise ArtifactError('V1 validation applicability requires version-bound RawBatch v2')
            profile = {name: raw[name] for name in (
                'source_profile_ref', 'source_profile_version', 'source_profile_digest')}
            profiles[domain][_digest(_json_bytes(profile))] = profile
        parent = commit.manifest['parent_commit_ref']
        if parent is not None:
            pending.append(load_domain_commit(reader.data_root, domain, parent['domain_commit_id']))
    content = {'ruleset': RULESET, 'ruleset_digest': ruleset_digest,
        'contract_versions': contracts,
        'builder_versions': {d: sorted(v.values(), key=_json_bytes) for d, v in builders.items()},
        'source_profile_versions': {d: sorted(v.values(), key=_json_bytes) for d, v in profiles.items()},
        'validated_semantic_scope': semantic_scope}
    return dict(content, applicability_digest=_digest(_json_bytes(content)))


def assess_applicability(approved_signature, required_signature):
    """Only compare applicability. Actual incremental data checks always remain due."""
    for signature in (approved_signature, required_signature):
        content = {k: v for k, v in signature.items() if k != 'applicability_digest'}
        if signature.get('applicability_digest') != _digest(_json_bytes(content)):
            raise ArtifactError('validation applicability digest mismatch')
    changed = sorted(k for k in required_signature if k != 'applicability_digest'
                     and required_signature[k] != approved_signature.get(k))
    return {'version_validation': 'REQUIRED' if changed else 'APPLICABLE',
            'changed_semantics': changed, 'incremental_data_validation': 'REQUIRED',
            'data_coverage': 'NOT_ASSESSED'}
