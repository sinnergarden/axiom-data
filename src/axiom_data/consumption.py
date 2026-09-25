"""Snapshot-bound market Reader and minimal Qlib-compatible binary view."""

from __future__ import annotations

import math
import struct
import heapq
import json
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from importlib.resources import files
from typing import Any

from axiom_data.artifacts import (
    ArtifactError,
    _content_tree_digests,
    _derived_identity,
    _digest,
    _identity,
    _identity_digest,
    _json_bytes,
    _layout,
    _load_manifest,
    _publish_directory,
    _relative_file,
    _timestamp,
    _validate_manifest_identity,
    _write_file,
    _write_manifest,
    _load_snapshot_with_commits,
    load_raw_batch,
    _safe_path,
)


MARKET_VIEW_FIELDS = (
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "volume_shares",
    "amount_cny",
    "adj_factor",
    "up_limit",
    "down_limit",
    "is_suspended",
    "turnover_rate",
    "total_market_cap_cny",
    "circulating_market_cap_cny",
)
_QLIB_EXPORTER_REVISION = "qlib-binary-market.v1"
_ADJUSTED_PRICE_FIELDS = ("open", "high", "low", "close")
MARKET_BATCH_SIZE = 50


def validate_session(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ArtifactError(f"{name} must be an ISO date")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ArtifactError(f"{name} must be an ISO date") from exc
    return parsed.isoformat()


def validate_symbols(values: object) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ArtifactError("symbols must be an ordered sequence")
    result = tuple(_identity("symbol", value) for value in values)
    if not result or len(result) != len(set(result)):
        raise ArtifactError("symbols must be non-empty and unique")
    for symbol in result:
        qlib_symbol(symbol)
    return result


def ordered_row(row: Mapping[str, Any], fields: Sequence[str]) -> dict[str, Any]:
    try:
        return {field: row[field] for field in fields}
    except KeyError as exc:
        raise ArtifactError(f"canonical row has no field {exc.args[0]!r}") from exc


@contextmanager
def market_view_batch(reader, configs, kind):
    """Read each complete market input once for a bounded group of Views."""
    from axiom_data.verification_cache import file_state, validation_paths

    if kind not in {'market_replay', 'market_qlib'} or len(configs) > MARKET_BATCH_SIZE:
        raise ArtifactError('invalid market View batch')
    symbols = set()
    for config in configs:
        selected = validate_symbols(config['symbols'])
        if len(selected) != 1:
            raise ArtifactError('market View batch requires one symbol per View')
        if validate_session(config['start_session'], 'start_session') > validate_session(config['end_session'], 'end_session'):
            raise ArtifactError('market View batch has a reversed scope')
        if kind == 'market_qlib':
            _validate_qlib_inputs(reader, **config, structural_only=True)
        symbols.add(selected[0])
    start = min(validate_session(c['start_session'], 'start_session') for c in configs)
    end = max(validate_session(c['end_session'], 'end_session') for c in configs)
    requested = tuple(sorted(symbols))
    domains = ('market_daily', 'security_status', 'price_limits', 'corporate_actions') if kind == 'market_replay' else ('market_daily',)
    with validation_paths() as observed:
        rows = {
            'security_master': reader.security_master(requested),
            'trading_calendar': reader.trading_calendar(start_session=start, end_session=end),
        }
        for domain in domains:
            rows[domain] = (reader.market_daily(requested, start, end) if domain == 'market_daily'
                            else reader.facts(domain, symbols=requested, start_session=start, end_session=end))
    grouped = {domain: {symbol: [] for symbol in symbols} for domain in domains}
    for domain in domains:
        for row in rows[domain]:
            grouped[domain][row['symbol']].append(row)
    rows.update(grouped)
    previous = getattr(reader, '_market_view_batch', None)
    reader._market_view_batch = (kind, start, end, symbols, rows)
    try:
        yield
    finally:
        reader._market_view_batch = previous
        if any(file_state(path) != state for path, state in observed.items()):
            raise ArtifactError('market View source changed during batch')


def prepared_market_view_rows(reader, kind, domain, selected, start, end):
    batch = getattr(reader, '_market_view_batch', None)
    if batch is None or len(selected) != 1:
        return None
    prepared_kind, lower, upper, symbols, rows = batch
    if prepared_kind != kind or start < lower or end > upper or selected[0] not in symbols:
        return None
    if domain == 'security_master':
        return tuple(row for row in rows[domain] if row['symbol'] == selected[0])
    if domain == 'trading_calendar':
        return tuple(row for row in rows[domain] if start <= row['session'] <= end)
    date_key = 'effective_date' if domain == 'corporate_actions' else 'session'
    return tuple(row for row in rows[domain][selected[0]] if start <= row[date_key] <= end)


class SnapshotReader:
    """Read only rows reachable from one explicit, fully validated Snapshot."""

    def __init__(self, data_root: str | Path, snapshot_id: str) -> None:
        self.data_root = Path(data_root)
        self._verified_lineage = {}
        from axiom_data.verification_cache import validation_paths, file_state
        with validation_paths() as observed:
            observed.update({p:file_state(p) for p in files('axiom_data').rglob('*')
                             if p.is_file() and p.suffix in {'.py', '.json'}})
            self.snapshot, self.commits = _load_snapshot_with_commits(
                self.data_root, snapshot_id, lineage_index=self._verified_lineage)
        if any(file_state(p) != state for p, state in observed.items()):
            raise ArtifactError('Snapshot inputs changed during validation')
        self._view_validation_paths = observed
        self._security_projection = OrderedDict()
        self._security_projection_bytes = 0

    def schema(self, domain: str) -> tuple[str, ...]:
        try:
            fields = self.commits[domain].contract["fields"]
        except KeyError as exc:
            raise ArtifactError(f"snapshot has no readable domain {domain!r}") from exc
        return tuple(field["name"] for field in fields)

    def trading_calendar(
        self,
        *,
        exchange: str | None = None,
        start_session: str | None = None,
        end_session: str | None = None,
    ) -> tuple[dict[str, Any], ...]:
        start = validate_session(start_session, "start_session") if start_session else None
        end = validate_session(end_session, "end_session") if end_session else None
        fields = self.schema("trading_calendar")
        return tuple(
            ordered_row(row, fields)
            for row in self.commits["trading_calendar"].rows
            if (exchange is None or row["exchange"] == exchange)
            and (start is None or row["session"] >= start)
            and (end is None or row["session"] <= end)
        )

    def security_master(
        self, symbols: Sequence[str] | None = None
    ) -> tuple[dict[str, Any], ...]:
        selected = set(validate_symbols(symbols)) if symbols is not None else None
        fields = self.schema("security_master")
        return tuple(
            ordered_row(row, fields)
            for row in self.commits["security_master"].rows
            if selected is None or row["symbol"] in selected
        )

    def market_daily(
        self,
        symbols: Sequence[str],
        start_session: str,
        end_session: str,
    ) -> tuple[dict[str, Any], ...]:
        selected = set(validate_symbols(symbols))
        start = validate_session(start_session, "start_session")
        end = validate_session(end_session, "end_session")
        if start > end:
            raise ArtifactError("market session interval is reversed")
        fields = self.schema("market_daily")
        return tuple(
            ordered_row(row, fields)
            for row in self.session_rows('market_daily', start, end, selected)
            if row["symbol"] in selected and start <= row["session"] <= end
        )

    def session_rows(self, domain, start, end, symbols=None):
        from axiom_data.partition_rows import PartitionRows, SESSION_PARTITION_DOMAINS
        rows = self.commits[domain].rows
        if (isinstance(rows, PartitionRows) and domain in SESSION_PARTITION_DOMAINS
                and domain != 'benchmark_daily' and symbols and len(symbols) <= 64):
            return self._security_session_rows(rows, start, end, symbols)
        if isinstance(rows, PartitionRows) and domain in SESSION_PARTITION_DOMAINS and (start is not None or end is not None):
            return rows.sessions(start, end)
        return rows

    _session_rows = session_rows

    def _security_session_rows(self, rows, start, end, symbols):
        """Cache verified projections, never partial validation or persisted facts."""
        selected = tuple(sorted(symbols))
        selected_set = set(selected)
        projected = []
        for entry in rows.entries:
            month = entry['key']
            if (start is not None and month < start[:7]) or (end is not None and month > end[:7]):
                continue
            target = rows.layout.domain_objects(rows.domain) / entry['object_id']
            path = _safe_path(rows.layout.root, target / 'rows.json', closure=target)
            stat = path.stat()
            # Content ID identifies the input. File metadata only invalidates a
            # local optimization; neither time nor path chooses an artifact.
            stamp = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_ctime_ns)
            key = (rows.domain, entry['object_id'], selected)
            cached = self._security_projection.pop(key, None)
            if cached is not None:
                self._security_projection_bytes -= len(cached[1])
            if cached is not None and cached[0] == stamp:
                payload = cached[1]
            else:
                # Exhaust the complete month so count/order/digest checks finish
                # even when all remaining rows belong to unrequested securities.
                values = [row for row in rows.sessions(month + '-01', month + '-31')
                          if row['symbol'] in selected_set]
                after = path.stat()
                if (after.st_dev, after.st_ino, after.st_size, after.st_ctime_ns) != stamp:
                    raise ArtifactError('partition changed during security projection')
                payload = _json_bytes(values)
            # ponytail: 8 MiB encoded payload and 128 entries per Reader. Larger
            # projections are read normally; no disk index or eviction service.
            if len(payload) <= 8 * 1024 * 1024:
                while self._security_projection and (len(self._security_projection) >= 128 or
                        self._security_projection_bytes + len(payload) > 8 * 1024 * 1024):
                    _, (_, previous) = self._security_projection.popitem(last=False)
                    self._security_projection_bytes -= len(previous)
                self._security_projection[key] = (stamp, payload)
                self._security_projection_bytes += len(payload)
            projected.append(json.loads(payload))
        order = rows.contract['sort_order']
        return heapq.merge(*projected, key=lambda row: tuple(row[field] for field in order))

    def leaf_fact(self, leaf, *, symbol, target_session, knowledge_cutoff, pit_policy):
        return leaf_facts(self, leaf, symbol=symbol, target_session=target_session,
                          knowledge_cutoff=knowledge_cutoff, pit_policy=pit_policy)

    def as_of(self, domain: str, *, knowledge_cutoff: str, pit_policy: str,
              symbols: Sequence[str] | None = None, start_session: str | None = None,
              end_session: str | None = None) -> tuple[dict[str, Any], ...]:
        from axiom_data.pit import select_revisions, select_financial_revisions
        from axiom_data.domains.events import EVENT_DOMAINS, DAILY_DOMAINS
        if (start_session is not None or end_session is not None) and domain not in DAILY_DOMAINS:
            raise ArtifactError('session-bounded PIT requires a daily event domain')
        if domain in EVENT_DOMAINS:
            from axiom_data.pit import select_event_revisions
            return select_event_revisions(self.facts(domain,symbols=symbols,
                                        start_session=start_session,end_session=end_session),policy=pit_policy,
                                        knowledge_cutoff=knowledge_cutoff)
        universe = domain == "universe_membership"
        selector = select_financial_revisions if domain == "financial_events" else select_revisions
        result = selector(self.facts(domain, symbols=None if universe else symbols),
                                  policy=pit_policy, knowledge_cutoff=knowledge_cutoff,
                                  **({} if domain == "financial_events" else {"group_states":self.commits[domain].manifest.get("group_states")}))
        return self._project_membership(domain, result, symbols) if universe else result

    def _project_membership(self, domain, rows, symbols):
        if symbols is None:
            return rows
        # Reuse existing symbol admission, after the complete state was validated.
        # Its filtered canonical rows never become selector/closure inputs.
        self.facts(domain, symbols=symbols)
        selected = set(validate_symbols(symbols))
        return tuple(row for row in rows if row["symbol"] in selected)

    def financial_derived(self, *, knowledge_cutoff: str, pit_policy: str,
                          symbols: Sequence[str] | None = None) -> tuple[dict[str, Any], ...]:
        from axiom_data.pit import financial_derived
        return financial_derived(self.facts("financial_events", symbols=symbols),
                                 policy=pit_policy, knowledge_cutoff=knowledge_cutoff)

    def members(self, group_id: str, target_session: str, *, knowledge_cutoff: str,
                pit_policy: str, domain: str = "universe_membership", symbols: Sequence[str] | None = None) -> tuple[dict[str, Any], ...]:
        from axiom_data.pit import members
        if domain not in {"universe_membership", "industry_membership"}:
            raise ArtifactError("membership domain required")
        if domain=='industry_membership' and self.commits[domain].ref.contract_version=='industry_membership.v3':
            return tuple(r for r in self.industry_facts(group_id,target_session,knowledge_cutoff=knowledge_cutoff,
                         pit_policy=pit_policy,symbols=symbols) if r['availability_state']=='classified')
        from axiom_data.financial_coverage import membership_coverage
        membership_coverage(self,domain,group_id,target_session,target_session,pit_policy,knowledge_cutoff,symbols)
        result = members(self.facts(domain), group_id=group_id,
                       target_session=validate_session(target_session, "target_session"),
                       knowledge_cutoff=knowledge_cutoff, policy=pit_policy,
                       group_states=self.commits[domain].manifest.get("group_states"))
        return self._project_membership(domain, result, symbols)

    def members_from_view_ref(self, ref, group_id, target_session, *, symbols=None):
        """Read a financial View's Snapshot-bound universe membership reference."""
        from axiom_data.pit import instant
        required={'snapshot_id','domain_commit_id','identity_digest','universe_ids',
                  'start_session','end_session','pit_policy','knowledge_cutoff'}
        domain_ref=self.snapshot.manifest['domain_refs']['universe_membership']
        if (not isinstance(ref,dict) or set(ref)!=required or
            ref['snapshot_id']!=self.snapshot.ref.snapshot_id or
            ref['domain_commit_id']!=self.commits['universe_membership'].ref.commit_id or
            ref['identity_digest']!=domain_ref['identity_digest'] or
            not isinstance(ref['universe_ids'],list) or group_id not in ref['universe_ids']):
            raise ArtifactError('financial membership ref does not match Snapshot')
        day=validate_session(target_session,'target_session')
        if not (validate_session(ref['start_session'],'start_session')<=day<=
                validate_session(ref['end_session'],'end_session')):
            raise ArtifactError('financial membership ref does not cover session')
        cutoff=min(instant(ref['knowledge_cutoff']),instant(day+'T23:59:59+08:00')).isoformat()
        return self.members(group_id,day,knowledge_cutoff=cutoff,pit_policy=ref['pit_policy'],symbols=symbols)

    def industry_facts(self, group_id, target_session, *, knowledge_cutoff, pit_policy, symbols=None):
        from axiom_data.financial_coverage import membership_coverage
        from axiom_data.sw_industry import project_state
        if self.commits['industry_membership'].ref.contract_version!='industry_membership.v3':
            raise ArtifactError('industry availability states require industry_membership.v3')
        session=validate_session(target_session,'target_session')
        membership_coverage(self,'industry_membership',group_id,session,session,pit_policy,knowledge_cutoff,symbols)
        states=self.as_of('industry_membership',knowledge_cutoff=knowledge_cutoff,pit_policy=pit_policy,symbols=symbols)
        security={r['symbol']:r for r in self.security_master()}
        return tuple(project_state(r,security[r['symbol']],session) for r in states)

    def membership_facts(self, group_id, target_session, *, knowledge_cutoff, pit_policy,
                         domain='universe_membership'):
        rows=self.members(group_id,target_session,knowledge_cutoff=knowledge_cutoff,
                          pit_policy=pit_policy,domain=domain)
        from axiom_data.pit import select_group_states
        states=self.commits[domain].manifest.get('group_states')
        group_observation=(next(s for s in select_group_states(states,policy=pit_policy,knowledge_cutoff=knowledge_cutoff) if s['universe_id']==group_id) if states is not None else None)
        return {'group_id':group_id,'group_observation':group_observation,'version':self.commits[domain].ref.commit_id,
                'snapshot_id':self.snapshot.ref.snapshot_id,'pit_policy':pit_policy,
                'knowledge_cutoff':knowledge_cutoff,'target_session':target_session,'rows':rows}

    def historical_union(self, group_id: str, start_session: str, end_session: str,
                         lookback_start: str, *, knowledge_cutoff: str,
                         pit_policy: str) -> tuple[str, ...]:
        from axiom_data.pit import historical_union
        from axiom_data.financial_coverage import membership_coverage
        membership_coverage(self,'universe_membership',group_id,lookback_start,end_session,pit_policy,knowledge_cutoff)
        return historical_union(self.facts("universe_membership"), group_id=group_id,
            start_session=validate_session(start_session, "start_session"),
            end_session=validate_session(end_session, "end_session"),
            lookback_start=validate_session(lookback_start, "lookback_start"),
            knowledge_cutoff=knowledge_cutoff, policy=pit_policy,
            group_states=self.commits["universe_membership"].manifest.get("group_states"))

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
        from axiom_data.domains import FUNDAMENTAL_DOMAINS, EVENT_DOMAINS
        if domain in EVENT_DOMAINS and symbols is not None:
            requested=set(validate_symbols(symbols))
            if not requested<={r['symbol'] for r in self.security_master()}:
                raise ArtifactError('unknown security')
        if domain in FUNDAMENTAL_DOMAINS:
            from axiom_data.financial_coverage import require_symbols
            if domain=='universe_membership' and 'group_states' in self.commits[domain].manifest:
                available=self.commits[domain].manifest['builder_config'].get('symbols') or [r['symbol'] for r in self.security_master()]
                if symbols is not None and (not symbols or not set(symbols)<=set(available)):
                    raise ArtifactError('INSUFFICIENT_SCOPE: universe symbols')
            else:
                require_symbols(self.commits[domain].rows,symbols)
            if start_session is not None or end_session is not None:
                if domain != 'valuation_daily':
                    raise ArtifactError('INSUFFICIENT_SCOPE: use explicit PIT membership/financial Reader')
                candidates=[r for r in self.commits[domain].rows if symbols is None or r['symbol'] in symbols]
                from axiom_data.financial_coverage import require_range
                days=[r['session'] for r in candidates]
                if not days:raise ArtifactError('INSUFFICIENT_SCOPE: no valuation coverage')
                require_range(start_session or min(days),end_session or max(days),min(days),max(days))
                expected={(r['symbol'],c['session']) for r in candidates for c in self.trading_calendar(
                    start_session=start_session or min(days),end_session=end_session or max(days)) if c['is_open']}
                if not expected<={(r['symbol'],r['session']) for r in candidates}:
                    raise ArtifactError('INSUFFICIENT_SCOPE: valuation gap')
        available = self.schema(domain)
        selected_fields = tuple(fields or available)
        if not selected_fields or len(selected_fields) != len(set(selected_fields)) or any(
            field not in available for field in selected_fields
        ):
            raise ArtifactError("Fact fields must be a non-empty ordered schema subset")
        selected_symbols = set(validate_symbols(symbols)) if symbols is not None else None
        start = validate_session(start_session, "start_session") if start_session else None
        end = validate_session(end_session, "end_session") if end_session else None
        if start is not None and end is not None and start > end:
            raise ArtifactError("fact session interval is reversed")
        if domain=='corporate_actions' and (start is not None or end is not None):
            if any(row.get('observation_state','dated_action')!='dated_action'
                and (selected_symbols is None or row['symbol'] in selected_symbols)
                for row in self.commits[domain].rows):
                raise ArtifactError('INSUFFICIENT_SCOPE: unresolved corporate action observations; inspect without date projection')
        rows = []
        for row in self.session_rows(domain, start, end, selected_symbols):
            row_symbol = row.get("symbol")
            row_session = row.get("session", row.get("effective_date"))
            if selected_symbols is not None and row_symbol not in selected_symbols:
                continue
            if start is not None and (not isinstance(row_session, str) or row_session < start):
                continue
            if end is not None and (not isinstance(row_session, str) or row_session > end):
                continue
            rows.append(ordered_row(row, selected_fields))
        return tuple(rows)


