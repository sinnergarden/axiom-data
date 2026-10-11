"""Explicit immutable JSON parts of native DataBatches, not a PIT bypass.

Native identity is the existing sorted-key, compact, UTF-8 canonical JSON
algorithm used by Engine stock_evidence.native_ref and Research saved artifacts.
Physical file hashes are distinct. Snapshot loading remains the normal loader.
Working charges cover admitted retention and Python conversion reservations.
Parquet decoder pages, dictionaries and a single value can allocate beyond this
budget before batch admission; external process RSS guards remain necessary.
"""
from contextlib import closing, contextmanager
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from pathlib import Path, PurePosixPath
import json
import re
import shutil
import tempfile

import pandas as pd

from .protocols import DataBatch, EventQuery, QueryError, QuerySpec, _json_safe
from .storage import LocalStore, _json_bytes, _json_chunks, _coerce
from .reader import _object_size, _policy_limitations
from .portable import _publish_new_directory
from ._native_canonical_json import validate_canonical_chunks

VERSION = 'data_native_view_v1'
EXPORTER = 'native_json_parts_v1'
LIMIT_FIELDS = {'max_part_bytes', 'max_working_bytes', 'max_saved_bytes', 'max_rows_per_block'}
NULLABLE = {'int':'Int64','integer':'Int64','int64':'Int64','int32':'Int32',
    'bool':'boolean','boolean':'boolean','float':'Float64','double':'Float64',
    'float64':'Float64','float32':'Float32','string':'string','str':'string','utf8':'string'}
_JSON_ESCAPE=re.compile(r'[\x00-\x1f"\\]')


def _require(ok, message):
    if not ok: raise QueryError(message)


def _limits(value):
    _require(type(value) is dict and set(value) == LIMIT_FIELDS, 'exact native view limits required')
    _require(all(type(v) is int and v > 0 for v in value.values()), 'positive integer native view limits required')
    return dict(value)


def _ref(raw):
    return 'sha256:' + sha256(raw).hexdigest()


def _mark(path):
    s=path.stat()
    return (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)


def _path(root, uri):
    _require(type(uri) is str, 'native file path must be a string')
    p=PurePosixPath(uri)
    _require(type(uri) is str and not p.is_absolute() and '..' not in p.parts and str(p)==uri,
             'unsafe native view file path')
    current=root
    for component in p.parts:
        current=current/component
        _require(not current.is_symlink(), 'native view symlink refused')
    return current


def _pairs(items):
    out={}
    for k,v in items:
        _require(k not in out, 'duplicate native JSON key')
        out[k]=v
    return out


def _reject(_):
    raise QueryError('nonfinite native JSON constant')


def _encoding_bound(value):
    """Count repeated JSON values, without constructing strings or a copy."""
    if isinstance(value,dict):
        return 2+sum(_encoding_bound(k)+_encoding_bound(v)+2 for k,v in value.items())
    if isinstance(value,(list,tuple)): return 2+sum(_encoding_bound(v)+1 for v in value)
    if isinstance(value,str):
        if value.isascii():
            return 2+len(value)+sum(5 if ord(m[0])<32 else 1 for m in _JSON_ESCAPE.finditer(value))
        return 2+4*len(value)+sum(2 for m in _JSON_ESCAPE.finditer(value) if ord(m[0])<32)
    if value is None or isinstance(value,bool): return 5
    if isinstance(value,int): return value.bit_length()*30103//100000+3
    if isinstance(value,float): return 32
    return 128


def _largest_scalar(value):
    if isinstance(value,dict):
        return max((max(_largest_scalar(k),_largest_scalar(v)) for k,v in value.items()),default=128)
    if isinstance(value,(list,tuple)): return max(map(_largest_scalar,value),default=128)
    return _encoding_bound(value)


