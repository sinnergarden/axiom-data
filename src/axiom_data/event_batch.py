"""Bounded multi-cutoff transport around the existing event revision selector."""
from dataclasses import replace
import json
from .protocols import EventQuery, QueryError


def iter_event_cutoffs(data, *, snapshot, query, cutoffs, maximum_cache_bytes=64*1024**2,
                       maximum_partition_rows=100000):
    """Yield original event batches for up to 32 cutoffs and 256 securities.

    One pinned Snapshot and evidence index are shared. Every cutoff uses the
    original read_events selector; cached Arrow partitions never cache selected
    revisions. Cache space is reserved before reading using a conservative
    64-byte-per-declared-cell estimate and checked against actual Arrow bytes.
    Oversized partitions/cache fail; no eviction silently repeats source I/O.
    The first Arrow decode and variable-width values are not an RSS hard limit.
    This helper reads only, owns its cache until exhaustion/close, and yields
    one DataBatch at a time rather than retaining every cutoff's output.
    """
    from .event_reader import read_events
    from .public_evidence import evidence_index
    if not isinstance(query, EventQuery) or not 0 < len(query.symbols) <= 256:
        raise QueryError('bounded EventQuery with at most 256 securities required')
    cutoffs = tuple(cutoffs)
    if not 0 < len(cutoffs) <= 32 or type(maximum_cache_bytes) is not int or maximum_cache_bytes <= 0:
        raise QueryError('one to 32 cutoffs and positive cache budget required')
    if type(maximum_partition_rows) is not int or maximum_partition_rows <= 0:
        raise QueryError('positive partition row limit required')
    if snapshot in ('', 'current', 'latest'):
        raise QueryError('pinned Snapshot required')
    store = data.store
    manifest_path = store._path(f'snapshots/{snapshot}.json')
    def mark(path):
        stat = path.stat()
        return stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns
    manifest_epoch = mark(manifest_path)
    manifest = store.load_snapshot(snapshot)
    def check_epoch():
        if mark(manifest_path) != manifest_epoch:
            raise QueryError('event batch Snapshot epoch changed')
        for part in (manifest['domains'].get('public_evidence') or {}).get('partitions',[]):
            store.verify_partition(part)

    class Cache:
        _native_chunks = None
        _native_evidence_index = None
        def __init__(self):
            self.tables = {}; self.retained = 0
        def __getattr__(self, name):
            return getattr(store, name)
        def read_partition(self, part, *, columns=None, symbols=None, sessions=None):
            key = json.dumps([part, columns, symbols, sessions],sort_keys=True)
            if key not in self.tables:
                rows = part.get('rows')
                if type(rows) is not int or not 0 <= rows <= maximum_partition_rows:
                    raise QueryError('event batch partition row budget exceeded')
                # Evidence partitions use their domain schema rather than an
                # unbounded all-column estimate.
                width = len(columns) if columns is not None else max(
                    (len(d['contract']['fields']) for d in manifest['domains'].values()),default=1)
                reserved = 4096 + rows * max(1,width) * 64
                if self.retained + reserved > maximum_cache_bytes:
                    raise QueryError('event batch cache budget exceeded before partition read')
                table = store.read_partition(part,columns=columns,symbols=symbols,sessions=sessions)
                if table.nbytes > reserved:
                    raise QueryError('event batch variable-width partition exceeds reservation')
                self.retained += reserved; self.tables[key] = table
            store.verify_partition(part)
            return self.tables[key]
    check_epoch()
    cached = Cache()
    try:
        evidence = evidence_index(cached,manifest,query.domain)
        for cutoff in cutoffs:
            check_epoch()
            batch = read_events(cached,snapshot,replace(query,cutoff=cutoff),_snapshot=manifest,_evidence=evidence)
            check_epoch()
            yield batch
    finally:
        cached.tables.clear()