@dataclass(frozen=True, slots=True)
class QlibViewRef:
    view_id: str
    manifest_digest: str


@dataclass(frozen=True, slots=True)
class QlibView:
    ref: QlibViewRef
    manifest: dict[str, Any]


def qlib_symbol(symbol: object) -> str:
    if (
        not isinstance(symbol, str)
        or len(symbol) != 9
        or symbol[6] != "."
        or not symbol[:6].isdigit()
        or symbol[7:] not in {"SH", "SZ"}
    ):
        raise ArtifactError("QlibView symbol is not canonical")
    code, suffix = symbol.split(".")
    return f"{suffix}{code}"


def feature_bytes(start_index: int, values: Sequence[object]) -> bytes:
    encoded = [float(start_index)]
    for value in values:
        if value is None:
            encoded.append(float("nan"))
        elif isinstance(value, bool):
            encoded.append(1.0 if value else 0.0)
        elif isinstance(value, (int, float)) and math.isfinite(float(value)):
            encoded.append(float(value))
        else:
            raise ArtifactError("Qlib market fields must be finite numeric values or null")
    return struct.pack(f"<{len(encoded)}f", *encoded)


def build_qlib_view(
    data_root: str | Path,
    snapshot_id: str,
    *,
    symbols: Sequence[str],
    start_session: str,
    end_session: str,
    fields: Sequence[str] = MARKET_VIEW_FIELDS,
    adjusted_price_view_id: str | None = None,
    price_basis: str = "unadjusted",
    pit_policy: str = "best_effort",
    decision_cutoff: str | None = None,
    created_at: str | None = None,
) -> QlibViewRef:
    """Atomically create one immutable Qlib binary view from an explicit Snapshot."""

    return _build_qlib_view(SnapshotReader(data_root, snapshot_id),symbols=symbols,
        start_session=start_session,end_session=end_session,fields=fields,
        adjusted_price_view_id=adjusted_price_view_id,price_basis=price_basis,pit_policy=pit_policy,
        decision_cutoff=decision_cutoff,created_at=created_at)


