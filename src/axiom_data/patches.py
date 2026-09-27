"""Immutable corrections to committed canonical interpretation, never to Raw."""
from collections.abc import Sequence
import heapq
import itertools

from axiom_data.artifacts import (ArtifactError, RawBatches, _layout, _json_bytes,
    _json_copy, _digest, _raw_ref, _load_manifest, _write_manifest,
    _publish_directory, _identity_digest, _derived_identity,
    _validate_manifest_identity, load_raw_batch)
from axiom_data.build import _identities
from axiom_data.contracts import load_contract, require_writable_contract

PROTOCOL = 'canonical_patch.v1'


def row_digest(row):
    return _digest(_json_bytes(row))


def _validate(root, manifest):
    contract = load_contract(manifest['contract_version'])
    if contract['domain'] != manifest['domain'] or row_digest(contract) != manifest['contract_digest']:
        raise ArtifactError('patch domain/contract mismatch')
    if not isinstance(manifest.get('reason'), str) or not manifest['reason'].strip():
        raise ArtifactError('patch reason required')
    refs = manifest.get('source_evidence_refs')
    if not isinstance(refs, list) or not refs:
        raise ArtifactError('patch requires immutable Raw evidence')
    evidence = {}
    for ref in refs:
        raw = load_raw_batch(root, ref['raw_batch_id'])
        if _raw_ref(raw) != ref or raw.manifest['domain'] != manifest['domain'] or raw.manifest['status'] != 'success':
            raise ArtifactError('patch evidence binding mismatch')
        from axiom_data.source_completeness import validate_raw_completeness
        validate_raw_completeness(raw)
        if raw.ref.raw_batch_id in evidence:
            raise ArtifactError('duplicate patch evidence')
        evidence[raw.ref.raw_batch_id] = raw.manifest
    ops = manifest.get('operations')
    if not isinstance(ops, list) or not ops:
        raise ArtifactError('ordered patch operations required')
    for op in ops:
        kind = op.get('op')
        fields = {'op', 'key'} | ({'row'} if kind == 'insert' else
            {'expected_old_digest', 'row'} if kind == 'replace' else
            {'expected_old_digest'} if kind == 'tombstone' else set())
        if kind not in {'insert', 'replace', 'tombstone'} or set(op) != fields:
            raise ArtifactError('invalid patch operation fields')
        key = op['key']
        if not isinstance(key, dict) or set(key) != set(contract['primary_key']):
            raise ArtifactError('patch requires exact contract primary key')
        try:
            hash(tuple(key[k] for k in contract['primary_key']))
        except TypeError as exc:
            raise ArtifactError('invalid patch primary key') from exc
        if kind != 'insert' and (not isinstance(op['expected_old_digest'], str) or
                len(op['expected_old_digest']) != 71 or not op['expected_old_digest'].startswith('sha256:')):
            raise ArtifactError('patch old row digest required')
        if kind != 'tombstone':
            row = op['row']
            if not isinstance(row, dict) or any(row.get(k) != v for k,v in key.items()):
                raise ArtifactError('patch replacement must preserve exact key')
            # Validate individual canonical fields now; complete state/dependencies
            # are validated by the normal builder after ordered application.
            from axiom_data.artifacts import _validate_domain_rows
            _validate_domain_rows(manifest['domain'], [row], contract=contract)
            from axiom_data.pit import instant
            sources = [(row.get('source_ref'), row.get('first_observed_at'))]
            sources += [(o.get('source_ref'), o.get('observed_at')) for o in row.get('observations', [])]
            if row.get('boundary_source_ref'):
                sources.append((row['boundary_source_ref'], row.get('first_observed_at')))
            for observation in row.get('observations', []):
                sources += [(ref, observation.get('observed_at')) for ref in observation.get('state_raw_refs', [])]
                if observation.get('boundary_source_ref'):
                    sources.append((observation['boundary_source_ref'], observation.get('observed_at')))
            for source, when in sources:
                if source is None:
                    continue  # Daily row contracts do not carry observation fields.
                if source not in evidence or when is None or instant(when) < instant(evidence[source]['retrieved_at']):
                    raise ArtifactError('patch provenance or observation availability mismatch')
    return contract