class _ProjectionStore:
    """Export-local Arrow projections charged to the existing Reader LRU."""
    def __init__(self, store, reader, writer):
        self.base,self.reader,self.writer=store,reader,writer
        self.statistics=writer.statistics
        self.seen={};self.track_positions=False;self.last_positions=();self.event_keys=set()
        self.evidence={}

    def __getattr__(self,name):
        return getattr(self.base,name)

    @staticmethod
    def _arrow_charge(table):
        schema=table.schema
        metadata=[schema.metadata or {},*[field.metadata or {} for field in schema]]
        return (table.get_total_buffer_size()+4096+512*table.num_columns+
            256*sum(column.num_chunks for column in table.columns)+
            sum(4*len(field.name) for field in schema)+
            sum(4*(len(k)+len(v))+256 for values in metadata for k,v in values.items()))

    def _observe_decoder(self,kind,size):
        key=('peak_parquet_encoded_row_group_bytes' if kind=='encoded_pages'
             else 'peak_decoded_arrow_batch_buffer_bytes')
        self.statistics[key]=max(self.statistics.get(key,0),size)

    def _projection_batches(self,part,names):
        """Admit decoded batches, then optionally retain a bounded projection.

        The decoder's page/dictionary/single-value allocation precedes admission
        and is not subject to max_working_bytes. No page-size estimate is used
        as an expanded Arrow bound. A complete dictionary-chunked projection
        can share the existing LRU; insufficient cache is a single-pass path.
        """
        import pyarrow as pa
        key='native-arrow-batches:'+sha256(_json_bytes([self.reader.snapshot_id,part,names])).hexdigest()
        self.verify_partition(part)
        entry=self.reader._cache.get(key)
        if entry is not None:
            self.reader._cache.move_to_end(key)
            self.statistics['parquet_projection_hits']+=1
            with self.writer.scope('arrow-borrow',entry[1]):
                table=entry[0];offset=0
                if table.num_rows==0: yield 0,table
                else:
                    with table.to_reader(max_chunksize=4096) as batches:
                        for batch in batches:
                            current=pa.Table.from_batches([batch])
                            yield offset,current
                            offset+=batch.num_rows
                            del batch,current
            return
        _require(not self.track_positions or key not in self.event_keys,
            'native event projections exceed Reader cache; increase cache_bytes or source_symbol_block')
        if self.track_positions:
            if key not in self.event_keys: self.writer.add('projection-descriptors',4*_object_size(key)+1024)
            self.event_keys.add(key)
        self.statistics['parquet_decodes']+=1
        if isinstance(self.base,LocalStore):
            membership=self.reader.snapshot.get('domains',{}).get('universe_membership',{})
            dependencies=any(p['uri']==part['uri'] and p['file_sha256']==part['file_sha256']
                for p in membership.get('partitions',()))
            batches=self.base._native_partition_batches(part,columns=names,_observe=self._observe_decoder,
                _membership_dependencies=dependencies)
        else:
            # Small in-memory test Stores already expose their source values.
            # This is not a proof about an opaque third-party decoder.
            rows=getattr(self.base,'rows',{}).get(part['uri'])
            _require(rows is not None,'native Store requires a partition batch iterator')
            logical=sum(_encoding_bound(row.get(n)) for row in rows for n in (row if names is None else names))
            self.writer.check(4*logical+4096+512*len(rows))
            whole=self.base.read_partition(part,columns=names)
            batches=((offset,whole.slice(offset,64)) for offset in range(0,max(1,whole.num_rows),64))
        pending=[];buffers=set();dictionaries={};charge=4096;caching=bool(self.reader.cache_bytes)
        with self.writer.scope('projection-build'):
            try:
                for offset,table in batches:
                    self._observe_decoder('arrow',table.get_total_buffer_size())
                    self.statistics['parquet_record_batches_decoded']=self.statistics.get('parquet_record_batches_decoded',0)+1
                    actual=self._arrow_charge(table)
                    # The batch already exists. Reject before retaining it,
                    # publishing it to LRU or expanding it into Python rows.
                    _require(actual<=self.writer.remaining(),'native decoded Arrow batch exceeds working budget before retention')
                    with self.writer.scope('decoded-arrow-batch',actual):
                        if caching:
                            # Arrow can emit identical dictionary values in
                            # separate buffers for successive batches. Share
                            # only equal dictionaries; their index order stays
                            # unchanged and the pending projection owns them.
                            for number,column in enumerate(table.columns):
                                if pa.types.is_dictionary(column.type):
                                    array=column.chunk(0);previous=dictionaries.get(number)
                                    if previous is not None and array.dictionary.equals(previous):
                                        shared=pa.DictionaryArray.from_arrays(array.indices,previous,ordered=array.type.ordered)
                                        table=table.set_column(number,table.schema.field(number),shared)
                            fresh=set()
                            for column in table.columns:
                                for array in column.chunks:
                                    arrays=[array.dictionary,array.indices] if pa.types.is_dictionary(array.type) else [array]
                                    for value in arrays:
                                        fresh.update((buf.address,buf.size) for buf in value.buffers() if buf is not None)
                            fresh-=buffers
                            addition=sum(size for _,size in fresh)+512*(table.num_columns+1)
                            if not pending: addition+=actual-table.get_total_buffer_size()
                            candidate=charge+addition
                            increment=candidate-self.writer.retained.get('projection-build',0)
                            if candidate>self.reader.cache_bytes or increment>self.writer.remaining():
                                pending=[];buffers=set();dictionaries={};self.writer.reserve('projection-build',0);caching=False
                            else:
                                self.writer.reserve('projection-build',candidate)
                                buffers.update(fresh);pending.append(table);charge=candidate
                                dictionaries={number:column.chunk(0).dictionary for number,column in enumerate(table.columns)
                                    if pa.types.is_dictionary(column.type)}
                            column=array=previous=shared=arrays=value=None;fresh=set()
                        yield offset,table
                    del table
                if caching and pending:
                    publication=4096+256*len(pending)*pending[0].num_columns
                    if publication<=self.writer.remaining():
                        with self.writer.scope('projection-publication',publication):
                            table=pa.concat_tables(pending)
                            actual=self._arrow_charge(table)
                            # Source buffers remain charged by projection-build
                            # until this transfer to the existing LRU finishes.
                            if actual<=self.reader.cache_bytes and actual<=self.writer.remaining():
                                self.reader._put_entry(key,table,actual)
                            del table
                pending=[];buffers=set();dictionaries={}
            finally:
                close=getattr(batches,'close',None)
                if close is not None: close()

    def _filtered_batch(self,table,offset,*,symbols=None,sessions=None):
        import pyarrow as pa
        import pyarrow.compute as pc
        mask=None
        if symbols is not None:
            _require('security_id' in table.column_names,'partition lacks security_id')
            dtype=table.schema.field('security_id').type
            dtype=dtype.value_type if pa.types.is_dictionary(dtype) else dtype
            mask=pc.is_in(table['security_id'],value_set=pa.array(list(symbols),type=dtype))
        if sessions is not None:
            _require('session' in table.column_names,'partition lacks session')
            dtype=table.schema.field('session').type
            dtype=dtype.value_type if pa.types.is_dictionary(dtype) else dtype
            current=pc.is_in(table['session'],value_set=pa.array([_coerce(s,dtype) for s in sessions],type=dtype))
            mask=current if mask is None else pc.and_(mask,current)
        physical=range(offset,offset+table.num_rows) if mask is None else pc.indices_nonzero(mask)
        return (table if mask is None else table.filter(mask)),physical

    def _filter_charge(self,table,symbols,sessions):
        values=sum(4*_encoding_bound(value)+128 for selected in (symbols,sessions)
            if selected is not None for value in selected)
        return 2*self._arrow_charge(table)+4096+16*table.num_rows+values

    def _python_bound(self,table):
        """Bound supported Python row objects from decoded logical value sizes.

        Dictionary payload is counted once for EACH referenced output value.
        Membership dependency lists count the sliced child payload, each child
        object/pointer and each parent list container before row expansion.
        Fixed per-cell/dict overhead and UTF-8-to-Unicode expansion are explicit;
        this reservation is independent of Parquet decoder estimates.
        """
        import pyarrow as pa
        import pyarrow.compute as pc
        logical=0;list_elements=0;list_containers=0
        for field,column in zip(table.schema,table.columns):
            for array in column.chunks:
                dictionary=pa.types.is_dictionary(array.type)
                values=array.dictionary if dictionary else array
                dtype=values.type
                if pa.types.is_list(dtype):
                    _require(field.name=='dependency_raw_batch_ids' and pa.types.is_string(dtype.value_type),
                        'native Python conversion requires the membership list<string> dependency column')
                    # values spans the parent buffer, even on an offset slice.
                    # Count just this slice's child span, conservatively also
                    # including children hidden by a nullable parent bitmap.
                    begin=array.offsets[0].as_py();end=array.offsets[-1].as_py()
                    children=array.values.slice(begin,end-begin)
                    list_elements+=len(children);list_containers+=len(array)
                    with self.writer.scope('conversion-lengths',4096+16*len(children)):
                        lengths=pc.binary_length(children)
                        logical+=pc.sum(lengths).as_py() or 0
                        del lengths
                    del children
                elif pa.types.is_string(dtype) or pa.types.is_large_string(dtype) or pa.types.is_binary(dtype) or pa.types.is_large_binary(dtype):
                    workspace=4096+16*(len(values)+len(array))
                    with self.writer.scope('conversion-lengths',workspace):
                        lengths=pc.binary_length(values)
                        selected=pc.take(lengths,array.indices) if dictionary else lengths
                        logical+=pc.sum(selected).as_py() or 0
                        del lengths,selected
                elif pa.types.is_fixed_size_binary(dtype): logical+=dtype.byte_width*len(array)
                elif not pa.types.is_null(dtype): logical+=(dtype.bit_width*len(array)+7)//8
        self.statistics['peak_python_logical_value_bytes']=max(self.statistics.get('peak_python_logical_value_bytes',0),logical)
        self.statistics['peak_python_list_elements']=max(self.statistics.get('peak_python_list_elements',0),list_elements)
        self.statistics['peak_python_list_containers']=max(self.statistics.get('peak_python_list_containers',0),list_containers)
        return (4*logical+128*(list_elements+list_containers)+128*table.num_rows*table.num_columns+512*table.num_rows+
            sum(4*len(name)+128 for name in table.column_names)+4096)

    def read_partition(self,part,*,columns=None,symbols=None,sessions=None):
        """Preserve the private Store protocol; producer uses batch iteration."""
        import pyarrow as pa
        names=None if columns is None else list(dict.fromkeys([*columns,
            *(['security_id'] if symbols is not None else []),*(['session'] if sessions is not None else [])]))
        owner=self.writer.token('direct-arrow');tables=[]
        with closing(self._projection_batches(part,names)) as batches:
            for offset,table in batches:
                with self.writer.scope('arrow-filter',self._filter_charge(table,symbols,sessions)):
                    selected,_=self._filtered_batch(table,offset,symbols=symbols,sessions=sessions)
                    self.writer.add(owner,self._arrow_charge(selected));tables.append(selected)
        return pa.concat_tables(tables).select(columns) if columns is not None else pa.concat_tables(tables)

    def _native_chunks(self,part,*,owner,columns=None,symbols=None,sessions=None,positions=False,retain=True):
        """Convert at most 64 rows after reserving their expanded Python graph."""
        names=None if columns is None else list(dict.fromkeys([*columns,
            *(['security_id'] if symbols is not None else []),*(['session'] if sessions is not None else [])]))
        with closing(self._projection_batches(part,names)) as batches:
            for offset,table in batches:
                with self.writer.scope('arrow-filter',self._filter_charge(table,symbols,sessions)):
                    table,physical=self._filtered_batch(table,offset,symbols=symbols,sessions=sessions)
                    if columns is not None: table=table.select(columns)
                    if not table.num_rows:
                        del table,physical
                        continue
                    for start in range(0,table.num_rows,64):
                        chunk=table.slice(start,64)
                        bound=self._python_bound(chunk)
                        precharge=4*bound+512*chunk.num_rows
                        self.statistics['peak_python_conversion_reservation_bytes']=max(
                            self.statistics.get('peak_python_conversion_reservation_bytes',0),precharge)
                        _require(precharge<=self.writer.remaining(),'native Python conversion exceeds working budget before row expansion')
                        with self.writer.scope('row-conversion',precharge):
                            rows=chunk.to_pylist()
                            self.statistics['python_batches_converted']=self.statistics.get('python_batches_converted',0)+1
                            ordinals=(list(physical[start:start+len(rows)]) if isinstance(physical,range)
                                else [offset+i for i in physical.slice(start,len(rows)).to_pylist()]) if positions else None
                            if retain:
                                actual=4*_object_size(rows)+512*len(rows)
                                _require(actual<=precharge,'native Python rows exceeded flat-schema conversion reservation')
                                self.writer.retained['row-conversion']=precharge-actual
                                self.writer.add(owner,actual)
                            yield rows,ordinals
                            del rows,ordinals
                        del chunk
                del table,physical

    def _native_evidence_index(self,domain_name):
        from .protocols import ConflictError
        domain=self.reader.snapshot.get('domains',{}).get('public_evidence')
        if not domain or domain_name=='public_evidence': return {}
        if domain_name in self.evidence:
            for part in domain['partitions']: self.verify_partition(part)
            return self.evidence[domain_name]
        owner='evidence:'+domain_name;result={}
        self.writer.reserve(owner,4096)
        for part in domain['partitions']:
            with closing(self._native_chunks(part,owner=owner,retain=False)) as chunks:
                for rows,_ in chunks:
                    kept=[row for row in rows if row['target_domain']==domain_name]
                    self.writer.add(owner,4*_object_size(kept)+512*len(kept))
                    for row in kept:
                        key=(row['target_key'],row['target_revision']);previous=result.get(key)
                        if previous is not None and (previous['public_at'],previous['document_sha256'])!=(row['public_at'],row['document_sha256']):
                            raise ConflictError('conflicting public evidence for one fact revision')
                        result[key]=row
                    del kept
                rows=()
        self.evidence[domain_name]=result
        return result

    def _native_domain_rows(self,manifest,name,columns,*,owner,sessions=None,symbols=None):
        from .public_evidence import apply_evidence
        domain=manifest.get('domains',{}).get(name)
        if not domain: return []
        months={s[:7] for s in sessions} if sessions is not None else None
        result=[]
        for part in domain['partitions']:
            label=str(part['partition'])
            if months is not None and len(label)==7 and label[4]=='-' and label not in months: continue
            with closing(self._native_chunks(part,owner=owner,columns=columns,symbols=symbols,sessions=sessions)) as chunks:
                for rows,_ in chunks: result.extend(rows)
        return apply_evidence(result,index=self._native_evidence_index(name),key_fields=domain['contract']['logical_key'])

    def _native_read_groups(self,domain,query):
        from datetime import date
        from .public_evidence import apply_evidence
        groups={};membership=query.domain=='universe_membership'
        self.writer.reserve('source',4096)
        symbols,sessions=set(query.symbols),set(query.sessions)
        for part in self.reader._parts_for_query(domain,query):
            with closing(self._native_chunks(part,owner='source',columns=self.reader._columns(domain,query),
                    symbols=query.symbols,sessions=None if membership else query.sessions)) as chunks:
                for rows,_ in chunks:
                    for row in rows:
                        if not membership and isinstance(row.get('session'),date): row['session']=row['session'].isoformat()
                        if row.get('security_id') not in symbols or (not membership and row.get('session') not in sessions): continue
                        key=self.reader._group_key(row,domain,query,unselected=membership)
                        if key is not None: groups.setdefault(key,[]).append(row)
        evidence=self._native_evidence_index(query.domain)
        for revisions in groups.values(): apply_evidence(revisions,index=evidence,key_fields=domain['contract']['logical_key'])
        if membership:
            selected={}
            for (symbol,universe,event),revisions in groups.items():
                if universe!=query.universe_id: continue
                if event is None: self.reader._group_key(revisions[0],domain,query)
                selected[symbol,event]=revisions
            return selected
        return groups

    def verify_partition(self,part):
        if part['uri'] not in self.seen:
            self.writer.add('store-cache',4096+4*_object_size(part['uri']))
        if isinstance(self.base,LocalStore):
            with self.writer.scope('file-validation',131072+4096):
                self.base.verify_partition(part)
        else: self.base.verify_partition(part)
        self.seen[part['uri']]=part

    def verify_used(self):
        for part in tuple(self.seen.values()): self.verify_partition(part)

    def read_raw_record(self,record):
        if record.get('payload_uri') not in self.seen:
            self.writer.add('store-cache',4096+4*_object_size(record.get('payload_uri')))
        if isinstance(self.base,LocalStore):
            self.writer.reserve('raw-payload',2*self.base._path(record['payload_uri']).stat().st_size+4096)
        raw=self.base.read_raw_record(record)
        self.writer.reserve('raw-payload',2*len(raw)+4096)
        if 'payload_uri' in record:
            self.seen[record['payload_uri']]={'uri':record['payload_uri'],
                'file_sha256':record['payload_sha256']}
        return raw

    def get_raw(self,batch_id):
        raw=(self.base.get_raw(batch_id,_budget=self.writer) if isinstance(self.base,LocalStore)
            else self.base.get_raw(batch_id))
        self.writer.reserve('raw-record',4*_object_size(raw)+4096)
        return raw


