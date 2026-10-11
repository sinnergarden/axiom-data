"""Public local Data service: explicit writes, snapshot-bound read-only queries.

This facade uses inline-domain Parquet snapshots.
"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Any

from .protocols import DataBatch, EventQuery, QueryError, QuerySpec, UpdateRequest
from .storage import LocalStore


class Data:
    """Own a movable data root and a small collection of fixed-snapshot readers.

    Construction and reading do not create directories, contact suppliers or
    publish artifacts. Resolve ``current`` at a job boundary, then use the
    returned ID throughout that experiment or decision batch. Reader caches are
    process-local optimizations; correctness never depends on their presence.
    """

    def __init__(self, root: str | Path, *, cache_bytes: int = 64 * 1024 * 1024,
                 max_readers: int = 4):
        if type(max_readers) is not int or max_readers < 1:
            raise ValueError("max_readers must be a positive integer")
        if type(cache_bytes) is not int or cache_bytes < 0:
            raise ValueError("cache_bytes must be a nonnegative integer")
        self.store = LocalStore(root)
        self.cache_bytes = cache_bytes
        self.max_readers = max_readers
        self._readers: OrderedDict[str, Any] = OrderedDict()
        self._column_source = None

    def resolve(self, reference: str = "current") -> str:
        """Resolve an alias once; no downstream read follows mutable pointers."""
        return self.store.resolve(reference)

    def _reader(self, snapshot: str, *, refresh: bool = False):
        if snapshot in {"current", "latest"}:
            raise QueryError("resolve current once before calling read")
        source=self._column_source() if self._column_source is not None else None
        if source is not None and not source._closed and (refresh or snapshot!=source._snapshot_id):
            owned=source._snapshot_id
            source.close()
            # A revoked owner must not keep its old Snapshot alongside a newly
            # loaded Reader. Ordinary reader caching remains unchanged otherwise.
            self._readers.pop(owned,None)
        if refresh:
            self._readers.pop(snapshot, None)
        reader = self._readers.pop(snapshot, None)
        if reader is None:
            from .reader import SnapshotQueryReader
            reader = SnapshotQueryReader(self.store, snapshot, cache_bytes=self.cache_bytes)
        self._readers[snapshot] = reader
        while len(self._readers) > self.max_readers:
            self._readers.popitem(last=False)
        return reader

    def open_column_source(self, *, snapshot: str, limits):
        """Own bounded in-process columns for a fixed Snapshot, without a View.

        limits supplies cache_bytes and max_working_bytes. The owner selects
        market_daily open/close or adjustment_factors factor with QuerySpec and
        shares the ordinary Reader PIT/revision algorithm. Only explicit legacy
        materialization builds records/by_key. close, Reader refresh, changed
        source bytes or a process boundary revoke outstanding selections.
        The column API requires flat numeric physical values; ordinary read
        retains its schema support and uses its original path for other types.
        changed_keys tracks actual fact dependencies; pure cutoff moves still
        change full query_binding/selection_ref without marking all keys updated.
        Parquet/normal Snapshot decoder transients still need an external RSS
        guard. Opening replaces cached Reader graphs, never adds a second one.
        """
        from .column_source import ColumnSource
        return ColumnSource(self,snapshot=snapshot,limits=limits)

    def read(self, *, snapshot: str, query: QuerySpec) -> DataBatch:
        """Read declared fields and knowledge cutoffs from a concrete snapshot.

        Visibility is applied before revision selection and derivation. Expected
        missing facts remain explicit nulls with reasons; unsupported semantics
        or missing required domains raise a descriptive error. This method may
        reuse memory, but does not write a View, cache file or business artifact.
        """
        return self._reader(snapshot).read(query)

    def read_market(self, *, snapshot: str, query: QuerySpec) -> DataBatch:
        """Read execution-side facts, preserving their separate replay purpose.

        The Runtime is responsible for releasing events according to its clock.
        This result must never be substituted for an earlier decision FactBatch.
        """
        if query.purpose != "market_replay":
            raise QueryError("read_market requires purpose='market_replay'")
        if query.price_basis != "unadjusted":
            raise QueryError("market replay requires unadjusted prices")
        return self.read(snapshot=snapshot, query=query)

    def members(self, *, snapshot: str, query: QuerySpec) -> DataBatch:
        """Read historical membership with explicit effective and knowledge time."""
        if query.domain != "universe_membership":
            raise QueryError("members requires the universe_membership domain")
        return self.read(snapshot=snapshot, query=query)

    def events(self, *, snapshot: str, query: EventQuery) -> DataBatch:
        """Read native economic events after PIT selection, without daily filling."""
        from .event_reader import read_events
        if snapshot in {"current", "latest"}:
            raise QueryError("resolve current once before calling events")
        return read_events(self.store, snapshot, query)

    def states(self, *, snapshot: str, query: QuerySpec) -> DataBatch:
        """Explain requested dates using calendar, identity and suspension evidence.

        Closed dates are diagnostic rows, not synthetic canonical sessions.
        Missing source facts never become zero prices or inferred suspensions.
        """
        from .local_states import read_states
        if snapshot in {"current", "latest"}:
            raise QueryError("resolve current once before calling states")
        return read_states(self.store, snapshot, query)

    def plan_scope(self, *, snapshot: str, **scope):
        """Bind history union, warmup and outside-pool holdings to a Snapshot."""
        from .local_states import plan_research_scope
        if snapshot in {"current", "latest"}:
            raise QueryError("resolve current once before planning scope")
        return plan_research_scope(self.store, snapshot, **scope)

    def export_qlib(self, *, snapshot: str, queries, destination, **options):
        """Explicitly materialize numeric daily queries as an immutable Qlib view.

        The selected Snapshot, QuerySpecs and PIT cutoffs are frozen. Export
        reads monthly batches, preserves nulls and native units, never contacts
        a source or advances current. Events require an explicit daily projection
        owned by the consumer; they are not automatically flattened here.
        """
        from .qlib_export import export_qlib
        return export_qlib(self, snapshot=snapshot, queries=queries,
                           destination=destination, **options)

    def export_native_view(self, *, snapshot: str, reads, destination, limits, source_symbol_block=64):
        """Explicitly save original native selections in bounded JSON parts.

        Full Query and PIT identities remain unchanged. No supplier or fact
        root writes. Use this Data instance sequentially until export finishes;
        limits include the normal Snapshot baseline and shared Reader cache.
        Existing destinations are refused; failure publishes no final view.
        """
        from .native_view import export_native_view
        return export_native_view(self, snapshot=snapshot, reads=reads,
                                  destination=destination, limits=limits,
                                  source_symbol_block=source_symbol_block)

    def export_review_display(self, *, snapshot: str, price_query: QuerySpec,
                              factor_query: QuerySpec, anchor_session: str,
                              destination, security_query: EventQuery | None = None,
                              event_queries=()):
        """Explicitly save retrospective OHLCV and optional labels/events.

        Public queries fix the Snapshot, one historical_exploration cutoff,
        scope and native units. Only OHLC is adjusted; source facts/receipts,
        model inputs, current and accounts are unchanged. The complete price
        span ends at anchor_session. Existing destinations are refused; failed
        exports leave no final artifact. Nothing contacts a supplier. Events
        and names are requested explicitly and retain their Reader provenance.
        No ordinary read requires this export or an on-disk result cache.
        """
        from .protocols import ConflictError
        from .review_display import save_review_display
        target = Path(destination).resolve()
        if target.exists():
            raise ConflictError("review display destination already exists")
        if any(target.is_relative_to(self.store.root.resolve() / name)
               for name in ("raw", "canonical", "snapshots", "operations")):
            raise QueryError("review display destination must be outside fact storage")
        self._reader(snapshot, refresh=True)
        prices = self.read(snapshot=snapshot, query=price_query)
        factors = self.read(snapshot=snapshot, query=factor_query)
        names = self.events(snapshot=snapshot, query=security_query) if security_query is not None else None
        events = tuple(self.events(snapshot=snapshot, query=q) for q in event_queries)
        return save_review_display(prices, factors, anchor_session=anchor_session,
                                   destination=target, security_master=names, events=events)

    def update(self, *, base_snapshot: str | None, request: UpdateRequest):
        """Persist supplied source observations and atomically publish changes.

        Source acquisition is explicit and injectable. Successful Raw survives a
        later normalization failure; failure never promotes a partial Snapshot.
        A duplicate observation is retained even when fact state is unchanged.
        """
        from .updates import apply_update
        if base_snapshot in {"current", "latest"}:
            raise QueryError("resolve the base snapshot before starting an update")
        return apply_update(self.store, base_snapshot=base_snapshot, request=request)

    def select_raw(self, *, domains, receipt_cutoff):
        """Preview successful domain Raw through an inclusive actual receipt.

        Use the returned explicit IDs for historical field expansion; ordinary
        snapshot replay uses that snapshot's own IDs. No network, files, cache
        or publication. Preserve the preview with the rebuild request.
        """
        if domains is None:
            raise QueryError("historical Raw selection needs explicit domains")
        return self.store.select_raw(domains=domains, receipt_cutoff=receipt_cutoff)

    def rebuild(self, *, base_snapshot: str, raw_batch_ids, domains,
                operation_id: str, build_context, promote: bool = True,
                domain_overrides=None):
        """Rebuild selected domains from saved Raw, preserving observation time.

        This never downloads replacement history or modifies old raw/canonical
        files. Unselected domain state and provenance are retained exactly.
        ``domain_overrides`` explicitly replaces selected domains' contract,
        source_profile or normalizer when correcting a mapping or adding fields;
        original source bytes and their observation timestamps remain unchanged.
        A ``canonical_symbols`` override selects explicitly bound source codes
        already present in saved whole-market Raw. It is recorded as a derived
        selection; missing source rows remain missing, without refetching.
        For explicit dividend v3, ``canonical_event_keys`` selects complete
        security_id/report_period/announcement_date/process_status dictionaries
        with canonical ISO dates. It retains every candidate of each chosen key
        and is saved as a derived scope for subsequent rebuilds; original Raw
        requests, payloads and actual receipts are never modified.
        A single-security dividend v3 Raw request with an explicitly bound
        original params.ts_code may omit canonical_symbols; a rebuild can
        supply both full symbol and Native-key scopes as derived overrides.
        An unbound or whole-market request cannot use this exception.
        Without explicit overrides, the base Snapshot's effective contract,
        source_profile and saved canonical selection are replayed. New operation
        IDs and the ordinary CLI therefore retain added fields and symbols.
        Explicit normalizer overrides are saved for subsequent rebuilds.
        Supply the full Raw closure and desired symbol scope for each replaced
        domain. Only affected domains are rebuilt; old Snapshots stay readable.
        """
        from .updates import rebuild_from_raw
        return rebuild_from_raw(self.store, base_snapshot=base_snapshot,
                                raw_batch_ids=raw_batch_ids, domains=domains,
                                operation_id=operation_id,
                                build_context=build_context, promote=promote,
                                domain_overrides=domain_overrides)

    def inspect(self, snapshot: str, *, required_scope: QuerySpec | None = None) -> dict:
        """Describe a requested scope; a manifest inventory is not a data audit.

        Without a query, return stored coverage and partition counts only. With
        a query, also report missing/not-yet-visible requested cells. No source
        is contacted, unrelated domains are not read, and nothing is repaired.
        """
        if snapshot in {"current", "latest"}:
            raise QueryError("resolve the snapshot before inspecting a scope")
        manifest = self.store.load_snapshot(snapshot)
        report = {
            "snapshot_id": snapshot,
            "domains": {name: {"coverage": domain.get("coverage", {}),
                               "partitions": len(domain["partitions"])}
                        for name, domain in manifest["domains"].items()},
            "status": "inventory_only", "issues": [],
            "note": "Stored coverage is not an independent source completeness audit.",
        }
        if required_scope is None:
            return report
        batch = self.read(snapshot=snapshot, query=required_scope)
        for field, metadata in batch.field_meta.items():
            for row in metadata.get("by_key", []):
                if row.get("missing_reason"):
                    report["issues"].append({"field": field, **row})
        report.update(status="limited" if report["issues"] else "query_complete",
                      query=batch.context["query"], rows=len(batch.frame))
        return report

    def dictionary(self, *, snapshot: str | None = None, domains=None,
                   raw_batch_id: str | None = None) -> dict:
        """Read supported fact mappings, optionally overlaid by a fixed Snapshot.

        Returns JSON-ready field meanings, source/canonical units, conversions,
        time rules and query methods. Snapshot availability is declaration only;
        queries establish actual values/visibility. No writes or supplier calls.
        Optional raw_batch_id inspects only that saved response's extra columns.
        """
        from .dictionary import fact_dictionary
        return fact_dictionary(self.store, snapshot=snapshot, domains=domains,
                               raw_batch_id=raw_batch_id)

    def clear_cache(self) -> None:
        """Drop local readers; never deletes data or persisted research results."""
        source=self._column_source() if self._column_source is not None else None
        if source is not None: source.close()
        self._readers.clear()
