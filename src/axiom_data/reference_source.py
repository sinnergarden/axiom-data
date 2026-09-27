"""Small Tushare adapters for the frozen reference reference source profile."""

from __future__ import annotations
from axiom_data.deprecated.resources import collector_revision, builder_config as historical_builder_config, resource_file, profile_generation, historical_profile, source_reference


import json
from collections.abc import Mapping, Sequence
from importlib.resources import files
from pathlib import Path
from typing import Any

from axiom_data.artifacts import (
    ArtifactConflictError,
    ArtifactError,
    MarketDomainBuilder,
    RawBatch,
    RawBatchRef,
    _RAW_BATCH_WRITE_SCHEMA,
    _digest,
    _json_bytes,
    _json_copy,
    load_raw_batch,
    validate_domain_commit_closure,
    write_raw_batch,
)
from axiom_data.tushare import (
    _merge_canonical,
    _optional_float,
    _response_records,
    _retrieved_at,
    _scaled_float,
    _source_date,
    _source_symbol,
    _tushare_raw_batch_id,
)
from axiom_data.domains.market import _checked_security_identity_state


_PROFILE_NAME = "tushare_reference.v1.json"
_PROFILE_ENDPOINT_FIELDS = (
    "source_profile_ref",
    "canonical_domains",
    "primary_key",
    "represented_session_field",
    "fields",
    "units",
    "field_mapping",
    "missing_semantics",
    "date_semantics",
    "terminal_history_risk",
)
_EXPECTED_ENDPOINTS = {
    "security_status": {"daily", "suspend_d"},
    "price_limits": {"stk_limit"},
    "corporate_actions": {"dividend"},
    "adjustment_factors": {"adj_factor"},
    "benchmark_daily": {"index_daily"},
    "security_capital": {"daily_basic"},
}
_SECURITY_SESSION_SCOPE_POLICY = "exchange_security.v1"
_SECURITY_SESSION_SCOPE_DOMAINS = frozenset({"adjustment_factors", "security_capital"})


def load_reference_source_profile(version="tushare_reference.v1") -> dict[str, Any]:
    content = resource_file("source_profiles", version + ".json").read_bytes()
    profile = json.loads(content)
    if not isinstance(profile, dict) or profile.get("profile_version") != version or profile_generation(version) != "tushare_reference.v1":
        raise ArtifactError("packaged reference source profile is invalid")
    return profile


def _normalized_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    pit = profile.get("pit_classification")
    endpoints = profile.get("endpoints")
    if not isinstance(pit, Mapping) or not isinstance(endpoints, Mapping):
        raise ArtifactError("reference source profile semantics are invalid")
    required_pit = (
        "retrieved_at",
        "represented_session",
        "historical_availability",
        "revision_capability",
    )
    try:
        return _json_copy(
            {
                "profile_version": profile["profile_version"],
                "pit_classification": {name: pit[name] for name in required_pit},
                "endpoints": {
                    endpoint: {
                        name: definition[name] for name in _PROFILE_ENDPOINT_FIELDS
                    }
                    for endpoint, definition in endpoints.items()
                },
            }
        )
    except (KeyError, TypeError) as exc:
        raise ArtifactError("reference source profile semantics are incomplete") from exc


def reference_source_profile_digest(profile: Mapping[str, Any] | None = None) -> str:
    return _digest(_json_bytes(_normalized_profile(profile or load_reference_source_profile())))


def _endpoint_profile(endpoint: str) -> dict[str, Any]:
    value = load_reference_source_profile().get("endpoints", {}).get(endpoint)
    if not isinstance(value, dict):
        raise ArtifactError(f"unsupported reference Tushare endpoint: {endpoint!r}")
    return value