def _validate_qlib_inputs(reader, *, symbols, start_session, end_session, fields=MARKET_VIEW_FIELDS,
                          adjusted_price_view_id=None, price_basis='unadjusted', pit_policy='best_effort',
                          decision_cutoff=None, created_at=None, structural_only=False):
    """Shared request admission for first publication and completed-View reuse."""
    data_root = reader.data_root
    selected = validate_symbols(symbols)
    start = validate_session(start_session, "start_session")
    end = validate_session(end_session, "end_session")
    if start > end:
        raise ArtifactError("QlibView session interval is reversed")
    view_fields = tuple(fields)
    if (
        not view_fields
        or len(view_fields) != len(set(view_fields))
        or any(field not in MARKET_VIEW_FIELDS for field in view_fields)
    ):
        raise ArtifactError("QlibView fields must be an ordered unique market field subset")
    if price_basis not in {"unadjusted", "anchor_adjusted"}:
        raise ArtifactError("QlibView price basis is invalid")
    if pit_policy not in {"strict_decision_time", "research_non_pit", "best_effort"}:
        raise ArtifactError("QlibView PIT policy is invalid")
    adjusted = None
    if price_basis == "anchor_adjusted":
        if pit_policy not in {"strict_decision_time", "research_non_pit"}:
            raise ArtifactError("adjusted QlibView requires an explicit PIT policy")
        if adjusted_price_view_id is None:
            raise ArtifactError("adjusted QlibView requires an explicit Derived ref")
        from axiom_data.views import _load_adjusted_price_view

        adjusted = _load_adjusted_price_view(data_root, adjusted_price_view_id,
            checked_reader=reader, structural_only=structural_only)
        if adjusted.manifest["snapshot_ref"]["snapshot_id"] != reader.snapshot.ref.snapshot_id:
            raise ArtifactError("QlibView Derived ref belongs to another Snapshot")
        if adjusted.manifest["scope"] != {
            "symbols": list(selected),
            "start_session": start,
            "end_session": end,
            "interval": "closed",
        }:
            raise ArtifactError("QlibView Derived scope mismatch")
        if decision_cutoff is None:
            raise ArtifactError("adjusted QlibView requires an explicit decision cutoff")
        cutoff = validate_session(decision_cutoff, "decision_cutoff")
        if (
            pit_policy != adjusted.manifest["pit_policy"]
            or cutoff != adjusted.manifest["decision_cutoff"]
        ):
            raise ArtifactError("QlibView PIT policy/cutoff differs from its Derived view")
    elif adjusted_price_view_id is not None:
        raise ArtifactError("unadjusted QlibView must not carry an adjusted Derived ref")

    return selected, start, end, view_fields, adjusted


