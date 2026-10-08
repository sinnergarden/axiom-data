"""Explicit immutable JSON parts of native DataBatches, not a PIT bypass.

Native identity is the existing sorted-key, compact, UTF-8 canonical JSON
algorithm used by Engine stock_evidence.native_ref and Research saved artifacts.
Physical file hashes are distinct. Snapshot loading remains the normal loader.
Working charges are conservative reservations, not process RSS measurements.
"""
from collections import OrderedDict
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from pathlib import Path, PurePosixPath
import json
import shutil
import tempfile

import pandas as pd

from .protocols import DataBatch, EventQuery, QueryError, QuerySpec, _json_safe
from .storage import _json_bytes, _json_chunks, _coerce
from .reader import _object_size, _policy_limitations
from .portable import _publish_new_directory
from ._native_canonical_json import validate_canonical_chunks

VERSION = 'data_native_view_v1'
EXPORTER = 'native_json_parts_v1'
LIMIT_FIELDS = {'max_part_bytes', 'max_working_bytes', 'max_saved_bytes', 'max_rows_per_block'}
NULLABLE = {'int':'Int64','integer':'Int64','int64':'Int64','int32':'Int32',
    'bool':'boolean','boolean':'boolean','float':'Float64','double':'Float64',
    'float64':'Float64','float32':'Float32','string':'string','str':'string','utf8':'string'}


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


class _ProjectionStore:
    """Export-local Arrow projections charged to the existing Reader LRU."""
    def __init__(self, store, reader, statistics):
        self.base,self.reader,self.statistics=store,reader,statistics
        self.seen={};self.track_positions=False;self.last_positions=();self.event_keys=set()

    def __getattr__(self,name):
        return getattr(self.base,name)

    def read_partition(self,part,*,columns=None,symbols=None,sessions=None):
        import pyarrow as pa
        import pyarrow.compute as pc
        names=list(dict.fromkeys([*columns,*(['security_id'] if symbols is not None else []),
                                  *(['session'] if sessions is not None else [])]))
        key='native-arrow:'+sha256(_json_bytes([self.reader.snapshot_id,part,names])).hexdigest()
        self.verify_partition(part)
        entry=self.reader._cache.get(key)
        if entry is None:
            _require(not self.track_positions or key not in self.event_keys,
                'native event projections exceed Reader cache; increase cache_bytes or source_symbol_block')
            table=self.base.read_partition(part,columns=names)
            self.statistics['parquet_decodes']+=1
            self.reader._put_entry(key,table,table.get_total_buffer_size()+256)
        else:
            table=entry[0];self.reader._cache.move_to_end(key)
            self.statistics['parquet_projection_hits']+=1
        if self.track_positions: self.event_keys.add(key)
        if symbols is not None:
            _require('security_id' in table.column_names,'partition lacks security_id')
            mask=pc.is_in(table['security_id'],value_set=pa.array(list(symbols),type=table.schema.field('security_id').type))
            if self.track_positions: self.last_positions=pc.indices_nonzero(mask).to_pylist()
            table=table.filter(mask) if symbols else table.slice(0,0)
        elif self.track_positions: self.last_positions=range(table.num_rows)
        if sessions is not None:
            _require('session' in table.column_names,'partition lacks session')
            dtype=table.schema.field('session').type
            values=[_coerce(s,dtype) for s in sessions]
            table=table.filter(pc.is_in(table['session'],value_set=pa.array(values,type=dtype))) if sessions else table.slice(0,0)
        return table.select(columns)

    def verify_partition(self,part):
        self.base.verify_partition(part)
        self.seen[part['uri']]=part

    def verify_used(self):
        for part in self.seen.values(): self.base.verify_partition(part)

    def read_raw_record(self,record):
        raw=self.base.read_raw_record(record)
        if 'payload_uri' in record:
            self.seen[record['payload_uri']]={'uri':record['payload_uri'],
                'file_sha256':record['payload_sha256']}
        return raw