class TushareReferenceCollector:
    """Freeze one allow-listed endpoint response for one explicit reference domain."""

    implementation_revision = "tushare-reference-collector.v1"

    def __init__(self, data_root: str | Path, client: object | None = None) -> None:
        self.data_root = Path(data_root)
        self.client = client

    def _client(self) -> object:
        if self.client is not None:
            return self.client
        from axiom_data.tushare import TushareCollector

        return TushareCollector(self.data_root)._client()

    def collect(
        self,
        domain: str,
        endpoint: str,
        params: Mapping[str, Any],
        *,
        retrieved_at: str | None = None,
        profile_version: str = "tushare_reference.v1",
        _resume_historical: bool = False,
    ) -> RawBatchRef:
        if historical_profile(profile_version) and not _resume_historical:
            raise ArtifactError("historical SourceProfiles are read-only")
        if domain not in _EXPECTED_ENDPOINTS or endpoint not in _EXPECTED_ENDPOINTS[domain]:
            raise ArtifactError("endpoint is not assigned to the requested reference domain")
        profile = load_reference_source_profile(profile_version)
        endpoint_profile = profile["endpoints"][endpoint]
        if domain not in endpoint_profile["canonical_domains"]:
            raise ArtifactError("source profile does not authorize the domain mapping")
        if not isinstance(params, Mapping):
            raise ArtifactError("Tushare request params must be a mapping")
        request_params = _json_copy(dict(params))
        fields_value = endpoint_profile["fields"]
        if not isinstance(fields_value, list) or any(
            not isinstance(field, str) for field in fields_value
        ):
            raise ArtifactError("reference endpoint fields are invalid")
        query = getattr(self._client(), "query", None)
        if not callable(query):
            raise ArtifactError("Tushare client must provide query(endpoint, ...)")
        records = _response_records(
            query(endpoint, fields=",".join(fields_value), **request_params)
        )
        payload = _json_bytes(records)
        observed_at = _retrieved_at(retrieved_at)
        request = {"endpoint": endpoint, "params": request_params, "fields": fields_value}
        profile_digest = reference_source_profile_digest(profile)
        from axiom_data.source_completeness import source_profile_completeness_binding
        completeness = source_profile_completeness_binding(profile['profile_version'], profile_digest)
        identity_fields = {
            "source_completeness": completeness,
            "domain": domain,
            "source_profile_ref": endpoint_profile["source_profile_ref"],
            "source_profile_version": profile["profile_version"],
            "source_profile_digest": profile_digest,
            "collector_code_ref": f"axiom-data.{self.implementation_revision}",
            "request": request,
            "retrieved_at": observed_at,
            "payload_digest": _digest(payload),
        }
        raw_id = _tushare_raw_batch_id(
            f"{domain}-{endpoint}", _RAW_BATCH_WRITE_SCHEMA, identity_fields
        )
        pit = profile["pit_classification"]
        return write_raw_batch(
            self.data_root,
            raw_id,
            domain=domain,
            source_profile=endpoint_profile["source_profile_ref"],
            source_profile_version=profile["profile_version"],
            source_profile_digest=profile_digest,
            request=request,
            retrieved_at=observed_at,
            payload=payload,
            collector_code=f"axiom-data.{self.implementation_revision}",
            summary={
                "source_completeness": completeness,
                "rows": len(records),
                "response_fields": fields_value,
                "represented_session_field": endpoint_profile["represented_session_field"],
                "historical_availability": pit["historical_availability"],
                "revision_capability": pit["revision_capability"],
            },
        )


