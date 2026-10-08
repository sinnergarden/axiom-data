"""Bounded, process-local daily columns sharing the ordinary Reader selector.

No disk View, Qlib export, cross-process handle or row/by_key result cache.
Limits charge the loaded Snapshot, shared LRU, pinned evicted blocks, selection
arrays and conversion reservations. Decoder pages and normal Snapshot-loader
transients remain outside that retained budget and need an external RSS guard.
"""
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import date, datetime, timezone
from dataclasses import replace
from hashlib import sha256
import os
import sys
from types import MappingProxyType
import weakref

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

from .protocols import ConflictError, QueryError, QuerySpec
from .reader import (_ENTRY_BYTES, _object_size, _instant, _release_time,
                     _policy_for, _select_daily_revision, _select_daily_ordinals)
from .storage import LocalStore, _json_digest

VERSION='data_column_selection_v1'
_FIELDS={'market_daily':('open','close'),'adjustment_factors':('factor',)}
_DTYPES={'float':'<f8','double':'<f8','float64':'<f8','float32':'<f4',
    'int':'<i8','integer':'<i8','int64':'<i8','int32':'<i4','int16':'<i2','int8':'i1',
    'uint64':'<u8','uint32':'<u4','uint16':'<u2','uint8':'u1','bool':'?','boolean':'?'}
_REASONS=(None,'source_missing','not_visible_at_cutoff','not_provided')
_BASES=(None,'declared_vendor_assumption','revision_bound_source_evidence','first_observed_at')
_ADJUST_REASONS=(*_REASONS,'price_missing','invalid_price','missing_anchor_factor',
    'invalid_anchor_factor','missing_factor','invalid_factor','invalid_adjusted_value')
_EVIDENCE=('target_domain','target_key','target_revision','public_at','document_sha256','raw_batch_id')
_EPOCH=datetime(1970,1,1,tzinfo=timezone.utc)
_NAT=np.iinfo(np.int64).min
_KEY_DTYPE=np.dtype([('security','<i4'),('session','<i8')])


def _require(ok,message):
    if not ok: raise QueryError(message)


def _freeze(value):
    if isinstance(value,Mapping): return MappingProxyType({k:_freeze(v) for k,v in value.items()})
    if isinstance(value,(list,tuple)): return tuple(_freeze(v) for v in value)
    return value


def _plain(value):
    if isinstance(value,Mapping): return {k:_plain(v) for k,v in value.items()}
    if isinstance(value,tuple): return [_plain(v) for v in value]
    return value


def _ref(value): return 'sha256:'+_json_digest(value)


def _mark(path):
    s=path.stat()
    return s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns


def _ns(value,*,bounded=True):
    delta=value.astimezone(timezone.utc)-_EPOCH
    result=((delta.days*86400+delta.seconds)*1000000+delta.microseconds)*1000+getattr(value,'nanosecond',0)
    if bounded: _require(_NAT<result<=np.iinfo(np.int64).max,'available_at is outside datetime64[ns] range')
    return result


def _array_ref(array):
    """Canonical primitive dtype, shape, C order and actual little-endian bytes."""
    _require(not array.dtype.hasobject,'object arrays cannot bind column facts')
    dtype=array.dtype.newbyteorder('<')
    array=np.asarray(array,dtype=dtype,order='C')
    digest=sha256()
    digest.update(_json_digest({'dtype':dtype.str,'shape':list(array.shape),'order':'C'}).encode())
    if array.size: digest.update(memoryview(array.view('u1').reshape(-1)))
    return 'sha256:'+digest.hexdigest()


def _readonly(array):
    # An immutable bytes backing prevents callers re-enabling WRITEABLE.
    dtype=array.dtype.newbyteorder('<')
    return np.frombuffer(np.asarray(array,dtype=dtype,order='C').tobytes(order='C'),
                         dtype=dtype).reshape(array.shape)


def _binding(query,cutoffs):
    return dict(domain=query.domain,fields=list(query.fields),symbols=list(query.symbols),
        sessions=list(query.sessions),pit_policy=query.pit_policy,
        cutoff_by_session={s:cutoffs[s].isoformat() for s in query.sessions},
        purpose=query.purpose,price_basis=query.price_basis,adjustment_anchor=query.adjustment_anchor,
        universe_id=query.universe_id,
        policy_by_session=dict(query.policy_by_session) if query.policy_by_session is not None else None)


def _table_charge(table):
    from .native_view import _ProjectionStore
    return _ProjectionStore._arrow_charge(table)


def _release_charge(owner_ref,kind,size):
    owner=owner_ref()
    if owner is not None: owner._charges[kind]-=size


def _clock_column(column):
    """Parse each distinct scalar once; errors are flags, checked in query scope."""
    if not len(column) or pa.types.is_null(column.type):
        return np.zeros(len(column),dtype='<i8'),np.ones(len(column),dtype='?')
    encoded=pc.dictionary_encode(column).unify_dictionaries().combine_chunks()
    values=encoded.dictionary.to_pylist();numbers=np.zeros(len(values)+1,dtype='<i8')
    bad=np.ones(len(values)+1,dtype='?')
    for i,value in enumerate(values):
        try:
            number=_ns(_instant(value,'clock'),bounded=False)
            if _NAT<number<=np.iinfo(np.int64).max: numbers[i]=number;bad[i]=False
        except (QueryError,ValueError,OverflowError,TypeError): pass
    indices=pc.fill_null(encoded.indices,-1).to_numpy(zero_copy_only=False)
    return numbers[indices],bad[indices]


class _Rows(list):
    """Requested group scratch stays charged for its complete selector lifetime."""
    def __init__(self,owner,charge):
        owner._check_budget(charge);super().__init__()
        self.owner,self.charge=owner,charge;owner._temporary+=charge
    def __del__(self):
        owner=getattr(self,'owner',None)
        if owner is not None: owner._temporary-=self.charge


class ReadOnlyArray:
    """Revocable array borrow. to_numpy/np.asarray makes an owned readonly copy.

    Borrow indexing checks owner, process and source marks. Copies are explicit
    ownership transfers and remain ordinary caller-owned arrays after close.
    Requested primitive dtypes are normalized to little-endian before copying.
    copy=False is refused; no writable view or raw mutable buffer escapes.
    """
    __slots__=('_selection','_name','_slices')
    def __init__(self,selection,name,slices=()):
        self._selection,self._name,self._slices=selection,name,slices
    def _get(self):
        self._selection._check()
        value=self._selection._arrays[self._name]
        for key in self._slices: value=value[key]
        return value
    @property
    def shape(self): return self._get().shape
    @property
    def dtype(self): return self._get().dtype
    @property
    def ndim(self): return self._get().ndim
    def __len__(self): return len(self._get())
    def __getitem__(self,key):
        def basic(value):
            if isinstance(value,tuple): return all(basic(v) for v in value)
            if isinstance(value,slice): return all(v is None or type(v) is int or isinstance(v,np.integer) for v in (value.start,value.stop,value.step))
            return value is None or value is Ellipsis or type(value) is int or isinstance(value,np.integer)
        _require(basic(key),'advanced indexing requires an explicit owned column copy')
        value=self._get()[key]
        if isinstance(value,np.ndarray): return ReadOnlyArray(self._selection,self._name,(*self._slices,key))
        return value.item() if value.dtype.kind!='M' else value
    def to_numpy(self,*,dtype=None):
        value=self._get()
        target=(np.dtype(dtype) if dtype is not None else value.dtype).newbyteorder('<')
        _require(not target.hasobject,'column copies require a primitive dtype')
        with self._selection._owner._reserve(2*value.size*target.itemsize+4096):
            return _readonly(np.asarray(value,dtype=target))
    def __array__(self,dtype=None,copy=None):
        _require(copy is not False,'column borrows cannot expose a zero-copy ndarray')
        return self.to_numpy(dtype=dtype)
    def __reduce__(self): raise QueryError('column borrows cannot cross a process boundary')