class _MonthlyGroups:
    """One requested month of original revisions; parent Query stays unchanged."""
    def __init__(self,reader,query,check):
        self.reader,self.query,self.check=reader,query,check
        self.domain=reader.snapshot['domains'][query.domain]
        self.month=None;self.groups={}

    def load(self,month):
        if month!=self.month:
            self.groups={}
            self.check(0)
            days=tuple(s for s in self.query.sessions if s[:7]==month)
            q=replace(self.query,sessions=days,cutoff_by_session={s:self.query.cutoff_by_session[s] for s in days},
                policy_by_session=({s:self.query.policy_by_session[s] for s in days} if self.query.policy_by_session is not None else None))
            self.groups=self.reader._read_groups(self.domain,q)
            self.month=month
        return self.groups

    def get(self,key,default=None):
        return self.load(key[1][:7]).get(key,default)


class _StateReferences:
    """Operation-owned unselected master/listings; one calendar/status window."""
    def __init__(self,reader,writer):
        self.reader,self.writer=reader,writer
        self.static={};self.window=None;self.current=None

    def prepare(self,query,day):
        from .local_states import _reference_data,_listing_data
        symbols=tuple(query.symbols)
        if symbols not in self.static:
            owner=self.writer.token('state-master');self.writer.reserve(owner,4096)
            _,identities,_=_reference_data(self.reader.store,self.reader.snapshot,query,
                _names=('security_master',),_owner=owner)
            listings=_listing_data(self.reader.store,self.reader.snapshot,query,_owner=owner)
            self.static[symbols]=(identities,listings,owner)
            self.writer.add('descriptors',1024+4*_object_size(symbols))
        days=tuple(s for s in query.sessions if s[:7]==day[:7])
        key=(symbols,days)
        if key!=self.window:
            self.current=None;self.writer.release('state-window')
            self.writer.reserve('state-window',4096)
            window=replace(query,sessions=days,cutoff_by_session={s:query.cutoff_by_session[s] for s in days},
                policy_by_session=({s:query.policy_by_session[s] for s in days} if query.policy_by_session is not None else None))
            calendar,_,statuses=_reference_data(self.reader.store,self.reader.snapshot,window,
                _names=('trading_calendar','security_status'),_owner='state-window')
            self.current=(calendar,statuses);self.window=key
        identities,listings,owner=self.static[symbols]
        calendar,statuses=self.current
        temporary=2*(self.writer.retained[owner]+self.writer.retained['state-window'])+4096*len(symbols)*(len(query.fields)+2)
        return (calendar,identities,statuses,listings),temporary


