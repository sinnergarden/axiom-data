"""Bounded reads of existing immutable DomainCommit v2 row arrays."""
from collections.abc import Sequence
import hashlib
import heapq
import io
import itertools
import json
import sys

from axiom_data.artifacts import ArtifactError, _json_bytes, _safe_path
from axiom_data.partitions import POLICY, partition_key

STREAM_ROW_THRESHOLD = 100000
WHOLE_STATE_DOMAINS = {'universe_membership', 'industry_membership', 'trading_calendar', 'security_master'}
SESSION_PARTITION_DOMAINS = {'market_daily', 'security_status', 'price_limits',
    'adjustment_factors', 'benchmark_daily', 'security_capital', 'valuation_daily',
    'margin_daily', 'moneyflow_daily'}


def rows_digest(rows):
    digest = hashlib.sha256(b'[')
    for index, row in enumerate(rows):
        if index:
            digest.update(b',')
        digest.update(_json_bytes(row))
    digest.update(b']')
    return 'sha256:' + digest.hexdigest()


def array_rows(stream, chunk_size=16384):
    """Decode a JSON array without materializing the array or changing its values."""
    decoder = json.JSONDecoder(object_pairs_hook=lambda pairs: {
        sys.intern(key): value for key, value in pairs})
    buffer = ''
    position = 0
    ended = False

    def fill():
        nonlocal buffer, position, ended
        more = stream.read(chunk_size)
        buffer = buffer[position:] + more
        position = 0
        ended = not more

    def token():
        nonlocal position
        while True:
            while position < len(buffer) and buffer[position].isspace():
                position += 1
            if position < len(buffer) or ended:
                return buffer[position:position + 1]
            fill()

    if token() != '[':
        raise ArtifactError('partition rows must be a JSON array')
    position += 1
    if token() != ']':
        while True:
            if token() != '{':
                raise ArtifactError('partition row must be an object')
            while True:
                try:
                    row, stop = decoder.raw_decode(buffer, position)
                    position = stop
                    break
                except json.JSONDecodeError as exc:
                    if ended:
                        raise ArtifactError('invalid partition JSON') from exc
                    fill()
            yield row
            separator = token()
            if separator == ']':
                break
            if separator != ',':
                raise ArtifactError('invalid partition array separator')
            position += 1
    position += 1
    if token():
        raise ArtifactError('trailing partition JSON content')


class PartitionRows(Sequence):
    """Replayable sequence; every pass verifies the exact immutable object bytes."""

    def __init__(self, layout, domain, manifest, contract):
        if manifest.get('partition_policy') != POLICY or manifest.get('output_files') != []:
            raise ArtifactError('unsupported partition storage contract')
        entries = manifest.get('partitions')
        if not isinstance(entries, list):
            raise ArtifactError('complete partition map required')
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
            if entry['content_digest'] != 'sha256:' + identity:
                raise ArtifactError('partition content digest mismatch')
            if any(type(entry[k]) is not int or entry[k] < 0 for k in ('rows', 'bytes')):
                raise ArtifactError('invalid partition size/count')
        self.layout, self.domain = layout, domain
        self.manifest, self.contract = manifest, contract
        self.entries = entries
        self.count = sum(e['rows'] for e in entries)

    def __len__(self):
        return self.count

    def _key(self, row):
        try:
            return tuple(row[k] for k in self.contract['sort_order'])
        except (KeyError, TypeError) as exc:
            raise ArtifactError('partition row sort key missing') from exc

    def _rows(self, entry):
        target = self.layout.domain_objects(self.domain) / entry['object_id']
        path = _safe_path(self.layout.root, target / 'rows.json', closure=target)
        with path.open('rb') as content:
            digest = hashlib.file_digest(content, 'sha256').hexdigest()
            if digest != entry['object_id'] or content.tell() != entry['bytes']:
                raise ArtifactError('partition content digest mismatch')
            content.seek(0)
            previous, count = None, 0
            try:
                with io.TextIOWrapper(content, encoding='utf-8') as text:
                    for row in array_rows(text):
                        if partition_key(self.domain, row) != entry['key']:
                            raise ArtifactError('partition key/content mismatch')
                        key = self._key(row)
                        if previous is not None and key < previous:
                            raise ArtifactError('partition sort order mismatch')
                        previous = key
                        count += 1
                        yield row
            except (UnicodeDecodeError, KeyError, TypeError) as exc:
                raise ArtifactError('invalid partition row content') from exc
            if count != entry['rows']:
                raise ArtifactError('partition row count mismatch')

    def __iter__(self):
        yield from self._iter_entries(self.entries)

    def sessions(self, start, end):
        """Read complete month objects within an already validated composition."""
        if self.domain not in SESSION_PARTITION_DOMAINS:
            raise ArtifactError('session pruning is not supported for this domain')
        yield from self._iter_entries([e for e in self.entries
            if (start is None or e['key'] >= start[:7]) and (end is None or e['key'] <= end[:7])])

    def _iter_entries(self, entries):
        iterators = [self._rows(e) for e in entries]
        try:
            yield from heapq.merge(*iterators, key=self._key)
        finally:
            for iterator in iterators:
                iterator.close()

    def __getitem__(self, index):
        if isinstance(index, slice):
            start, stop, step = index.indices(len(self))
            if step < 0:
                return tuple(self)[index]
            return tuple(itertools.islice(self, start, stop, step))
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        return next(itertools.islice(self, index, index + 1))

    def __eq__(self, other):
        if not isinstance(other, Sequence) or len(self) != len(other):
            return False
        missing = object()
        return all(a == b for a, b in itertools.zip_longest(self, other, fillvalue=missing))

    def validate(self, validator):
        digest = hashlib.sha256(b'[')
        batch, previous, count = [], None, 0
        same_keys = self.contract['primary_key'] == self.contract['sort_order']
        seen = set() if not same_keys else None
        for row in self:
            key = self._key(row)
            if previous is not None and key <= previous:
                raise ArtifactError('canonical sort order or duplicate primary key')
            if seen is not None:
                primary = tuple(row[k] for k in self.contract['primary_key'])
                if primary in seen:
                    raise ArtifactError('duplicate primary key')
                seen.add(primary)
            previous = key
            if count:
                digest.update(b',')
            digest.update(_json_bytes(row))
            count += 1
            batch.append(row)
            if len(batch) == 4096:
                validator(batch)
                batch.clear()
        validator(batch)
        digest.update(b']')
        if count != len(self) or 'sha256:' + digest.hexdigest() != self.manifest.get('logical_content_digest'):
            raise ArtifactError('DomainCommit logical content digest mismatch')