class DictionaryValues:
    """Compact reason/basis codes; dictionary[code] gives the original label."""
    __slots__=('_selection','_name','_dictionary')
    def __init__(self,selection,name,dictionary):
        self._selection,self._name,self._dictionary=selection,name,dictionary
    @property
    def codes(self): self._selection._check();return ReadOnlyArray(self._selection,self._name)
    @property
    def dictionary(self): self._selection._check();return self._dictionary
    def __getitem__(self,key):
        codes=self.codes[key]
        if isinstance(codes,ReadOnlyArray): return codes
        return self._dictionary[codes]


class Column:
    """One ordered field with original dtype/unit/basis and revocable arrays.

    Validity denotes a present source scalar. Nonfinite floating scalars retain
    their bits; Data's adjustment kernel applies its existing missing/invalid
    rules. Missing reason labels keep the ordinary Reader's source states.
    """
    __slots__=('_selection','_field')
    def __init__(self,selection,field): self._selection,self._field=selection,field
    @property
    def metadata(self):
        self._selection._check();return self._selection._headers[self._field]
    @property
    def dtype(self): return self.values.dtype
    @property
    def values(self): self._selection._check();return ReadOnlyArray(self._selection,'value:'+self._field)
    @property
    def validity(self): self._selection._check();return ReadOnlyArray(self._selection,'valid:'+self._field)
    @property
    def missing_reason(self): return DictionaryValues(self._selection,'reason:'+self._field,self._selection._reasons)
    @property
    def available_at(self): self._selection._check();return ReadOnlyArray(self._selection,'available')


class ProvenanceColumn:
    """Lazy version-column borrow, without constructing per-row provenance dicts."""
    __slots__=('_selection','_name','_lineage')
    def __init__(self,selection,name,lineage='native'):
        self._selection,self._name,self._lineage=selection,name,lineage
    def __getitem__(self,key):
        self._selection._check()
        _require(isinstance(key,tuple) and len(key)==2,'provenance uses (session_index, security_index)')
        if self._name=='availability_basis':
            return _BASES[int(self._selection._arrays['basis' if self._lineage=='native' else 'basis:'+self._lineage][key])]
        row=self._selection._row(key,self._lineage)
        return row.get(self._name) if row is not None else None
    def __reduce__(self): raise QueryError('column borrows cannot cross a process boundary')


class ChangedKeys:
    """Columnar added/updated/removed key coordinates; iteration yields keys.

    Added/updated coordinates index this selection's axes. Removed coordinates
    index previous_axes, detached from the previous selection's lifetime.
    Updated keys describe actual version/source/value/validity/clock/basis or
    anchor dependencies. A cutoff or policy label with identical selected facts
    changes query_binding/selection_ref, without invalidating all numeric keys.
    """
    __slots__=('_selection',)
    def __init__(self,selection): self._selection=selection
    @property
    def added(self): return ReadOnlyArray(self._selection,'change:added')
    @property
    def updated(self): return ReadOnlyArray(self._selection,'change:updated')
    @property
    def removed(self): return ReadOnlyArray(self._selection,'change:removed')
    @property
    def previous_axes(self):
        self._selection._check();return self._selection._previous_axes
    def __iter__(self):
        selection=self._selection;selection._check()
        for kind,axes in (('added',selection._axes),('updated',selection._axes),('removed',selection._previous_axes)):
            for i,j in selection._arrays['change:'+kind]:
                selection._check();yield axes['sessions'][int(i)],axes['security'][int(j)]


class _RowView(Mapping):
    __slots__=('block','ordinal','names','overlay')
    def __init__(self,block,ordinal,names,evidence=None,logical_key=()):
        self.block,self.ordinal,self.names,self.overlay=block,int(ordinal),names,{}
        if evidence:
            from .public_evidence import _key
            item=evidence.get((_key(self,logical_key),self.get('revision_id')))
            if item is not None:
                existing=self.get('source_available_at')
                if existing is not None and existing!=item['public_at']:
                    raise ConflictError('attached public time conflicts with canonical revision evidence')
                self.overlay={'source_available_at':item['public_at'],
                    'evidence_ref':'raw:'+item['raw_batch_id']+'#'+item['document_sha256']}
    def __iter__(self): return iter(self.names)
    def __len__(self): return len(self.names)
    def __getitem__(self,name):
        if name not in self.names: raise KeyError(name)
        if name in self.overlay: return self.overlay[name]
        value=self.block.columns[name][self.ordinal].as_py()
        if name=='session' and isinstance(value,date): return value.isoformat()
        return value