class _Appender:
    def __init__(self,sink,field=None): self.sink,self.field=sink,field
    def append(self,value): self.sink.append(value,self.field)


class _Writer:
    def __init__(self,stage,limits,reader,*,data=None,reads=()):
        self.stage,self.limits,self.reader=stage,limits,reader
        self.files={};self.statistics={'parquet_decodes':0,'parquet_projection_hits':0,
            'producer_blocks':0,'event_source_groups':0,'logical_replays':0,'snapshot_graph_bytes':_object_size(reader.snapshot),
            'peak_working_charge_bytes':0,'peak_accepted_working_charge_bytes':0,
            'parquet_decoder_limits':'row count is bounded; page, dictionary and single-value allocations are not bounded by max_working_bytes; external RSS guard required'}
        self.total=0
        self.coverage_memo={}
        self.retained={}
        self._serial=0
        self.other_readers=[] if data is None else [r for r in data._readers.values() if r is not reader]
        self.statistics['other_snapshot_graph_bytes']=sum(_object_size(r.snapshot) for r in self.other_readers)
        self.retained['inputs']=4*_object_size([vars(r['query']) for r in reads])+1024*len(reads)
        stores={id(r.store):r.store for r in [reader,*self.other_readers]}
        self.retained['store-cache']=4*_object_size(tuple(getattr(store,n,{}) for store in stores.values() for n in
            ('_hash_cache','_raw_offsets','_profile_cache')))

    def check(self,extra=0):
        charge=(self.statistics['snapshot_graph_bytes']+self.statistics['other_snapshot_graph_bytes']+
            self.reader._cached_bytes+sum(r._cached_bytes for r in self.other_readers)+
            sum(self.retained.values())+extra+65536)
        self.statistics['peak_working_charge_bytes']=max(self.statistics['peak_working_charge_bytes'],charge)
        peaks=self.statistics.setdefault('peak_owner_charge_bytes',{});families={}
        for owner,size in self.retained.items():
            family=owner.split(':',1)[0]
            families[family]=families.get(family,0)+size
        for family,size in families.items(): peaks[family]=max(peaks.get(family,0),size)
        _require(charge<=self.limits['max_working_bytes'],'native export working budget exceeded')
        self.statistics['peak_accepted_working_charge_bytes']=max(self.statistics['peak_accepted_working_charge_bytes'],charge)

    def reserve_source(self,extra):
        self.reserve('source',extra)

    def reserve(self,owner,size):
        _require(type(size) is int and size>=0,'invalid native working charge')
        self.retained[owner]=size
        self.check()

    def add(self,owner,size):
        self.reserve(owner,self.retained.get(owner,0)+size)

    def release(self,owner):
        self.retained.pop(owner,None)

    def token(self,prefix):
        self._serial+=1
        return prefix+':'+str(self._serial)

    @contextmanager
    def scope(self,owner,size=0):
        previous=self.retained.get(owner)
        try:
            self.reserve(owner,size)
            yield owner
        finally:
            if previous is None: self.release(owner)
            else: self.retained[owner]=previous

    def remaining(self):
        used=(self.statistics['snapshot_graph_bytes']+self.statistics['other_snapshot_graph_bytes']+
            self.reader._cached_bytes+sum(r._cached_bytes for r in self.other_readers)+sum(self.retained.values())+65536)
        return max(0,self.limits['max_working_bytes']-used)

    def save(self,value,kind):
        if kind=='coverage' and id(value) in self.coverage_memo:
            original,desc=self.coverage_memo[id(value)]
            _require(original is value,'coverage owner identity mismatch')
            return dict(desc)
        folder='coverage' if kind=='coverage' else 'parts'
        (self.stage/folder).mkdir(exist_ok=True)
        if kind=='array':
            self.check(32*_encoding_bound(value))
            raw=_json_bytes(value)
            _require(len(raw)<=self.limits['max_part_bytes'],'native array exceeds part budget')
            digest=_ref(raw);uri=folder+'/'+digest[7:]+'.json'
            if uri not in self.files: (self.stage/uri).write_bytes(raw)
            size=len(raw)
        else:
            # Streaming retains one scalar encoder chunk, not a coverage copy.
            self.check(4*_largest_scalar(value)+65536)
            with tempfile.NamedTemporaryFile(dir=self.stage,delete=False) as stream:
                temporary=Path(stream.name);h=sha256();size=0
                try:
                    for raw in _json_chunks(value):
                        self.check(32*len(raw));size+=len(raw)
                        _require(self.total+size<=self.limits['max_saved_bytes'],'native saved byte budget exceeded')
                        h.update(raw);stream.write(raw)
                    digest='sha256:'+h.hexdigest();uri=folder+'/'+digest[7:]+'.json'
                    stream.close()
                    if uri not in self.files: temporary.rename(self.stage/uri)
                finally: temporary.unlink(missing_ok=True)
        if uri not in self.files:
            self.add('descriptors',2048+4*_object_size((uri,digest,kind)))
            self.total+=size
            _require(self.total<=self.limits['max_saved_bytes'],'native saved byte budget exceeded')
            self.files[uri]={'sha256':digest,'bytes':size,'kind':kind}
        desc={'uri':uri,'sha256':digest,'bytes':size}
        if kind=='coverage':
            self.add('descriptors',1024+4*_object_size(desc))
            self.coverage_memo[id(value)]=(value,desc)
        return desc


