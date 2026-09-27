"""Source-bound security master with official exchange termination boundaries."""
from axiom_data.deprecated.resources import resource_file, profile_generation
from io import BytesIO
from importlib.resources import files
import json
import re
from xml.etree import ElementTree as ET
from zipfile import ZipFile, BadZipFile
from axiom_data.artifacts import ArtifactError, _digest, _json_bytes, write_raw_batch, MarketDomainBuilder, validate_domain_commit_closure
from axiom_data.tushare import _source_date, _source_symbol, _raw_endpoint_rows, TushareMarketBuilder
from axiom_data.sw_source import load_profile as sw_profile, profile_digest as sw_digest, payload_issues, validate_request


def profile():
    return json.loads(resource_file('source_profiles', 'exchange_security.v1.json').read_bytes())


def parse_termination(exchange, payload):
    """Read frozen originals. The XLSX source uses text dates; other layouts fail."""
    if exchange not in profile()['endpoints']:
        raise ArtifactError('unsupported boundary exchange')
    try:
        if exchange == 'SSE':
            value=json.loads(payload); rows=value['result']
            if value['pageHelp']['total'] != len(rows) or not rows:
                raise ArtifactError('incomplete exchange termination table')
            rows=[dict(r,SECURITY_CODE=r['B_STOCK_CODE'] if r['STOCK_TYPE']=='2' else r['A_STOCK_CODE']) for r in rows]
            fields=['SECURITY_CODE','LIST_DATE','DELIST_DATE']
        else:
            ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
            with ZipFile(BytesIO(payload)) as book:
                if len([n for n in book.namelist() if re.fullmatch(r'xl/worksheets/sheet[0-9]+\.xml',n)])!=1:
                    raise ArtifactError('unexpected exchange workbook sheets')
                strings=[]
                if 'xl/sharedStrings.xml' in book.namelist():
                    strings=[''.join(x.itertext()) for x in ET.fromstring(book.read('xl/sharedStrings.xml')).findall('s:si',ns)]
                matrix=[]
                for row in ET.fromstring(book.read('xl/worksheets/sheet1.xml')).findall('s:sheetData/s:row',ns):
                    cells={}
                    for cell in row.findall('s:c',ns):
                        column=re.match('[A-Z]+',cell.attrib['r'])[0]
                        if cell.find('s:f',ns) is not None:raise ArtifactError('formula in boundary evidence')
                        text=cell.findtext('s:v',default='',namespaces=ns)
                        if cell.get('t')=='s':text=strings[int(text)]
                        elif cell.get('t')=='inlineStr':text=''.join(cell.find('s:is',ns).itertext())
                        cells[column]=text
                    matrix.append(cells)
                header=matrix[0]
                rows=[{name:row.get(col,'') for col,name in header.items()} for row in matrix[1:]]
                fields=['证券代码','上市日期','终止上市日期']
                if not set(fields)<=set(header.values()) or not rows:
                    raise ArtifactError('unexpected exchange termination columns')
        result={}
        for row in rows:
            code=str(row[fields[0]])
            if not re.fullmatch('[0-9]{1,6}',code):raise ArtifactError('invalid exchange security identity')
            symbol=code.zfill(6)+('.SH' if exchange=='SSE' else '.SZ')
            item={'symbol':symbol,'exchange':exchange,'list_session':_source_date(row[fields[1]]),
                  'delist_session':_source_date(row[fields[2]])}
            if item['delist_session']<item['list_session'] or symbol in result:
                raise ArtifactError('duplicate or reversed exchange boundary')
            result[symbol]=item
        return result
    except (KeyError,TypeError,ValueError,IndexError,BadZipFile,ET.ParseError) as exc:
        raise ArtifactError('malformed exchange termination evidence') from exc


def publish_termination(data_root, *, exchange, payload, retrieved_at):
    rows=parse_termination(exchange,payload); p=profile(); request=p['endpoints'][exchange]
    code='exchange-security-'+_digest(files('axiom_data').joinpath('exchange_security.py').read_bytes())[7:]
    from axiom_data.source_completeness import source_profile_completeness_binding
    completeness=source_profile_completeness_binding(p['profile_version'],_digest(_json_bytes(p)))
    identity='exchange-termination-'+_digest(_json_bytes({'profile':p,'exchange':exchange,'payload':_digest(payload),'retrieved_at':retrieved_at,'code':code,'source_completeness':completeness}))[7:]
    return write_raw_batch(data_root,identity,domain='security_master',source_profile='exchange.termination.'+exchange,
        source_profile_version=p['profile_version'],source_profile_digest=_digest(_json_bytes(p)),request=request,
        retrieved_at=retrieved_at,payload=payload,collector_code=code,summary={'rows':len(rows),'historical_knowledge':'best_effort','source_completeness':completeness})


