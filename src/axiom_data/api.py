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

    def resolve(self, reference: str = "current") -> str:
        """Resolve an alias once; no downstream read follows mutable pointers."""
        return self.store.resolve(reference)

    def _reader(self, snapshot: str):
        if snapshot in {"current", "latest"}:
            raise QueryError("resolve current once before calling read")
        reader = self._readers.pop(snapshot, None)
        if reader is None:
            from .reader import SnapshotQueryReader
            reader = SnapshotQueryReader(self.store, snapshot, cache_bytes=self.cache_bytes)
        self._readers[snapshot] = reader
        while len(self._readers) > self.max_readers:
            self._readers.popitem(last=False)
        return reader

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
        self._readers.clear()