class _Sink:
    def __init__(self,writer,headers):
        self.writer,self.headers=writer,headers
        self.records=_Appender(self);self.pending=None;self.pending_meta={}
        self.rows=[];self.meta={n:[] for n in headers};self.sizes={n:2 for n in ['records',*headers]}
        self.blocks=[];self.count=0
        self.writer.add('descriptors',4096+4*_object_size(headers))

    def begin_row(self,size=0):
        self.writer.reserve('output-row',size+4096*(len(self.headers)+2))

    def metadata(self,field): return _Appender(self,field)

    def append(self,value,field):
        self.writer.add('output-pending',4*_object_size(value)+1024)
        if field is None:
            _require(self.pending is None,'native sink record alignment failure');self.pending=value
        else:
            _require(field not in self.pending_meta,'native sink metadata alignment failure');self.pending_meta[field]=value
        if self.pending is not None and set(self.pending_meta)==set(self.headers):
            self.writer.check(32*_encoding_bound([self.pending,self.pending_meta]))
            sizes={'records':len(_json_bytes(_json_safe(self.pending)))+64}
            sizes.update({n:len(_json_bytes(_json_safe(v)))+1 for n,v in self.pending_meta.items()})
            if self.rows and (len(self.rows)>=self.writer.limits['max_rows_per_block'] or
                any(self.sizes[n]+sizes[n]>self.writer.limits['max_part_bytes'] for n in sizes)):
                self.flush()
            _require(all(v+2<=self.writer.limits['max_part_bytes'] for v in sizes.values()),'one native row exceeds part budget')
            self.writer.add('output-buffer',self.writer.retained.get('output-pending',0))
            self.rows.append(self.pending)
            for n,v in self.pending_meta.items(): self.meta[n].append(v)
            for n,v in sizes.items(): self.sizes[n]+=v
            self.pending=None;self.pending_meta={}
            self.writer.release('output-pending');self.writer.release('output-row')

    def flush(self):
        if not self.rows: return
        with self.writer.scope('output-conversion',32*sum(self.sizes.values())):
            self._flush()

    def _flush(self):
        frame=pd.DataFrame.from_records(self.rows)
        for name,header in self.headers.items():
            dtype=NULLABLE.get(str(header.get('dtype')).lower())
            if dtype and name in frame.columns: frame[name]=pd.array([r[name] for r in self.rows],dtype=dtype)
        records=_json_safe(frame.to_dict(orient='records'))
        block={'ordinal':self.count,'rows':len(records),'records':self.writer.save(records,'array'),
            'by_key':{n:self.writer.save(_json_safe(values),'array') for n,values in self.meta.items()},
            'sessions':list(dict.fromkeys(r['session'] for r in records)) if records and 'session' in records[0] else []}
        self.writer.add('descriptors',4*_object_size(block)+2048)
        self.blocks.append(block);self.count+=len(records);self.writer.statistics['producer_blocks']+=1
        self.rows=[];self.meta={n:[] for n in self.headers};self.sizes={n:2 for n in ['records',*self.headers]}
        self.writer.release('output-buffer')

    def finish(self,context,metadata):
        _require(self.pending is None and not self.pending_meta,'incomplete native sink row')
        self.flush()
        headers={n:{k:_json_safe(v) for k,v in h.items() if k!='by_key'} for n,h in metadata.items()}
        _require(headers==self.headers,'native metadata header changed during export')
        context=dict(context);coverage=self.writer.save(context.pop('coverage'),'coverage')
        self.writer.add('descriptors',4*_object_size(context)+4096)
        return {'context':_json_safe(context),'coverage':coverage,'field_meta':headers,
            'row_key':context.get('logical_key',['security_id','session']),
            'row_count':self.count,'blocks':self.blocks}


def _file_chunks(root,desc,statistics,phase,marks=None):
    path=_path(root,desc['uri']);before=_mark(path)
    _require(before[2]==desc['bytes'],'native file size mismatch')
    if marks is not None: _require(before==marks[desc['uri']],'native file changed after admission')
    statistics[phase+'_reads']=statistics.get(phase+'_reads',0)+1
    with path.open('rb') as stream:
        while raw:=stream.read(65536):
            statistics[phase+'_bytes']=statistics.get(phase+'_bytes',0)+len(raw)
            yield raw
    _require(_mark(path)==before,'native file changed while reading')


def _array_chunks(root,descs,statistics,phase,marks=None):
    yield b'[';first=True
    for desc in descs:
        if not first: yield b','
        # Parts contain only complete canonical arrays. Remove their brackets
        # with a two-byte tail, without decoding or retaining the whole part.
        tail=b'';begin=True
        with closing(_file_chunks(root,desc,statistics,phase,marks)) as chunks:
            for raw in chunks:
                if begin: _require(raw[:1]==b'[','native array bracket mismatch');raw=raw[1:];begin=False
                combined=tail+raw
                if len(combined)>1: yield combined[:-1]
                tail=combined[-1:]
        _require(tail==b']','native array bracket mismatch');first=False
    yield b']'


def _replay(root,batch,statistics,marks=None):
    yield b'{"context":{'
    values={**batch['context'],'coverage':None}
    for i,key in enumerate(sorted(values)):
        if i: yield b','
        yield _json_bytes(key)+b':'
        if key=='coverage': yield from _file_chunks(root,batch['coverage'],statistics,'replay',marks)
        else: yield _json_bytes(values[key])
    yield b'},"field_meta":{'
    for i,name in enumerate(sorted(batch['field_meta'])):
        if i: yield b','
        yield _json_bytes(name)+b':{'
        header={**batch['field_meta'][name],'by_key':None}
        for j,key in enumerate(sorted(header)):
            if j: yield b','
            yield _json_bytes(key)+b':'
            if key=='by_key': yield from _array_chunks(root,[b['by_key'][name] for b in batch['blocks']],statistics,'replay',marks)
            else: yield _json_bytes(header[key])
        yield b'}'
    yield b'},"records":'
    yield from _array_chunks(root,[b['records'] for b in batch['blocks']],statistics,'replay',marks)
    yield b'}'