class _Block:
    def __init__(self,owner,key,part,table,domain):
        self.owner,self.key,self.part,self.table=weakref.ref(owner),key,part,table
        self.columns={name:table[name] for name in table.column_names}
        self.ref=_ref({'snapshot':owner._snapshot_id,'domain':domain,'partition':part,
            'fields':table.column_names,'contract_ref':owner._domain_refs[domain]})
        self.charge=_table_charge(table)+4*_object_size(self.ref)
        self.security={};self.keys=None;self.order=None;self.lengths={};self.sessions=frozenset()
        self.values={};self.clocks={}
        # Numeric byte lengths are admitted once with the block. Group scratch
        # fees then need only array gathers, without warm Arrow kernels or text
        # expansion across the whole partition.
        for name,column in self.columns.items():
            pieces=[]
            for chunk in column.chunks:
                dictionary=pa.types.is_dictionary(chunk.type)
                values=chunk.dictionary if dictionary else chunk;dtype=values.type
                if pa.types.is_string(dtype) or pa.types.is_large_string(dtype) or pa.types.is_binary(dtype) or pa.types.is_large_binary(dtype):
                    lengths=pc.binary_length(values)
                    if dictionary: lengths=pc.take(lengths,chunk.indices)
                    pieces.append(pc.fill_null(lengths,0).to_numpy(zero_copy_only=False).astype('<i8'))
                elif pa.types.is_fixed_size_binary(dtype): pieces.append(np.full(len(chunk),dtype.byte_width,dtype='<i8'))
            if pieces:
                self.lengths[name]=np.concatenate(pieces)
                self.charge+=self.lengths[name].nbytes+256

        if (domain!='public_evidence' and table.num_rows and
                not pa.types.is_null(table['security_id'].type) and not pa.types.is_null(table['session'].type)):
            encoded=pc.dictionary_encode(table['security_id']).unify_dictionaries().combine_chunks()
            labels=encoded.dictionary.to_pylist()
            self.security={value:i for i,value in enumerate(labels) if value is not None}
            codes=pc.fill_null(encoded.indices,-1).to_numpy(zero_copy_only=False)
            dates=pc.dictionary_encode(table['session']).unify_dictionaries().combine_chunks()
            days=[]
            for value in dates.dictionary.to_pylist():
                try:
                    parsed=date.fromisoformat(value) if isinstance(value,str) else value
                    days.append(parsed.toordinal() if not isinstance(parsed,datetime) and (not isinstance(value,str) or parsed.isoformat()==value) else -1)
                except (ValueError,AttributeError,TypeError): days.append(-1)
            indices=pc.fill_null(dates.indices,-1).to_numpy(zero_copy_only=False)
            days=np.asarray([*days,-1],dtype='<i8')[indices]
            keys=np.empty(table.num_rows,dtype=_KEY_DTYPE)
            keys['security']=codes;keys['session']=days
            self.order=np.argsort(keys,order=('security','session'),kind='stable')
            self.keys=keys[self.order]
            self.sessions=frozenset(days[days>0])
            self.charge+=self.keys.nbytes+self.order.nbytes+4*_object_size(self.security)+1024
        if domain!='public_evidence':
            for name in ('first_observed_at','source_available_at'):
                self.clocks[name]=_clock_column(table[name])
                self.charge+=sum(a.nbytes+256 for a in self.clocks[name])
            seq=table['revision_sequence'];kind=seq.type
            if pa.types.is_integer(kind):
                raw=pc.fill_null(seq,0).to_numpy(zero_copy_only=False)
                valid=np.asarray(seq.is_valid());self.sequence=raw.astype('<i8')
                self.sequence_bad=~valid
                if pa.types.is_unsigned_integer(kind): self.sequence_bad|=raw>np.iinfo(np.int64).max
            else:
                self.sequence=np.zeros(table.num_rows,dtype='<i8');self.sequence_bad=np.ones(table.num_rows,dtype='?')
            refs=table['evidence_ref']
            # Null/empty references are false under the scalar Reader rule.
            if pa.types.is_null(refs.type): self.source_bound=np.zeros(table.num_rows,dtype='?')
            elif 'evidence_ref' in self.lengths: self.source_bound=self.lengths['evidence_ref']>0
            elif pa.types.is_boolean(refs.type) or pa.types.is_integer(refs.type) or pa.types.is_floating(refs.type):
                self.source_bound=np.asarray(pc.fill_null(pc.cast(refs,pa.bool_()),False))
            else: self.source_bound=np.asarray(refs.is_valid())
            self.source_bound&=np.asarray(table['source_available_at'].is_valid())
            for name in _FIELDS[domain]:
                if name not in self.columns: continue
                col=table[name];valid=np.asarray(col.is_valid())
                raw=np.zeros(table.num_rows) if pa.types.is_null(col.type) else pc.fill_null(col,0).to_numpy(zero_copy_only=False)
                self.values[name]=(raw,valid)
                self.charge+=raw.nbytes+valid.nbytes+512
            self.charge+=self.sequence.nbytes+self.sequence_bad.nbytes+self.source_bound.nbytes+4*_object_size(self.sessions)+1024
    def matching(self,symbol,session):
        code=self.security.get(symbol)
        if code is None or self.keys is None: return ()
        key=np.array((code,date.fromisoformat(session).toordinal()),dtype=_KEY_DTYPE)
        start=np.searchsorted(self.keys,key,side='left');end=np.searchsorted(self.keys,key,side='right')
        return self.order[start:end]
    def candidates(self,days,symbols):
        """One vector range lookup per block, restricted to its actual sessions."""
        active=np.flatnonzero(np.isin(days,np.fromiter(self.sessions,dtype='<i8')))
        if self.keys is None or not len(active): return np.empty(0,dtype='<i8'),np.empty(0,dtype='<i8'),0
        codes=np.fromiter((self.security.get(s,-1) for s in symbols),dtype='<i4')
        keys=np.empty((len(active),len(symbols)),dtype=_KEY_DTYPE)
        keys['security']=codes;keys['session']=days[active,None]
        starts=np.searchsorted(self.keys,keys.ravel(),side='left');ends=np.searchsorted(self.keys,keys.ravel(),side='right')
        sizes=ends-starts;sizes[np.tile(codes<0,len(active))]=0
        total=int(sizes.sum());offsets=np.cumsum(sizes)-sizes
        fee=192*total+4096
        owner=self.owner();owner._check_budget(fee);owner._temporary+=fee
        try:
            positions=np.arange(total,dtype='<i8')+np.repeat(starts-offsets,sizes)
            cells=np.repeat((active[:,None]*len(symbols)+np.arange(len(symbols))).ravel(),sizes)
            return cells,self.order[positions],fee
        except Exception:
            owner._temporary-=fee;raise


class _Evidence(dict):
    def __init__(self,blocks,domain,max_ref_bytes):
        super().__init__();self.blocks=tuple(blocks);self.charge=4096;self.max_ref_bytes=max_ref_bytes
        for block in blocks:
            for ordinal in range(block.table.num_rows):
                if block.columns['target_domain'][ordinal].as_py()!=domain: continue
                row=_RowView(block,ordinal,_EVIDENCE)
                key=row['target_key'],row['target_revision'];previous=self.get(key)
                if previous is not None and (previous['public_at'],previous['document_sha256'])!=(row['public_at'],row['document_sha256']):
                    raise ConflictError('conflicting public evidence for one fact revision')
                if key not in self: self.charge+=4*_object_size(key)+512
                self[key]=row
        self.charge+=sys.getsizeof(self)
        self.target_cells=set()
        import json
        for target,_ in self:
            try:
                key=json.loads(target);self.target_cells.add((key['security_id'],key['session']))
            except (ValueError,TypeError,KeyError): pass
        self.charge+=4*_object_size(self.target_cells)


class _Groups:
    def __init__(self,owner,query,blocks,evidence):
        self.owner,self.query,self.blocks,self.evidence=owner,query,tuple(blocks),evidence
        domain=owner._reader.snapshot['domains'][query.domain]
        self.names=tuple(owner._reader._columns(domain,query));self.logical_key=tuple(domain['contract']['logical_key'])
        self.days=np.fromiter((date.fromisoformat(s).toordinal() for s in query.sessions),dtype='<i8')
        self.by_session={s:tuple(b for b in self.blocks if day in b.sessions) for s,day in zip(query.sessions,self.days)}
    def get(self,key,default=None):
        symbol,session=key
        matches=[(block,block.matching(symbol,session)) for block in self.by_session[session]]
        count=sum(len(indices) for _,indices in matches);logical=0
        for block,indices in matches:
            if not len(indices): continue
            with self.owner._reserve(4096+16*len(indices)):
                logical+=sum(int(block.lengths[name][indices].sum()) for name in self.names if name in block.lengths)
        if self.evidence is not None: logical+=count*self.evidence.max_ref_bytes
        rows=_Rows(self.owner,20*logical+256*count*len(self.names)+1024*count+4096)
        for block,indices in matches:
            for i in indices: rows.append(_RowView(block,int(i),self.names,self.evidence,self.logical_key))
        return rows if rows else ([] if default is None else default)