def _build_qlib_view(reader, *, symbols, start_session, end_session, fields=MARKET_VIEW_FIELDS,
                     adjusted_price_view_id=None, price_basis='unadjusted',pit_policy='best_effort',
                     decision_cutoff=None,created_at=None):
    selected, start, end, view_fields, adjusted = _validate_qlib_inputs(reader,
        symbols=symbols, start_session=start_session, end_session=end_session, fields=fields,
        adjusted_price_view_id=adjusted_price_view_id, price_basis=price_basis,
        pit_policy=pit_policy, decision_cutoff=decision_cutoff, created_at=created_at,
        structural_only=True)
    data_root = reader.data_root

    if adjusted is not None:
        cutoff = adjusted.manifest['decision_cutoff']

    prepared_calendar = prepared_market_view_rows(reader, 'market_qlib', 'trading_calendar', selected, start, end)
    calendar = sorted(
        {
            row["session"]
            for row in (prepared_calendar if prepared_calendar is not None else reader.trading_calendar(
                start_session=start, end_session=end))
            if row["is_open"] is True
        }
    )
    if not calendar:
        raise ArtifactError("QlibView scope has no open calendar sessions")
    prepared_security = prepared_market_view_rows(reader, 'market_qlib', 'security_master', selected, start, end)
    security = {row["symbol"]: row for row in (prepared_security if prepared_security is not None else reader.security_master(selected))}
    if set(security) != set(selected):
        raise ArtifactError("QlibView scope includes an unknown security identity")
    prepared_market = prepared_market_view_rows(reader, 'market_qlib', 'market_daily', selected, start, end)
    market = {(row["session"], row["symbol"]): dict(row) for row in
              (prepared_market if prepared_market is not None else reader.market_daily(selected, start, end))}
    if adjusted is not None:
        adjusted_rows = {
            (row["session"], row["symbol"]): row for row in adjusted.rows
        }
        for key, row in market.items():
            derived = adjusted_rows.get(key)
            if derived is not None:
                for field in _ADJUSTED_PRICE_FIELDS:
                    row[field] = derived[field]

    instrument_scope: list[dict[str, str]] = []
    for symbol in selected:
        identity = security[symbol]
        listed = identity["list_session"]
        if listed is None:
            raise ArtifactError("QlibView requires a known list_session")
        eligible = [
            session
            for session in calendar
            if session >= listed
            and (
                identity["delist_session"] is None
                or session < identity["delist_session"]
            )
        ]
        if not eligible:
            raise ArtifactError(f"QlibView has no identity interval for {symbol!r}")
        instrument_scope.append(
            {
                "symbol": symbol,
                "qlib_symbol": qlib_symbol(symbol),
                "start_session": eligible[0],
                "end_session": eligible[-1],
                "storage_path": f"features/{qlib_symbol(symbol).lower()}",
            }
        )

    outputs: dict[str, bytes] = {
        "calendars/day.txt": ("\n".join(calendar) + "\n").encode("ascii"),
        "instruments/all.txt": (
            "".join(
                f"{item['qlib_symbol']}\t{item['start_session']}\t{item['end_session']}\n"
                for item in instrument_scope
            )
        ).encode("ascii"),
    }
    calendar_index = {session: index for index, session in enumerate(calendar)}
    for item in instrument_scope:
        symbol = item["symbol"]
        first = calendar_index[item["start_session"]]
        last = calendar_index[item["end_session"]]
        sessions = calendar[first : last + 1]
        for field in view_fields:
            values = [market.get((session, symbol), {}).get(field) for session in sessions]
            outputs[f"{item['storage_path']}/{field}.day.bin"] = feature_bytes(
                first, values
            )

    output_files = [
        {"path": path, "content_digest": _digest(content), "bytes": len(content)}
        for path, content in sorted(outputs.items())
    ]
    exporter = {
        "implementation": "axiom_data.consumption.build_qlib_view",
        "revision": _QLIB_EXPORTER_REVISION,
    }
    exporter["digest"] = _digest(_json_bytes(exporter))
    manifest: dict[str, Any] = {
        "artifact_type": "qlib_view",
        "schema_version": "qlib_view.v2" if adjusted is not None else "qlib_view.v1",
        "snapshot_ref": {
            "snapshot_id": reader.snapshot.ref.snapshot_id,
            "identity_digest": reader.snapshot.manifest["identity_digest"],
        },
        "fields": list(view_fields),
        "scope": {
            "symbols": list(selected),
            "start_session": start,
            "end_session": end,
            "interval": "closed",
        },
        "exporter_ref": exporter,
        "exporter_config": {
            "calendar": "union-of-open-SSE-SZSE-sessions",
            "feature_dtype": "little-endian-float32",
            "missing_value": "IEEE-754 NaN",
        },
        "instrument_storage_scope": instrument_scope,
        "output_files": output_files,
        "validation_summary": {
            "status": "PASS",
            "calendar_sessions": len(calendar),
            "instruments": len(instrument_scope),
            "fields": len(view_fields),
        },
    }
    if adjusted is not None:
        manifest.update(
            {
                "derived_refs": [
                    {
                        "view_id": adjusted.ref.view_id,
                        "identity_digest": adjusted.manifest["identity_digest"],
                    }
                ],
                "price_basis": price_basis,
                "anchor_session": adjusted.manifest["anchor_session"],
                "pit_policy": pit_policy,
                "pit_qualification": adjusted.manifest["pit_qualification"],
                "decision_cutoff": cutoff,
                "source_quality_refs": adjusted.manifest["domain_refs"],
            }
        )
    identity_digest = _identity_digest(manifest, "view_id")
    view_id = _derived_identity("qlib", identity_digest)
    manifest["view_id"] = view_id
    manifest["identity_digest"] = identity_digest
    manifest["created_at"] = _timestamp(created_at)

    layout = _layout(data_root)
    target = layout.qlib_exports / view_id

    def prepare(candidate: Path) -> None:
        for relative, content in outputs.items():
            path = candidate.joinpath(*relative.split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            _write_file(path, content)
        _write_manifest(candidate, manifest)

    _publish_directory(layout, target, prepare, identity_digest=identity_digest)
    return _load_qlib_view(layout.root, view_id, checked_reader=reader, structural_only=True).ref


def load_qlib_view(data_root: str | Path, view_id: str) -> QlibView:
    """Verify one exact view, all files, and its source Snapshot closure."""
    return _load_qlib_view(data_root, view_id)


def _load_qlib_view(data_root, view_id, *, checked_reader=None, structural_only=False):

    if isinstance(view_id, str) and view_id.startswith("pr7-fact-"):
        from axiom_data.event_views import load_event_fact_view
        return load_event_fact_view(data_root, view_id)
    if isinstance(view_id, str) and view_id.startswith("pr6-fact-"):
        from axiom_data.financial_views import load_financial_fact_view
        return load_financial_fact_view(data_root, view_id)
    layout = _layout(data_root)
    view_id = _identity("view_id", view_id)
    target = layout.qlib_exports / view_id
    manifest, manifest_digest = _load_manifest(
        layout.root,
        target,
        artifact_type="qlib_view",
        schema_version=("qlib_view.v1", "qlib_view.v2"),
        identity_field="view_id",
        identity=view_id,
    )
    _validate_manifest_identity(manifest, "view_id", "qlib", view_id)
    snapshot_ref = manifest.get("snapshot_ref")
    if not isinstance(snapshot_ref, dict) or not isinstance(
        snapshot_ref.get("snapshot_id"), str
    ):
        raise ArtifactError("QlibView snapshot ref is invalid")
    from axiom_data.views import _view_reader
    reader = _view_reader(layout.root, snapshot_ref["snapshot_id"], checked_reader)
    snapshot = reader.snapshot
    if snapshot_ref != {
        "snapshot_id": snapshot.ref.snapshot_id,
        "identity_digest": snapshot.manifest["identity_digest"],
    }:
        raise ArtifactError("QlibView snapshot ref does not match its artifact")
    if manifest.get("schema_version") == "qlib_view.v2":
        from axiom_data.views import _load_adjusted_price_view

        derived_refs = manifest.get("derived_refs")
        if not isinstance(derived_refs, list) or len(derived_refs) != 1:
            raise ArtifactError("adjusted QlibView Derived refs are invalid")
        ref = derived_refs[0]
        if not isinstance(ref, dict) or not isinstance(ref.get("view_id"), str):
            raise ArtifactError("adjusted QlibView Derived ref is invalid")
        adjusted = _load_adjusted_price_view(layout.root, ref["view_id"],
            checked_reader=reader, structural_only=structural_only)
        if ref != {
            "view_id": adjusted.ref.view_id,
            "identity_digest": adjusted.manifest["identity_digest"],
        }:
            raise ArtifactError("adjusted QlibView Derived ref mismatch")
        if (
            manifest.get("price_basis") != "anchor_adjusted"
            or manifest.get("anchor_session") != adjusted.manifest["anchor_session"]
            or manifest.get("decision_cutoff") != adjusted.manifest["decision_cutoff"]
            or manifest.get("pit_policy") != adjusted.manifest["pit_policy"]
            or manifest.get("pit_qualification")
            != adjusted.manifest["pit_qualification"]
        ):
            raise ArtifactError("adjusted QlibView anchor/PIT metadata is invalid")
        if manifest.get("source_quality_refs") != adjusted.manifest["domain_refs"]:
            raise ArtifactError("adjusted QlibView source/quality refs are invalid")
        if manifest.get("scope") != adjusted.manifest["scope"]:
            raise ArtifactError("adjusted QlibView scope differs from its Derived view")
        validate_session(manifest["decision_cutoff"], "QlibView decision cutoff")
    fields = manifest.get("fields")
    scope = manifest.get("scope")
    instruments = manifest.get("instrument_storage_scope")
    if (
        not isinstance(fields, list)
        or not fields
        or len(fields) != len(set(fields))
        or any(field not in MARKET_VIEW_FIELDS for field in fields)
        or not isinstance(scope, dict)
        or scope.get("interval") != "closed"
        or not isinstance(scope.get("symbols"), list)
        or not isinstance(instruments, list)
        or len(instruments) != len(scope["symbols"])
    ):
        raise ArtifactError("QlibView field or scope metadata is invalid")
    validate_session(scope.get("start_session"), "QlibView scope start")
    validate_session(scope.get("end_session"), "QlibView scope end")
    if (
        validate_symbols(scope["symbols"]) != tuple(scope["symbols"])
        or scope["start_session"] > scope["end_session"]
    ):
        raise ArtifactError("QlibView scope is invalid")
    if [item.get("symbol") for item in instruments if isinstance(item, dict)] != scope[
        "symbols"
    ]:
        raise ArtifactError("QlibView instrument order does not match its scope")
    for item in instruments:
        if (
            not isinstance(item, dict)
            or item.get("qlib_symbol") != qlib_symbol(item.get("symbol", ""))
            or item.get("storage_path")
            != f"features/{item['qlib_symbol'].lower()}"
        ):
            raise ArtifactError("QlibView instrument mapping is invalid")
        validate_session(item.get("start_session"), "QlibView instrument start")
        validate_session(item.get("end_session"), "QlibView instrument end")
        if not (
            scope["start_session"]
            <= item["start_session"]
            <= item["end_session"]
            <= scope["end_session"]
        ):
            raise ArtifactError("QlibView instrument scope is invalid")
    exporter = manifest.get("exporter_ref")
    if not isinstance(exporter, dict) or set(exporter) != {
        "implementation",
        "revision",
        "digest",
    }:
        raise ArtifactError("QlibView exporter ref is invalid")
    descriptor = {
        "implementation": exporter["implementation"],
        "revision": exporter["revision"],
    }
    if descriptor != {
        "implementation": "axiom_data.consumption.build_qlib_view",
        "revision": _QLIB_EXPORTER_REVISION,
    } or exporter["digest"] != _digest(_json_bytes(descriptor)):
        raise ArtifactError("QlibView exporter ref digest mismatch")
    if manifest.get("exporter_config") != {
        "calendar": "union-of-open-SSE-SZSE-sessions",
        "feature_dtype": "little-endian-float32",
        "missing_value": "IEEE-754 NaN",
    }:
        raise ArtifactError("QlibView exporter config is invalid")
    output_files = manifest.get("output_files")
    if not isinstance(output_files, list):
        raise ArtifactError("QlibView output manifest is invalid")
    required_paths = {"calendars/day.txt", "instruments/all.txt"}
    required_paths.update(
        f"{item['storage_path']}/{field}.day.bin"
        for item in instruments
        for field in fields
    )
    declared_paths = [
        entry.get("path") for entry in output_files if isinstance(entry, dict)
    ]
    if (
        len(declared_paths) != len(output_files)
        or len(declared_paths) != len(required_paths)
        or set(declared_paths) != required_paths
    ):
        raise ArtifactError("QlibView output file set is invalid")
    expected: dict[str, str] = {}
    for entry in output_files:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise ArtifactError("QlibView output entry is invalid")
        path = _relative_file(layout.root, target, entry["path"])
        if not path.is_file():
            raise ArtifactError("QlibView output file is missing")
        content = path.read_bytes()
        if entry.get("content_digest") != _digest(content) or entry.get("bytes") != len(
            content
        ):
            raise ArtifactError("QlibView output file digest mismatch")
        if entry["path"].endswith(".day.bin") and (
            len(content) < 8 or len(content) % 4
        ):
            raise ArtifactError("QlibView feature file framing is invalid")
        expected[entry["path"]] = entry["content_digest"]
    if _content_tree_digests(target) != expected:
        raise ArtifactError("QlibView contains undeclared output content")
    if not isinstance(manifest.get("created_at"), str):
        raise ArtifactError("QlibView created_at is missing")
    _timestamp(manifest["created_at"])
    calendar_path = _relative_file(
        layout.root, target, "calendars/day.txt"
    )
    calendar = tuple(calendar_path.read_text(encoding="ascii").splitlines())
    if not calendar or calendar != tuple(sorted(set(calendar))):
        raise ArtifactError("QlibView calendar is not ordered and unique")
    for value in calendar:
        validate_session(value, "QlibView calendar session")
    instruments_path = _relative_file(
        layout.root, target, "instruments/all.txt"
    )
    expected_instruments = "".join(
        f"{item['qlib_symbol']}\t{item['start_session']}\t{item['end_session']}\n"
        for item in instruments
    )
    if instruments_path.read_text(encoding="ascii") != expected_instruments:
        raise ArtifactError("QlibView instruments file does not match its manifest")
    if manifest.get("validation_summary") != {
        "status": "PASS",
        "calendar_sessions": len(calendar),
        "instruments": len(instruments),
        "fields": len(fields),
    }:
        raise ArtifactError("QlibView validation summary is invalid")
    return QlibView(QlibViewRef(view_id, manifest_digest), manifest)


class QlibViewReader:
    """Read the exact binary field layout emitted by build_qlib_view()."""

    def __init__(self, data_root: str | Path, view_id: str) -> None:
        self.data_root = Path(data_root)
        self.view = load_qlib_view(self.data_root, view_id)
        self.path = (_layout(self.data_root).derived_commits("pr7_fact") / view_id
                     if view_id.startswith("pr7-fact-") else _layout(self.data_root).derived_commits("pr6_fact") / view_id
                     if view_id.startswith("pr6-fact-") else _layout(self.data_root).qlib_exports / view_id)

    def fact_metadata(self):
        if self.view.manifest.get('artifact_type') == 'pr7_fact_view':
            return {'view_id':self.view.ref.view_id,'snapshot_ref':self.view.manifest['snapshot_ref'],
                    'validated_scope':self.view.manifest['validated_scope'],
                    'rows':tuple({'symbol':r['symbol'],'session':r['session'],'fields':r['facts']} for r in self.view.rows)}
        if self.view.manifest.get('artifact_type') != 'pr6_fact_view':
            raise ArtifactError('financial FactView metadata required')
        if self.view.manifest['schema_version']=='pr6_fact_view.v1':
            raise ArtifactError('METADATA_NOT_IN_V1_CONTRACT')
        return {'view_id':self.view.ref.view_id,'snapshot_ref':self.view.manifest['snapshot_ref'],
                'industry_mapping':self.view.manifest['industry_mapping'],
                'validated_scope':self.view.manifest['validated_scope'],
                'rows':tuple({'symbol':r['symbol'],'session':r['session'],'fields':r['facts']} for r in self.view.rows)}

    def calendar(self) -> tuple[str, ...]:
        layout = _layout(self.data_root)
        path = _relative_file(
            layout.root, self.path, "calendars/day.txt"
        )
        content = path.read_text(encoding="ascii")
        return tuple(line for line in content.splitlines() if line)

    def instrument_mapping(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (item["symbol"], item["qlib_symbol"])
            for item in self.view.manifest["instrument_storage_scope"]
        )

    def market_daily(self, *, include_missing: bool = False) -> tuple[dict[str, Any], ...]:
        calendar = self.calendar()
        fields = tuple(self.view.manifest["fields"])
        rows: list[dict[str, Any]] = []
        for item in self.view.manifest["instrument_storage_scope"]:
            values_by_field: dict[str, tuple[float, ...]] = {}
            first_index: int | None = None
            for field in fields:
                layout = _layout(self.data_root)
                path = _relative_file(
                    layout.root,
                    self.path,
                    f"{item['storage_path']}/{self.view.manifest.get('qlib_field_mapping', {}).get(field, field)}.day.bin",
                )
                content = path.read_bytes()
                if len(content) < 8 or len(content) % 4:
                    raise ArtifactError("Qlib feature file has invalid float32 framing")
                values = struct.unpack(f"<{len(content) // 4}f", content)
                current_first = int(values[0])
                if values[0] != current_first or not 0 <= current_first < len(calendar):
                    raise ArtifactError("Qlib feature file has an invalid calendar offset")
                if first_index is None:
                    first_index = current_first
                elif first_index != current_first:
                    raise ArtifactError("Qlib feature fields have inconsistent offsets")
                values_by_field[field] = values[1:]
            assert first_index is not None
            lengths = {len(values) for values in values_by_field.values()}
            if len(lengths) != 1:
                raise ArtifactError("Qlib feature fields have inconsistent lengths")
            length = lengths.pop()
            if first_index + length > len(calendar):
                raise ArtifactError("Qlib feature values exceed their calendar")
            if (
                calendar[first_index] != item["start_session"]
                or calendar[first_index + length - 1] != item["end_session"]
            ):
                raise ArtifactError("Qlib feature range does not match instrument scope")
            for offset in range(length):
                if (self.view.manifest['schema_version'] in {'pr7_fact_view.v2','pr7_fact_view.v3'}
                    and calendar[first_index+offset] not in item['valid_sessions']):
                    continue
                values: dict[str, Any] = {}
                for field in fields:
                    value = values_by_field[field][offset]
                    if math.isnan(value):
                        values[field] = None
                    elif field == "is_suspended":
                        values[field] = bool(value)
                    else:
                        values[field] = value
                if include_missing or any(value is not None for value in values.values()):
                    rows.append(
                        {
                            "session": calendar[first_index + offset],
                            "symbol": item["symbol"],
                            **values,
                        }
                    )
        return tuple(sorted(rows, key=lambda row: (row["session"], row["symbol"])))


def compare_direct_and_qlib(
    data_root: str | Path,
    snapshot_id: str,
    view_id: str,
    *,
    relative_tolerance: float = 1e-6,
) -> dict[str, Any]:
    """Compare exact keys/schema/missingness and tolerant float values."""

    direct = SnapshotReader(data_root, snapshot_id)
    view = QlibViewReader(data_root, view_id)
    manifest = view.view.manifest
    if manifest["snapshot_ref"]["snapshot_id"] != direct.snapshot.ref.snapshot_id:
        raise ArtifactError("QlibView belongs to another Snapshot")
    scope = manifest["scope"]
    fields = tuple(manifest["fields"])
    direct_rows = {
        (row["session"], row["symbol"]): row
        for row in direct.market_daily(
            scope["symbols"], scope["start_session"], scope["end_session"]
        )
    }
    if manifest.get("schema_version") == "qlib_view.v2":
        from axiom_data.views import load_adjusted_price_view

        adjusted = load_adjusted_price_view(
            data_root, manifest["derived_refs"][0]["view_id"]
        )
        adjusted_rows = {
            (row["session"], row["symbol"]): row for row in adjusted.rows
        }
        for key, row in direct_rows.items():
            derived = adjusted_rows.get(key)
            if derived is not None:
                for field in _ADJUSTED_PRICE_FIELDS:
                    row[field] = derived[field]
        adjusted_ref = {
            "view_id": adjusted.ref.view_id,
            "manifest_digest": adjusted.ref.manifest_digest,
            "identity_digest": adjusted.manifest["identity_digest"],
            "content_digest": adjusted.manifest["output"]["content_digest"],
            "anchor_session": adjusted.manifest["anchor_session"],
            "pit_policy": adjusted.manifest["pit_policy"],
            "pit_qualification": adjusted.manifest["pit_qualification"],
            "decision_cutoff": adjusted.manifest["decision_cutoff"],
        }
        pit_binding = {
            "pit_policy": adjusted.manifest["pit_policy"],
            "pit_qualification": adjusted.manifest["pit_qualification"],
            "decision_cutoff": adjusted.manifest["decision_cutoff"],
            "anchor_session": adjusted.manifest["anchor_session"],
        }
    else:
        adjusted_ref = None
        pit_binding = None
    view_rows = {
        (row["session"], row["symbol"]): row for row in view.market_daily()
    }
    expected_calendar = tuple(
        sorted(
            {
                row["session"]
                for row in direct.trading_calendar(
                    start_session=scope["start_session"],
                    end_session=scope["end_session"],
                )
                if row["is_open"] is True
            }
        )
    )
    expected_mapping = tuple((symbol, qlib_symbol(symbol)) for symbol in scope["symbols"])
    mismatches: list[dict[str, Any]] = []
    for key in sorted(set(direct_rows) | set(view_rows)):
        if key not in direct_rows or key not in view_rows:
            mismatches.append({"key": list(key), "reason": "key_presence"})
            continue
        for field in fields:
            left = direct_rows[key][field]
            right = view_rows[key][field]
            if left is None or right is None:
                equal = left is None and right is None
            elif isinstance(left, bool):
                equal = left is right
            else:
                equal = math.isclose(
                    float(left), float(right), rel_tol=relative_tolerance, abs_tol=1e-6
                )
            if not equal:
                mismatches.append(
                    {
                        "key": list(key),
                        "field": field,
                        "direct": left,
                        "qlib": right,
                        "reason": "value_or_null",
                    }
                )
    checks = {
        "snapshot": manifest["snapshot_ref"]["snapshot_id"]
        == direct.snapshot.ref.snapshot_id,
        "field_order": fields
        == tuple(field for field in direct.schema("market_daily") if field in fields),
        "calendar": view.calendar() == expected_calendar,
        "instrument_mapping": view.instrument_mapping() == expected_mapping,
        "keys": set(direct_rows) == set(view_rows),
        "values_and_nulls": not mismatches,
    }
    qlib_report_ref = {
        "view_id": view.view.ref.view_id,
        "manifest_digest": view.view.ref.manifest_digest,
        "identity_digest": view.view.manifest["identity_digest"],
    }
    if manifest.get("schema_version") == "qlib_view.v2":
        qlib_report_ref.update(
            {
                "content_digest": _digest(
                    _json_bytes(
                        [
                            [entry["path"], entry["content_digest"]]
                            for entry in view.view.manifest["output_files"]
                        ]
                    )
                ),
                "anchor_session": manifest["anchor_session"],
                "pit_policy": manifest["pit_policy"],
                "pit_qualification": manifest["pit_qualification"],
                "decision_cutoff": manifest["decision_cutoff"],
                "derived_view_id": manifest["derived_refs"][0]["view_id"],
            }
        )
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "source_snapshot_ref": {
            "snapshot_id": direct.snapshot.ref.snapshot_id,
            "manifest_digest": direct.snapshot.ref.manifest_digest,
            "identity_digest": direct.snapshot.manifest["identity_digest"],
        },
        "qlib_view_ref": qlib_report_ref,
        "adjusted_price_view_ref": adjusted_ref,
        "pit_binding": pit_binding,
        "checks": checks,
        "scope": scope,
        "fields": list(fields),
        "direct_keys": len(direct_rows),
        "qlib_keys": len(view_rows),
        "relative_tolerance": relative_tolerance,
        "mismatches": mismatches,
    }