def _replay_digest(root,batch,statistics,marks=None):
    digest=sha256()
    with closing(_replay(root,batch,statistics,marks)) as chunks:
        for raw in chunks: digest.update(raw)
    return 'sha256:'+digest.hexdigest()


def export_native_view(data,*,snapshot,reads,destination,limits,source_symbol_block=64):
    """Save original selections with explicit operation-owned working charges."""
    limits=_limits(limits);destination=Path(destination).resolve()
    _require(type(source_symbol_block) is int and source_symbol_block>0,'positive source symbol block required')
    _require(not destination.exists(),'native view destination already exists')
    _require(not destination.is_relative_to(data.store.root.resolve()),'native view must be outside Data root')
    _require(type(reads) in (list,tuple) and bool(reads),'native read requests required')
    for request in reads:
        _require(type(request) is dict and set(request)=={'method','query'},'exact native read request required')
        method,q=request['method'],request['query']
        _require(method in ('read','read_market','members','states','events') and
            isinstance(q,EventQuery if method=='events' else QuerySpec),'unsupported native view method/query')
    reader=None;original_store=None;stage=None
    cold_snapshot=snapshot not in data._readers
    snapshot_admitted=False
    try:
        # The unchanged normal loader owns transient JSON decoding and full
        # identity validation. File size is metadata, not an allocation bound;
        # an external RSS guard must cover this phase. Check the resulting
        # graph before admitting it to native processing.
        reader=data._reader(snapshot);original_store=reader.store
        destination.parent.mkdir(parents=True,exist_ok=True)
        stage=Path(tempfile.mkdtemp(prefix='.native-',dir=destination.parent))
        writer=_Writer(stage,limits,reader,data=data,reads=reads)
        writer.statistics['snapshot_loaded_during_export']=cold_snapshot
        writer.statistics['snapshot_manifest_file_bytes']=(reader._snapshot_mark[2] if reader._snapshot_mark is not None else None)
        writer.statistics['snapshot_loader_limits']='normal JSON load and full identity validation have unbounded transient allocations; external RSS guard required'
        writer.check();snapshot_admitted=True
        snapshot_path,snapshot_mark=reader._snapshot_path,reader._snapshot_mark
        if snapshot_mark is not None:
            _require(_mark(snapshot_path)==snapshot_mark,'Snapshot changed after Reader load')
        reader.store=_ProjectionStore(original_store,reader,writer)
        writer.check();batches=[];state_references=_StateReferences(reader,writer)
        for request in reads:
            method,q=request['method'],request['query']
            if method=='events':
                from .event_reader import read_events, _validate, _event_limitations
                _require(q.domain=='corporate_actions','native events v1 requires corporate_actions')
                domain,keys,declared,*_=_validate(q,reader.snapshot)
                headers={n:{'dtype':declared[n].get('dtype'),'unit':declared[n].get('unit'),
                            'basis':declared[n].get('basis')} for n in q.fields}
                sink=_Sink(writer,headers);scope=[];fallback=0;context=None
                evidence=reader._evidence(q);reader.store.track_positions=True
                reader.store.event_keys.clear()
                writer.release('projection-descriptors')
                for start in range(0,len(q.symbols),source_symbol_block):
                    writer.statistics['event_source_groups']+=1
                    # All source/candidate borrows last through this group;
                    # returned unavailable scopes acquire their own owner first.
                    with writer.scope('source'),writer.scope('candidates'),writer.scope('event-selection'):
                        context,count,encounters=read_events(reader.store,snapshot,q,_snapshot=reader.snapshot,
                            _sink=sink,_source_symbols=q.symbols[start:start+source_symbol_block],
                            _evidence=evidence)
                        writer.add('event-scope',4*_object_size(encounters)+4096)
                        scope.extend(encounters);fallback+=count
                        del encounters
                reader.store.track_positions=False;reader.store.last_positions=()
                identity_unavailable=sum(s.get('source_issue')!='phase_date_not_provided' for _,s in scope)
                context['limitations']=_event_limitations(q,domain.get('source_profile') or {},fallback,
                    identity_unavailable,phase_date_missing=('event_phase' in context and
                    any(s.get('source_issue')=='phase_date_not_provided' for _,s in scope)))
                context.pop('unavailable_event_scope',None)
                if scope: context['unavailable_event_scope']=[s for _,s in sorted(scope,key=lambda item:item[0])]
                batch=sink.finish(context,headers)
                del scope,context,evidence
                writer.release('event-scope')
            else:
                domain,declared,cutoffs=reader._validate(q)
                if method=='read_market': _require(q.purpose=='market_replay','read_market requires market_replay')
                if method=='members': _require(q.domain=='universe_membership','members requires universe_membership')
                if method=='states':
                    _require(q.domain=='market_daily' and q.fields==('close',),
                             'native states v1 requires market_daily close only')
                    _require(str(declared['close'].get('dtype')).lower() in
                        {'float','double','float64','float32'},'native states v1 requires floating close')
                headers={n:{'dtype':declared[n].get('dtype'),'unit':declared[n].get('unit')} for n in q.fields}
                if method=='states': headers['market_state']={'dtype':'string','unit':None}
                sink=_Sink(writer,headers)
                groups=None if q.domain=='universe_membership' else _MonthlyGroups(reader,q,writer.reserve_source)
                if method!='states':
                    batch=reader._read(q,sink=sink,groups=groups)
                else:
                    from .local_states import read_states
                    context=None;fallback=[0]
                    def counted(value): fallback[0]+=value
                    for day in q.sessions:
                        references,temporary=state_references.prepare(q,day)
                        with writer.scope('state-day',temporary):
                            part=replace(q,sessions=(day,),cutoff_by_session={day:q.cutoff_by_session[day]},
                                policy_by_session=({day:q.policy_by_session[day]} if q.policy_by_session is not None else None))
                            selected={(s,day):groups.get((s,day),[]) for s in q.symbols}
                            market=reader._read(part,groups=selected,copy_coverage=False,_on_fallback=counted)
                            native=read_states(reader.store,snapshot,part,_reader=reader,_market=market,_references=references)
                            records=_json_safe(native.frame.to_dict(orient='records'))
                            metadata=_json_safe(native.field_meta);context=native.context
                            for i,row in enumerate(records):
                                sink.begin_row()
                                sink.records.append(row)
                                for name in headers: sink.metadata(name).append(metadata[name]['by_key'][i])
                            del selected,market,native,records,metadata,row,references
                        # Context contains only Snapshot coverage and small query
                        # diagnostics; account its owner before the next window.
                        writer.reserve('state-context',4*_object_size({k:v for k,v in context.items() if k!='coverage'})+4096)
                    context=dict(context);context['query']=dict(context['query'],sessions=list(q.sessions),
                        cutoff_by_session={s:cutoffs[s].isoformat() for s in q.sessions},
                        policy_by_session=dict(q.policy_by_session) if q.policy_by_session is not None else None)
                    context['limitations']=[*_policy_limitations(q,domain.get('source_profile') or {},fallback[0]),
                        "coverage gap classification describes this Snapshot's observations, not proof the gap was known at a historical decision"]
                    batch=sink.finish(context,headers)
                    del context
                    writer.release('state-context')
                if groups is not None: groups.groups={}
                del groups
                writer.release('source');writer.release('membership-state');writer.release('membership-selection')
            batch['method']=method
            batch['native_ref']=_replay_digest(stage,batch,writer.statistics)
            writer.statistics['logical_replays']+=1
            _require(all(b['native_ref']!=batch['native_ref'] for b in batches),
                     'duplicate native view request; reuse its native_ref')
            writer.add('descriptors',2048)
            batches.append(batch)
            del batch,sink
        manifest={'contract_version':VERSION,'logical_contract_version':'data_batch_v1',
            'exporter_version':EXPORTER,'snapshot_id':snapshot,'batches':batches,
            'files':writer.files,'statistics':writer.statistics}
        with writer.scope('manifest-encoding',32*_encoding_bound(manifest)):
            raw=_json_bytes(manifest)
            _require(len(raw)<=limits['max_part_bytes'] and writer.total+len(raw)<=limits['max_saved_bytes'],'native manifest budget exceeded')
            (stage/'manifest.json').write_bytes(raw)
        reader.store.verify_used()
        if snapshot_mark is not None:
            _require(_mark(snapshot_path)==snapshot_mark,'Snapshot changed during native export')
        _publish_new_directory(stage,destination)
        return {'contract_version':VERSION,'manifest_uri':str(destination/'manifest.json'),
            'content_digest':_ref(raw),'native_refs':[b['native_ref'] for b in batches],
            'statistics':deepcopy(writer.statistics)}
    finally:
        if reader is not None and original_store is not None: reader.store=original_store
        if cold_snapshot and not snapshot_admitted and reader is not None and data._readers.get(snapshot) is reader:
            data._readers.pop(snapshot,None)
        if stage is not None and stage.exists(): shutil.rmtree(stage)


