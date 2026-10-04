"""Resumable Raw-first updates and explicit offline reconstruction.

The caller supplies source bytes and a concrete parent Snapshot. This module
never fetches data. An operation checkpoint binds its complete input identity;
retrying a failed operation reuses its already logged Raw observations.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
from hashlib import sha256
from itertools import chain
import json
import tempfile
from pathlib import Path
from typing import Any
from collections.abc import Mapping, Sequence
from functools import lru_cache

from .protocols import ConflictError, DataError, IngestBatch, OperationResult, UpdateRequest
from .sources import normalize_batch
from .storage import LocalStore
from .builder import operation_context


_PROVENANCE = frozenset({"first_observed_at", "raw_batch_id"})


def _encode(value: Any) -> bytes:
    def default(item: Any) -> str:
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        raise TypeError(f"cannot encode {type(item).__name__}")

    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False, default=default).encode("utf-8")


def _fingerprint(value: Any) -> str:
    try:
        return sha256(_encode(value)).hexdigest()
    except (TypeError, ValueError) as exc:
        raise DataError("operation input must be strict JSON data") from exc


def _instant(value: Any) -> datetime:
    try:
        parsed = (datetime.fromisoformat(value.replace("Z", "+00:00"))
                  if isinstance(value, str) else value)
    except ValueError as exc:
        raise DataError(f"invalid observation timestamp: {value!r}") from exc
    if not isinstance(parsed, datetime) or parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DataError("observation timestamps require a timezone")
    return parsed.astimezone(timezone.utc)


def _plain(value: Any) -> Any:
    """Normalize Arrow dates/timestamps and source strings for row comparison."""
    if isinstance(value, datetime):
        return _instant(value).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


@lru_cache(maxsize=32768)
def _session_month(session: str) -> str:
    try:
        return date.fromisoformat(session).isoformat()[:7]
    except ValueError as exc:
        raise DataError(f"invalid session for partitioning: {session!r}") from exc


def _partition(row: Mapping[str, Any], contract: Mapping[str, Any]) -> str:
    session = row.get("session")
    if session is not None:
        return _session_month(str(session))
    # An event's report period is safe as a partition key only when it is
    # part of the immutable logical key. Announcement/effective dates may be
    # corrected by a later revision and must not separate those revisions.
    if "report_period" in (contract.get("logical_key") or ()):
        period = row.get("report_period")
        if period is None:
            raise DataError("period-keyed event lacks report_period")
        try:
            parsed = date.fromisoformat(str(period))
        except ValueError as exc:
            raise DataError(f"invalid report_period for partitioning: {period!r}") from exc
        return f"period-{parsed.year:04d}"
    return "history"


def _key(row: Mapping[str, Any], contract: Mapping[str, Any]) -> tuple[Any, ...]:
    names = contract.get("logical_key")
    if not isinstance(names, (tuple, list)) or not names:
        raise DataError("contract.logical_key is invalid")
    result = tuple(_plain(row.get(name)) for name in names)
    if any(item is None for item in result):
        raise DataError("canonical row lacks logical key")
    return result


def _same_fact(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
    # The observation and its Raw pointer identify the first receipt, rather
    # than the vendor fact. All evidence and source-order fields are material.
    def content(row: Mapping[str, Any]) -> dict[str, Any]:
        material = _plain({key: value for key, value in row.items() if key not in _PROVENANCE})
        if material.get("source_available_at") is not None:
            material["source_available_at"] = _instant(material["source_available_at"]).isoformat()
        # Typed Parquet materializes optional storage columns as null even when
        # a normalized event never supplied those columns.
        return {key: value for key, value in material.items() if value is not None}

    return content(first) == content(second)


def _merge_rows(old_rows: Sequence[Mapping[str, Any]], new_rows: Sequence[Mapping[str, Any]],
                contract: Mapping[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    by_revision: dict[tuple[Any, ...], dict[str, Any]] = {}
    by_order: dict[tuple[Any, ...], str] = {}
    changed = False
    for source, is_new in chain(((row, False) for row in old_rows),
                                ((row, True) for row in new_rows)):
        row = _plain(dict(source))
        logical = _key(row, contract)
        revision = row.get("revision_id")
        if not isinstance(revision, str) or not revision:
            raise DataError("canonical row lacks revision_id")
        sequence = row.get("revision_sequence")
        if sequence is not None:
            if isinstance(sequence, bool) or not isinstance(sequence, int):
                raise DataError("revision_sequence must be an integer")
            order_key = (*logical, sequence)
            previous_revision = by_order.get(order_key)
            if previous_revision is not None and previous_revision != revision:
                raise ConflictError("conflicting source revisions at identical sequence")
            by_order[order_key] = revision
        identity = (*logical, revision)
        prior = by_revision.get(identity)
        if prior is None:
            by_revision[identity] = row
            changed |= is_new
            continue
        if not _same_fact(prior, row):
            raise ConflictError("conflicting content for identical source revision")
        prior_time = _instant(prior.get("first_observed_at"))
        next_time = _instant(row.get("first_observed_at"))
        # Equal timestamps retain the first encountered Raw reference. On an
        # update that is the existing partition's original observation; a
        # selected-Raw rebuild uses the caller's explicit input order.
        if next_time < prior_time:
            prior["first_observed_at"] = row["first_observed_at"]
            prior["raw_batch_id"] = row["raw_batch_id"]
            changed = True
    return list(by_revision.values()), changed


def _merge_terminal(old_rows, new_rows, contract):
    """Preserve observed terminal-state transitions under an explicit policy.

    Consecutive equal values retain the original observation. A→B→A creates a
    new A occurrence, so replay before the return to A still sees B. Rebuild is
    ordered by actual stored observations, never by caller file ordering.
    """
    groups = {}
    fields = set(contract["fields"]) - {"revision_id", "revision_sequence", "first_observed_at", "raw_batch_id", "source_available_at", "evidence_ref"}
    def content(row):
        return _fingerprint({name: _plain(row.get(name)) for name in sorted(fields)})
    for row in chain(old_rows, new_rows):
        groups.setdefault(_key(row, contract), []).append(_plain(dict(row)))
    merged = []
    for key, rows in groups.items():
        last_content = None
        last_occurrence = None
        observed_values = {}
        for row in sorted(rows, key=lambda r: _instant(r["first_observed_at"])):
            stamp, identity = _instant(row["first_observed_at"]), content(row)
            if stamp in observed_values and identity != observed_values[stamp]:
                raise ConflictError(f"ambiguous terminal responses at identical observation time: {key}")
            observed_values[stamp] = identity
            if identity == last_content:
                # Re-observing the same terminal value does not start another
                # occurrence or move its first_observed_at.  Revision-bound
                # public evidence can, however, enrich that occurrence in a
                # new Snapshot without rewriting its old Snapshot.
                prior_evidence = (last_occurrence.get("source_available_at"),
                                  last_occurrence.get("evidence_ref"))
                new_evidence = (row.get("source_available_at"), row.get("evidence_ref"))
                if all(new_evidence):
                    if all(prior_evidence) and prior_evidence != new_evidence:
                        raise ConflictError(f"conflicting terminal revision evidence: {key}")
                    if not all(prior_evidence):
                        last_occurrence["source_available_at"] = row["source_available_at"]
                        last_occurrence["evidence_ref"] = row["evidence_ref"]
                continue
            row["revision_id"] = "terminal:" + _fingerprint({"content": identity, "observed_at": stamp.isoformat()})
            row["revision_sequence"] = None
            merged.append(row)
            last_content, last_occurrence = identity, row
    def comparable(rows):
        return sorted((_encode({k: v for k, v in _plain(row).items() if v is not None}) for row in rows))
    return merged, bool(merged) if not old_rows else comparable(merged) != comparable(old_rows)


def _coverage(old: Mapping[str, Any] | None, requests: Sequence[Mapping[str, Any]],
              *, bulk_chunk: Mapping[str, Any] | None = None) -> dict[str, Any]:
    result = deepcopy(dict(old)) if old else {"basis": "observed_responses_only"}
    if bulk_chunk is not None:
        # Raw retains every exact request. Summarize a bounded job chunk in the
        # Snapshot to avoid duplicating hundreds of thousands of request JSON
        # objects across successive candidate manifests.
        digest = _fingerprint(list(requests))
        item = {"job_fingerprint": bulk_chunk["job_fingerprint"],
                "chunk_index": bulk_chunk["chunk_index"],
                "request_count": len(requests), "requests_sha256": digest,
                "completeness": "unverified"}
        chunks = result.setdefault("observed_chunks", [])
        if item not in chunks:
            chunks.append(item)
        return result
    observations = result.setdefault("observed_requests", [])
    if not isinstance(observations, list):
        raise DataError("coverage.observed_requests must be a list")
    seen = {_encode(item) for item in observations}
    for request in requests:
        item = {"request": deepcopy(dict(request)), "completeness": "unverified"}
        identity = _encode(item)
        if identity not in seen:
            observations.append(item)
            seen.add(identity)
    return result


def _base(store: LocalStore, snapshot_id: str | None) -> dict[str, Any] | None:
    if snapshot_id in ("current", "latest"):
        raise DataError("resolve the base Snapshot before updating")
    return store.load_snapshot(snapshot_id) if snapshot_id is not None else None


def _join_source_profiles(first: Mapping[str, Any], second: Mapping[str, Any]) -> dict[str, Any]:
    """Union compatible stable-ID and next-open calendar extensions.

    Each Raw remains tied to its original map for normalization. The domain's
    aggregate maps can grow, but existing bindings cannot change.
    """
    # Raw records in a batch commonly share a large frozen identity/calendar
    # profile. Comparing it is necessary; copying both nested maps for every
    # response is not. Only the two maps being extended are copied below.
    if first == second:
        return dict(first)
    left, right = dict(first), dict(second)
    has_cap = any(key in first or key in second
                  for key in ("response_limit", "response_limit_basis"))
    cap_a, cap_b = left.pop("response_limit", None), right.pop("response_limit", None)
    cap_basis_a = left.pop("response_limit_basis", None)
    cap_basis_b = right.pop("response_limit_basis", None)
    a, b = left.pop("identity_map", None), right.pop("identity_map", None)
    left_availability, right_availability = left.get("availability"), right.get("availability")
    calendar_a = calendar_b = None
    if (isinstance(left_availability, dict) and isinstance(right_availability, dict) and
            isinstance(left_availability.get("next_open_session_by_date"), dict) and
            isinstance(right_availability.get("next_open_session_by_date"), dict)):
        left_availability = dict(left_availability)
        right_availability = dict(right_availability)
        left["availability"], right["availability"] = left_availability, right_availability
        calendar_a = left_availability.pop("next_open_session_by_date")
        calendar_b = right_availability.pop("next_open_session_by_date")
    extensible_profile = (str(left.get("id", "")).startswith("tushare.local.") or
                          left.get("id") == "issuer_fund_disclosure_supplement_v1")
    if not extensible_profile or left != right or (a is None) != (b is None):
        raise ConflictError("source profile changed; explicitly rebuild the domain")
    result = left
    if has_cap:
        if cap_a == cap_b and cap_basis_a == cap_basis_b:
            result["response_limit"] = cap_a
            if cap_basis_a is not None:
                result["response_limit_basis"] = cap_basis_a
        else:
            # The domain can combine capped normal and uncapped VIP responses.
            # A cap is a property of each original Raw request, not of the
            # merged economic facts or PIT release rule.
            result["response_limit"] = None
            result["response_limit_basis"] = "per_raw_observation"
    if a is not None:
        if not isinstance(a, dict) or not isinstance(b, dict):
            raise ConflictError("source profile identity_map must be a mapping")
        if any(code in b and b[code] != identity for code, identity in a.items()):
            raise ConflictError("identity_map remaps a stable security ID")
        merged = {**a, **b}
        if len(set(merged.values())) != len(merged):
            raise ConflictError("identity_map assigns one stable security ID to multiple source codes")
        result["identity_map"] = merged
    if calendar_a is not None or calendar_b is not None:
        if (result.get("availability", {}).get("date_rule") != "next_open" or
                not isinstance(calendar_a, dict) or not isinstance(calendar_b, dict)):
            raise ConflictError("next-open calendar changed outside its declared rule")
        if any(day in calendar_b and calendar_b[day] != next_day
               for day, next_day in calendar_a.items()):
            raise ConflictError("next-open calendar remaps a source date")
        result["availability"]["next_open_session_by_date"] = {**calendar_a, **calendar_b}
    return result


def _state(store: LocalStore, operation_id: str, kind: str, fingerprint: str,
           base_snapshot: str | None) -> dict[str, Any]:
    state = store.read_operation(operation_id)
    if state is None:
        state = {"kind": kind, "fingerprint": fingerprint,
                 "base_snapshot": base_snapshot, "status": "running", "raw_batch_ids": []}
        store.write_operation(operation_id, state)
    elif state.get("kind") != kind or state.get("fingerprint") != fingerprint:
        raise ConflictError(f"operation_id {operation_id!r} is bound to different inputs")
    return state


def _completed(state: Mapping[str, Any], operation_id: str) -> OperationResult | None:
    if state.get("status") != "success":
        return None
    result = state.get("result")
    if not isinstance(result, Mapping):
        raise DataError("completed operation lacks result")
    return OperationResult(snapshot_id=result["snapshot_id"], changed=result["changed"],
                           operation_id=operation_id, issues=tuple(result.get("issues", ())),
                           status=result.get("status", "success"))


def _fail(store: LocalStore, operation_id: str, state: dict[str, Any], exc: Exception) -> None:
    state.update(status="failed", error={"type": type(exc).__name__, "message": str(exc)})
    store.write_operation(operation_id, state)


def _finish(store: LocalStore, operation_id: str, state: dict[str, Any],
            snapshot_id: str, changed: bool) -> OperationResult:
    result = OperationResult(snapshot_id, changed, operation_id)
    state.update(status="success", result={"snapshot_id": snapshot_id, "changed": changed,
                                            "issues": [], "status": "success"})
    state.pop("error", None)
    store.write_operation(operation_id, state)
    return result


_NORMALIZED_BUFFER_ROWS = 100_000

def _build_domain(store: LocalStore, name: str, old: Mapping[str, Any] | None,
                  entries: Sequence[tuple[IngestBatch, Mapping[str, Any]]],
                  build_context: Mapping[str, Any], *, replace: bool) -> tuple[dict[str, Any], bool]:
    first = entries[0][0]
    contract = deepcopy(dict(first.contract))
    # Join without cloning the large frozen maps for every Raw. The join never
    # mutates its inputs; copy the finalized profile once into the manifest.
    profile = dict(first.source_profile)
    for batch, _ in entries:
        if batch.domain != name or dict(batch.contract) != contract:
            raise ConflictError(f"domain {name} has incompatible contract")
        profile = _join_source_profiles(profile, batch.source_profile)
    if old and not replace:
        if old["contract"] != contract:
            raise ConflictError(f"domain {name} contract changed; explicitly rebuild it")
        old_map = old["source_profile"].get("identity_map")
        new_map = profile.get("identity_map")
        if isinstance(old_map, dict) and isinstance(new_map, dict) and any(
                new_map.get(code) != identity for code, identity in old_map.items()):
            raise ConflictError(f"domain {name} identity_map deleted or remapped an existing security")
        profile = _join_source_profiles(old["source_profile"], profile)

    old_parts = {} if replace or not old else {part["partition"]: part for part in old["partitions"]}
    partitions = dict(old_parts)
    facts_changed = False
    # Normal monthly ingestion stays in memory. Long offline rebuilds spill
    # normalized contributions to temporary month files, so twelve years of
    # Raw never become one resident Python row list. The files are disposable;
    # durable recovery remains the immutable Raw and operation checkpoint.
    with tempfile.TemporaryDirectory(prefix="axiom-normalize-") as staging:
        incoming: dict[str, list[dict[str, Any]]] = {}
        spilled: dict[str, Path] = {}
        buffered = 0

        def spill():
            nonlocal buffered
            for month, rows in incoming.items():
                path = spilled.setdefault(month, Path(staging) / (month + ".jsonl"))
                with path.open("ab") as stream:
                    stream.writelines(_encode(row) + b"\n" for row in rows)
            incoming.clear()
            buffered = 0

        for batch, raw in entries:
            for row in normalize_batch(batch, raw):
                partition = _partition(row, contract)
                if partition != "history" and "history" in old_parts:
                    raise ConflictError(
                        f"domain {name} has a legacy history partition; explicitly rebuild from saved Raw"
                    )
                incoming.setdefault(partition, []).append(row)
                buffered += 1
            if buffered >= _NORMALIZED_BUFFER_ROWS:
                spill()
        for month in sorted(set(incoming) | set(spilled)):
            rows = incoming.pop(month, [])
            if month in spilled:
                with spilled[month].open("rb") as stream:
                    # Preserve observation order on equal timestamps.
                    rows = [json.loads(line) for line in stream] + rows
            previous = old_parts.get(month)
            existing = store.read_partition(previous).to_pylist() if previous else []
            order = profile.get("revision_order")
            if order == "announcement_day_then_terminal_v1":
                if "actual_announcement_date" not in contract.get("fields", {}):
                    raise ConflictError("announcement-day revision policy requires actual_announcement_date")
                # A later announcement is a distinct version of the same
                # economic period; receipts on that announcement day retain
                # terminal A→B→A transitions without invented vendor order.
                merge_contract = {**contract, "logical_key": [
                    *contract["logical_key"], "actual_announcement_date"]}
                merged, changed = _merge_terminal(existing, rows, merge_contract)
            elif order == "terminal_observation_v1":
                merged, changed = _merge_terminal(existing, rows, contract)
            else:
                merged, changed = _merge_rows(existing, rows, contract)
            if changed or previous is None:
                partitions[month] = store.write_partition(name, month, merged, contract)
                facts_changed = True
    if old is not None and not replace and not facts_changed and profile == old["source_profile"]:
        # This ingestion path records only unverified request observations, not
        # new completeness evidence. A different fetch scope/operation ID alone
        # cannot strengthen coverage. Keep it in Raw without version churn.
        return deepcopy(dict(old)), False
    requests = [batch.request for batch, _ in entries]
    coverage = _coverage(None if replace else old.get("coverage") if old else None, requests,
                         bulk_chunk=build_context.get("bulk_chunk"))
    old_ids = [] if replace or not old else list(old["raw_batch_ids"])
    raw_ids = list(dict.fromkeys([*old_ids, *(raw["batch_id"] for _, raw in entries)]))
    domain = {"contract": contract, "source_profile": deepcopy(profile),
              "partitions": [partitions[key] for key in sorted(partitions)],
              "raw_batch_ids": raw_ids, "coverage": coverage,
              "build_context": deepcopy(dict(build_context))}
    # Additional duplicate Raw observations do not by themselves make a new
    # Snapshot. The log still keeps each observation.
    material = {key: value for key, value in domain.items()
                if key not in {"raw_batch_ids", "build_context"}}
    prior_material = ({key: value for key, value in old.items()
                       if key not in {"raw_batch_ids", "build_context"}}
                      if old else None)
    changed = (old is None or material != prior_material or facts_changed or
               (replace and old is not None and
                (raw_ids != list(old["raw_batch_ids"]) or
                 domain["build_context"] != old["build_context"])))
    if not changed and old is not None:
        return deepcopy(dict(old)), False
    return domain, changed


def _publish(store: LocalStore, base_snapshot: str | None, base: dict[str, Any] | None,
             grouped: Mapping[str, Sequence[tuple[IngestBatch, Mapping[str, Any]]]],
             build_context: Mapping[str, Any], promote: bool, operation_id: str,
             state: dict[str, Any], *, replace: bool,
             replacement_domains: Mapping[str, Mapping[str, Any]] | None = None) -> OperationResult:
    domains = deepcopy(base["domains"]) if base else {}
    changed = False
    for name, entries in grouped.items():
        old = domains.get(name)
        rebuilt, domain_changed = _build_domain(store, name, old, entries, build_context,
                                                replace=replace)
        if domain_changed:
            domains[name] = rebuilt
            changed = True
    for name, rebuilt in (replacement_domains or {}).items():
        if domains.get(name) != rebuilt:
            domains[name] = deepcopy(dict(rebuilt))
            changed = True
    if promote:
        pointer = store.root / "current.json"
        current = store.resolve("current") if pointer.exists() else None
        if changed:
            candidate_body = {"schema_version": "local_data_v1", "parent_snapshot": base_snapshot,
                              "domains": domains, "build_context": dict(build_context)}
            candidate_id = "s_" + _fingerprint(candidate_body)
        else:
            candidate_id = base_snapshot
        if current not in (base_snapshot, candidate_id):
            raise ConflictError("current Snapshot changed since the operation base")
    if not changed:
        if base_snapshot is None:
            raise DataError("an empty initial update cannot produce a Snapshot")
        return _finish(store, operation_id, state, base_snapshot, False)
    manifest = store.publish_snapshot(domains, parent_snapshot=base_snapshot,
                                      build_context=build_context, promote=promote)
    return _finish(store, operation_id, state, manifest["snapshot_id"], True)


def apply_update(store: LocalStore, *, base_snapshot: str | None,
                 request: UpdateRequest) -> OperationResult:
    """Persist every supplied observation, merge revisions, and publish atomically.

    The identity includes the parent, request, exact payload bytes, contract,
    profile, normalizer and observation time. A retry of the same operation ID
    resumes at the first unlogged batch. Failed normalization is checkpointed;
    it cannot move ``current``. No source call occurs here.
    """
    if not isinstance(request, UpdateRequest):
        raise DataError("request must be an UpdateRequest")
    if not isinstance(request.build_context, Mapping):
        raise DataError("build_context must be a mapping")
    identity = {"kind": "update", "base_snapshot": base_snapshot,
                "build_context": dict(request.build_context), "promote": request.promote,
                "batches": [{"domain": batch.domain, "payload_sha256": sha256(batch.payload).hexdigest(),
                             "request": dict(batch.request), "contract": dict(batch.contract),
                             "source_profile": dict(batch.source_profile),
                             "observed_at": _instant(batch.observed_at).isoformat(),
                             "normalizer": batch.normalizer} for batch in request.batches]}
    fingerprint = _fingerprint(identity)
    with store.writer():
        state = _state(store, request.operation_id, "update", fingerprint, base_snapshot)
        result = _completed(state, request.operation_id)
        if result is not None:
            return result
        context = operation_context(store, state, request.operation_id, request.build_context)
        try:
            base = _base(store, base_snapshot)
            records: list[dict[str, Any]] = []
            recovered = store.find_raw_by_operation(request.operation_id)
            checkpointed = store.get_raw_many(state["raw_batch_ids"])
            if any(index >= len(request.batches) for index in recovered):
                raise ConflictError("operation Raw log has an out-of-range batch index")
            if recovered and set(recovered) != set(range(max(recovered) + 1)):
                raise ConflictError("operation Raw log has a missing batch index")
            for index, batch in enumerate(request.batches):
                if index < len(state["raw_batch_ids"]):
                    record = checkpointed[state["raw_batch_ids"][index]]
                    if index in recovered and recovered[index]["batch_id"] != record["batch_id"]:
                        raise ConflictError("operation checkpoint and Raw log disagree")
                elif index in recovered:
                    record = recovered[index]
                    state["raw_batch_ids"].append(record["batch_id"])
                    store.write_operation(request.operation_id, state)
                else:
                    record = store.write_raw(batch.payload, domain=batch.domain,
                                             request=batch.request, contract=batch.contract,
                                             source_profile=batch.source_profile,
                                             observed_at=batch.observed_at,
                                             normalizer=batch.normalizer,
                                             operation_id=request.operation_id,
                                             batch_index=index)
                    state["raw_batch_ids"].append(record["batch_id"])
                    store.write_operation(request.operation_id, state)
                if (record.get("domain") != batch.domain or
                        record.get("payload_sha256") != sha256(batch.payload).hexdigest() or
                        record.get("request") != dict(batch.request) or
                        record.get("contract") != dict(batch.contract) or
                        record.get("source_profile") != dict(batch.source_profile) or
                        record.get("normalizer") != batch.normalizer or
                        _instant(record.get("observed_at")) != _instant(batch.observed_at) or
                        store.read_raw_record(record) != batch.payload):
                    raise ConflictError("checkpointed Raw differs from operation inputs")
                records.append(record)
            grouped: dict[str, list[tuple[IngestBatch, Mapping[str, Any]]]] = {}
            for batch, record in zip(request.batches, records):
                grouped.setdefault(batch.domain, []).append((batch, record))
            return _publish(store, base_snapshot, base, grouped, context,
                            request.promote, request.operation_id, state, replace=False)
        except Exception as exc:
            _fail(store, request.operation_id, state, exc)
            if isinstance(exc, DataError):
                raise
            raise DataError(f"update failed: {exc}") from exc


class _SavedBatch:
    """Raw metadata with a lazily read payload for bounded offline construction.

    Public ingestion still uses IngestBatch. This internal duck-typed input
    avoids holding all historical source bytes during a rebuild; normalization
    accesses payload once and retains only its current response.
    """
    def __init__(self, store, raw, override=None):
        self._store, self._raw = store, raw
        override = override or {}
        self.domain = raw["domain"]
        self.request = raw["request"]
        if "canonical_symbols" in override:
            # This is a derived normalization selection, never a new fetch or
            # a mutation of the immutable original Raw request/receipt.
            self.request = {**self.request,
                            "canonical_symbols": list(override["canonical_symbols"])}
        self.contract = override.get("contract", raw["contract"])
        self.source_profile = override.get("source_profile", raw["source_profile"])
        self.normalizer = override.get("normalizer", raw["normalizer"])
        self.observed_at = raw["observed_at"]

    @property
    def payload(self):
        return self._store.read_raw_record(self._raw)


def apply_saved_raw(store: LocalStore, *, base_snapshot: str | None,
                    raw_batch_ids: Sequence[str], operation_id: str,
                    build_context: Mapping[str, Any], promote: bool = True) -> OperationResult:
    """Merge already checkpointed successful responses without a second fetch/log.

    The source runner owns fetch attempts; this operation owns one atomic fact
    publication. Failed/capped responses cannot be used as successful facts.
    """
    ids = tuple(raw_batch_ids)
    if not ids or len(set(ids)) != len(ids):
        raise DataError("apply_saved_raw requires unique nonempty Raw IDs")
    fingerprint = _fingerprint({"kind": "saved_raw", "base_snapshot": base_snapshot,
                                "raw_batch_ids": ids, "build_context": dict(build_context),
                                "promote": promote})
    with store.writer():
        state = _state(store, operation_id, "saved_raw", fingerprint, base_snapshot)
        completed = _completed(state, operation_id)
        if completed is not None:
            return completed
        build_context = operation_context(store, state, operation_id, build_context)
        try:
            base = _base(store, base_snapshot)
            saved = store._raw_records(ids, shared_profiles=True)
            grouped = {}
            for identity in ids:
                raw = saved[identity]
                if raw.get("status") not in {"success", "empty"}:
                    raise DataError(f"Raw observation is not successful: {identity}")
                if not raw.get("domain") or not raw.get("contract") or not raw.get("normalizer"):
                    raise DataError("saved Raw lacks its normalization contract")
                batch = _SavedBatch(store, raw)
                grouped.setdefault(batch.domain, []).append((batch, raw))
            state["raw_batch_ids"] = list(ids)
            store.write_operation(operation_id, state)
            return _publish(store, base_snapshot, base, grouped, build_context, promote,
                            operation_id, state, replace=False)
        except Exception as exc:
            _fail(store, operation_id, state, exc)
            if isinstance(exc, DataError):
                raise
            raise DataError(f"saved Raw update failed: {exc}") from exc


def rebuild_from_raw(store: LocalStore, *, base_snapshot: str, raw_batch_ids: Sequence[str],
                     domains: Sequence[str], operation_id: str,
                     build_context: Mapping[str, Any], promote: bool = True,
                     domain_overrides: Mapping[str, Mapping[str, Any]] | None = None) -> OperationResult:
    """Reconstruct exactly selected domains from selected saved Raw observations.

    The selected domain's old partitions are replaced, while every unselected
    domain manifest is copied from the parent. Original observation times and
    Raw pointers are retained. This operation never contacts a source.
    Membership produced from saved original supplier Raw is rebuilt from its
    frozen source configuration; its contract/profile/normalizer cannot be
    overridden here.
    """
    if not isinstance(build_context, Mapping):
        raise DataError("build_context must be a mapping")
    wanted = tuple(domains)
    ids = tuple(raw_batch_ids)
    overrides = dict(domain_overrides or {})
    if not wanted or not ids or len(set(wanted)) != len(wanted) or len(set(ids)) != len(ids):
        raise DataError("rebuild needs unique selected domains and Raw batch IDs")
    if any(name not in wanted for name in overrides):
        raise DataError("rebuild override names must be selected domains")
    for name, override in overrides.items():
        if not isinstance(override, Mapping) or not set(override).issubset(
                {"contract", "source_profile", "normalizer", "canonical_symbols"}):
            raise DataError(f"invalid rebuild override for {name}")
        if (("contract" in override and not isinstance(override["contract"], Mapping)) or
                ("source_profile" in override and not isinstance(override["source_profile"], Mapping)) or
                ("normalizer" in override and not isinstance(override["normalizer"], str))):
            raise DataError(f"invalid rebuild override values for {name}")
        if "canonical_symbols" in override:
            codes = override["canonical_symbols"]
            if (not isinstance(codes, (list, tuple)) or not codes or
                    any(not isinstance(code, str) or not code for code in codes) or
                    len(set(codes)) != len(codes)):
                raise DataError("canonical_symbols needs unique nonempty source codes")
    fingerprint = _fingerprint({"kind": "rebuild", "base_snapshot": base_snapshot,
                                "raw_batch_ids": ids, "domains": wanted,
                                "build_context": dict(build_context), "promote": promote,
                                "domain_overrides": overrides})
    with store.writer():
        state = _state(store, operation_id, "rebuild", fingerprint, base_snapshot)
        result = _completed(state, operation_id)
        if result is not None:
            return result
        build_context = operation_context(store, state, operation_id, build_context)
        try:
            base = _base(store, base_snapshot)
            membership_old = ((base or {}).get("domains", {}).get("universe_membership")
                              if "universe_membership" in wanted else None)
            listing_old = ((base or {}).get("domains", {}).get("listing_events")
                           if "listing_events" in wanted else None)
            if listing_old is not None and (listing_old.get("source_profile") or {}).get("id") == "tushare.stock_basic.listing_events.v1":
                from .vendor_listing import vendor_listing_source_chain
                listing_history = vendor_listing_source_chain(store, listing_old)
            else:
                listing_history = []
            vendor_history = []
            if membership_old is not None and (membership_old.get("source_profile") or {}).get("id") == "tushare.index_weight.dated_membership.v1":
                from .vendor_membership import vendor_membership_source_chain
                vendor_history = vendor_membership_source_chain(store, membership_old)
            special_membership = bool(vendor_history)
            if special_membership and "universe_membership" in overrides:
                raise DataError("derived membership rebuild does not accept domain overrides")
            if listing_history and "listing_events" in overrides:
                raise DataError("derived listing rebuild does not accept domain overrides")
            grouped: dict[str, list[tuple[IngestBatch, Mapping[str, Any]]]] = {
                name: [] for name in wanted
                if (name != "universe_membership" or not special_membership)
                and (name != "listing_events" or not listing_history)}
            # Replay the base Snapshot's effective interpretation, not the
            # older capture-time contract/profile in each immutable Raw.
            # Explicit caller overrides replace these inherited settings.
            effective = {}
            for name in grouped:
                old = (base or {}).get("domains", {}).get(name)
                inherited = {}
                if old is not None:
                    inherited = {"contract": old["contract"], "source_profile": old["source_profile"]}
                    context = old.get("build_context", {})
                    selection = context.get("canonical_selection", {}).get(name)
                    if selection is not None:
                        inherited["canonical_symbols"] = selection
                    normalizer = context.get("normalizer_overrides", {}).get(name)
                    if normalizer is not None:
                        inherited["normalizer"] = normalizer
                effective[name] = {**inherited, **overrides.get(name, {})}
            saved = store._raw_records(ids, shared_profiles=True)
            membership_ids = set()
            listing_ids = set(listing_history[-1]["stock_basic_raw_batch_ids"]) if listing_history else set()
            if listing_history and not listing_ids <= set(ids):
                raise DataError("vendor listing rebuild omitted original stock_basic Raw")
            if vendor_history:
                membership_ids.update(vendor_history[-1]["index_weight_raw_batch_ids"])
                if not membership_ids <= set(ids):
                    raise DataError("vendor membership rebuild omitted original index_weight Raw")
            for batch_id in ids:
                raw = saved[batch_id]
                if (special_membership and batch_id in membership_ids) or batch_id in listing_ids:
                    continue
                domain = raw.get("domain")
                if domain not in grouped:
                    raise DataError(f"Raw {batch_id} does not belong to a selected domain")
                contract, profile, normalizer = (raw.get("contract"), raw.get("source_profile"),
                                                  raw.get("normalizer"))
                if not isinstance(contract, dict) or not isinstance(profile, dict) or not isinstance(normalizer, str):
                    raise DataError(f"Raw {batch_id} lacks domain/contract/normalizer metadata for rebuild")
                override = effective[domain]
                if "canonical_symbols" in override:
                    if "canonical_symbols" not in raw["request"]:
                        raise DataError("offline selection requires an original canonical_symbols request")
                    old_map = raw["source_profile"].get("identity_map", {})
                    new_map = override.get("source_profile", raw["source_profile"]).get("identity_map", {})
                    if any(new_map.get(code) != identity for code, identity in old_map.items()):
                        raise ConflictError("offline symbol expansion cannot remap a stable identity")
                    if any(code not in new_map for code in override["canonical_symbols"]):
                        raise DataError("offline selected symbols require explicit stable identities")
                batch = _SavedBatch(store, raw, override)
                grouped[domain].append((batch, raw))
            if any(not entries for entries in grouped.values()):
                raise DataError("each selected domain requires at least one selected Raw batch")
            replacements = {}
            if listing_history:
                from .vendor_listing import build_vendor_listing_domain

                rebuilt_listing = None
                for ordinal, config in enumerate(listing_history):
                    rebuilt_listing = build_vendor_listing_domain(
                        store, stock_basic_raw_batch_ids=config["stock_basic_raw_batch_ids"],
                        identity_map=config["identity_map"],
                        operation_id="offline_vendor_listing_rebuild",
                        old_domain=rebuilt_listing, old_snapshot_id=base_snapshot,
                        prior_source_config=(listing_history[ordinal - 1] if ordinal else None))
                rebuilt_listing["build_context"] = {**deepcopy(listing_old["build_context"]),
                                                     "builder": build_context["builder"],
                                                     "operation_id": operation_id,
                                                     "rebuild_config": {k: v for k, v in build_context.items() if k != "builder"}}
                replacements["listing_events"] = rebuilt_listing
            if vendor_history:
                from .vendor_membership import build_vendor_membership_domain

                rebuilt = None
                for ordinal, config in enumerate(vendor_history):
                    rebuilt = build_vendor_membership_domain(
                        store, index_weight_raw_batch_ids=config["index_weight_raw_batch_ids"],
                        identity_map=config["identity_map"],
                        verified_through=config["verified_through"],
                        between_snapshots=config["between_snapshots"],
                        operation_id="offline_vendor_membership_rebuild",
                        old_domain=rebuilt, old_snapshot_id=base_snapshot,
                        prior_source_config=(vendor_history[ordinal - 1] if ordinal else None))
                rebuilt["build_context"] = {**deepcopy(membership_old["build_context"]),
                                             "builder": build_context["builder"],
                                             "operation_id": operation_id,
                                             "rebuild_config": {k: v for k, v in build_context.items() if k != "builder"}}
                replacements["universe_membership"] = rebuilt
            selections = {name: list(override["canonical_symbols"])
                          for name, override in effective.items() if "canonical_symbols" in override}
            if selections:
                build_context = {**build_context, "canonical_selection": selections}
            normalizers = {name: override["normalizer"] for name, override in effective.items()
                           if "normalizer" in override}
            if normalizers:
                build_context = {**build_context, "normalizer_overrides": normalizers}
            state["raw_batch_ids"] = list(ids)
            store.write_operation(operation_id, state)
            return _publish(store, base_snapshot, base, grouped, build_context, promote,
                            operation_id, state, replace=True,
                            replacement_domains=replacements)
        except Exception as exc:
            _fail(store, operation_id, state, exc)
            if isinstance(exc, DataError):
                raise
            raise DataError(f"rebuild failed: {exc}") from exc