def publish_patch(data_root, *, domain, contract_version, reason, operations, source_raw_batch_ids):
    """Publish explicit ordered corrections supported by exact immutable Raw refs."""
    require_writable_contract(domain, contract_version)
    layout = _layout(data_root)
    contract = load_contract(contract_version)
    manifest = {'artifact_type': 'canonical_patch', 'schema_version': PROTOCOL,
        'domain': domain, 'contract_version': contract_version,
        'contract_digest': row_digest(contract), 'reason': reason,
        'operations': _json_copy(operations),
        'source_evidence_refs': [_raw_ref(load_raw_batch(layout.root, identity))
            for identity in _identities('source_raw_batch_ids', source_raw_batch_ids)]}
    _validate(layout.root, manifest)
    digest = _identity_digest(manifest, 'patch_id')
    identity = _derived_identity('patch', digest)
    manifest.update(patch_id=identity, identity_digest=digest)
    _publish_directory(layout, layout.patches / identity, lambda path: _write_manifest(path, manifest))
    return {'patch_id': identity, 'manifest_digest': _digest(_json_bytes(manifest)), 'manifest': manifest}


def load_patch(data_root, patch_id):
    layout = _layout(data_root)
    _identities('patch_ids', [patch_id])
    manifest, digest = _load_manifest(layout.root, layout.patches / patch_id,
        artifact_type='canonical_patch', schema_version=PROTOCOL,
        identity_field='patch_id', identity=patch_id)
    _validate_manifest_identity(manifest, 'patch_id', 'patch', patch_id)
    _validate(layout.root, manifest)
    return {'patch_id': patch_id, 'manifest_digest': digest, 'manifest': manifest}


def patch_ref(patch):
    return {key: patch[key] for key in ('patch_id', 'manifest_digest')}


def load_refs(root, refs, contract):
    if not isinstance(refs, list):
        raise ArtifactError('ordered patch refs required')
    _identities('patch_ids', [ref['patch_id'] for ref in refs])
    patches = []
    for ref in refs:
        patch = load_patch(root, ref['patch_id'])
        if patch_ref(patch) != ref or patch['manifest']['domain'] != contract['domain'] or patch['manifest']['contract_digest'] != row_digest(contract):
            raise ArtifactError('patch ref/domain/contract mismatch')
        patches.append(patch)
    return patches


class PatchedRows(Sequence):
    """Replayable sorted overlay holding only the keys touched by corrections."""
    def __init__(self, rows, contract, patches):
        self.base, self.contract = rows, contract
        self.key = lambda row: tuple(row[k] for k in contract['primary_key'])
        self.sort = lambda row: tuple(row[k] for k in contract['sort_order'])
        ops = [op for p in patches for op in p['manifest']['operations']]
        self.changed = {self.key(op['key']): None for op in ops}
        for row in rows:
            key = self.key(row)
            if key in self.changed:
                self.changed[key] = row
        self.original = dict(self.changed)
        for op in ops:
            key = self.key(op['key'])
            old = self.changed[key]
            if (op['op'] == 'insert' and old is not None) or (op['op'] != 'insert' and
                    (old is None or row_digest(old) != op['expected_old_digest'])):
                raise ArtifactError('patch precondition conflict at exact canonical key')
            self.changed[key] = op.get('row')
        self.count = len(rows) + sum(r is not None for r in self.changed.values()) - sum(r is not None for r in self.original.values())

    def __len__(self):
        return self.count

    def __iter__(self):
        retained = (row for row in self.base if self.key(row) not in self.changed)
        yield from heapq.merge(retained, sorted((r for r in self.changed.values() if r is not None), key=self.sort), key=self.sort)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return list(self)[index]
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        return next(itertools.islice(self, index, index + 1))