def _report_mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ArtifactError(f"PR3 report {name} must be an object")
    return value


def _report_ref(
    value: object, fields: Sequence[str], name: str
) -> dict[str, Any]:
    mapping = _report_mapping(value, name)
    missing = [field for field in fields if field not in mapping]
    if missing:
        raise ArtifactError(f"PR3 report {name} is missing immutable refs")
    return {field: mapping[field] for field in fields}


def _validate_pass_comparison(report: Mapping[str, Any], name: str) -> None:
    if report.get("status") != "PASS":
        return
    checks = _report_mapping(report.get("checks"), f"{name} checks")
    if (
        not checks
        or any(value is not True for value in checks.values())
        or report.get("mismatches") != []
    ):
        raise ArtifactError(f"PR3 {name} PASS is inconsistent with its results")


def validate_pr3_report_refs(
    run_manifest: Mapping[str, Any],
    direct_qlib_report: Mapping[str, Any],
    offline_rebuild_report: Mapping[str, Any],
) -> None:
    """Cross-check the immutable refs in the three fixed PR3 evidence reports."""

    run = _report_mapping(run_manifest, "run manifest")
    direct = _report_mapping(direct_qlib_report, "direct/Qlib report")
    offline = _report_mapping(offline_rebuild_report, "offline rebuild report")
    _validate_pass_comparison(direct, "direct/Qlib report")
    run_snapshot_value = _report_mapping(run.get("snapshot"), "run snapshot ref")
    run_view_value = _report_mapping(run.get("qlib_view"), "run QlibView ref")
    run_snapshot = _report_ref(
        run_snapshot_value,
        ("snapshot_id", "manifest_digest", "identity_digest"),
        "run snapshot ref",
    )
    run_view = _report_ref(
        run_view_value,
        ("view_id", "manifest_digest", "identity_digest"),
        "run QlibView ref",
    )
    direct_snapshot = _report_ref(
        direct.get("source_snapshot_ref"),
        tuple(run_snapshot),
        "direct source Snapshot ref",
    )
    direct_view = _report_ref(
        direct.get("qlib_view_ref"), tuple(run_view), "direct QlibView ref"
    )
    if direct_snapshot != run_snapshot or direct_view != run_view:
        raise ArtifactError("PR3 direct/Qlib report refs do not match the run manifest")
    if direct.get("scope") != run_view_value.get("scope"):
        raise ArtifactError("PR3 direct/Qlib report scope does not match the run manifest")
    if direct.get("fields") != run_view_value.get("fields"):
        raise ArtifactError("PR3 direct/Qlib report fields do not match the run manifest")

    run_commits_value = _report_mapping(run.get("domain_commits"), "run DomainCommit refs")
    run_commits = {
        domain: _report_ref(
            run_commits_value.get(domain),
            (
                "domain_commit_id",
                "manifest_digest",
                "identity_digest",
                "logical_content_digest",
                "contract_digest",
            ),
            f"run {domain} DomainCommit ref",
        )
        for domain in ("trading_calendar", "security_master", "market_daily")
    }
    raw_fields = (
        "role",
        "raw_batch_id",
        "manifest_digest",
        "payload_digest",
    )
    run_raw_value = run.get("raw_batches")
    if not isinstance(run_raw_value, list):
        raise ArtifactError("PR3 run RawBatch refs must be a list")
    run_raw = [
        _report_ref(value, raw_fields, "run RawBatch ref") for value in run_raw_value
    ]
    run_raw_summary = {
        "ordered_raw_batch_ids": [ref["raw_batch_id"] for ref in run_raw],
        "ordered_refs_digest": _digest(_json_bytes(run_raw)),
    }
    expected_root_map = {
        "domain_commits": {
            domain: ref["domain_commit_id"] for domain, ref in run_commits.items()
        },
        "snapshot": run_snapshot["snapshot_id"],
        "qlib_view": run_view["view_id"],
    }
    if offline.get("source_root_artifact_map") != expected_root_map:
        raise ArtifactError("PR3 offline source root artifact map is inconsistent")

    source_refs = _report_mapping(
        offline.get("source_artifact_refs"), "offline source artifact refs"
    )
    rebuilt_refs = _report_mapping(
        offline.get("rebuilt_artifact_refs"), "offline rebuilt artifact refs"
    )

    def checked_artifact_refs(
        refs: Mapping[str, Any], name: str
    ) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any], dict[str, Any]]:
        snapshot_ref = _report_ref(
            refs.get("snapshot"), tuple(run_snapshot), f"{name} Snapshot ref"
        )
        view_ref = _report_ref(
            refs.get("qlib_view"), tuple(run_view), f"{name} QlibView ref"
        )
        commits_value = _report_mapping(
            refs.get("domain_commits"), f"{name} DomainCommit refs"
        )
        commits = {
            domain: _report_ref(
                commits_value.get(domain), tuple(run_commits[domain]), f"{name} {domain} ref"
            )
            for domain in run_commits
        }
        raw = _report_ref(
            refs.get("raw_batches"),
            ("ordered_raw_batch_ids", "ordered_refs_digest"),
            f"{name} RawBatch refs",
        )
        raw_ids = raw["ordered_raw_batch_ids"]
        if not isinstance(raw_ids, list) or any(
            not isinstance(raw_id, str) for raw_id in raw_ids
        ):
            raise ArtifactError(f"PR3 report {name} RawBatch IDs must be a list")
        return snapshot_ref, commits, raw, view_ref

    source_snapshot, source_commits, source_raw, source_view = checked_artifact_refs(
        source_refs, "offline source"
    )
    rebuilt_snapshot, rebuilt_commits, rebuilt_raw, rebuilt_view = checked_artifact_refs(
        rebuilt_refs, "offline rebuilt"
    )
    if (
        source_snapshot != run_snapshot
        or source_commits != run_commits
        or source_raw != run_raw_summary
        or source_view != run_view
    ):
        raise ArtifactError("PR3 offline source refs do not match the run manifest")

    rebuilt_map = offline.get("rebuilt_root_artifact_map")
    if not isinstance(rebuilt_map, Mapping) or rebuilt_map != {
        "domain_commits": {
            domain: ref["domain_commit_id"]
            for domain, ref in rebuilt_commits.items()
        },
        "snapshot": rebuilt_snapshot["snapshot_id"],
        "qlib_view": rebuilt_view["view_id"],
    }:
        raise ArtifactError("PR3 offline rebuilt root artifact map is inconsistent")

    offline_direct = _report_mapping(
        offline.get("direct_qlib_equivalence"), "offline direct/Qlib report"
    )
    _validate_pass_comparison(offline_direct, "offline direct/Qlib report")
    if (
        _report_ref(
            offline_direct.get("source_snapshot_ref"),
            tuple(run_snapshot),
            "offline direct Snapshot ref",
        )
        != rebuilt_snapshot
        or _report_ref(
            offline_direct.get("qlib_view_ref"),
            tuple(run_view),
            "offline direct QlibView ref",
        )
        != rebuilt_view
    ):
        raise ArtifactError("PR3 offline direct/Qlib refs do not match rebuilt refs")

    expected_identity = {
        "root_artifact_maps": offline.get("source_root_artifact_map")
        == offline.get("rebuilt_root_artifact_map"),
        "snapshot": source_snapshot["snapshot_id"]
        == rebuilt_snapshot["snapshot_id"]
        and source_snapshot["identity_digest"]
        == rebuilt_snapshot["identity_digest"],
        "domain_commits": all(
            source_commits[domain]["domain_commit_id"]
            == rebuilt_commits[domain]["domain_commit_id"]
            and source_commits[domain]["identity_digest"]
            == rebuilt_commits[domain]["identity_digest"]
            for domain in run_commits
        ),
        "raw_batches": source_raw == rebuilt_raw,
        "qlib_view": source_view["view_id"] == rebuilt_view["view_id"]
        and source_view["identity_digest"] == rebuilt_view["identity_digest"],
    }
    if offline.get("identity_equality") != expected_identity:
        raise ArtifactError("PR3 offline identity equality summary is inconsistent")
    logical = _report_mapping(offline.get("logical_equality"), "offline logical equality")
    catalog = _report_mapping(offline.get("catalog_rebuild"), "offline catalog rebuild")
    if offline.get("status") == "PASS" and (
        not all(expected_identity.values())
        or logical.get("canonical_rows") is not True
        or catalog.get("status") != "PASS"
        or offline_direct.get("status") != "PASS"
    ):
        raise ArtifactError("PR3 offline PASS is not supported by its artifact refs")


