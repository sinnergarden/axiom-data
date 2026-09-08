"""Immutable canonical objects; complete maps reuse identical historical partitions."""
from collections import defaultdict
import json
from axiom_data.artifacts import (ArtifactError, _digest, _json_bytes, _safe_path,
    _publish_directory, _write_file)

POLICY = 'domain_time_blocks.v1'


def partition_key(domain, row):
    if domain == 'security_master':
        return row['exchange']
    if domain == 'financial_events':
        return row['endpoint'] + '-' + row['report_period'][:4]
    if domain in {'holder_count_events', 'top_holders_reports', 'forecast_observations'}:
        return row['report_period'][:4]
    if domain in {'universe_membership', 'industry_membership'}:
        return row['effective_from'][:4]
    session = row.get('session') or row.get('effective_date')
    if not session:
        # Undated corporate-action observations remain explicit, never assigned a made-up date.
        return 'undated'
    return session[:7]


def publish_partitions(layout, domain, rows):
    groups = defaultdict(list)
    for row in rows:
        groups[partition_key(domain, row)].append(row)
    result = []
    for key, values in sorted(groups.items()):
        content = _json_bytes(values)
        digest = _digest(content)
        target = layout.domain_objects(domain) / digest[7:]
        if target.exists():
            path = _safe_path(layout.root, target / 'rows.json', closure=target)
            if path.read_bytes() != content:
                raise ArtifactError('immutable partition content conflict')
        else:
            _publish_directory(layout, target, lambda path: _write_file(path / 'rows.json', content))
        result.append({'key': key, 'object_id': digest[7:], 'content_digest': digest,
                       'rows': len(values), 'bytes': len(content)})
    return result


def read_partitions(layout, domain, manifest, contract):
    if manifest.get('partition_policy') != POLICY or manifest.get('output_files') != []:
        raise ArtifactError('unsupported partition storage contract')
    entries = manifest.get('partitions')
    if not isinstance(entries, list):
        raise ArtifactError('complete partition map required')
    rows = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {'key', 'object_id', 'content_digest', 'rows', 'bytes'}:
            raise ArtifactError('invalid partition entry')
        key, identity = entry['key'], entry['object_id']
        if not isinstance(key, str) or key in seen:
            raise ArtifactError('duplicate or invalid partition key')
        seen.add(key)
        if not isinstance(identity, str) or len(identity) != 64 or any(c not in '0123456789abcdef' for c in identity):
            raise ArtifactError('invalid partition object identity')
        target = layout.domain_objects(domain) / identity
        content = _safe_path(layout.root, target / 'rows.json', closure=target).read_bytes()
        if (_digest(content) != 'sha256:' + identity or entry['content_digest'] != 'sha256:' + identity
            or entry['bytes'] != len(content)):
            raise ArtifactError('partition content digest mismatch')
        try:
            values = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArtifactError('invalid partition JSON') from exc
        if not isinstance(values, list) or len(values) != entry['rows']:
            raise ArtifactError('partition row count mismatch')
        if any(not isinstance(r, dict) or partition_key(domain, r) != key for r in values):
            raise ArtifactError('partition key/content mismatch')
        rows.extend(values)
    rows.sort(key=lambda row: tuple(row[name] for name in contract['sort_order']))
    if _digest(_json_bytes(rows)) != manifest.get('logical_content_digest'):
        raise ArtifactError('DomainCommit logical content digest mismatch')
    return rows