class _MonthlyGroups:
    """One requested month of original revisions; parent Query stays unchanged."""
    def __init__(self,reader,query,check):
        self.reader,self.query,self.check=reader,query,check
        self.domain=reader.snapshot['domains'][query.domain]
        self.month=None;self.groups={};self.fallbacks={}

    def load(self,month):
        if month!=self.month:
            self.groups={}
            self.check(0)
            days=tuple(s for s in self.query.sessions if s[:7]==month)
            q=replace(self.query,sessions=days,cutoff_by_session={s:self.query.cutoff_by_session[s] for s in days},
                policy_by_session=({s:self.query.policy_by_session[s] for s in days} if self.query.policy_by_session is not None else None))
            parts=self.reader._parts_for_query(self.domain,q)
            # Precharge a source projection before Arrow/Python allocation.
            self.check(sum(p.get('rows',0)*(256+128*len(self.reader._columns(self.domain,q))) for p in parts))
            self.groups=self.reader._read_groups(self.domain,q)
            self.check(4*_object_size(self.groups))
            self.month=month
            self.fallbacks[month]=sum(not (r.get('source_available_at') is not None and r.get('evidence_ref'))
                for rows in self.groups.values() for r in rows)
        return self.groups

    def get(self,key,default=None):
        return self.load(key[1][:7]).get(key,default)

    def values(self):
        for month in dict.fromkeys(s[:7] for s in self.query.sessions):
            yield from self.load(month).values()


class _Appender:
    def __init__(self,sink,field=None): self.sink,self.field=sink,field
    def append(self,value): self.sink.append(value,self.field)


class _Writer:
    def __init__(self,stage,limits,reader):
        self.stage,self.limits,self.reader=stage,limits,reader
        self.files={};self.statistics={'parquet_decodes':0,'parquet_projection_hits':0,
            'producer_blocks':0,'event_source_groups':0,'logical_replays':0,'snapshot_graph_bytes':_object_size(reader.snapshot),
            'peak_working_charge_bytes':0}
        self.total=0
        self.coverage_memo={}
        self.retained={}

    def check(self,extra=0):
        charge=self.statistics['snapshot_graph_bytes']+self.reader._cached_bytes+sum(self.retained.values())+extra+65536
        self.statistics['peak_working_charge_bytes']=max(self.statistics['peak_working_charge_bytes'],charge)
        _require(charge<=self.limits['max_working_bytes'],'native export working budget exceeded')

    def reserve_source(self,extra):
        self.retained['source']=extra
        self.check()

    def save(self,value,kind):
        if kind=='coverage' and id(value) in self.coverage_memo:
            original,desc=self.coverage_memo[id(value)]
            _require(original is value,'coverage owner identity mismatch')
            return dict(desc)
        folder='coverage' if kind=='coverage' else 'parts'
        (self.stage/folder).mkdir(exist_ok=True)
        if kind=='array':
            raw=_json_bytes(value)
            _require(len(raw)<=self.limits['max_part_bytes'],'native array exceeds part budget')
            digest=_ref(raw);uri=folder+'/'+digest[7:]+'.json'
            if uri not in self.files: (self.stage/uri).write_bytes(raw)
            size=len(raw)
        else:
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
            self.total+=size
            _require(self.total<=self.limits['max_saved_bytes'],'native saved byte budget exceeded')
            self.files[uri]={'sha256':digest,'bytes':size,'kind':kind}
        desc={'uri':uri,'sha256':digest,'bytes':size}
        if kind=='coverage': self.coverage_memo[id(value)]=(value,desc)
        return desc


