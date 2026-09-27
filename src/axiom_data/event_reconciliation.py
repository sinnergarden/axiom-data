"""Independent raw/unit and frozen shareholder checks; no legacy truth adoption."""
import hashlib
import json
import math
from pathlib import Path
from axiom_data import SnapshotReader
from axiom_data.artifacts import ArtifactError,load_raw_batch
from axiom_data.event_views import load_event_fact_view
from axiom_data.consumption import QlibViewReader
from axiom_data.domains.events import EVENT_DOMAINS


def reconcile(root,refs,reference):
    import pandas as pd
    reader=SnapshotReader(root,refs['snapshot_id']);source_checks=[];legacy=[]
    manifest=json.loads((reference/'reference_manifest.json').read_text())
    for item in manifest['files']:
        if hashlib.sha256((reference/item['path']).read_bytes()).hexdigest()!=item['sha256']:raise ArtifactError('frozen shareholder digest mismatch')
    tables={d:pd.read_parquet(reference/name) for d,name in [('holder_count_events','holder_num.parquet'),('top_holders_reports','top10_holder_ratio.parquet')]}
    margin={'balance':'rzye','buy':'rzmre','repay':'rzche','lend_balance_volume':'rqyl','lend_repay_volume':'rqchl','lend_sell_volume':'rqmcl','total_balance':'rzrqye'}
    money={'big_buy':'buy_elg_amount','big_sell':'sell_elg_amount','net':'net_mf_amount'}
    for domain in EVENT_DOMAINS:
        for row in reader.facts(domain):
            raw=load_raw_batch(root,row['source_ref']);sources=json.loads(raw.payload)
            matched=[s for s in sources if s['ts_code']==row['symbol'] and s.get('trade_date',s.get('end_date'))==(row['session'] or row['report_period']).replace('-','')
                     and (not row['announcement'] or s['ann_date']==row['announcement'].replace('-',''))]
            if not matched:raise ArtifactError('source row missing')
            if domain=='top_holders_reports':
                valid=len(matched)==10 and len({s['holder_name'] for s in matched})==10 and all(s.get('hold_ratio') is not None and s.get('hold_amount') is not None for s in matched)
                expected={'top10_ratio':math.fsum(s['hold_ratio'] for s in matched) if valid else None}
                if len(row['holders'])!=len(matched):raise ArtifactError('raw holder group truncated')
                for s in matched:
                    h=next(h for h in row['holders'] if h['name']==s['holder_name'])
                    if (h['shares'],h['ratio'],h['category'])!=(s.get('hold_amount'),s.get('hold_ratio'),s.get('holder_type')):raise ArtifactError('holder row mapping differs')
            elif domain=='holder_count_events':expected={'number':matched[0].get('holder_num')}
            elif domain=='forecast_observations':
                s=matched[0];expected={'type':s['type'],'announcement':s['ann_date'][:4]+'-'+s['ann_date'][4:6]+'-'+s['ann_date'][6:], 'period_end':s['end_date'][:4]+'-'+s['end_date'][4:6]+'-'+s['end_date'][6:]}
            else:
                mapping=margin if domain=='margin_daily' else money;scale=1 if domain=='margin_daily' else 10000
                expected={k:None if matched[0].get(v) is None else matched[0][v]*scale for k,v in mapping.items()}
            for field,value in expected.items():
                actual=row['values'][field]
                same=(value==actual) or (isinstance(value,(int,float)) and isinstance(actual,(int,float)) and math.isclose(value,actual,rel_tol=1e-12,abs_tol=1e-8))
                if not same:raise ArtifactError('independent mapping/unit check failed')
                source_checks.append({'domain':domain,'symbol':row['symbol'],'period':row['session'] or row['report_period'],'field':field,'expected':value,'actual':actual,'source_ref':row['source_ref'],'status':'exact_match'})
            if domain in tables:
                table=tables[domain];matches=table[(table.inst==row['symbol'])&(table.end_date==row['report_period'])&(table.ann_date==row['announcement'])]
                field='number' if domain=='holder_count_events' else 'top10_ratio';column='holder_num' if field=='number' else field
                values=[None if pd.isna(v) else float(v) for v in matches[column]]
                actual=row['values'][field]
                status='unavailable_historical_evidence' if not values else 'exact_match' if any(v==actual or v is not None and actual is not None and math.isclose(v,actual,rel_tol=1e-8,abs_tol=1e-6) for v in values) else 'supplier_drift'
                legacy.append({'domain':domain,'symbol':row['symbol'],'period':row['report_period'],'announcement':row['announcement'],'axiom':actual,'frozen_qsys_values':values,'status':status})
        if domain not in tables:legacy.append({'domain':domain,'status':'unavailable_historical_evidence','reason':'frozen reference frozen package contains no immutable fact rows for this endpoint; mutable Qsys not read'})
    view=load_event_fact_view(root,refs['event_view_id']);binary=QlibViewReader(root,refs['event_view_id']).market_daily(include_missing=True)
    direct={(r['symbol'],r['session']):r for r in view.rows};checks=0
    if set(direct)!={(r['symbol'],r['session']) for r in binary}:raise ArtifactError('Qlib key mismatch')
    for r in binary:
        for f,value in direct[(r['symbol'],r['session'])]['values'].items():
            actual=r[f]
            if not (value is None and actual is None or value is not None and actual is not None and math.isclose(value,actual,rel_tol=1e-6,abs_tol=1e-6)):raise ArtifactError('Qlib numeric mismatch')
            checks+=1
    return {'status':'PASS','source_checks':source_checks,'frozen_qsys_comparisons':legacy,'qlib_numeric_checks':checks,
        'reference_manifest':manifest,'historical_qualification':'best_effort terminal history; immutable Qsys snapshot is reconciliation evidence only','unknown_count':0}


# Compatibility exports for historical callers.