__all__ = [
    "MARKET_VIEW_FIELDS",
    "QlibView",
    "QlibViewReader",
    "QlibViewRef",
    "SnapshotReader",
    "build_qlib_view",
    "compare_direct_and_qlib",
    "load_qlib_view",
]


def request_coverage(reader,domain,symbol,session):
    """Admit bounded requests, including explicit empty supplier responses."""
    known=getattr(reader,'_event_known_symbols',None)
    if known is None:
        known={r['symbol'] for r in reader.security_master()}
        reader._event_known_symbols=known
    if symbol not in known:raise ArtifactError('unknown security')
    if domain not in reader.commits:raise ArtifactError('Snapshot lacks event domain')
    # Only this checked Reader owns the index. New Readers validate their closure
    # again; no disk cache or execution report can supply request authority.
    if not hasattr(reader,'_pr7_request_intervals'):reader._pr7_request_intervals={}
    if domain not in reader._pr7_request_intervals:
        identity=reader.commits[domain].ref.commit_id;refs=set()
        while identity:
            node=reader._verified_lineage[(domain,identity)]
            refs.update(node['raw_batch_ids'])
            identity=node['parent_commit_id']
        intervals={}
        for ref in sorted(refs):
            raw=load_raw_batch(reader.data_root,ref);params=raw.manifest['request']['params']
            intervals.setdefault(params['ts_code'],[]).append((params['start_date'],params['end_date']))
        reader._pr7_request_intervals[domain]=intervals
    day=session.replace('-','')
    if any(start<=day<=end for start,end in reader._pr7_request_intervals[domain].get(symbol,())):return
    raise ArtifactError('INSUFFICIENT_SCOPE: no bounded supplier request for '+symbol+' '+session)