class ColumnSelection:
    """Readonly daily selection bound to its live ColumnSource.

    axes orders sessions, security and fields. Arrays use (session, security).
    selected_version_indices maps lineage name to int64 (..., 2) coordinates:
    source_block_refs position, then original physical row ordinal; -1 is absent.
    available_at uses UTC datetime64[ns] (NaT for an absent selection); times
    outside that primitive's range are explicitly refused. Reason/basis
    dictionaries encode labels without row dictionaries. Source
    block refs bind immutable partition/projection facts, independently of IO
    batch boundaries. selection_ref additionally binds typed array bytes and
    the complete QuerySpec. previous is only a diff hint; selection always runs.
    """
    @property
    def contract_version(self): return VERSION
    def __init__(self,owner,query,binding,headers,blocks,evidence,arrays,fallback):
        self._owner,self._query=owner,query
        self._binding=_freeze(binding);self._headers=_freeze(headers)
        self._axes=_freeze({'sessions':query.sessions,'security':query.symbols,'fields':query.fields})
        self._blocks=tuple(blocks);self._evidence=evidence;self._arrays=arrays
        self._fallback=fallback;self._closed=False;self._reasons=_REASONS
        self._previous_axes=_freeze({'sessions':(),'security':(),'fields':()})
        self._derived=None;self._charge=0;self._selection_ref=None
        self._lineage={'native':(query,evidence)}
    def _check(self):
        _require(not self._closed,'column selection is closed');self._owner._check()
    @property
    def snapshot_ref(self): self._check();return self._owner._snapshot_id
    @property
    def query_binding(self): self._check();return self._binding
    @property
    def axes(self): self._check();return self._axes
    @property
    def columns(self): self._check();return MappingProxyType({n:Column(self,n) for n in self._axes['fields']})
    @property
    def selected_version_indices(self):
        self._check();return MappingProxyType({n.removeprefix('index:'):ReadOnlyArray(self,n)
            for n in self._arrays if n.startswith('index:')})
    @property
    def provenance(self):
        self._check()
        names=('revision_id','revision_sequence','raw_batch_id','first_observed_at',
               'source_available_at','evidence_ref','availability_basis')
        if self._derived is None: return MappingProxyType({n:ProvenanceColumn(self,n) for n in names})
        return MappingProxyType({lineage:MappingProxyType({n:ProvenanceColumn(self,n,lineage) for n in names})
            for lineage in self._lineage})
    @property
    def source_block_refs(self): self._check();return tuple(b.ref for b in self._blocks)
    @property
    def source_binding(self):
        self._check();return _freeze({'snapshot_ref':self._owner._snapshot_id,
            'domain':self._binding['domain'],'source_block_refs':self.source_block_refs,
            'contract_ref':self._owner._domain_refs[self._binding['domain']]})
    @property
    def revision_binding(self):
        self._check();return _freeze({'indices':{n:_array_ref(a) for n,a in self._arrays.items() if n.startswith('index:')},
            'source_block_refs':self.source_block_refs})
    @property
    def evidence_binding(self):
        self._check();return _freeze({'source_block_refs':self._evidence_refs(),
            'selected_versions':self.revision_binding})
    def _evidence_refs(self):
        return tuple(dict.fromkeys(b.ref for _,evidence in self._lineage.values()
            if evidence is not None for b in evidence.blocks))
    @property
    def derivation(self): self._check();return _freeze(self._derived)
    @property
    def changed_keys(self): self._check();return ChangedKeys(self)
    @property
    def selection_ref(self): self._check();return self._selection_ref
    def _row(self,key,lineage='native'):
        coordinates=self._arrays['index:'+lineage][key]
        if coordinates[0]<0: return None
        query,evidence=self._lineage[lineage]
        domain=self._owner._reader.snapshot['domains'][query.domain]
        return _RowView(self._blocks[int(coordinates[0])],int(coordinates[1]),
            tuple(self._owner._reader._columns(domain,query)),evidence,tuple(domain['contract']['logical_key']))
    def to_batch(self):
        """Explicit detached legacy materialization; never needed to consume columns."""
        self._check()
        self._owner._statistics['legacy_materializations']+=1
        def materialize(query):
            return self._owner._reader._read(query,groups=self._owner._groups(query))
        queries=[self._query] if self._derived is None else [self._lineage[n][0] for n in ('price','factor')]
        fee=sum(self._legacy_fee(q) for q in queries)
        if self._derived is not None: fee+=4*self._legacy_fee(queries[0])
        with self._owner._reserve(fee):
            if self._derived is None: return materialize(self._query)
            from .derived import adjust_prices
            return adjust_prices(materialize(queries[0]),materialize(queries[1]),
                fields=self._axes['fields'],anchor_session=self._derived['anchor_session'],
                factor_field=self._derived['factor_field'],decision_session=self._derived['decision_session'])
    def _legacy_fee(self,query):
        # Explicit compatibility copies are the one place full records/by_key
        # are requested. Reserve their Python graph before constructing it.
        domain=self._owner._reader.snapshot['domains'][query.domain]
        metadata=max((sum(int(lengths.max(initial=0)) for name,lengths in b.lengths.items()
            if name not in ('session',*query.fields)) for b in self._blocks),default=0)
        metadata+=max((e.max_ref_bytes for _,e in self._lineage.values() if e is not None),default=0)
        cells=len(query.sessions)*len(query.symbols)
        return cells*(4096*len(query.fields)+20*metadata+1024)+8*_object_size((domain.get('coverage'),vars(query)))+16384
    def close(self):
        if self._closed: return
        self._closed=True
        token=getattr(self,'_release_token',None)
        if token is not None: token()
        self._arrays={};self._blocks=();self._evidence=None;self._lineage={};self._charge=0
        self._query=self._binding=self._headers=self._axes=self._previous_axes=self._derived=self._selection_ref=None
    def __reduce__(self): raise QueryError('column selections cannot cross a process boundary')