def apply_patches(rows, contract, patches):
    return PatchedRows(rows, contract, patches) if patches else rows


def publish_partitions(layout, domain, rows):
    from axiom_data.partition_rows import PartitionRows
    from axiom_data.partitions import publish_partitions as publish, partition_key
    if not isinstance(rows, PatchedRows) or not isinstance(rows.base, PartitionRows):
        return publish(layout, domain, rows)
    affected = {partition_key(domain, row) for row in
        [*rows.original.values(), *rows.changed.values()] if row is not None}
    entries = [entry for entry in rows.base.entries if entry['key'] not in affected]
    changed = (row for row in rows if partition_key(domain, row) in affected)
    return sorted(entries + publish(layout, domain, changed), key=lambda entry: entry['key'])


def ancestry(root, parent):
    """Read already validated ancestor manifests; retain refs, never historical rows."""
    layout = _layout(root)
    chain = []
    node = parent.manifest if parent else None
    while node is not None:
        chain.append(node)
        ref = node['parent_commit_ref']
        if ref is None:
            break
        node, _ = _load_manifest(layout.root,
            layout.domain_commits(ref['domain']) / ref['domain_commit_id'],
            artifact_type='domain_commit',
            schema_version=('domain_commit.v1', 'domain_commit.v2', 'domain_commit.v3', 'domain_commit.v4'),
            identity_field='domain_commit_id', identity=ref['domain_commit_id'])
    return list(reversed(chain))


def build_rows(builder, contract, parent, raw_batches, patches):
    """One correction/replay path shared by staging and closure validation."""
    from axiom_data.domains import FUNDAMENTAL_DOMAINS, EVENT_DOMAINS
    from axiom_data.partition_rows import PartitionRows
    previous = parent.rows if parent else ()
    chain = ancestry(builder.layout.root, parent) if parent and parent.manifest.get('patch_protocol') else []
    inherited = [p for node in chain for p in load_refs(builder.layout.root, node['ordered_patch_refs'], contract)]
    if set(p['patch_id'] for p in inherited) & set(p['patch_id'] for p in patches):
        raise ArtifactError('patch already present in ancestor lineage')
    if not raw_batches and parent:
        builder.group_states = parent.manifest.get('group_states', [])
        if parent.manifest.get('partition_policy'):
            previous = PartitionRows(builder.layout, builder.domain, parent.manifest, contract)
        validate_source_availability(builder, contract, previous, patches)
        return apply_patches(previous, contract, patches)
    replaying = builder.domain in FUNDAMENTAL_DOMAINS + EVENT_DOMAINS
    if inherited and replaying:
        ids = list(dict.fromkeys([r['raw_batch_id'] for node in chain for r in node['ordered_raw_batch_refs']]
            + [raw.ref.raw_batch_id for raw in raw_batches]))
        builder.parent_group_states = []
        rows = builder._build_rows(contract, (), RawBatches(builder.layout.root, ids))
        rows = apply_patches(rows, contract, inherited)
    else:
        rows = builder._build_rows(contract, previous, raw_batches)
        if inherited:
            # Incremental builders already carry corrections. New Raw must not
            # silently resurrect a tombstone or overwrite a replacement.
            final = {tuple(op['key'][k] for k in contract['primary_key']): op.get('row')
                for patch in inherited for op in patch['manifest']['operations']}
            actual = {tuple(row[k] for k in contract['primary_key']): row for row in rows
                if tuple(row[k] for k in contract['primary_key']) in final}
            if any(actual.get(key) != row for key,row in final.items()):
                raise ArtifactError('new Raw conflicts with inherited correction')
    validate_source_availability(builder, contract, rows, patches)
    return apply_patches(rows, contract, patches)


def evidence_ids(root, parent):
    return {ref['raw_batch_id'] for node in ancestry(root, parent)
        for patch in load_refs(root, node['ordered_patch_refs'], parent.contract)
        for ref in patch['manifest']['source_evidence_refs']} if parent and parent.manifest.get('patch_protocol') else set()