class _Sink:
    def __init__(self,writer,headers):
        self.writer,self.headers=writer,headers
        self.records=_Appender(self);self.pending=None;self.pending_meta={}
        self.rows=[];self.meta={n:[] for n in headers};self.sizes={n:2 for n in ['records',*headers]}
        self.blocks=[];self.count=0

    def metadata(self,field): return _Appender(self,field)

    def append(self,value,field):
        if field is None:
            _require(self.pending is None,'native sink record alignment failure');self.pending=value
        else:
            _require(field not in self.pending_meta,'native sink metadata alignment failure');self.pending_meta[field]=value
        if self.pending is not None and set(self.pending_meta)==set(self.headers):
            sizes={'records':len(_json_bytes(_json_safe(self.pending)))+64}
            sizes.update({n:len(_json_bytes(_json_safe(v)))+1 for n,v in self.pending_meta.items()})
            if self.rows and (len(self.rows)>=self.writer.limits['max_rows_per_block'] or
                any(self.sizes[n]+sizes[n]>self.writer.limits['max_part_bytes'] for n in sizes)):
                self.flush()
            _require(all(v+2<=self.writer.limits['max_part_bytes'] for v in sizes.values()),'one native row exceeds part budget')
            self.writer.check(32*sum(self.sizes.values())+32*sum(sizes.values()))
            self.rows.append(self.pending)
            for n,v in self.pending_meta.items(): self.meta[n].append(v)
            for n,v in sizes.items(): self.sizes[n]+=v
            self.pending=None;self.pending_meta={}

    def flush(self):
        if not self.rows: return
        frame=pd.DataFrame.from_records(self.rows)
        for name,header in self.headers.items():
            dtype=NULLABLE.get(str(header.get('dtype')).lower())
            if dtype and name in frame.columns: frame[name]=pd.array([r[name] for r in self.rows],dtype=dtype)
        records=_json_safe(frame.to_dict(orient='records'))
        block={'ordinal':self.count,'rows':len(records),'records':self.writer.save(records,'array'),
            'by_key':{n:self.writer.save(_json_safe(values),'array') for n,values in self.meta.items()},
            'sessions':list(dict.fromkeys(r['session'] for r in records)) if records and 'session' in records[0] else []}
        self.blocks.append(block);self.count+=len(records);self.writer.statistics['producer_blocks']+=1
        self.rows=[];self.meta={n:[] for n in self.headers};self.sizes={n:2 for n in ['records',*self.headers]}

    def finish(self,context,metadata):
        _require(self.pending is None and not self.pending_meta,'incomplete native sink row')
        self.flush()
        headers={n:{k:_json_safe(v) for k,v in h.items() if k!='by_key'} for n,h in metadata.items()}
        _require(headers==self.headers,'native metadata header changed during export')
        context=dict(context);coverage=self.writer.save(context.pop('coverage'),'coverage')
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
        for raw in _file_chunks(root,desc,statistics,phase,marks):
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


