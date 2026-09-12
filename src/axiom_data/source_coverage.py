"""Versioned observation-coverage lineage, separate from canonical row content."""
from axiom_data.artifacts import ArtifactError, _commit_ref, _digest, _json_bytes, _raw_ref

POLICY = 'source_observations.v2'
LEGACY_POLICY = 'source_observations.v1'


def observation(raw, *, policy=POLICY, evidence=None):
    """Project a validated source observation without retaining its payload."""
    if policy not in {POLICY, LEGACY_POLICY}:
        raise ArtifactError('unsupported source coverage policy')
    if policy == LEGACY_POLICY:
        from axiom_data.source_completeness import validate_raw_completeness_legacy as validate
    else:
        from axiom_data.source_completeness import validate_raw_completeness as validate
    admission = validate(raw) if policy == LEGACY_POLICY else validate(raw, evidence=evidence)
    if policy == POLICY and admission.get('complete') is not True:
        raise ArtifactError('complete source evidence required for coverage')
    manifest = raw.manifest
    summary = manifest.get('summary', {})
    if manifest.get('status') != 'success':
        raise ArtifactError('coverage requires successful Raw')
    if any(summary.get(key) in (False, 'partial', 'PARTIAL', 'truncated', 'TRUNCATED')
           for key in ('complete', 'completeness', 'result_status')):
        raise ArtifactError('partial source observation cannot expand complete coverage')
    if summary.get('truncated') is True or summary.get('partial') is True:
        raise ArtifactError('partial source observation cannot expand complete coverage')
    return {'raw_ref': _raw_ref(raw), 'source_profile': manifest.get('source_profile_ref', manifest.get('source_profile')),
            'source_profile_version': manifest['source_profile_version'],
            'source_profile_digest': manifest['source_profile_digest'],
            'request': manifest['request'], 'observed_at': manifest['retrieved_at'],
            'result_status': manifest['status'], 'payload_admission': admission,
            'row_count': admission['row_count'], 'empty_result': admission['row_count'] == 0}


def state(parent, parent_raw_ids, observations, *, policy=POLICY):
    """Record new observations, or revalidation when coverage policy changes.

    Exact replay keeps the coverage digest. A distinct observation (including
    an empty complete result) changes it, even when canonical rows are equal.
    Offline replay uses the same explicit parent and Raw identities.
    """
    if policy not in {POLICY, LEGACY_POLICY}:
        raise ArtifactError('unsupported source coverage policy')
    previous = None
    if parent is not None:
        previous = parent.manifest.get('source_coverage', {}).get('state_digest')
        if previous is None:
            previous = _digest(_json_bytes({'legacy_parent': _commit_ref(parent),
                                           'raw_batch_ids': sorted(parent_raw_ids)}))
    binding_digest = None
    if policy == POLICY:
        from axiom_data.source_completeness import current_contract_binding
        binding_digest = _digest(_json_bytes(current_contract_binding()))
    parent_coverage = parent.manifest.get('source_coverage', {}) if parent is not None else {}
    revalidate = (policy == POLICY and parent is not None
                  and (parent_coverage.get('schema_version') != policy
                       or parent_coverage.get('policy_binding_digest') != binding_digest))
    new = sorted((item for item in observations if revalidate or
                  item['raw_ref']['raw_batch_id'] not in parent_raw_ids),
                 key=lambda item: item['raw_ref']['raw_batch_id'])
    projection = {'parent_coverage_digest': previous, 'observations': new}
    if policy == POLICY:
        # The complete payload_admission (including effective policy digest)
        # is already in each observation; no Raw bytes are rewritten/recollected.
        projection['schema_version'] = policy
        projection['revalidation'] = revalidate
        projection['policy_binding_digest'] = binding_digest
    digest = previous if previous is not None and not new and not revalidate else _digest(_json_bytes(projection))
    result = {'schema_version': policy, 'parent_coverage_digest': previous,
              'observations': new, 'state_digest': digest}
    if policy == POLICY:
        result['policy_binding_digest'] = binding_digest
    return result