def dependency_session(reader, domain, session):
    """Plan against the source bound frozen in this Snapshot's DomainCommit.

    An absent bound retains legacy strict request coverage. Never infer a
    cutoff from returned rows or a different security's observed coverage.
    """
    if domain not in reader.commits:
        raise ArtifactError('Snapshot lacks event domain')
    end = reader.commits[domain].manifest['builder_config'].get('end_session')
    return min(session, validate_session(end, 'source end_session')) if end is not None else session


def exchange_sessions(reader,symbols,start,end):
    """Validate each requested exchange for every calendar day before projection."""
    from datetime import date, timedelta
    from axiom_data.domains.market import _symbol
    start=validate_session(start,'start_session');end=validate_session(end,'end_session')
    if start>end:raise ArtifactError('reversed calendar scope')
    master=reader.security_master();calendar=reader.trading_calendar()
    days=[];day=date.fromisoformat(start)
    while day<=date.fromisoformat(end):
        days.append(day.isoformat());day+=timedelta(days=1)
    result={}
    for symbol in validate_symbols(symbols):
        identities=[r for r in master if r['symbol']==symbol]
        if not identities:raise ArtifactError('unknown security')
        if len(identities)!=1:raise ArtifactError('ambiguous security exchange mapping')
        exchange=identities[0]['exchange']
        if exchange not in {'SSE','SZSE'}:raise ArtifactError('unknown exchange')
        _symbol(symbol,exchange)
        rows=[r for r in calendar if r['exchange']==exchange and start<=r['session']<=end]
        by_day={r['session']:r for r in rows}
        if len(rows)!=len(by_day) or set(by_day)!=set(days):
            raise ArtifactError('INSUFFICIENT_SCOPE: '+exchange+' calendar coverage')
        if any(type(r['is_open']) is not bool for r in rows):raise ArtifactError('invalid exchange session state')
        result[symbol]={'exchange':exchange,'sessions':[d for d in days if by_day[d]['is_open']]}
    return result