def export_native_view(data,*,snapshot,reads,destination,limits,source_symbol_block=64):
    """Save original daily/member/state selections without a whole DataBatch.

    Existing destinations and fact-storage destinations are refused. A failed
    export leaves no final view. No supplier, current, Raw or Snapshot writes.
    Use the Data instance sequentially during this operation. Every limit is
    explicit; Snapshot baseline and Reader LRU are included in working charges.
    """
    limits=_limits(limits);destination=Path(destination).resolve()
    _require(type(source_symbol_block) is int and source_symbol_block>0,'positive source symbol block required')
    _require(not destination.exists(),'native view destination already exists')
    _require(not destination.is_relative_to(data.store.root.resolve()),'native view must be outside Data root')
    _require(type(reads) in (list,tuple) and bool(reads),'native read requests required')
    reader=data._reader(snapshot)
    destination.parent.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix='.native-',dir=destination.parent))
    writer=_Writer(stage,limits,reader);original_store=reader.store
    snapshot_path,snapshot_mark=reader._snapshot_path,reader._snapshot_mark
    if snapshot_mark is not None:
        _require(_mark(snapshot_path)==snapshot_mark,'Snapshot changed after Reader load')
    reader.store=_ProjectionStore(original_store,reader,writer.statistics)
    try:
        writer.check();batches=[]
        for request in reads:
            _require(type(request) is dict and set(request)=={'method','query'},'exact native read request required')
            method,q=request['method'],request['query']
            _require(method in ('read','read_market','members','states','events') and
                isinstance(q,EventQuery if method=='events' else QuerySpec),'unsupported native view method/query')
            if method=='events':
                from .event_reader import read_events, _validate, _event_limitations
                _require(q.domain=='corporate_actions','native events v1 requires corporate_actions')
                domain,keys,declared,*_=_validate(q,reader.snapshot)
                headers={n:{'dtype':declared[n].get('dtype'),'unit':declared[n].get('unit'),
                            'basis':declared[n].get('basis')} for n in q.fields}
                sink=_Sink(writer,headers);scope=[];fallback=0;context=None
                evidence=reader._evidence(q);reader.store.track_positions=True
                reader.store.event_keys.clear()
                for start in range(0,len(q.symbols),source_symbol_block):
                    writer.statistics['event_source_groups']+=1
                    context,count,encounters=read_events(reader.store,snapshot,q,_snapshot=reader.snapshot,
                        _sink=sink,_source_symbols=q.symbols[start:start+source_symbol_block],
                        _evidence=evidence,_check=writer.reserve_source)
                    scope.extend(encounters);fallback+=count
                    writer.reserve_source(0)
                reader.store.track_positions=False
                context['limitations']=_event_limitations(q,domain.get('source_profile') or {},fallback,len(scope))
                context.pop('unavailable_event_scope',None)
                if scope: context['unavailable_event_scope']=[s for _,s in sorted(scope,key=lambda item:item[0])]
                batch=sink.finish(context,headers)
                batch['method']=method
                h=sha256()
                for raw in _replay(stage,batch,writer.statistics): h.update(raw)
                batch['native_ref']='sha256:'+h.hexdigest();writer.statistics['logical_replays']+=1
                _require(all(b['native_ref']!=batch['native_ref'] for b in batches),
                         'duplicate native view request; reuse its native_ref')
                batches.append(batch)
                continue
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
                context=None
                for day in q.sessions:
                    part=replace(q,sessions=(day,),cutoff_by_session={day:q.cutoff_by_session[day]},
                        policy_by_session=({day:q.policy_by_session[day]} if q.policy_by_session is not None else None))
                    selected={(s,day):groups.get((s,day),[]) for s in q.symbols}
                    market=reader._read(part,groups=selected,copy_coverage=False)
                    native=read_states(reader.store,snapshot,part,_reader=reader,_market=market)
                    # Do not call to_json: it would copy the full coverage even
                    # though this diagnostic frame contains only one session.
                    records=_json_safe(native.frame.to_dict(orient='records'))
                    metadata=_json_safe(native.field_meta);context=native.context
                    for i,row in enumerate(records):
                        sink.records.append(row)
                        for name in headers: sink.metadata(name).append(metadata[name]['by_key'][i])
                context=dict(context);context['query']=dict(context['query'],sessions=list(q.sessions),
                    cutoff_by_session={s:cutoffs[s].isoformat() for s in q.sessions},
                    policy_by_session=dict(q.policy_by_session) if q.policy_by_session is not None else None)
                fallback=sum(groups.fallbacks.values()) if q.pit_policy=='market_pit_safe_v1' else 0
                context['limitations']=[*_policy_limitations(q,domain.get('source_profile') or {},fallback),
                    'coverage gap classification describes this Snapshot\'s observations, not proof the gap was known at a historical decision']
                batch=sink.finish(context,headers)
            batch['method']=method
            h=sha256()
            for raw in _replay(stage,batch,writer.statistics): h.update(raw)
            batch['native_ref']='sha256:'+h.hexdigest();writer.statistics['logical_replays']+=1
            _require(all(b['native_ref']!=batch['native_ref'] for b in batches),
                     'duplicate native view request; reuse its native_ref')
            batches.append(batch)
            writer.reserve_source(0)
        manifest={'contract_version':VERSION,'logical_contract_version':'data_batch_v1',
            'exporter_version':EXPORTER,'snapshot_id':snapshot,'batches':batches,
            'files':writer.files,'statistics':writer.statistics}
        raw=_json_bytes(manifest)
        writer.check(32*len(raw))
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
        reader.store=original_store
        if stage.exists(): shutil.rmtree(stage)


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
                outcome=validate_canonical_chunks(_file_chunks(root,ref,view.statistics,'admission',view._marks),
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
        _require(ordinal==batch['row_count'],'native batch row count mismatch')
        h=sha256()
        for chunk in _replay(root,batch,view.statistics,view._marks): h.update(chunk)
        _require('sha256:'+h.hexdigest()==batch['native_ref'],'native logical identity mismatch')
        view.statistics['logical_replays']+=1;view._batches[batch['native_ref']]=batch
    _require(used==set(files),'unused native file closure')
    _require(view._admitted==set(files),'native file admission closure')
    _require(_mark(p)==before,'native manifest changed during admission')
    view._marks['manifest.json']=before
    return view