class NativeView:
    """One process-local admitted view; selected arrays are decoded afresh."""
    def __init__(self,root,manifest,limits):
        self.root,self._manifest,self.limits=root,manifest,limits
        self._closed=False;self._marks={};self._batches={};self.statistics={};self._admitted=set()
        self._base=_object_size(manifest)

    @property
    def manifest(self):
        _require(not self._closed,'native view closed');return deepcopy(self._manifest)

    def _small(self,desc,phase):
        _require(desc['bytes']<=self.limits['max_part_bytes'],'native part exceeds consumer budget')
        _require(self._base+32*desc['bytes']+65536<=self.limits['max_working_bytes'],'native decode budget exceeded')
        raw=b''.join(_file_chunks(self.root,desc,self.statistics,phase,self._marks or None))
        _require(_ref(raw)==desc['sha256'],'native part digest mismatch')
        value=json.loads(raw,object_pairs_hook=_pairs,parse_constant=_reject)
        _require(_json_bytes(value)==raw,'noncanonical native part')
        self.statistics[phase+'_decodes']=self.statistics.get(phase+'_decodes',0)+1
        if phase=='cold' and desc['uri'] not in self._admitted:
            self._admitted.add(desc['uri']);self.statistics['file_validations']+=1
        return value

    def _decoded_block(self,batch,block,phase):
        refs=[block['records'],*block['by_key'].values()]
        unique={r['uri']:r for r in refs}
        _require(self._base+32*sum(r['bytes'] for r in unique.values())+65536<=self.limits['max_working_bytes'],'native block decode budget exceeded')
        decoded={uri:self._small(r,phase) for uri,r in unique.items()}
        rows=decoded[block['records']['uri']]
        _require(type(rows) is list and len(rows)==block['rows'] and all(type(r) is dict for r in rows),'native record count/type mismatch')
        keys=[tuple(r.get(k) for k in batch['row_key']) for r in rows]
        _require(all(all(v is not None for v in key) for key in keys),'native row key missing')
        metadata={}
        for name,ref in block['by_key'].items():
            values=decoded[ref['uri']]
            _require(type(values) is list and len(values)==len(rows) and all(type(v) is dict for v in values) and
                [tuple(r.get(k) for k in batch['row_key']) for r in values]==keys,'native metadata ordinal/key mismatch')
            metadata[name]={**deepcopy(batch['field_meta'][name]),'by_key':values}
        sessions=list(dict.fromkeys(r['session'] for r in rows)) if rows and 'session' in rows[0] else []
        _require(sessions==block['sessions'],'native block session mismatch')
        return rows,metadata

    def iter_blocks(self,*,native_ref,sessions=None):
        """Yield owned row views retaining the complete parent's native identity.

        No whole-source re-admission. Selected files are rehashed/decoded and
        counted; retaining yielded blocks is the caller's memory responsibility.
        A sessions selector filters daily rows only and never changes Query.
        """
        _require(not self._closed,'native view closed')
        _require(native_ref in self._batches,'unknown native batch reference')
        batch=self._batches[native_ref]
        self._unchanged()
        wanted=None if sessions is None else set(sessions)
        if wanted is not None:
            _require('sessions' in batch['context']['query'] and wanted<=set(batch['context']['query']['sessions']),'native selector outside original daily query')
        for block in batch['blocks']:
            _require(not self._closed,'native view closed')
            if wanted is not None and not wanted.intersection(block['sessions']): continue
            rows,meta=self._decoded_block(batch,block,'selected')
            if wanted is not None:
                indices=[i for i,r in enumerate(rows) if r['session'] in wanted]
                rows=[rows[i] for i in indices]
                meta={n:{**h,'by_key':[h['by_key'][i] for i in indices]} for n,h in meta.items()}
            else: indices=range(len(rows))
            _require(not self._closed,'native view closed')
            for uri in ('manifest.json',batch['coverage']['uri']):
                _require(_mark(_path(self.root,uri))==self._marks[uri],'native view changed after admission')
            yield {'native_ref':native_ref,'ordinal':block['ordinal'],'records':rows,
                   'ordinals':[block['ordinal']+i for i in indices],
                   'field_meta':meta,'context':deepcopy(batch['context']),
                   'bindings':deepcopy([block['records'],*block['by_key'].values()])}
            del rows,meta,indices
        self._unchanged()

    def _unchanged(self):
        _require(not self._closed,'native view closed')
        for uri,mark in self._marks.items():
            _require(_mark(_path(self.root,uri))==mark,'native view changed after admission')

    def close(self):
        self._closed=True;self._batches.clear()

    def __enter__(self):
        _require(not self._closed,'native view closed');return self

    def __exit__(self,*_): self.close()


def open_native_view(directory,*,manifest_sha256,limits):
    """Completely admit physical bytes and replay each original native hash.

    No Data root, query or supplier access. Integrity is local to this handle,
    never a persisted exemption. Full coverage is validated once per unique
    file, without constructing its graph. Runtime retains its semantic audit.
    """
    try:
        return _open_native_view(directory,manifest_sha256=manifest_sha256,limits=limits)
    except QueryError:
        raise
    except (KeyError,TypeError,IndexError,ValueError,OSError) as exc:
        raise QueryError('invalid native view: '+str(exc)) from exc


