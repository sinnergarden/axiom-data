"""Bounded PR6 forensic comparisons; archived Qsys never supplies canonical facts."""
from __future__ import annotations
import hashlib
import json
import math
import struct
from datetime import date,timedelta
from pathlib import Path
from axiom_data.artifacts import ArtifactError, load_raw_batch
from axiom_data.consumption import SnapshotReader, QlibViewReader
from axiom_data.pr6_views import load_pr6_fact_view
from axiom_data.pit import historical_union, fingerprint


def validate_reference(root):
    root=Path(root)
    manifest=json.loads((root/'reference_manifest.json').read_text())
    for item in manifest['files']:
        path=root/item['path']
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:
            raise ArtifactError('frozen forensic reference digest mismatch')
    return manifest


def reconcile(data_root,snapshot_id,view_id):
    import pandas as pd
    root=Path(data_root);reference=root/'evidence'/'pr6_reference'
    reference_manifest=validate_reference(reference)
    reader=SnapshotReader(root,snapshot_id);view=load_pr6_fact_view(root,view_id)
    binary=QlibViewReader(root,view_id).market_daily(include_missing=True)
    comparisons=[]
    for direct,export in zip(view.rows,binary,strict=True):
        if (direct['symbol'],direct['session'])!=(export['symbol'],export['session']):
            raise ArtifactError('direct/Qlib key mismatch')
        for field,value in direct['values'].items():
            actual=export[field]
            if not ((value is None and actual is None) or (value is not None and actual is not None and math.isclose(value,actual,rel_tol=1e-6,abs_tol=1e-6))):
                raise ArtifactError('direct/Qlib value mismatch')
            comparisons.append([direct['symbol'],direct['session'],field])
    source_checks=[]
    independent_fields={'income':{'revenue':('revenue',1),'oper_cost':('oper_cost',1),'net_income':('n_income',1)},
        'balancesheet':{'accounts_receivable':('accounts_receiv',1),'current_assets':('total_cur_assets',1),'current_liabilities':('total_cur_liab',1),'equity':('total_hldr_eqy_exc_min_int',1),'inventory':('inventories',1),'total_assets':('total_assets',1)},
        'cashflow':{'operating':('n_cashflow_act',1)},
        'fina_indicator':{'current_ratio':('current_ratio',1),'debt_ratio':('debt_to_assets',0.01),'gross_margin':('grossprofit_margin',0.01),'roe':('roe',0.01)},
        'daily_basic':{'pe':('pe',1),'pb':('pb',1),'ps':('ps',1)}}
    for domain in ('financial_events','valuation_daily'):
        for row in reader.commits[domain].rows:
            raw=load_raw_batch(root,row['source_ref']);endpoint=raw.manifest['request']['endpoint']
            matches=[]
            for source in json.loads(raw.payload):
                if source['ts_code']!=row['symbol']:continue
                represented=source.get('end_date',source.get('trade_date'))
                if represented!=row.get('report_period',row.get('session')).replace('-',''):continue
                values={target:None if source.get(field) is None else source[field]*scale
                        for target,(field,scale) in independent_fields[endpoint].items()}
                if values==row['values']:matches.append(source)
            if not matches:raise ArtifactError('independent source mapping mismatch')
            source_checks.append({'revision_id':row['revision_id'],'source_ref':row['source_ref'],'fields':len(row['values'])})
    ttm_checks=[]
    for symbol in view.manifest['scope']['symbols']:
        facts=reader.as_of('financial_events',symbols=[symbol],pit_policy='best_effort_vendor_v1',knowledge_cutoff=view.manifest['knowledge_cutoff'])
        periods={r['report_period']:r for r in facts if r['endpoint']=='income' and r['report_type']=='1'}
        if {'2024-03-31','2024-06-30','2024-09-30','2024-12-31','2025-03-31'}<=set(periods):
            for field in ('revenue','net_income'):
                values=[periods[p]['values'][field] for p in ['2024-03-31','2024-06-30','2024-09-30','2024-12-31','2025-03-31']]
                quarters=[values[1]-values[0],values[2]-values[1],values[3]-values[2],values[4]] if all(v is not None for v in values) else None
                expected=sum(quarters) if quarters is not None else None
                actual=[r['values']['financial.ttm_'+field] for r in view.rows if r['symbol']==symbol]
                if any(v!=expected for v in actual):raise ArtifactError('independent TTM arithmetic mismatch')
                ttm_checks.append({'symbol':symbol,'field':field,'quarters':quarters,'expected':expected,'axiom_values':actual})
    old_income=pd.read_parquet(reference/'pr4/frozen_income/income.parquet')
    income=[]
    for row in reader.commits['financial_events'].rows:
        if row['endpoint']!='income':continue
        matches=old_income[(old_income.ts_code==row['symbol']) &
                          (old_income.end_date==row['report_period'].replace('-','')) &
                          (old_income.report_type.astype(str)==row['report_type'])]
        for field,source in [('revenue','revenue'),('net_income','n_income'),('oper_cost','oper_cost')]:
            values=[None if pd.isna(v) else float(v) for v in matches[source]]
            actual=row['values'][field]
            matched=any((actual is None and v is None) or (actual is not None and v is not None and math.isclose(actual,v,rel_tol=1e-9)) for v in values)
            income.append({'symbol':row['symbol'],'report_period':row['report_period'],'field':field,
                'axiom':actual,'frozen_qsys_versions':values,'result':'MATCH' if matched else 'DRIFT' if values else 'MISSING_FROZEN_REFERENCE',
                'revision_id':row['revision_id'],'source_ref':row['source_ref']})
    old_members=pd.read_parquet(reference/'pr4/frozen_cohort/universe/csi1800_pit_v2/membership.parquet')
    rows=[]
    for r in old_members.to_dict('records'):
        start=date.fromisoformat(r['effective_from']);stop=date.fromisoformat(r['effective_to'])+timedelta(days=1)
        rows.append({'group_id':'csi1800_pit_v2','symbol':r['instrument'],'effective_from':start.isoformat(),
            'effective_to':stop.isoformat(),'logical_event_key':fingerprint(r),'revision_id':fingerprint(r),
            'source_ref':'frozen-pr4-membership','source_available_at':None,'first_observed_at':'2026-09-06T00:00:00Z',
            'vendor_available_at':start.isoformat()+'T23:59:59+08:00','pit_qualification':'best_effort'})
    union=historical_union(rows,group_id='csi1800_pit_v2',start_session='2007-01-01',end_session='2026-07-31',
        lookback_start='2007-01-01',knowledge_cutoff='2026-09-07T00:00:00Z',policy='operational_pit_v1')
    frozen_union=json.loads((reference/'pr4/historical_union_verification.json').read_text())['membership_derived_historical_union']
    if len(union)!=frozen_union['unique_instrument_count'] or fingerprint(list(union))!=frozen_union['ordered_digest']:
        raise ArtifactError('historical union differs from frozen PR4 exact identity')
    membership=[]
    for index in ('000906.SH','000852.SH'):
        for session in ('2025-05-30','2025-06-30'):
            actual={r['symbol'] for r in reader.members(index,session,pit_policy='best_effort_vendor_v1',knowledge_cutoff='2025-07-01T00:00:00Z')}
            day=session.replace('-','')
            expected=set(old_members.loc[(old_members.index_code==index)&(old_members.effective_from<=day)&(old_members.effective_to>=day),'instrument']) & set(reader.commits['security_master'].rows[i]['symbol'] for i in range(len(reader.commits['security_master'].rows)))
            membership.append({'index':index,'session':session,'axiom':sorted(actual),'frozen_qsys':sorted(expected),
                               'result':'MATCH' if actual==expected else 'DRIFT'})
    # Frozen Qlib series are value references only; their old availability and
    # fallback behavior are not adopted as Axiom contracts.
    qlib_dir=reference/'qsys/qlib_bin';calendar=(qlib_dir/'calendars/day.txt').read_text().splitlines()
    field_map={'valuation.pe':'pe','valuation.pb':'pb','valuation.ps':'ps',
               'indicator.current_ratio':'current_ratio','indicator.debt_ratio':'debt_to_assets',
               'indicator.gross_margin':'grossprofit_margin','indicator.roe':'roe',
               'income.revenue':'revenue','income.net_income':'net_income','cashflow.operating':'op_cashflow'}
    legacy=[]
    for row in view.rows:
        for leaf,field in field_map.items():
            path=qlib_dir/'features'/row['symbol'].lower()/(field+'.day.bin')
            value=None
            if path.exists() and row['session'] in calendar:
                content=path.read_bytes();values=struct.unpack('<'+'f'*(len(content)//4),content)
                offset=calendar.index(row['session'])-int(values[0])+1
                if 1<=offset<len(values) and math.isfinite(values[offset]):value=values[offset]
            actual=row['values'][leaf]
            same=(value is None and actual is None) or (value is not None and actual is not None and math.isclose(value,actual,rel_tol=1e-5,abs_tol=1e-6))
            legacy.append({'symbol':row['symbol'],'session':row['session'],'field':leaf,'axiom':actual,
                           'frozen_qsys':value,'result':'MATCH' if same else 'DRIFT'})
    return {'schema_version':'pr6_reconciliation.v1','reference_manifest_digest':fingerprint(reference_manifest),
        'direct_qlib_comparisons':len(comparisons),'source_mapping_checks':source_checks,'independent_ttm_checks':ttm_checks,'income':income,'membership':membership,
        'historical_union':{'count':len(union),'ordered_digest':fingerprint(list(union)),'interval_conversion':'inclusive_end_plus_one_day'},
        'legacy_qlib':legacy,'industry_reference_limitation':'frozen Qsys industry integer encoding lacks frozen taxonomy map; supplier labels and Axiom labels checked through RawBatch closure, legacy code cannot be equated to Axiom code',
        'qualification':'terminal source dates remain best_effort; frozen Qsys income binding does not prove historical revision availability'}