def validate_incremental_replay(root, commit, parent, raw_refs, patches):
    from axiom_data.artifacts import MarketDomainBuilder, _equal_rows
    from axiom_data.tushare import TushareMarketBuilder
    from axiom_data.reference_source import TushareReferenceBuilder
    from axiom_data.exchange_security import ExchangeSecurityBuilder
    builders = {cls.__module__ + '.' + cls.__qualname__: cls for cls in
        (MarketDomainBuilder, TushareMarketBuilder, TushareReferenceBuilder, ExchangeSecurityBuilder)}
    from axiom_data.deprecated.resources import builder_implementation
    name = builder_implementation(commit.manifest['builder_implementation_ref']['implementation'])
    if name not in builders:
        raise ArtifactError('unsupported canonical correction replay builder')
    deps = {domain: ref['domain_commit_id'] for domain,ref in commit.manifest['dependency_commit_refs'].items()}
    options = ({'calendar_commit_id': deps.get('trading_calendar'), 'security_master_commit_id': deps.get('security_master')}
        if builders[name] is TushareMarketBuilder else {'dependency_commit_ids': deps})
    builder = builders[name](root, commit.ref.domain,
        builder_config=commit.manifest['builder_config'], **options)
    expected = build_rows(builder, commit.contract, parent,
        RawBatches(root, [ref['raw_batch_id'] for ref in raw_refs]), patches)
    if not _equal_rows(expected, commit.rows):
        raise ArtifactError('canonical rows differ from ordered correction replay')


def validate_source_availability(builder, contract, rows, patches):
    """Use the existing source mapper to prove identity and observation times.

    Economic corrections may change revision fingerprints. Their observation
    authority cannot be invented by the correction, even with a fresh key.
    """
    import copy
    proposed = [op['row'] for patch in patches for op in patch['manifest']['operations'] if 'row' in op]
    if not proposed:
        return
    key_fields = ['logical_event_key'] if 'logical_event_key' in proposed[0] else contract['primary_key']
    key = lambda row: tuple(row[field] for field in key_fields)
    wanted = {key(row) for row in proposed}
    ids = {ref['raw_batch_id'] for patch in patches for ref in patch['manifest']['source_evidence_refs']}
    # Include known observations for these facts so selecting only a later Raw
    # cannot move the earliest proved observation forward.
    for row in rows:
        if key(row) in wanted:
            ids.update(o['source_ref'] for o in row.get('observations', []))
            if row.get('source_ref'):
                ids.add(row['source_ref'])
    mapper = copy.copy(builder)
    mapper.parent_group_states = []
    try:
        supported = mapper._build_rows(contract, (), RawBatches(builder.layout.root, sorted(ids)))
    except (ValueError, KeyError, TypeError) as exc:
        raise ArtifactError('unsupported patch source availability evidence') from exc
    # Compare actual identity with mapper output as well as the supplied key.
    # These are the canonical components of the existing source logical/action
    # keys; value fields and interval ends remain interpretation corrections.
    identity_fields = ('symbol', 'endpoint', 'session', 'report_period',
        'report_type', 'group_id', 'effective_from', 'action_type', 'announcement_date')
    fields = ('source_ref', 'first_observed_at', 'source_available_at',
        'vendor_available_at', 'availability_basis', 'pit_qualification', 'boundary_source_ref')
    def signature(row):
        observations = [{k:v for k,v in o.items() if k not in {'revision_id', 'observation_id'}}
            for o in row.get('observations', [])]
        return (key(row), row_digest({field:row[field] for field in identity_fields if field in row}),
                row_digest({field:row[field] for field in fields if field in row}),
                row_digest(sorted(observations, key=row_digest)))
    allowed = {signature(row) for row in supported if key(row) in wanted}
    if any(signature(row) not in allowed for row in proposed):
        raise ArtifactError('unsupported patch logical key or source availability evidence')