def _open_native_view(directory,*,manifest_sha256,limits):
    limits=_limits(limits);root=Path(directory).absolute()
    _require(not root.is_symlink(),'native view root symlink refused')
    p=_path(root,'manifest.json');before=_mark(p);size=before[2]
    _require(size<=limits['max_part_bytes'] and 32*size+65536<=limits['max_working_bytes'],'native manifest decode budget exceeded')
    raw=p.read_bytes();_require(_mark(p)==before,'native manifest changed while reading')
    _require(_ref(raw)==manifest_sha256,'native manifest digest mismatch')
    manifest=json.loads(raw,object_pairs_hook=_pairs,parse_constant=_reject)
    _require(_json_bytes(manifest)==raw,'noncanonical native manifest')
    _require(set(manifest)=={'contract_version','logical_contract_version','exporter_version','snapshot_id','batches','files','statistics'} and
        manifest['contract_version']==VERSION and manifest['logical_contract_version']=='data_batch_v1' and manifest['exporter_version']==EXPORTER,'native manifest version/fields mismatch')
    view=NativeView(root,manifest,limits)
    view.statistics={'file_validations':0,'coverage_validations':0,'logical_replays':0,'manifest_bytes':size}
    files=manifest['files'];used=set()
    _require(type(files) is dict and sum(f['bytes'] for f in files.values())+size<=limits['max_saved_bytes'],'native inventory byte budget exceeded')
    actual={str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()}
    _require(actual==set(files)|{'manifest.json'},'untracked or missing native files')
    for uri,desc in files.items():
        _require(set(desc)=={'sha256','bytes','kind'} and type(desc['bytes']) is int and desc['bytes']>0 and desc['kind'] in ('array','coverage'),'native file descriptor mismatch')
        ref={'uri':uri,'sha256':desc['sha256'],'bytes':desc['bytes']}
        view._marks[uri]=_mark(_path(root,uri))
        if desc['kind']=='coverage':
            try:
                with closing(_file_chunks(root,ref,view.statistics,'admission',view._marks)) as chunks:
                    outcome=validate_canonical_chunks(chunks,
                        maximum_workspace_bytes=limits['max_working_bytes'],caller_retained_bytes=lambda:view._base)
            except ValueError as exc:
                raise QueryError('invalid native coverage: '+str(exc)) from exc
            _require(outcome['digest']==desc['sha256'] and outcome['size']==desc['bytes'],'native file digest mismatch')
            view.statistics['coverage_validations']+=1
            view.statistics['file_validations']+=1;view._admitted.add(uri)
        # Arrays are admitted with their owning block below. This avoids a
        # separate whole-inventory JSON decode before ordinal/key validation.
    for batch in manifest['batches']:
        _require(set(batch)=={'context','coverage','field_meta','row_key','row_count','blocks','method','native_ref'},'native batch fields mismatch')
        _require(batch['context']['contract_version']=='data_batch_v1' and batch['context']['snapshot_id']==manifest['snapshot_id'] and 'coverage' not in batch['context'],'native context binding mismatch')
        _require(batch['native_ref'] not in view._batches,'duplicate native batch reference')
        _require(type(batch['row_key']) is list and bool(batch['row_key']) and
            len(set(batch['row_key']))==len(batch['row_key']) and
            all(type(k) is str and k for k in batch['row_key']),'native row key definition mismatch')
        event=batch['method']=='events'
        _require(batch['method'] in ('read','read_market','members','states','events') and
            batch['row_key']==(batch['context'].get('logical_key') if event else ['security_id','session']) and
            type(batch['row_count']) is int and batch['row_count']>=0,
            'unsupported native batch method/key')
        query=batch['context']['query']
        _require(type(query.get('symbols')) is list and bool(query['symbols']) and
            len(set(query['symbols']))==len(query['symbols']), 'native query symbols mismatch')
        if event:
            _require(batch['context']['domain']=='corporate_actions' and 'security_id' in batch['row_key']
                and query.get('time_field') and 'cutoff' in query, 'native event query mismatch')
        else:
            _require(type(query.get('sessions')) is list and bool(query['sessions']) and
            len(set(query['symbols']))==len(query['symbols']) and len(set(query['sessions']))==len(query['sessions']) and
            set(query['cutoff_by_session'])==set(query['sessions']) and
            batch['row_count']==len(query['symbols'])*len(query['sessions']), 'native complete query grid mismatch')
        names=set(query['fields'])|({'market_state'} if batch['method']=='states' else set())
        _require(set(batch['field_meta'])==names and
            all(type(h) is dict and 'by_key' not in h for h in batch['field_meta'].values()),
            'native field metadata headers mismatch')
        def bind(ref,kind):
            _require(type(ref) is dict and set(ref)=={'uri','sha256','bytes'} and ref['uri'] in files and
                files[ref['uri']]=={'sha256':ref['sha256'],'bytes':ref['bytes'],'kind':kind},'native part reference mismatch')
            used.add(ref['uri'])
        bind(batch['coverage'],'coverage');ordinal=0;previous=None
        for block in batch['blocks']:
            _require(set(block)=={'ordinal','rows','records','by_key','sessions'} and block['ordinal']==ordinal and
                type(block['rows']) is int and 0<block['rows']<=limits['max_rows_per_block'] and
                set(block['by_key'])==set(batch['field_meta']),'native block coverage/ordinal mismatch')
            bind(block['records'],'array')
            for ref in block['by_key'].values(): bind(ref,'array')
            rows,_=view._decoded_block(batch,block,'cold')
            if event:
                positions={s:i for i,s in enumerate(query['symbols'])}
                for row in rows:
                    _require(set(row)==names|set(batch['row_key'])|{query['time_field']} and
                        row['security_id'] in positions,'native event fields/symbol mismatch')
                    order=(positions[row['security_id']],row[query['time_field']] or '',
                           tuple(str(row[k]) for k in batch['row_key']))
                    _require(previous is None or order>previous,'native original event order/keys mismatch')
                    previous=order
            else:
                width=len(query['symbols'])
                _require(all((r['session'],r['security_id'])==(query['sessions'][(ordinal+i)//width],query['symbols'][(ordinal+i)%width])
                    and set(r)==names|{'security_id','session'} for i,r in enumerate(rows)),
                    'native original row order/fields mismatch')
            ordinal+=block['rows']
            del rows,_
        _require(ordinal==batch['row_count'],'native batch row count mismatch')
        _require(_replay_digest(root,batch,view.statistics,view._marks)==batch['native_ref'],
                 'native logical identity mismatch')
        view.statistics['logical_replays']+=1;view._batches[batch['native_ref']]=batch
    _require(used==set(files),'unused native file closure')
    _require(view._admitted==set(files),'native file admission closure')
    _require(_mark(p)==before,'native manifest changed during admission')
    view._marks['manifest.json']=before
    return view