def _raw_tables(
    data_root: Path,
    domain: str,
    raw_batches: Sequence[RawBatch],
    symbols: set[str],
    start: str,
    end: str,
) -> tuple[dict[str, list[tuple[dict[str, Any], RawBatch]]], str]:
    profile = load_reference_source_profile()
    profile_digest = reference_source_profile_digest(profile)
    grouped: dict[str, list[tuple[dict[str, Any], RawBatch]]] = {}
    covered: dict[str, set[str]] = {
        name: set() for name in _EXPECTED_ENDPOINTS[domain]
    }
    observed: list[str] = []
    for raw in raw_batches:
        profile_version = raw.manifest.get("source_profile_version")
        profile = load_reference_source_profile(profile_version)
        profile_digest = reference_source_profile_digest(profile)
        if raw.manifest.get("schema_version") != "raw_batch.v2":
            raise ArtifactError("reference Tushare builder requires raw_batch.v2")
        request = raw.manifest.get("request")
        endpoint = request.get("endpoint") if isinstance(request, dict) else None
        if endpoint not in _EXPECTED_ENDPOINTS[domain]:
            raise ArtifactError(f"{domain} received an unexpected source endpoint")
        definition = profile["endpoints"][endpoint]
        if (
            raw.manifest.get("domain") != domain
            or raw.manifest.get("source_profile_ref") != definition["source_profile_ref"]
            or raw.manifest.get("source_profile_version") != profile["profile_version"]
            or raw.manifest.get("source_profile_digest") != profile_digest
        ):
            raise ArtifactError("reference RawBatch source profile binding mismatch")
        if raw.manifest.get("collector_code_ref") not in {
            "axiom-data." + collector_revision(profile_version, TushareReferenceCollector.implementation_revision),
            "axiom-data." + TushareReferenceCollector.implementation_revision,
        }:
            raise ArtifactError("reference RawBatch collector revision mismatch")
        if request.get("fields") != definition["fields"]:
            raise ArtifactError("reference RawBatch request fields differ from SourceProfile")
        summary = raw.manifest.get("summary")
        if not isinstance(summary, dict) or (
            summary.get("response_fields") != definition["fields"]
            or summary.get("represented_session_field")
            != definition["represented_session_field"]
        ):
            raise ArtifactError("reference RawBatch response schema differs from SourceProfile")
        params = request.get("params")
        if not isinstance(params, dict):
            raise ArtifactError("reference RawBatch request params are invalid")
        request_symbols = _request_symbols(raw)
        if not request_symbols <= symbols:
            raise ArtifactError("reference RawBatch symbol scope exceeds the build scope")
        covered[endpoint].update(request_symbols)
        if endpoint == "dividend":
            if set(params) != {"ts_code"}:
                raise ArtifactError("dividend request must use its security-only scope")
        else:
            if set(params) != {"ts_code", "start_date", "end_date"}:
                raise ArtifactError("reference session request fields are invalid")
            request_start = _source_date(params.get("start_date"))
            request_end = _source_date(params.get("end_date"))
            if request_start != start or request_end != end:
                raise ArtifactError("reference RawBatch date scope differs from build scope")
        try:
            rows = json.loads(raw.payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArtifactError("reference RawBatch payload is not JSON") from exc
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ArtifactError("reference RawBatch payload must contain row objects")
        for row in rows:
            if set(row) != set(definition["fields"]):
                raise ArtifactError("reference RawBatch payload fields differ from SourceProfile")
            if _source_symbol(row.get("ts_code")) not in request_symbols:
                raise ArtifactError("reference RawBatch payload security is outside its request")
            if endpoint == "dividend":
                if not isinstance(row.get("div_proc"), str) or not row["div_proc"]:
                    raise ArtifactError("dividend payload has no action-state observation")
            else:
                represented = _source_date(
                    row.get(definition["represented_session_field"])
                )
                assert request_start is not None and request_end is not None
                if not request_start <= represented <= request_end:
                    raise ArtifactError(
                        "reference RawBatch payload session is outside its request"
                    )
            grouped.setdefault(endpoint, []).append((row, raw))
        observed.append(raw.manifest["retrieved_at"])
    if set(grouped) != _EXPECTED_ENDPOINTS[domain]:
        missing = _EXPECTED_ENDPOINTS[domain] - set(grouped)
        # Empty but explicitly requested responses still count as endpoint evidence.
        requested = {
            raw.manifest["request"]["endpoint"] for raw in raw_batches
        }
        if requested != _EXPECTED_ENDPOINTS[domain]:
            raise ArtifactError(f"{domain} is missing source endpoints {sorted(missing)!r}")
        for endpoint in requested:
            grouped.setdefault(endpoint, [])
    for endpoint, values in covered.items():
        if values != symbols:
            raise ArtifactError(f"{endpoint} requests do not cover every scoped symbol")
    if not observed:
        raise ArtifactError("reference builder received no RawBatch observations")
    return grouped, max(observed)


def _request_symbols(raw: RawBatch) -> set[str]:
    params = raw.manifest["request"].get("params")
    if not isinstance(params, dict) or not isinstance(params.get("ts_code"), str):
        raise ArtifactError("reference RawBatch request lacks explicit ts_code scope")
    symbols = set(params["ts_code"].split(","))
    if not symbols or "" in symbols:
        raise ArtifactError("reference RawBatch ts_code scope is empty")
    for symbol in symbols:
        _source_symbol(symbol)
    return symbols


class TushareReferenceBuilder(MarketDomainBuilder):
    """Map the frozen reference profile without adding a supplier framework."""

    implementation_revision = "tushare-reference-builder.v1"

    def __init__(
        self,
        data_root: str | Path,
        domain: str,
        *,
        dependency_commit_ids: Mapping[str, str],
        builder_config: Mapping[str, Any],
        created_at: str | None = None,
    ) -> None:
        if domain not in _EXPECTED_ENDPOINTS:
            raise ArtifactError("TushareReferenceBuilder supports only reference reference domains")
        config = dict(builder_config)
        if 'corporate_action_reobservation' in config:
            if domain != 'corporate_actions' or config['corporate_action_reobservation'] != 'corporate_action_reobservation.v1':
                raise ArtifactError('unsupported corporate action reobservation policy')
            config['corporate_action_reobservation_digest'] = _digest(resource_file('source_profiles', 'corporate_action_reobservation.v1.json').read_bytes())
        if 'limit_qualification' in config:
            if domain != 'price_limits' or config['limit_qualification'] != 'zero_limit_pair.v1':
                raise ArtifactError('unsupported limit qualification')
            config['limit_qualification_digest'] = _digest(resource_file('source_profiles', 'zero_limit_pair.v1.json').read_bytes())
        if 'capital_qualification' in config:
            if domain != 'security_capital' or config['capital_qualification'] != 'capital_conflict.v1':
                raise ArtifactError('unsupported capital qualification')
            config['capital_qualification_digest'] = _digest(resource_file('source_profiles', 'capital_conflict.v1.json').read_bytes())
        if 'corporate_action_observations' in config:
            if domain!='corporate_actions' or config['corporate_action_observations']!='corporate_action_observations.v1':
                raise ArtifactError('unsupported corporate action observation mapping')
            config['corporate_action_observations_digest']=_digest(resource_file('source_profiles', config['corporate_action_observations']+'.json').read_bytes())
        if 'reference_source_partitioning' in config and config['reference_source_partitioning']!='security.v1':
            raise ArtifactError('unsupported reference source partitioning')
        if 'security_session_scope' in config and (
            domain not in _SECURITY_SESSION_SCOPE_DOMAINS
            or config['security_session_scope'] != _SECURITY_SESSION_SCOPE_POLICY
        ):
            raise ArtifactError('unsupported security session scope policy')
        if domain!='security_status' and 'session_suspension_policy' in config:
            raise ArtifactError('session suspension mapping requires security_status')
        from axiom_data.session_suspension import bind_profile
        bind_profile(config)
        profile = load_reference_source_profile(config.get("source_profile_version", "tushare_reference.v1"))
        config["source_profile_version"] = profile["profile_version"]
        config["source_profile_digest"] = reference_source_profile_digest(profile)
        if domain == "corporate_actions":
            config["promoted_div_proc"] = ["实施"]
        super().__init__(
            data_root,
            domain,
            dependency_commit_ids=dependency_commit_ids,
            builder_config=config,
            created_at=created_at,
        )

    def _scope(self) -> tuple[tuple[str, ...], str, str]:
        values = self.builder_config.get("symbols")
        start = self.builder_config.get("start_session")
        end = self.builder_config.get("end_session")
        if (
            not isinstance(values, list)
            or not values
            or any(not isinstance(value, str) for value in values)
            or len(values) != len(set(values))
            or not isinstance(start, str)
            or not isinstance(end, str)
        ):
            raise ArtifactError("reference builder requires ordered symbols and session bounds")
        symbols = tuple(_source_symbol(value) for value in values)
        start_date = _source_date(start)
        end_date = _source_date(end)
        assert start_date is not None and end_date is not None
        if start_date > end_date:
            raise ArtifactError("reference session scope is reversed")
        return symbols, start_date, end_date

    def _build_rows(
        self,
        contract: Mapping[str, Any],
        parent_rows: Sequence[Mapping[str, Any]],
        raw_batches: Sequence[RawBatch],
    ) -> list[dict[str, Any]]:
        symbols, start, end = self._scope()
        if self.domain == 'security_capital' and ((contract['contract_version'] == 'security_capital.v2') != bool(self.builder_config.get('capital_qualification'))):
            raise ArtifactError('capital qualification requires security_capital.v2 and explicit mapping')
        if self.domain=='corporate_actions' and ((contract['contract_version'] in {'corporate_actions.v2','corporate_actions.v3'}) != bool(self.builder_config.get('corporate_action_observations'))):
            raise ArtifactError('corporate action observations require explicit v2 contract/mapping')
        if self.builder_config.get('security_session_scope') == _SECURITY_SESSION_SCOPE_POLICY:
            self._validate_parent_security_sessions(parent_rows)
        if self.builder_config.get('reference_source_partitioning')=='security.v1':
            return self._partitioned_rows(contract,parent_rows,raw_batches,symbols,start,end)
        tables, observed_at = _raw_tables(
            self.layout.root, self.domain, raw_batches, set(symbols), start, end
        )
        if self.domain == "security_status":
            rows = self._status_rows(tables, symbols, start, end, observed_at)
        elif self.domain == "price_limits":
            rows = self._limit_rows(tables, symbols, start, end, observed_at, raw_batches)
        elif self.domain == "corporate_actions":
            rows = self._action_rows(tables, set(symbols), start, end)
        elif self.domain == "adjustment_factors":
            rows = self._factor_rows(tables, set(symbols), start, end)
        elif self.domain == "benchmark_daily":
            rows = self._benchmark_rows(tables, set(symbols), start, end)
        else:
            rows = self._capital_rows(tables, set(symbols), start, end)
        return self._merge_rows(contract, parent_rows, rows)

    def _merge_rows(self, contract, parent_rows, new_rows):
        if not self.builder_config.get('corporate_action_reobservation'):
            return _merge_canonical(contract, parent_rows, new_rows)
        from axiom_data.pit import instant
        rows = {}
        for values in (parent_rows, new_rows):
            for row in values:
                key = tuple(row[name] for name in contract['primary_key'])
                previous = rows.get(key)
                if previous is not None:
                    content = lambda value: {k:v for k,v in value.items() if k not in {'first_observed_at', 'source_ref'}}
                    if content(previous) != content(row):
                        raise ArtifactConflictError('corporate action revision content conflict')
                    if (instant(previous['first_observed_at']), previous['source_ref']) <= (instant(row['first_observed_at']), row['source_ref']):
                        continue
                rows[key] = _json_copy(dict(row))
        return sorted(rows.values(), key=lambda row:tuple(row[name] for name in contract['sort_order']))

    def _partitioned_rows(self,contract,parent_rows,raw_batches,symbols,start,end):
        from axiom_data.artifacts import RawBatches
        import hashlib
        chunks, endpoint_refs, observations = {}, {}, []
        for raw in raw_batches:
            scope=_request_symbols(raw)
            if len(scope)!=1 or not scope<=set(symbols):
                raise ArtifactError('partitioned reference requires individual scoped requests')
            symbol=next(iter(scope))
            chunks.setdefault(symbol,[]).append(raw.ref.raw_batch_id)
            observations.append(raw.manifest['retrieved_at'])
            count=len(json.loads(raw.payload))
            if count:
                endpoint=raw.manifest['request']['endpoint']
                endpoint_refs.setdefault(endpoint,[]).append((raw.ref.raw_batch_id,count))
        if set(chunks)!=set(symbols):raise ArtifactError('reference requests do not cover scoped securities')
        # Preserve the original full-scope provenance and observation time even
        # though source tables are materialized one security at a time.
        observed_at=max(observations)
        digest=hashlib.sha256(b'['); first=True
        for refs in endpoint_refs.values():
            for ref,count in refs:
                encoded=_json_bytes(ref)
                for _ in range(count):
                    if not first:digest.update(b',')
                    digest.update(encoded);first=False
        digest.update(b']')
        status_scope='scope-'+digest.hexdigest()
        limit_scope='scope-'+_digest(_json_bytes([raw.ref.raw_batch_id for raw in raw_batches]))[7:]
        self._row_dependency_cache={}
        def mapped():
            for symbol in symbols:
                chunk=RawBatches(self.layout.root,chunks[symbol])
                tables,_=_raw_tables(self.layout.root,self.domain,chunk,{symbol},start,end)
                if self.domain=='security_status':
                    yield from self._status_rows(tables,[symbol],start,end,observed_at,scope_ref=status_scope)
                elif self.domain=='price_limits':
                    yield from self._limit_rows(tables,[symbol],start,end,observed_at,chunk,scope_ref=limit_scope)
                elif self.domain=='corporate_actions':yield from self._action_rows(tables,{symbol},start,end)
                elif self.domain=='adjustment_factors':yield from self._factor_rows(tables,{symbol},start,end)
                elif self.domain=='benchmark_daily':yield from self._benchmark_rows(tables,{symbol},start,end)
                else:yield from self._capital_rows(tables,{symbol},start,end)
        try:
            return self._merge_rows(contract,parent_rows,mapped())
        finally:
            del self._row_dependency_cache

    def _dependency(self, domain: str):
        cache=getattr(self,'_row_dependency_cache',None)
        if cache is not None and domain in cache:return cache[domain]
        commit=validate_domain_commit_closure(
            self.layout.root, domain, self.dependency_commit_ids[domain]
        )
        if cache is not None:cache[domain]=commit
        return commit

    def _security_session_scope_enabled(self) -> bool:
        return self.builder_config.get("security_session_scope") == _SECURITY_SESSION_SCOPE_POLICY

    def _security_session_context(self, symbols: Sequence[str], start: str, end: str):
        calendar = self._dependency("trading_calendar")
        security = self._dependency("security_master")
        securities = {row["symbol"]: row for row in security.rows}
        calendars = {(row["exchange"], row["session"]): row for row in calendar.rows}
        identities = {}
        for symbol in symbols:
            identity = securities.get(symbol)
            if identity is None or identity.get("exchange") not in {"SSE", "SZSE"}:
                raise ArtifactError("security session scope requires a frozen security identity")
            identities[symbol] = identity
        return identities, calendars

    def _allowed_security_sessions(
        self,
        tables,
        endpoint: str,
        symbols: Sequence[str],
        start: str,
        end: str,
    ) -> set[tuple[str, str]]:
        """Return source sessions that are open and within frozen identity bounds."""

        if not self._security_session_scope_enabled():
            return set()
        identities, calendars = self._security_session_context(symbols, start, end)
        allowed: set[tuple[str, str]] = set()
        for source, _raw in tables[endpoint]:
            symbol = _source_symbol(source.get("ts_code"))
            session = _source_date(source.get("trade_date"))
            if symbol not in identities or not start <= session <= end:
                continue
            identity = identities[symbol]
            try:
                state = _checked_security_identity_state(identity, session)
            except (KeyError, TypeError, ValueError) as exc:
                raise ArtifactError("security session scope has an invalid security identity") from exc
            if state == "unknown":
                raise ArtifactError("security session scope cannot qualify unknown identity state")
            calendar_row = calendars.get((identity["exchange"], session))
            if calendar_row is None:
                raise ArtifactError("security session scope requires an exchange calendar row")
            if calendar_row["is_open"] is not True:
                continue
            if state == "within_identity_interval":
                allowed.add((symbol, session))
        return allowed

    def _validate_parent_security_sessions(
        self,
        parent_rows: Sequence[Mapping[str, Any]],
    ) -> None:
        """Reject invalid parent facts without narrowing an incremental patch."""

        if not parent_rows:
            return
        calendar = self._dependency("trading_calendar")
        security = self._dependency("security_master")
        identities = {row["symbol"]: row for row in security.rows}
        calendars = {(row["exchange"], row["session"]): row for row in calendar.rows}
        for row in parent_rows:
            symbol = row.get("symbol")
            session = row.get("session")
            if symbol not in identities or not isinstance(session, str):
                raise ArtifactError("security session scope refuses an invalid parent fact row")
            identity = identities[symbol]
            if identity.get("exchange") not in {"SSE", "SZSE"}:
                raise ArtifactError("security session scope has an invalid security identity")
            try:
                state = _checked_security_identity_state(identity, session)
            except (KeyError, TypeError, ValueError) as exc:
                raise ArtifactError("security session scope has an invalid security identity") from exc
            calendar_row = calendars.get((identity["exchange"], session))
            if state != "within_identity_interval" or calendar_row is None or calendar_row["is_open"] is not True:
                raise ArtifactError("security session scope refuses an invalid parent fact row")

    @staticmethod
    def _evidence(raw: RawBatch) -> dict[str, Any]:
        return {
            "source_available_at": None,
            "first_observed_at": raw.manifest["retrieved_at"],
            "availability_basis": "terminal_history_observed",
            "pit_qualification": "best_effort",
            "source_ref": raw.ref.raw_batch_id,
        }

    def _status_rows(self, tables, symbols, start, end, observed_at,scope_ref=None):  # type: ignore[no-untyped-def]
        daily_evidence={(r['ts_code'],r['trade_date']):r for r,_ in tables['daily']}
        daily: dict[tuple[str, str], RawBatch] = {}
        for row, raw in tables["daily"]:
            key = (_source_symbol(row.get("ts_code")), _source_date(row.get("trade_date")))
            if key in daily:
                raise ArtifactConflictError("daily status evidence has duplicate keys")
            daily[key] = raw
        suspended: dict[tuple[str, str], RawBatch] = {}
        for row, raw in tables["suspend_d"]:
            if self.builder_config.get('session_suspension_policy')=='session_suspension.v3':
                from axiom_data.session_suspension import qualified_daily_state
                if qualified_daily_state(row,daily_evidence.get((row.get('ts_code'),row.get('trade_date'))))!='full_day_halt':
                    continue
            else:
                if 'session_suspension_policy' in self.builder_config:
                    from axiom_data.session_suspension import qualified_partial_halt
                    if qualified_partial_halt(row,daily_evidence.get((row.get('ts_code'),row.get('trade_date'))),policy=self.builder_config['session_suspension_policy']):
                        continue
                if row.get("suspend_timing") not in (None, ""):
                    raise ArtifactError("intraday suspension timing is unsupported in reference")
            if row.get("suspend_type") == "S":
                key = (_source_symbol(row.get("ts_code")), _source_date(row.get("trade_date")))
                suspended[key] = raw
        if set(daily) & set(suspended):
            raise ArtifactConflictError("daily and suspension evidence conflict")
        calendar = self._dependency("trading_calendar")
        security = {row["symbol"]: row for row in self._dependency("security_master").rows}
        scope_ref = scope_ref or "scope-" + _digest(_json_bytes([raw.ref.raw_batch_id for values in tables.values() for _, raw in values])).split(":", 1)[1]
        rows = []
        for session_row in calendar.rows:
            session = session_row["session"]
            if not start <= session <= end or session_row["is_open"] is not True:
                continue
            for symbol in symbols:
                identity = security[symbol]
                exchange = "SSE" if symbol.endswith(".SH") else "SZSE"
                if session_row["exchange"] != exchange:
                    continue
                listed = identity["list_session"]
                delisted = identity["delist_session"]
                key = (symbol, session)
                if listed is not None and session < listed:
                    status, reason, source_ref = "not_yet_listed", "identity_not_effective", self.dependency_commit_ids["security_master"]
                elif delisted is not None and session >= delisted:
                    status, reason, source_ref = "delisted", "delisting_effective", self.dependency_commit_ids["security_master"]
                elif key in suspended:
                    status, reason, source_ref = "suspended", "explicit_full_day_suspension", suspended[key].ref.raw_batch_id
                elif key in daily:
                    status, reason, source_ref = "normal_active", "daily_observation", daily[key].ref.raw_batch_id
                else:
                    status, reason, source_ref = "unknown_source_gap", "source_gap", scope_ref
                rows.append({
                    "session": session,
                    "symbol": symbol,
                    "status": status,
                    "reason": reason,
                    "source_available_at": None,
                    "first_observed_at": observed_at,
                    "availability_basis": "terminal_history_observed",
                    "pit_qualification": "best_effort",
                    "source_ref": source_ref,
                })
        return rows

    def _limit_rows(self, tables, symbols, start, end, observed_at, raw_batches,scope_ref=None):  # type: ignore[no-untyped-def]
        found: dict[tuple[str, str], tuple[Mapping[str, Any], RawBatch]] = {}
        for row, raw in tables["stk_limit"]:
            key = (_source_symbol(row.get("ts_code")), _source_date(row.get("trade_date")))
            if key in found:
                raise ArtifactConflictError("price-limit source has duplicate keys")
            found[key] = (row, raw)
        calendar = self._dependency("trading_calendar")
        securities = {row["symbol"]: row for row in self._dependency("security_master").rows}
        source_ref = scope_ref or "scope-" + _digest(_json_bytes([raw.ref.raw_batch_id for raw in raw_batches])).split(":", 1)[1]
        rows = []
        for cal in calendar.rows:
            session = cal["session"]
            if not start <= session <= end or cal["is_open"] is not True:
                continue
            for symbol in symbols:
                exchange = "SSE" if symbol.endswith(".SH") else "SZSE"
                identity = securities[symbol]
                if cal["exchange"] != exchange or (identity["list_session"] and session < identity["list_session"]) or (identity["delist_session"] and session >= identity["delist_session"]):
                    continue
                source = found.get((symbol, session))
                upper = _optional_float(source[0].get("up_limit")) if source else None
                lower = _optional_float(source[0].get("down_limit")) if source else None
                if (upper is None) != (lower is None):
                    raise ArtifactError("incomplete price-limit pair is unsupported")
                zero_pair = self.builder_config.get('limit_qualification') == 'zero_limit_pair.v1' and upper == lower == 0
                if zero_pair:
                    upper = lower = None
                rows.append({
                    "session": session,
                    "symbol": symbol,
                    "limit_state": "limited" if upper is not None else "unknown",
                    "upper_limit": upper,
                    "lower_limit": lower,
                    "rule_ref": "zero_limit_pair.v1" if zero_pair else "tushare-observed-limit" if upper is not None else "unknown-rule",
                    "source_available_at": None,
                    "first_observed_at": source[1].manifest["retrieved_at"] if source else observed_at,
                    "availability_basis": "terminal_history_observed",
                    "pit_qualification": "best_effort",
                    "source_ref": source[1].ref.raw_batch_id if source else source_ref,
                })
        return rows

    def _action_rows(self, tables, symbols, start, end):  # type: ignore[no-untyped-def]
        rows = []
        qualified=bool(self.builder_config.get('corporate_action_observations'))
        for source, raw in tables["dividend"]:
            symbol = _source_symbol(source.get("ts_code"))
            if symbol not in symbols:
                continue
            if source.get("div_proc") != "实施":
                continue
            effective = _source_date(source.get("ex_date"), nullable=True)
            if effective is None and not qualified:
                raise ArtifactError("implemented corporate action requires ex_date")
            if effective is not None and not start <= effective <= end:
                continue
            terms = {
                "cash_dividend": _optional_float(source.get("cash_div_tax")),
                "stock_dividend": _optional_float(source.get("stk_bo_rate")),
                "capital_transfer": _optional_float(source.get("stk_co_rate")),
            }
            total_stock = _optional_float(source.get("stk_div"))
            mapped_stock = (terms["stock_dividend"] or 0) + (terms["capital_transfer"] or 0)
            if total_stock not in (None, 0, 0.0) and abs(total_stock - mapped_stock) > 1e-10:
                raise ArtifactError("dividend row contains unsupported stock terms")
            emitted = 0
            natural = [symbol, source.get("end_date"), source.get("ann_date"), source.get("div_proc")]
            if qualified and all(value in (None,0,0.0) for value in terms.values()):
                terms={'unresolved':None}
            for action_type, value in terms.items():
                if value in (None, 0, 0.0) and action_type!='unresolved':
                    continue
                action_id = "ca-" + _digest(_json_bytes([natural, action_type])).split(":", 1)[1]
                version = "obs-" + _digest(_json_bytes(source)).split(":", 1)[1]
                rows.append({
                    "symbol": symbol,
                    "action_id": action_id,
                    "action_version": version,
                    "action_type": action_type,
                    "announcement_date": _source_date(source.get("ann_date"), nullable=True),
                    "record_date": _source_date(source.get("record_date"), nullable=True),
                    "ex_date": effective,
                    "effective_date": effective,
                    "payment_date": _source_date(source.get("pay_date"), nullable=True),
                    "share_available_date": _source_date(source.get("div_listdate"), nullable=True),
                    "cash_per_share": value if action_type == "cash_dividend" else None,
                    "stock_ratio": value if action_type == "stock_dividend" else None,
                    "transfer_ratio": value if action_type == "capital_transfer" else None,
                    "split_ratio": None,
                    **self._evidence(raw),
                    **({'observation_state':'unresolved_terms' if action_type=='unresolved' else 'undated_action' if effective is None else 'dated_action'} if qualified else {}),
                })
                emitted += 1
            if emitted == 0:
                raise ArtifactError("implemented corporate action has no supported terms")
        return rows

    def _factor_rows(self, tables, symbols, start, end):  # type: ignore[no-untyped-def]
        rows = []
        allowed = self._allowed_security_sessions(
            tables, "adj_factor", tuple(sorted(symbols)), start, end
        ) if self._security_session_scope_enabled() else None
        for source, raw in tables["adj_factor"]:
            symbol = _source_symbol(source.get("ts_code"))
            session = _source_date(source.get("trade_date"))
            factor = _optional_float(source.get("adj_factor"))
            if symbol in symbols and start <= session <= end:
                if allowed is not None and (symbol, session) not in allowed:
                    continue
                if factor is None or factor <= 0:
                    raise ArtifactError("adjustment factor must be positive")
                rows.append({"session": session, "symbol": symbol, "factor": factor, **self._evidence(raw)})
        return rows

    def _benchmark_rows(self, tables, symbols, start, end):  # type: ignore[no-untyped-def]
        rows = []
        for source, raw in tables["index_daily"]:
            benchmark = _source_symbol(source.get("ts_code"))
            session = _source_date(source.get("trade_date"))
            close = _optional_float(source.get("close"))
            if benchmark in symbols and start <= session <= end:
                if close is None:
                    raise ArtifactError("benchmark close must be present")
                rows.append({"session": session, "benchmark": benchmark, "close": close, **self._evidence(raw)})
        return rows

    def _capital_rows(self, tables, symbols, start, end):  # type: ignore[no-untyped-def]
        rows = []
        allowed = self._allowed_security_sessions(
            tables, "daily_basic", tuple(sorted(symbols)), start, end
        ) if self._security_session_scope_enabled() else None
        for source, raw in tables["daily_basic"]:
            symbol = _source_symbol(source.get("ts_code"))
            session = _source_date(source.get("trade_date"))
            if symbol not in symbols or not start <= session <= end:
                continue
            if allowed is not None and (symbol, session) not in allowed:
                continue
            total = _scaled_float(source.get("total_share"), 10000)
            circulating = _scaled_float(source.get("float_share"), 10000)
            if total is None or circulating is None:
                raise ArtifactError("security capital source fields must be present")
            row = {"session": session, "symbol": symbol, "total_shares": total, "circulating_shares": circulating, **self._evidence(raw)}
            if self.builder_config.get('capital_qualification'):
                row.update(missing_reason=None, source_conflict=None)
                if 0 < total < circulating:
                    row.update(total_shares=None, circulating_shares=None,
                        missing_reason='source_capital_conflict', source_conflict={
                            'profile': 'capital_conflict.v1',
                            'total_share': source['total_share'], 'float_share': source['float_share']})
            rows.append(row)
        return rows


__all__ = [
    "TushareReferenceBuilder",
    "TushareReferenceCollector",
    "reference_source_profile_digest",
    "load_reference_source_profile",
]


# Compatibility exports for historical callers.
