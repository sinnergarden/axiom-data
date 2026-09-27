"""Snapshot-bound PR6 Fact/Qlib materialization using the existing artifact publisher."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Sequence
from axiom_data.consumption import _ordered_row
from importlib.resources import files
from axiom_data.artifacts import (ArtifactError, _layout, _json_bytes, _digest, _identity_digest,
    _derived_identity, _timestamp, _write_file, _write_manifest, _publish_directory,
    _load_manifest, _validate_manifest_identity, _safe_path, _identity)
from axiom_data.consumption import SnapshotReader, _feature_bytes, _qlib_symbol, _symbols, _session
from axiom_data.domains import FUNDAMENTAL_DOMAINS as PR6_DOMAINS
from axiom_data.domains.reference import weakest_pit_qualification
from axiom_data.deprecated.pr6_pit_v1 import instant, financial_derived, members, select_revisions
from axiom_data.views import DerivedView, DerivedViewRef

FIELD_MAP = {
 'income.revenue':('income','revenue'),'income.oper_cost':('income','oper_cost'),
 'income.net_income':('income','net_income'),'cashflow.operating':('cashflow','operating'),
 **{'balance.'+name:('balancesheet',name) for name in ('accounts_receivable','current_assets','current_liabilities','equity','inventory','total_assets')},
 **{'indicator.'+name:('fina_indicator',name) for name in ('current_ratio','debt_ratio','gross_margin','roe')},
}
DERIVED_FIELDS=('single_quarter_revenue','single_quarter_oper_cost','ttm_revenue','ttm_net_income')
WIDE_FIELDS=tuple(FIELD_MAP)+tuple('financial.'+f for f in DERIVED_FIELDS)+('valuation.pe','valuation.pb','valuation.ps','universe.membership','industry.membership')


def project(reader, scope, policy, cutoff):
    symbols=scope['symbols'];start=scope['start_session'];end=scope['end_session']
    _symbols(symbols);_session(start,'start');_session(end,'end');instant(cutoff)
    if start>end:raise ArtifactError('reversed PR6 View interval')
    if not scope['universe_ids'] or len(scope['universe_ids'])!=len(set(scope['universe_ids'])):
        raise ArtifactError('PR6 View requires explicit unique universes')
    known={r['symbol'] for r in reader.security_master()}
    if not set(symbols)<=known:raise ArtifactError('PR6 View scope lacks security identities')
    if any(d not in reader.commits for d in PR6_DOMAINS):raise ArtifactError('PR6 snapshot required')
    sessions=sorted({r['session'] for r in reader.trading_calendar(start_session=start,end_session=end) if r['is_open']})
    if not sessions:raise ArtifactError('PR6 View has no calendar coverage')
    taxonomy=sorted({r['industry_id'] for r in reader.facts('industry_membership')})
    encoding={industry:i+1 for i,industry in enumerate(taxonomy)}
    events=[];derived=[];wide=[];memberships=[];industries=[]
    for session in sessions:
        session_cutoff=min(instant(cutoff),instant(session+'T23:59:59+08:00')).isoformat()
        facts=reader.as_of('financial_events',knowledge_cutoff=session_cutoff,pit_policy=policy,symbols=symbols)
        stable=financial_derived(facts,policy=policy,knowledge_cutoff=session_cutoff)
        valuation=reader.as_of('valuation_daily',knowledge_cutoff=session_cutoff,pit_policy=policy,symbols=symbols)
        membership={}
        for group in scope['universe_ids']:
            for row in reader.members(group,session,knowledge_cutoff=session_cutoff,pit_policy=policy):
                membership[row['symbol']]=row
                memberships.append(dict(row,target_session=session))
        industry={r['symbol']:r for r in reader.members(scope['industry_system'],session,
            domain='industry_membership',knowledge_cutoff=session_cutoff,pit_policy=policy)}
        industries.extend(dict(r,target_session=session) for r in industry.values())
        events.extend(dict(r,target_session=session) for r in facts)
        derived.extend(dict(r,target_session=session) for r in stable)
        for symbol in symbols:
            values={f:None for f in WIDE_FIELDS};refs={}
            for leaf,(endpoint,field) in FIELD_MAP.items():
                candidates=[r for r in facts if r['symbol']==symbol and r['endpoint']==endpoint and
                            r['report_type']==('supplier_indicator' if endpoint=='fina_indicator' else '1')]
                if candidates:
                    row=max(candidates,key=lambda r:r['report_period']);values[leaf]=row['values'][field]
                    refs[leaf]=row['revision_id']
            for field in DERIVED_FIELDS:
                candidates=[r for r in stable if r['symbol']==symbol and r['field']==field and r['report_type']=='1']
                if candidates:
                    row=max(candidates,key=lambda r:r['report_period']);values['financial.'+field]=row['value']
                    refs['financial.'+field]=row['derived_id']
            for row in valuation:
                if row['symbol']==symbol and row['session']==session:
                    for field,value in row['values'].items():
                        values['valuation.'+field]=value;refs['valuation.'+field]=row['revision_id']
            # Absence is unknown outside demonstrated snapshot coverage.  A separate
            # cohort list, rather than a zero, expresses positive membership.
            values['universe.membership']=1 if symbol in membership else None
            values['industry.membership']=encoding[industry[symbol]['industry_id']] if symbol in industry else None
            wide.append({'session':session,'symbol':symbol,'values':values,'provenance':refs,
                         'knowledge_cutoff':session_cutoff})
    return {'wide':wide,'events':events,'derived':derived,'memberships':memberships,'industries':industries,
            'industry_encoding':encoding,'sessions':sessions}


def _files(payload, symbols, bundle):
    output={'code_bundle.json':_json_bytes(bundle),'rows.json':_json_bytes(payload)}
    sessions=payload['sessions']
    output['calendars/day.txt']=('\n'.join(sessions)+'\n').encode()
    output['instruments/all.txt']=''.join(f'{_qlib_symbol(s)}\t{sessions[0]}\t{sessions[-1]}\n' for s in symbols).encode()
    for symbol in symbols:
        rows=[r for r in payload['wide'] if r['symbol']==symbol]
        for field in WIDE_FIELDS:
            output[f'features/{_qlib_symbol(symbol).lower()}/{field.replace(".","__")}.day.bin']=_feature_bytes(0,[r['values'][field] for r in rows])
    return output


def _manifest(reader, scope, policy, cutoff, payload, bundle):
    contents=_files(payload,scope['symbols'],bundle)
    return {'artifact_type':'pr6_fact_view','schema_version':'pr6_fact_view.v1',
        'snapshot_ref':{'snapshot_id':reader.snapshot.ref.snapshot_id,'identity_digest':reader.snapshot.manifest['identity_digest']},
        'domain_refs':reader.snapshot.manifest['domain_refs'],'scope':scope,
        'pit_policy':policy,'knowledge_cutoff':instant(cutoff).isoformat(),
        'cutoff_policy':'min_knowledge_cutoff_session_end_Asia_Shanghai.v1',
        'instrument_storage_scope':[{'symbol':s,'qlib_symbol':_qlib_symbol(s),'storage_path':'features/'+_qlib_symbol(s).lower(),'start_session':payload['sessions'][0],'end_session':payload['sessions'][-1]} for s in scope['symbols']],
        'fields':list(WIDE_FIELDS),'qlib_field_mapping':{f:f.replace('.','__') for f in WIDE_FIELDS},
        'industry_encoding':payload['industry_encoding'],
        'implementation_digests':{name:_digest(content.encode()) for name,content in sorted(bundle.items())},
        'files':[{'path':p,'content_digest':_digest(b),'size':len(b)} for p,b in sorted(contents.items())],
        'pit_qualification':weakest_pit_qualification(payload['events']+payload['memberships']+payload['industries']),
        'validation_summary':{'status':'PASS','wide_rows':len(payload['wide']),'derived_rows':len(payload['derived'])}}


class LegacyReader(SnapshotReader):
    """v1 Reader semantics, intentionally without retroactive v2 admission rules."""
    def as_of(self,domain,*,knowledge_cutoff,pit_policy,symbols=None):
        return select_revisions(self.facts(domain,symbols=symbols),policy=pit_policy,knowledge_cutoff=knowledge_cutoff)

    def members(self,group_id,target_session,*,knowledge_cutoff,pit_policy,domain='universe_membership'):
        return members(self.commits[domain].rows,group_id=group_id,target_session=target_session,
                       knowledge_cutoff=knowledge_cutoff,policy=pit_policy)

    def facts(
        self,
        domain: str,
        *,
        symbols: Sequence[str] | None = None,
        start_session: str | None = None,
        end_session: str | None = None,
        fields: Sequence[str] | None = None,
    ) -> tuple[dict[str, Any], ...]:
        """Read one canonical fact domain without resolving or building anything."""

        if domain not in self.commits:
            raise ArtifactError(f"snapshot has no readable domain {domain!r}")
        available = self.schema(domain)
        selected_fields = tuple(fields or available)
        if not selected_fields or len(selected_fields) != len(set(selected_fields)) or any(
            field not in available for field in selected_fields
        ):
            raise ArtifactError("Fact fields must be a non-empty ordered schema subset")
        selected_symbols = set(_symbols(symbols)) if symbols is not None else None
        start = _session(start_session, "start_session") if start_session else None
        end = _session(end_session, "end_session") if end_session else None
        if start is not None and end is not None and start > end:
            raise ArtifactError("fact session interval is reversed")
        rows = []
        for row in self.commits[domain].rows:
            row_symbol = row.get("symbol")
            row_session = row.get("session", row.get("effective_date"))
            if selected_symbols is not None and row_symbol not in selected_symbols:
                continue
            if start is not None and (not isinstance(row_session, str) or row_session < start):
                continue
            if end is not None and (not isinstance(row_session, str) or row_session > end):
                continue
            rows.append(_ordered_row(row, selected_fields))
        return tuple(rows)