def select_latest_report(period_winners,target_session):
    """PIT has selected each period's revision; economic period now takes precedence."""
    candidates=[r for r in period_winners if r['report_period']<=target_session]
    return max(candidates,key=lambda r:r['report_period']) if candidates else None


def leaf_facts(reader,leaf,*,symbol,target_session,knowledge_cutoff,pit_policy):
    from axiom_data.domains.events import LEAF_DOMAINS, DAILY_DOMAINS
    from axiom_data.pit import instant
    if leaf not in LEAF_DOMAINS:raise ArtifactError('unknown event leaf')
    validate_session(target_session,'target_session');instant(knowledge_cutoff)
    domain=LEAF_DOMAINS[leaf];field=leaf.split('.',1)[1]
    if not exchange_sessions(reader,[symbol],target_session,target_session)[symbol]['sessions']:
        raise ArtifactError('CLOSED_SESSION: '+symbol+' '+target_session)
    source_session=dependency_session(reader,domain,target_session)
    request_coverage(reader,domain,symbol,source_session)
    bounds={'start_session':source_session,'end_session':source_session} if domain in DAILY_DOMAINS else {}
    selected=reader.as_of(domain,symbols=[symbol],pit_policy=pit_policy,knowledge_cutoff=knowledge_cutoff,**bounds)
    return event_leaf_metadata(reader,leaf,symbol,target_session,knowledge_cutoff,pit_policy,selected,source_session=source_session)


def event_leaf_metadata(reader,leaf,symbol,target_session,knowledge_cutoff,pit_policy,selected,*,source_session=None,value_unit=None):
    from axiom_data.domains.events import LEAF_DOMAINS, DAILY_DOMAINS
    from axiom_data.pit import instant, fingerprint
    from axiom_data.contracts import load_contract
    domain=LEAF_DOMAINS[leaf];field=leaf.split('.',1)[1]
    candidates=[r for r in selected if (r['session']==target_session if domain in DAILY_DOMAINS else
        domain=='forecast_observations' or r['report_period']<=target_session)]
    row=(select_latest_report(selected,target_session) if domain in {'holder_count_events','top_holders_reports'} else
         max(candidates,key=lambda r:(r['announcement'] or r['session'],r['report_period'] or r['session'])) if candidates else None)
    value=row['values'][field] if row else None
    reason=row['missing_reasons'].get(field) if row else 'no_observation_at_cutoff'
    if domain in DAILY_DOMAINS and source_session is not None and source_session < target_session:
        reason='source_scope_not_available'
    contract_version=reader.commits[domain].ref.contract_version
    metadata={'leaf':leaf,'symbol':symbol,'target_session':target_session,'snapshot_id':reader.snapshot.ref.snapshot_id,
        'contract_version':contract_version,'domain_commit_id':reader.commits[domain].ref.commit_id,
        'value':value,'unit':value_unit if value_unit is not None else load_contract(contract_version)['value_units'][field],
        'validity':'valid' if value is not None else 'missing','missing_reason':reason,
        'pit_policy':pit_policy,'knowledge_cutoff':instant(knowledge_cutoff).isoformat(),
        'pit_qualification':row['pit_qualification'] if row else 'unknown',
        'usable_at':row['usable_from'] if row else None,
        'source_ref':row['source_ref'] if row else None,'revision_ref':row['revision_id'] if row else None,
        'observation_ref':row['observation_ref'] if row else None,
        'report_period':row['report_period'] if row else None,
        'quality':row['group_completeness'] if row else 'no_visible_fact'}
    if domain=='top_holders_reports':
        metadata['holders']=row['holders'] if row else []
        metadata['component_refs']=[{'report_revision':row['revision_id'],'holder_id':h['holder_id']} for h in row['holders']] if row else []
        metadata['derived_ref']=fingerprint({'snapshot':reader.snapshot.ref.snapshot_id,'revision':row['revision_id'],'field':field,'value':value}) if row else None
    if domain=='forecast_observations':metadata['source_kind']='company_performance_forecast'
    return metadata



# Compatibility names for historical consumers.
_session = validate_session
_symbols = validate_symbols
_ordered_row = ordered_row
_qlib_symbol = qlib_symbol
_feature_bytes = feature_bytes
