"""Public contracts for axiom-data."""

from .artifacts import (
    ArtifactConflictError,
    ArtifactError,
    ArtifactNotFoundError,
    CatalogEntry,
    DataSnapshot,
    DataSnapshotRef,
    DomainCommit,
    MarketDomainBuilder,
    RawBatch,
    RawBatchRef,
    create_snapshot,
    list_catalog,
    load_domain_commit,
    load_raw_batch,
    load_snapshot,
    validate_snapshot_closure,
    lookup_catalog,
    rebuild_catalog,
    validate_domain_commit_closure,
    write_raw_batch,
)
from .build import (
    BuildApplication,
    BuildContractError,
    BuildExecutor,
    BuildRequest,
    DomainCommitRef,
)
from .consumption import (
    MARKET_VIEW_FIELDS,
    QlibView,
    QlibViewReader,
    QlibViewRef,
    SnapshotReader,
    build_qlib_view,
    compare_direct_and_qlib,
    load_qlib_view,
)
from .layout import DataRootLayout, LayoutError
from .reconciliation import (
    RECONCILIATION_CATEGORIES,
    independent_tushare_market_expectations,
    load_frozen_qsys_market,
    reconcile_market,
)
from .tushare import (
    TushareCollector,
    TushareMarketBuilder,
    load_tushare_source_profile,
    tushare_source_profile_digest,
)
from .reference_source import (
    TushareReferenceBuilder,
    TushareReferenceCollector,
    reference_source_profile_digest,
    load_reference_source_profile,
)
from .views import (
    DerivedView,
    DerivedViewRef,
    FactView,
    build_adjusted_price_view,
    build_market_replay_view,
    load_adjusted_price_view,
    load_market_replay_view,
)
from .reference_reconciliation import reconcile_reference_raw_mapping
from .evidence import validate_pr5_evidence
from .operations import plan_daily, daily, bootstrap, repair, inspect_scope
from .view_operation import materialize_views
from .recovery import verify_recovery
from .admission_plan import requirement_registry_digest, validate_admission_plan
from .gate_a import make_gate_a_plan, validate_gate_a, plan_historical_views, validate_terminal_evidence_plan, validate_terminal_evidence
from .full_admission import full_admission, validate_full_admission
from .daily_acceptance import execute_daily_case, validate_daily_evidence
from .notebook_acceptance import execute_notebook_smoke, validate_notebook_smoke


__all__ = [
    "full_admission", "validate_full_admission", "execute_daily_case",
    "validate_daily_evidence", "execute_notebook_smoke", "validate_notebook_smoke",
    "make_gate_a_plan",
    "validate_gate_a",
    "plan_historical_views",
    "validate_terminal_evidence_plan",
    "validate_terminal_evidence",
    "validate_admission_plan",
    "requirement_registry_digest",
    "verify_recovery",
    "materialize_views",
    "plan_daily",
    "daily",
    "bootstrap",
    "repair",
    "inspect_scope",
    "ArtifactConflictError",
    "ArtifactError",
    "ArtifactNotFoundError",
    "BuildApplication",
    "BuildContractError",
    "BuildExecutor",
    "BuildRequest",
    "CatalogEntry",
    "DataSnapshot",
    "DataSnapshotRef",
    "DataRootLayout",
    "DomainCommit",
    "DomainCommitRef",
    "DerivedView",
    "DerivedViewRef",
    "FactView",
    "LayoutError",
    "MARKET_VIEW_FIELDS",
    "MarketDomainBuilder",
    "QlibView",
    "QlibViewReader",
    "QlibViewRef",
    "RECONCILIATION_CATEGORIES",
    "RawBatch",
    "RawBatchRef",
    "SnapshotReader",
    "TushareCollector",
    "TushareReferenceBuilder",
    "TushareReferenceCollector",
    "TushareDm1Builder",
    "TushareDm1Collector",
    "TushareMarketBuilder",
    "build_qlib_view",
    "build_adjusted_price_view",
    "build_market_replay_view",
    "compare_direct_and_qlib",
    "create_snapshot",
    "independent_tushare_market_expectations",
    "list_catalog",
    "load_domain_commit",
    "load_raw_batch",
    "load_frozen_qsys_market",
    "load_qlib_view",
    "load_adjusted_price_view",
    "load_market_replay_view",
    "load_snapshot",
    "validate_snapshot_closure",
    "load_tushare_source_profile",
    "load_reference_source_profile",
    "load_dm1_source_profile",
    "lookup_catalog",
    "rebuild_catalog",
    "reconcile_market",
    "reconcile_dm1_raw_mapping",
    "reconcile_reference_raw_mapping",
    "tushare_source_profile_digest",
    "dm1_source_profile_digest",
    "reference_source_profile_digest",
    "validate_domain_commit_closure",
    "validate_pr5_evidence",
    "write_raw_batch",
]


# Compatibility exports for historical callers.
TushareDm1Collector = TushareReferenceCollector
TushareDm1Builder = TushareReferenceBuilder
load_dm1_source_profile = load_reference_source_profile
dm1_source_profile_digest = reference_source_profile_digest
reconcile_dm1_raw_mapping = reconcile_reference_raw_mapping