class ColumnSource:
    """Context owner for fixed-Snapshot unadjusted open/close and factor columns.

    limits is exactly {cache_bytes, max_working_bytes}; values are positive ints.
    cache_bytes covers the shared LRU plus still-borrowed evicted source blocks.
    max_working_bytes includes Snapshot, selections and construction/copy fees.
    Reservations reclaim the shared LRU first; evicted blocks still borrowed by
    a selection/group remain charged until their last reference is released.
    Ordinary detached DataBatch/NumPy outputs belong to their callers. Source
    marks are verified on every select and public borrow access. close/refresh,
    a changed Snapshot/partition, or another process revokes all old borrows.
    Selections consume columns through checked basic indexing or to_numpy;
    the latter returns an immutable caller-owned copy. Only to_batch requests
    full legacy records/by_key, with a construction reservation first.
    """
    def __init__(self,data,*,snapshot,limits):
        _require(isinstance(limits,Mapping) and set(limits)=={'cache_bytes','max_working_bytes'},
            'column source limits require exactly cache_bytes and max_working_bytes')
        _require(all(type(v) is int and v>0 for v in limits.values()),'column source limits must be positive integers')
        _require(snapshot not in ('current','latest') and bool(snapshot),'column source requires a fixed Snapshot ID')
        active=data._column_source() if data._column_source is not None else None
        _require(active is None or active._closed,'Data already owns an open column source')
        self._data=data;self._snapshot_id=snapshot;self._limits=dict(limits);self._pid=os.getpid()
        self._closed=False;self._reader=None;self._temporary=0;self._marks={}
        self._charges={'source':0,'selection':0,'group':0,'other':0};self._metadata_fee=0;self._schemas={}
        self._blocks=weakref.WeakValueDictionary();self._selections=weakref.WeakSet();self._groups_live=weakref.WeakSet()
        self._evidences=weakref.WeakValueDictionary()
        self._statistics={'partition_decodes':0,'record_batches':0,'source_cache_hits':0,
            'selections':0,'legacy_materializations':0,'peak_decoded_batch_bytes':0,
            'peak_source_bytes':0,'peak_working_bytes':0,'selected_cells':0}
        # There is one shared Reader/Snapshot graph, not an independent source loader.
        for key in tuple(data._readers):
            if key!=snapshot: data._readers.pop(key)
        cold=snapshot not in data._readers
        try:
            self._reader=data._reader(snapshot)
            _require(isinstance(self._reader.store,LocalStore),'column source requires LocalStore')
            self._original_cache_bytes=self._reader.cache_bytes
            self._snapshot_bytes=_object_size(self._reader.snapshot)
            self._reader._cache.clear();self._reader._cached_bytes=0
            self._reader.cache_bytes=self._limits['cache_bytes']
            self._domain_refs={name:_ref({'snapshot':snapshot,'domain':name,'contract':domain['contract'],
                'profile':domain.get('source_profile'),'evidence':self._reader.snapshot['domains'].get('public_evidence',{}).get('partitions',())})
                for name,domain in self._reader.snapshot['domains'].items() if name in (*_FIELDS,'public_evidence')}
            self._store=self._reader.store
            self._refresh_metadata()
            self._check_budget()
            data._column_source=weakref.ref(self);self._reader._column_owner=weakref.ref(self)
        except Exception:
            self.close()
            if cold: data._readers.pop(snapshot,None)
            raise
    def _supports(self,query):
        if not isinstance(query,QuerySpec) or query.domain not in _FIELDS or not set(query.fields).issubset(_FIELDS[query.domain]):
            return False
        declared=self._reader.snapshot.get('domains',{}).get(query.domain,{}).get('contract',{}).get('fields',{})
        return all(str(declared.get(n,{}).get('dtype')).lower() in _DTYPES for n in query.fields)
    def _usage(self):
        return self._charges['source']+self._charges['other']
    def _working(self):
        return self._snapshot_bytes+self._metadata_fee+sum(self._charges.values())+self._temporary
    def _refresh_metadata(self):
        # Graph sizing happens at ownership/cache lifecycle events, never in a
        # per-cell reservation or borrow. Reservations only check balances.
        if self._closed: return
        self._statistics['metadata_accounting_events']=self._statistics.get('metadata_accounting_events',0)+1
        self._metadata_fee=4*_object_size((self._store._hash_cache,self._store._profile_cache,
            self._store._raw_offsets,self._marks,self._domain_refs,self._limits,self._statistics,self._schemas,
            vars(self._blocks),vars(self._evidences),vars(self._selections),vars(self._groups_live)))+65536
    def _track(self,value,kind,size):
        self._charges[kind]+=size
        value._release_token=weakref.finalize(value,_release_charge,weakref.ref(self),kind,size)
    def _check_budget(self,extra=0):
        self._reclaim_cache(working_extra=extra)
        source=self._usage();working=self._working()+extra
        _require(source<=self._limits['cache_bytes'],'column source cache/borrow budget exceeded')
        if working>self._limits['max_working_bytes']:
            raise QueryError('column source working budget exceeded '
                f'(snapshot={self._snapshot_bytes}, metadata={self._metadata_fee}, '
                f'charges={self._charges}, temporary={self._temporary}, extra={extra}, '
                f'working={working}, limit={self._limits["max_working_bytes"]})')
        self._statistics['peak_source_bytes']=max(self._statistics['peak_source_bytes'],source)
        self._statistics['peak_working_bytes']=max(self._statistics['peak_working_bytes'],working)
    @contextmanager
    def _reserve(self,size):
        self._check_budget(size);self._temporary+=size
        try: yield
        finally: self._temporary-=size
    def _cache_removed(self,value,size):
        if not isinstance(value,(_Block,_Evidence)): self._charges['other']-=size
    def _cache_added(self,value,size):
        if not isinstance(value,(_Block,_Evidence)): self._charges['other']+=size
        self._refresh_metadata()
    def _make_room(self,size):
        if size>self._limits['cache_bytes']: return False
        return self._reclaim_cache(source_extra=size,working_extra=size)
    def _reclaim_cache(self,*,source_extra=0,working_extra=0):
        """Reclaim LRU ownership before admission, retaining every live borrow fee.

        Balances, not historical peaks, drive admission. Dropping an LRU entry
        releases a source fee only when no selection/group/evidence still pins
        it. Metadata is measured again only on an actual eviction lifecycle.
        """
        def fits():
            return (self._usage()+source_extra<=self._limits['cache_bytes'] and
                self._working()+working_extra<=self._limits['max_working_bytes'])
        while self._reader._cache and not fits():
            before=self._usage()
            _,entry=self._reader._cache.popitem(last=False)
            self._reader._cached_bytes-=entry[1];self._cache_removed(*entry);del entry
            self._statistics['cache_evictions']=self._statistics.get('cache_evictions',0)+1
            self._statistics['cache_reclaimed_bytes']=self._statistics.get('cache_reclaimed_bytes',0)+before-self._usage()
            self._refresh_metadata()
        return fits()
    def _physical_supported(self,query):
        """Column API limits never narrow ordinary Reader's physical schema."""
        import pyarrow.parquet as pq
        domain=self._reader.snapshot['domains'][query.domain]
        names=self._reader._columns(domain,query)
        for part in self._reader._parts_for_query(domain,query):
            self._store.verify_partition(part)
            key=(part['uri'],part['file_sha256'],tuple(names))
            supported=self._schemas.get(key)
            if supported is None:
                with pq.ParquetFile(self._store._path(part['uri']),pre_buffer=False,buffer_size=0) as source:
                    schema=source.schema_arrow;supported=True
                    for name in names:
                        if name not in schema.names: continue
                        dtype=schema.field(name).type
                        if pa.types.is_dictionary(dtype): dtype=dtype.value_type
                        numeric=pa.types.is_null(dtype) or pa.types.is_boolean(dtype) or pa.types.is_integer(dtype) or pa.types.is_floating(dtype)
                        scalar=numeric or pa.types.is_date(dtype) or pa.types.is_timestamp(dtype) or pa.types.is_string(dtype) or pa.types.is_large_string(dtype) or pa.types.is_binary(dtype) or pa.types.is_large_binary(dtype) or pa.types.is_fixed_size_binary(dtype)
                        if not scalar or (name in query.fields and not numeric): supported=False;break
                self._schemas[key]=supported;self._refresh_metadata();self._check_budget()
            if not supported: return False
        return True
    def _check(self):
        _require(not self._closed,'column source is closed')
        if os.getpid()!=self._pid:
            self.close();raise QueryError('column source cannot cross a process boundary')
        data=self._data
        if data is None or data._readers.get(self._snapshot_id) is not self._reader or self._reader.store is not self._store:
            self.close();raise QueryError('column source Reader was refreshed or replaced')
        try:
            if self._reader._snapshot_mark is not None:
                _require(_mark(self._reader._snapshot_path)==self._reader._snapshot_mark,'column source Snapshot changed')
            for path,mark in self._marks.items():
                _require(_mark(path)==mark,'column source partition changed')
        except (OSError,QueryError):
            self.close();raise QueryError('column source Snapshot or partition changed')
        self._check_budget()
    @property
    def statistics(self): self._check();return MappingProxyType(dict(self._statistics))
    def _block(self,part,domain,names):
        key='columns:'+_ref([self._snapshot_id,domain,part,names,self._domain_refs[domain]])
        entry=self._reader._cache.get(key)
        existing=entry[0] if entry is not None else self._blocks.get(key)
        if existing is not None:
            self._store.verify_partition(part)
            if entry is not None: self._reader._cache.move_to_end(key)
            self._statistics['source_cache_hits']+=1;return existing
        path=self._store._path(part['uri']);before=_mark(path)
        with self._reserve(135168): self._store.verify_partition(part)
        _require(_mark(path)==before,'column source partition changed during verification')
        pending=[];retained=0
        try:
            batches=self._store._native_partition_batches(part,columns=names)
            try:
                for _,table in batches:
                    size=_table_charge(table)
                    self._statistics['peak_decoded_batch_bytes']=max(self._statistics['peak_decoded_batch_bytes'],table.get_total_buffer_size())
                    self._check_budget(size);self._temporary+=size;retained+=size
                    pending.append(table);self._statistics['record_batches']+=1
                table=pa.concat_tables(pending)
                # Admission knows decoded logical buffers/row count. Sorting and
                # compact key dictionaries get a working reservation beforehand.
                with self._reserve(4*table.get_total_buffer_size()+256*table.num_rows+8192):
                    block=_Block(self,key,part,table,domain)
                _require(_mark(path)==before,'column source partition changed during decoding')
                # The block now owns the same Arrow buffers. Release batch
                # wrappers/fees before charging the complete block once.
                pending.clear();table=None;self._temporary-=retained;retained=0
                _require(self._make_room(block.charge+_ENTRY_BYTES),'column source cache/borrow budget exceeded')
                self._check_budget(block.charge+_ENTRY_BYTES)
                _require(self._reader._put_entry(key,block,block.charge),'column source cache admission failed')
                self._blocks[key]=block;self._marks[path]=before
                self._track(block,'source',block.charge+_ENTRY_BYTES);self._refresh_metadata();self._check_budget()
                self._statistics['partition_decodes']+=1
                return block
            finally:
                batches.close()
        finally:
            pending=[];self._temporary-=retained
    def _evidence(self,domain):
        manifest=self._reader.snapshot['domains'].get('public_evidence')
        if not manifest or not manifest.get('partitions'): return None
        key='column-evidence:'+_ref([self._snapshot_id,domain,manifest['partitions']])
        entry=self._reader._cache.get(key)
        existing=entry[0] if entry is not None else self._evidences.get(key)
        if existing is not None:
            if entry is not None: self._reader._cache.move_to_end(key)
            return existing
        blocks=[self._block(p,'public_evidence',list(_EVIDENCE)) for p in manifest['partitions']]
        logical=0;max_ref_bytes=0
        for block in blocks:
            for name in ('target_domain','target_key','target_revision'):
                logical+=int(block.lengths[name].sum()) if name in block.lengths else 0
            max_ref_bytes=max(max_ref_bytes,sum(int(block.lengths[n].max(initial=0)) if n in block.lengths else 0
                for n in ('raw_batch_id','document_sha256','public_at'))+8)
        with self._reserve(20*logical+sum(2048*b.table.num_rows for b in blocks)+4096):
            evidence=_Evidence(blocks,domain,max_ref_bytes)
        _require(self._make_room(evidence.charge+_ENTRY_BYTES),'column evidence cache/borrow budget exceeded')
        _require(self._reader._put_entry(key,evidence,evidence.charge),'column evidence cache admission failed')
        self._evidences[key]=evidence
        self._track(evidence,'source',evidence.charge+_ENTRY_BYTES);self._refresh_metadata();self._check_budget()
        return evidence
    def _groups(self,query):
        self._check()
        domain,_,_=self._reader._validate(query)
        _require(self._supports(query),'column source supports numeric market_daily open/close and adjustment_factors factor only')
        _require(self._physical_supported(query),'column source requires flat scalar numeric fact columns')
        # Projection stays narrow: an unrequested field's schema or values must
        # not make a formerly valid ordinary query fail.
        names=list(dict.fromkeys(['security_id','session',*query.fields,
            'revision_id','revision_sequence','first_observed_at','raw_batch_id','source_available_at','evidence_ref']))
        blocks=[self._block(part,query.domain,names) for part in self._reader._parts_for_query(domain,query)]
        groups=_Groups(self,query,blocks,self._evidence(query.domain));self._groups_live.add(groups)
        self._track(groups,'group',4*_object_size((vars(query),groups.names,groups.logical_key,groups.by_session))+groups.days.nbytes+4352)
        self._refresh_metadata();self._check_budget()
        return groups
    def select(self,*,query,previous=None):
        """Select afresh at every per-session cutoff; previous supplies only diff axes."""
        self._check()
        if previous is not None:
            _require(isinstance(previous,ColumnSelection) and previous._owner is self,'previous selection must belong to this owner')
            previous._check()
        domain,declared,cutoffs=self._reader._validate(query)
        _require(self._supports(query),'column source supports numeric market_daily open/close and adjustment_factors factor only')
        groups=self._groups(query);shape=len(query.sessions),len(query.symbols);cells=shape[0]*shape[1]
        headers={n:{'dtype':declared[n].get('dtype'),'unit':declared[n].get('unit'),
            'basis':declared[n].get('basis'),'price_basis':query.price_basis} for n in query.fields}
        dtype={n:np.dtype(_DTYPES[str(declared[n]['dtype']).lower()]) for n in query.fields}
        estimate=cells*(64+sum(d.itemsize+2 for d in dtype.values()))+8*_object_size(_binding(query,cutoffs))+8192
        with self._reserve(3*estimate):
            arrays={'index:native':np.full((*shape,2),-1,dtype='<i8'),
                'available':np.full(shape,_NAT,dtype='<i8').view('datetime64[ns]'),
                'basis':np.zeros(shape,dtype='u1')}
            for n in query.fields:
                arrays['value:'+n]=np.zeros(shape,dtype=dtype[n]);arrays['valid:'+n]=np.zeros(shape,dtype='?')
                arrays['reason:'+n]=np.zeros(shape,dtype='u1')
            fallback=self._select_arrays(query,domain,cutoffs,groups,arrays,dtype)
            selection=ColumnSelection(self,query,_binding(query,cutoffs),headers,groups.blocks,groups.evidence,arrays,fallback)
            self._finish(selection,previous)
        self._statistics['selections']+=1;self._statistics['selected_cells']+=cells
        return selection
    def _select_arrays(self,query,domain,cutoffs,groups,arrays,dtypes):
        shape=len(query.sessions),len(query.symbols);count=shape[0]*shape[1]
        index=arrays['index:native'].reshape(-1,2);available=arrays['available'].view('<i8').ravel();basis_out=arrays['basis'].ravel()
        policies=np.asarray([1 if _policy_for(query,s)=='best_effort_vendor_v1' else 2 if _policy_for(query,s)=='market_pit_safe_v1' else 3 for s in query.sessions],dtype='u1')
        clocks=np.asarray([min(max(_ns(cutoffs[s],bounded=False),_NAT),np.iinfo(np.int64).max) for s in query.sessions],dtype='<i8')
        profile=domain.get('source_profile') or {};order=profile.get('revision_order')
        release=np.asarray([_ns(_release_time(profile,s)) if policies[i]==1 else 0 for i,s in enumerate(query.sessions)],dtype='<i8')
        matched=np.zeros(count,dtype='?');sparse=np.zeros(count,dtype='?');population=np.zeros(count,dtype='<i8')
        bad_rank=np.zeros(count,dtype='?');fallbacks=np.zeros(count,dtype='<i8');ranks=np.full(count,_NAT,dtype='<i8')
        if groups.evidence:
            day_map={s:i for i,s in enumerate(query.sessions)};symbol_map={s:i for i,s in enumerate(query.symbols)}
            for symbol,day in groups.evidence.target_cells:
                if symbol in symbol_map and day in day_map: sparse[day_map[day]*shape[1]+symbol_map[symbol]]=True
        plans=[];plan_fee=0
        try:
            for slot,block in enumerate(groups.blocks):
                cells,rows,fee=block.candidates(groups.days,query.symbols)
                try:
                    self._statistics['batch_block_probes']=self._statistics.get('batch_block_probes',0)+1
                    if not len(cells): plans.append(np.empty(0,dtype='<i8'));continue
                    destinations,local=np.unique(cells,return_inverse=True);days=cells//shape[1]
                    matched[destinations]=True
                    observed,observed_bad=block.clocks['first_observed_at'];source,source_bad=block.clocks['source_available_at']
                    selected,usable,basis,exceptional=_select_daily_ordinals(cells=local,policies=policies[days],
                        cutoffs=clocks[days],releases=release[days],observed=observed[rows],observed_bad=observed_bad[rows],
                        source=source[rows],source_bad=source_bad[rows],source_bound=block.source_bound[rows],
                        sequence=block.sequence[rows],sequence_bad=block.sequence_bad[rows],count=len(destinations),
                        revision_order=order,sparse=sparse[destinations])
                    sparse[destinations]|=exceptional
                    visible=usable<=clocks[days]
                    np.add.at(population,cells[visible],1)
                    invalid=observed_bad[rows] if order=='terminal_observation_v1' else block.sequence_bad[rows]
                    bad_rank[cells[visible&invalid]]=True
                    if query.pit_policy=='market_pit_safe_v1': np.add.at(fallbacks,cells[~block.source_bound[rows]],1)
                    valid=selected>=0;dest=destinations[valid];position=selected[valid];physical=rows[position]
                    rank=observed[physical] if order=='terminal_observation_v1' else block.sequence[physical]
                    existing=index[dest,0]>=0;equal=existing&(rank==ranks[dest]);sparse[dest[equal]]=True
                    replace=(~existing)|(rank>ranks[dest]);dest=dest[replace];position=position[replace];physical=physical[replace]
                    index[dest,0]=slot;index[dest,1]=physical;ranks[dest]=rank[replace]
                    available[dest]=usable[position];basis_out[dest]=basis[position]
                    self._temporary+=destinations.nbytes+256;plan_fee+=destinations.nbytes+256;plans.append(destinations)
                finally: self._temporary-=fee
            sparse|=(population>1)&bad_rank
            if order=='announcement_day_then_terminal_v1': sparse|=population>1
            slots={id(b):i for i,b in enumerate(groups.blocks)};scalar_fallback=0;releases={}
            for cell in np.flatnonzero(sparse&matched):
                i,j=divmod(int(cell),shape[1]);day=query.sessions[i];symbol=query.symbols[j]
                rows=groups.get((symbol,day),[])
                selected,usable,basis,n=_select_daily_revision(rows,policy=_policy_for(query,day),profile=profile,
                    session=day,cutoff=cutoffs[day],key=f'{symbol}/{day}',count_fallback=query.pit_policy=='market_pit_safe_v1',vendor_releases=releases)
                scalar_fallback+=n;index[cell]=(-1,-1);available[cell]=_NAT;basis_out[cell]=0
                if selected is not None:
                    index[cell]=(slots[id(selected.block)],selected.ordinal);available[cell]=_ns(usable);basis_out[cell]=_BASES.index(basis)
                self._statistics['scalar_groups']=self._statistics.get('scalar_groups',0)+1
                rows=();selected=None
            for name in query.fields:
                values=arrays['value:'+name].ravel();valid=arrays['valid:'+name].ravel();reason=arrays['reason:'+name].ravel()
                reason[:]=np.where(matched,2,1)
                for slot,(block,destinations) in enumerate(zip(groups.blocks,plans)):
                    dest=destinations[index[destinations,0]==slot]
                    if not len(dest): continue
                    rows=index[dest,1];raw,present=block.values[name];gathered=raw[rows];present=present[rows]
                    # Source null defines the reason; nullable conversion may
                    # independently make a present NaN invalid.
                    reason[dest]=np.where(present,0,3)
                    target=dtypes[name]
                    if (target.kind in 'iu' and (gathered.dtype.kind not in 'iu' or gathered.dtype.itemsize>target.itemsize or gathered.dtype.kind!=target.kind)) or (target.kind=='b' and gathered.dtype.kind!='b'):
                        import pandas as pd
                        dtype=('UInt' if target.kind=='u' else 'Int')+str(target.itemsize*8) if target.kind!='b' else 'boolean'
                        converted=pd.array(gathered,dtype=dtype);present&=~np.asarray(converted.isna());gathered=converted.to_numpy(dtype=target,na_value=0)
                    values[dest]=gathered;valid[dest]=present
        finally: self._temporary-=plan_fee
        return int(fallbacks[~sparse].sum())+scalar_fallback
    def adjust(self,prices,factors,*,fields,anchor_session,factor_field='factor',decision_session=None,previous=None):
        """Apply Data's common-anchor kernel to live, already selected columns.

        Same owner, Snapshot, policies, purpose and one decision cutoff are
        required. Factor sessions are exactly price sessions plus anchor. Missing
        and invalid inputs retain Data's reason precedence. No DataBatch, row
        provenance graph, I/O or revision selection is created by this method.
        Output pins source blocks and compact lineage, independently of input
        selection lifetimes; available_at is the latest of its three inputs.
        """
        from .derived import PRICE_ADJUSTMENT_VERSION,_query_context,_validate_adjustment,_adjust_columns
        self._check()
        for value,label in ((prices,'prices'),(factors,'factors'),(previous,'previous')):
            if value is None and label=='previous': continue
            _require(isinstance(value,ColumnSelection) and value._owner is self,f'{label} selection must belong to this owner')
            value._check()
        _require(prices._derived is None and factors._derived is None,'adjust requires unadjusted selections')
        _require(prices._query.domain=='market_daily' and factors._query.domain=='adjustment_factors',
            'adjust requires market_daily prices and adjustment_factors factors')
        price_input=_query_context(prices._binding,'prices');factor_input=_query_context(factors._binding,'factors')
        anchor,decision=_validate_adjustment(price_input,factor_input,
            price_snapshot=self._snapshot_id,factor_snapshot=self._snapshot_id,
            price_columns=prices._axes['fields'],factor_columns=factors._axes['fields'],
            fields=fields,anchor_session=anchor_session,factor_field=factor_field,decision_session=decision_session)
        _require(set(fields).issubset(_FIELDS['market_daily']) and factor_field=='factor','adjust supports open/close and factor only')
        cutoff_ns=_ns(price_input[3],bounded=False)
        for value,label in ((prices,'prices'),(factors,'factors')):
            clocks=value._arrays['available'].view('<i8')
            if cutoff_ns<=_NAT: later=np.any(clocks!=_NAT)
            elif cutoff_ns>=np.iinfo(np.int64).max: later=False
            else: later=np.any((clocks!=_NAT)&(clocks>cutoff_ns))
            _require(not later,f'{label} provenance is later than the decision cutoff')
        query=replace(prices._query,fields=tuple(fields),price_basis='common_anchor_adjusted_v1',adjustment_anchor=anchor)
        binding=_plain(prices._binding);binding.update(fields=list(fields),price_basis=query.price_basis,adjustment_anchor=anchor)
        headers={n:{'dtype':'float64','unit':prices._headers[n]['unit'],
            'basis':'common_anchor_adjusted_v1','price_basis':query.price_basis,'recipe_version':PRICE_ADJUSTMENT_VERSION} for n in fields}
        shape=len(query.sessions),len(query.symbols);cells=shape[0]*shape[1]
        with self._reserve(3*(cells*(120+10*len(fields))+8*_object_size(binding)+16384)):
            blocks=tuple(dict.fromkeys((*prices._blocks,*factors._blocks)))
            slots={id(b):i for i,b in enumerate(blocks)}
            factor_days={s:i for i,s in enumerate(factors._axes['sessions'])}
            factor_symbols={s:i for i,s in enumerate(factors._axes['security'])}
            arrays={n:np.zeros(shape,dtype='<f8' if n.startswith('value:') else '?' if n.startswith('valid:') else 'u1')
                for field in fields for n in ('value:'+field,'valid:'+field,'reason:'+field)}
            for lineage in ('price','factor','anchor_factor'):
                arrays['index:'+lineage]=np.full((*shape,2),-1,dtype='<i8')
                arrays['available:'+lineage]=np.full(shape,_NAT,dtype='<i8').view('datetime64[ns]')
                arrays['basis:'+lineage]=np.zeros(shape,dtype='u1')
            arrays['available']=np.full(shape,_NAT,dtype='<i8').view('datetime64[ns]')
            arrays['basis']=np.zeros(shape,dtype='u1')
            factor_rows=np.fromiter((factor_days[s] for s in query.sessions),dtype='<i8')
            factor_cols=np.fromiter((factor_symbols[s] for s in query.symbols),dtype='<i8')
            factor_key=np.ix_(factor_rows,factor_cols);anchor_key=np.ix_(np.asarray([factor_days[anchor]]),factor_cols)
            for lineage,value,key in (('price',prices,None),('factor',factors,factor_key),('anchor_factor',factors,anchor_key)):
                coordinate=value._arrays['index:native'] if key is None else value._arrays['index:native'][key]
                coordinate=np.broadcast_to(coordinate,(*shape,2)).copy();valid=coordinate[...,0]>=0
                remap=np.asarray([slots[id(b)] for b in value._blocks],dtype='<i8')
                if np.any(valid): coordinate[...,0][valid]=remap[coordinate[...,0][valid]]
                arrays['index:'+lineage]=coordinate
                clock=value._arrays['available'] if key is None else value._arrays['available'][key]
                basis=value._arrays['basis'] if key is None else value._arrays['basis'][key]
                arrays['available:'+lineage]=np.broadcast_to(clock,shape).copy()
                arrays['basis:'+lineage]=np.broadcast_to(basis,shape).copy()
            arrays['available']=np.maximum.reduce([arrays['available:'+n].view('<i8') for n in ('price','factor','anchor_factor')]).view('datetime64[ns]')
            factor_value=factors._arrays['value:'+factor_field][factor_key];factor_valid=factors._arrays['valid:'+factor_field][factor_key]
            anchor_value=factors._arrays['value:'+factor_field][anchor_key];anchor_valid=factors._arrays['valid:'+factor_field][anchor_key]
            for field in fields:
                values,valid,reasons=_adjust_columns(prices._arrays['value:'+field],prices._arrays['valid:'+field],prices._arrays['reason:'+field],
                    factor_value,factor_valid,anchor_value,anchor_valid)
                arrays['value:'+field]=values;arrays['valid:'+field]=valid;arrays['reason:'+field]=reasons
            selection=ColumnSelection(self,query,binding,headers,blocks,None,arrays,0)
            selection._reasons=_ADJUST_REASONS
            selection._lineage={'price':(prices._query,prices._evidence),
                'factor':(factors._query,factors._evidence),'anchor_factor':(factors._query,factors._evidence)}
            selection._derived={'recipe_version':PRICE_ADJUSTMENT_VERSION,'formula':'price_t * factor_t / factor_anchor',
                'anchor_session':anchor,'decision_session':decision,'decision_cutoff':price_input[3].isoformat(),
                'factor_field':factor_field,'price_selection_ref':prices.selection_ref,'factor_selection_ref':factors.selection_ref}
            self._finish(selection,previous)
        return selection
    def _finish(self,selection,previous):
        shape=len(selection._axes['sessions']),len(selection._axes['security'])
        added=np.ones(shape,dtype='?');updated=np.zeros(shape,dtype='?')
        removed=np.empty((0,2),dtype='<i8')
        if previous is not None:
            selection._previous_axes=previous._axes
            old_days={s:i for i,s in enumerate(previous._axes['sessions'])}
            old_symbols={s:i for i,s in enumerate(previous._axes['security'])}
            current_days=set(selection._axes['sessions']);current_symbols=set(selection._axes['security'])
            absent_days=np.fromiter((s not in current_days for s in previous._axes['sessions']),dtype='?')
            absent_symbols=np.fromiter((s not in current_symbols for s in previous._axes['security']),dtype='?')
            removed=np.argwhere(absent_days[:,None]|absent_symbols[None,:]).astype('<i8')
            new_days=np.asarray([i for i,s in enumerate(selection._axes['sessions']) if s in old_days],dtype='<i8')
            new_symbols=np.asarray([i for i,s in enumerate(selection._axes['security']) if s in old_symbols],dtype='<i8')
            old_i=np.asarray([old_days[selection._axes['sessions'][i]] for i in new_days],dtype='<i8')
            old_j=np.asarray([old_symbols[selection._axes['security'][j]] for j in new_symbols],dtype='<i8')
            current=np.ix_(new_days,new_symbols);old=np.ix_(old_i,old_j)
            added[current]=False
            static=('domain','fields','price_basis','adjustment_anchor','universe_id')
            changed=np.full((len(new_days),len(new_symbols)),
                any(selection._binding[n]!=previous._binding[n] for n in static) or set(selection._lineage)!=set(previous._lineage),dtype='?')
            remap={ref:i for i,ref in enumerate(previous.source_block_refs)}
            slots=np.asarray([remap.get(b.ref,-2) for b in selection._blocks],dtype='<i8')
            for name,array in selection._arrays.items():
                if name.startswith('change:'): continue
                previous_array=previous._arrays.get(name)
                if previous_array is None or array.dtype!=previous_array.dtype:
                    changed[:]=True;continue
                a=array[current];b=previous_array[old]
                if name.startswith('index:'):
                    valid=a[...,0]>=0
                    if np.any(valid): a[...,0][valid]=slots[a[...,0][valid]]
                    changed|=np.any(a!=b,axis=-1)
                else:
                    # Compare primitive bytes, retaining NaN payload and -0.
                    changed|=np.any(a.view('u1').reshape((*changed.shape,a.dtype.itemsize))!=b.view('u1').reshape((*changed.shape,b.dtype.itemsize)),axis=-1)
            # Full query/cutoff identity still binds selection_ref below. Changes
            # here describe actual fact/lineage/clock/basis dependencies only.
            updated[current]=changed
        selection._arrays.update({'change:added':np.argwhere(added).astype('<i8'),
            'change:updated':np.argwhere(updated).astype('<i8'),'change:removed':removed})
        selection._arrays={n:_readonly(a) for n,a in selection._arrays.items()}
        selection._selection_ref=_ref({'contract_version':VERSION,'snapshot_ref':self._snapshot_id,
            'query_binding':_plain(selection._binding),'axes':_plain(selection._axes),'field_meta':_plain(selection._headers),
            'source_block_refs':[b.ref for b in selection._blocks],
            'evidence_block_refs':list(selection._evidence_refs()),
            'arrays':{n:_array_ref(a) for n,a in selection._arrays.items() if not n.startswith('change:')},
            'derivation':selection._derived})
        selection._charge=sum(a.nbytes+256 for a in selection._arrays.values())+4*_object_size((
            _plain(selection._binding),_plain(selection._headers),_plain(selection._axes),selection._selection_ref,
            selection._derived,selection._reasons,_plain(selection._previous_axes),
            tuple(vars(q) for q,_ in selection._lineage.values()),tuple(b.ref for b in selection._blocks)))+4096
        self._selections.add(selection);self._track(selection,'selection',selection._charge)
        self._refresh_metadata()
        try: self._check_budget()
        except Exception: selection.close();raise
    def close(self):
        if self._closed: return
        self._closed=True
        for selection in tuple(self._selections): selection.close()
        for groups in tuple(self._groups_live):
            groups.blocks=();groups.evidence=None;groups.by_session={};groups.query=None;groups.days=None
            token=getattr(groups,'_release_token',None)
            if token is not None: token()
        reader=self._reader
        if reader is not None:
            reader._column_owner=None
            reader._cache.clear();reader._cached_bytes=0
            reader.cache_bytes=getattr(self,'_original_cache_bytes',reader.cache_bytes)
        self._reader=None;self._data=None;self._blocks.clear();self._evidences.clear();self._groups_live.clear()
        self._marks.clear();self._store=None
        self._schemas.clear()
    def __enter__(self): self._check();return self
    def __exit__(self,*_): self.close()
    def __reduce__(self): raise QueryError('column sources cannot cross a process boundary')
    def __del__(self):
        try: self.close()
        except Exception: pass
