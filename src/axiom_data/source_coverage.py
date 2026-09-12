"""Versioned observation-coverage lineage, separate from canonical row content."""
from axiom_data.artifacts import ArtifactError, _commit_ref, _digest, _json_bytes, _raw_ref

POLICY = 'source_observations.v1'


def observation(raw):
    """Project a validated source observation without retaining its payload."""
    from axiom_data.source_completeness import validate_raw_completeness
    admission = validate_raw_completeness(raw)
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


def state(parent, parent_raw_ids, observations):
    """A compact chain: only newly observed Raw is recorded at this commit.

    Exact replay keeps the coverage digest. A distinct observation (including
    an empty complete result) changes it, even when canonical rows are equal.
    Offline replay uses the same explicit parent and Raw identities.
    """
    previous = None
    if parent is not None:
        previous = parent.manifest.get('source_coverage', {}).get('state_digest')
        if previous is None:
            previous = _digest(_json_bytes({'legacy_parent': _commit_ref(parent),
                                           'raw_batch_ids': sorted(parent_raw_ids)}))
    new = sorted((item for item in observations if item['raw_ref']['raw_batch_id'] not in parent_raw_ids),
                 key=lambda item: item['raw_ref']['raw_batch_id'])
    digest = previous if previous is not None and not new else _digest(_json_bytes({
        'parent_coverage_digest': previous, 'observations': new}))
    return {'schema_version': POLICY, 'parent_coverage_digest': previous,
            'observations': new, 'state_digest': digest}