class ExchangeSecurityBuilder(MarketDomainBuilder):
    implementation_revision='exchange-security-builder.v1'

    def __init__(self,data_root,domain='security_master',*,builder_config=None,**kwargs):
        if domain!='security_master':raise ArtifactError('boundary builder requires security_master')
        config=dict(builder_config or {})
        config['boundary_profile_digest']=_digest(_json_bytes(profile()))
        config['boundary_mapping_code']=_digest(files('axiom_data').joinpath('exchange_security.py').read_bytes())
        super().__init__(data_root,domain,builder_config=config,**kwargs)

    def __call__(self,request):
        from axiom_data.frozen_execution import is_frozen, execute_builder
        if not is_frozen():
            return execute_builder(self, request)
        if request.parent_commit:
            parent=validate_domain_commit_closure(self.layout.root,self.domain,request.parent_commit)
            if parent.manifest['builder_implementation_ref']['revision']!=self.implementation_revision:
                raise ArtifactError('exchange boundary mapping requires a new lineage root')
        return super().__call__(request)

    def _build_rows(self,contract,parent_rows,raw_batches):
        symbols,_,_=TushareMarketBuilder._scope(self)
        source={}; boundaries={}; seen=set(); p=profile()
        for raw in raw_batches:
            m=raw.manifest; version=m.get('source_profile_version')
            if version==p['profile_version']:
                exchange=m.get('request',{}).get('exchange')
                if (exchange not in p['endpoints'] or exchange in seen or m['request']!=p['endpoints'][exchange]
                    or m['source_profile_ref']!='exchange.termination.'+exchange
                    or m['source_profile_digest']!=_digest(_json_bytes(p))):
                    raise ArtifactError('exchange boundary source binding mismatch')
                boundaries.update(parse_termination(exchange,raw.payload));seen.add(exchange);continue
            if profile_generation(version)=='tushare_market.v1':
                grouped=_raw_endpoint_rows([raw])
                if set(grouped)!={'stock_basic'}:raise ArtifactError('security source endpoint mismatch')
                rows=grouped['stock_basic']
            elif version=='tushare_sw_pilot.v1':
                request=m['request'];params=request['params'];rows=json.loads(raw.payload)
                if (request['endpoint']!='stock_basic' or m['source_profile_ref']!='tushare.sw-pilot.stock_basic'
                    or m['source_profile_digest']!=sw_digest()
                    or request['fields']!=sw_profile()['endpoints']['stock_basic']['fields']):
                    raise ArtifactError('stock reference source binding mismatch')
                validate_request('stock_basic',params)
                if payload_issues('stock_basic',params,rows):raise ArtifactError('invalid stock reference payload')
            else:raise ArtifactError('unsupported security source profile')
            for row in rows:
                symbol=row['ts_code']
                if symbol in source:raise ArtifactError('duplicate stock identity observation')
                source[symbol]=row
        required={'SSE' if s.endswith('.SH') else 'SZSE' for s in symbols}
        if seen!=required:raise ArtifactError('missing or extraneous exchange boundary coverage')
        output=[]
        for symbol in symbols:
            row=source.get(symbol)
            if row is None:raise ArtifactError('missing scoped stock identity')
            exchange='SSE' if symbol.endswith('.SH') else 'SZSE'
            status=row.get('list_status'); listed=_source_date(row.get('list_date'))
            if status not in {'L','D','P'} or row.get('exchange')!=exchange:
                raise ArtifactError('invalid stock status/exchange')
            official=boundaries.get(symbol)
            if status=='D':
                if official is None:raise ArtifactError('delisted security lacks official boundary')
                if official['list_session']!=listed:raise ArtifactError('official/supplier listing boundary mismatch')
                delisted=official['delist_session']
            else:
                if official or row.get('delist_date'):raise ArtifactError('live/termination source contradiction')
                delisted=None
            output.append(dict(symbol=_source_symbol(symbol),exchange=exchange,list_session=listed,
                               delist_session=delisted,status='source-current-'+status))
        if {r['symbol'] for r in parent_rows}-set(symbols):raise ArtifactError('incremental security scope dropped identities')
        return sorted(output,key=lambda r:r['symbol'])
