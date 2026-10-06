"""Independent observed labels bound to saved retrospective display references."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import shutil
from tempfile import mkdtemp

from .derived import _instant
from .protocols import ConflictError, DataBatch, QueryError
from .review_display import SCHEMA as DISPLAY_SCHEMA


SCHEMA = 'review_security_labels_v1'


def _json_value(value):
    if type(value) in (str, int, float, bool, type(None)):
        return True
    if type(value) is list:
        return all(_json_value(item) for item in value)
    if type(value) is dict:
        return all(type(key) is str and _json_value(item) for key, item in value.items())
    return False


def _observed_labels(batch, symbols):
    context = batch.context
    query = context.get('query') or {}
    if (context.get('contract_version') != 'data_batch_v1' or context.get('domain') != 'security_master'
            or not context.get('snapshot_id') or not context.get('reader_version')
            or query.get('purpose') != 'historical_exploration'):
        raise QueryError('observed labels require a fixed security_master Reader batch')
    if set(query.get('symbols') or ()) != set(symbols):
        raise QueryError('observed label query must match the saved display securities')
    if not {'name', 'source_code'}.issubset(query.get('fields') or ()):
        raise QueryError('observed labels require name and source_code')
    cutoff = _instant(query.get('cutoff'), 'label cutoff')
    wire = batch.to_json()
    keys = {}
    for row in wire['records']:
        security = row.get('security_id')
        if security not in symbols or security in keys or row.get('listing_date') is None:
            raise QueryError('observed labels contain duplicate or unexpected security identities')
        keys[security] = row['listing_date']
        if 'name' not in row or 'source_code' not in row:
            raise QueryError('observed labels lack requested fields')
    for field in ('name', 'source_code'):
        metadata = (wire['field_meta'].get(field) or {}).get('by_key')
        if not isinstance(metadata, list):
            raise QueryError('observed labels require per-key provenance')
        found = set()
        for item in metadata:
            security = item.get('security_id')
            if security not in keys or security in found or item.get('listing_date') != keys[security]:
                raise QueryError('observed label provenance differs from records')
            found.add(security)
            if (not item.get('raw_batch_id') or not item.get('revision_id')
                    or _instant(item.get('first_observed_at'), 'label observation') > cutoff):
                raise QueryError('observed label provenance is missing or later than label cutoff')
            if item.get('usable_from') is not None and _instant(item['usable_from'], 'label usable_from') > cutoff:
                raise QueryError('observed label availability is later than label cutoff')
        if found != set(keys):
            raise QueryError('observed labels are missing per-key provenance')
    return wire


def save_review_security_labels(labels: DataBatch, *, destination, display_manifest,
                                display_manifest_sha256: str, run_ref) -> dict:
    """Save an independent observed-name batch beside an existing display.

    The supplied price manifest is read and verified, without loading its OHLCV
    or querying a source. Name symbols must match its fixed native price query;
    names retain their own Snapshot, aware cutoff and actual observation refs.
    Historical name validity is unknown; listing dates do not date a name.
    run_ref is a caller-supplied strict-JSON opaque value, copied unchanged.
    Data never imports Engine or inspects account semantics; the consumer checks
    the actual run identity and display binding. Writes a new immutable directory
    with securities.json/manifest.json; failure leaves no final directory.
    """
    destination = Path(destination)
    if destination.exists():
        raise ConflictError('security label destination already exists')
    if not isinstance(labels, DataBatch):
        raise QueryError('observed labels require a Reader DataBatch')
    display_bytes = Path(display_manifest).read_bytes()
    if sha256(display_bytes).hexdigest() != display_manifest_sha256:
        raise QueryError('security labels display manifest byte reference differs')
    display = json.loads(display_bytes)
    if display.get('contract_version') != DISPLAY_SCHEMA:
        raise QueryError('security labels require a supported saved price display')
    display_context = display.get('context') or {}
    symbols = ((display_context.get('price_source') or {}).get('query') or {}).get('symbols')
    if not isinstance(symbols, list) or not symbols or len(set(symbols)) != len(symbols):
        raise QueryError('saved price display lacks a fixed security set')
    if run_ref is None:
        raise QueryError('security labels require an opaque run reference')
    try:
        json.dumps(run_ref, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise QueryError('opaque run reference must be strict JSON') from exc
    if not _json_value(run_ref):
        raise QueryError('opaque run reference must be strict JSON with string object keys')
    run_ref = deepcopy(run_ref)
    wire = _observed_labels(labels, symbols)
    query = wire['context']['query']
    named = {r['security_id'] for r in wire['records'] if r.get('name') and r.get('source_code')}
    securities = {'name_kind': 'observed_label', 'name_valid_from': None, 'name_valid_to': None, 'batch': wire}
    payload = (json.dumps(securities, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n').encode()
    manifest = {'contract_version': SCHEMA, 'usage': 'retrospective_label', 'run_ref': run_ref,
                'security_ids': deepcopy(symbols), 'source_snapshot_id': wire['context']['snapshot_id'],
                'label_cutoff': query['cutoff'], 'reader_version': wire['context']['reader_version'],
                'pit_policy': query['pit_policy'], 'missing_name_security_ids': sorted(set(symbols) - named),
                'display_ref': {'uri': 'manifest.json', 'sha256': display_manifest_sha256, 'bytes': len(display_bytes),
                                'snapshot_id': display_context.get('snapshot_id'),
                                'knowledge_cutoff': display_context.get('knowledge_cutoff'),
                                'anchor_session': display_context.get('anchor_session')},
                'files': {'securities.json': {'uri': 'securities.json', 'bytes': len(payload),
                                             'sha256': sha256(payload).hexdigest()}}}
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(mkdtemp(prefix='.security-labels-', dir=destination.parent))
    try:
        (temporary / 'securities.json').write_bytes(payload)
        (temporary / 'manifest.json').write_bytes(manifest_bytes)
        if destination.exists():
            raise ConflictError('security label destination already exists')
        temporary.rename(destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return {**manifest, 'manifest_file_ref': {'uri': 'manifest.json', 'bytes': len(manifest_bytes),
                                            'sha256': sha256(manifest_bytes).hexdigest()}}


def load_review_security_labels(directory, *, manifest_sha256: str) -> dict:
    """Verify and return saved independent labels without queries or transforms.

    Reads only this directory's manifest/securities bytes. The returned manifest
    carries the opaque run and original price-display refs for the consumer to
    check against its own loaded run/display. No external paths are followed,
    no old file is changed and no label becomes historical decision evidence.
    """
    directory = Path(directory)
    manifest_bytes = (directory / 'manifest.json').read_bytes()
    if sha256(manifest_bytes).hexdigest() != manifest_sha256:
        raise QueryError('security label manifest byte reference differs')
    manifest = json.loads(manifest_bytes)
    if manifest.get('contract_version') != SCHEMA or set(manifest.get('files') or {}) != {'securities.json'}:
        raise QueryError('unsupported security label contract/files')
    reference = manifest['files']['securities.json']
    if reference.get('uri') != 'securities.json':
        raise QueryError('security label file must retain its declared local name')
    payload = (directory / 'securities.json').read_bytes()
    if len(payload) != reference.get('bytes') or sha256(payload).hexdigest() != reference.get('sha256'):
        raise QueryError('security label file byte reference differs')
    securities = json.loads(payload)
    context = securities['batch']['context']
    if (context.get('snapshot_id') != manifest.get('source_snapshot_id')
            or (context.get('query') or {}).get('cutoff') != manifest.get('label_cutoff')):
        raise QueryError('security label source context differs from manifest')
    return {'manifest': manifest, 'securities': securities}
