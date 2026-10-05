"""Axiom Data: immutable observations, snapshots and explicit queries.

Imports stay lazy so CLI help and planning do not initialize data dependencies.
"""

from importlib import import_module

_EXPORTS = {
    "reviewed_fund_share_conversion_batch": ("fund_share_conversions", "reviewed_fund_share_conversion_batch"),
    "attach_public_evidence": ("public_evidence", "attach_public_evidence"),
    "listing_catalogue_evidence": ("universe_sources", "listing_catalogue_evidence"),
    "plan_reference_bootstrap": ("universe_sources", "plan_reference_bootstrap"),
    "prepare_universe_scope": ("universe_sources", "prepare_universe_scope"),
    "resolve_index_preset": ("universe_sources", "resolve_index_preset"),
    "run_reference_bootstrap": ("universe_sources", "run_reference_bootstrap"),
    "save_prepared_scope": ("universe_sources", "save_prepared_scope"),
    'BulkJobPlan': ('bulk_jobs', 'BulkJobPlan'),
    'EtfJobPlan': ('etf_jobs', 'EtfJobPlan'),
    'prepare_etf_references': ('etf_jobs', 'prepare_etf_references'),
    'plan_etf_job': ('etf_jobs', 'plan_etf_job'),
    'run_etf_job': ('etf_jobs', 'run_etf_job'),
    'etf_job_status': ('etf_jobs', 'etf_job_status'),
    'estimate_etf_job': ('etf_jobs', 'estimate_etf_job'),
    'verify_etf_job': ('etf_jobs', 'verify_etf_job'),
    'audit_etf_snapshot': ('etf_jobs', 'audit_etf_snapshot'),
    'FullSourcePlan': ('full_sources', 'FullSourcePlan'),
    'ConflictError': ('protocols', 'ConflictError'),
    'CoverageError': ('protocols', 'CoverageError'),
    'Data': ('api', 'Data'),
    'DataBatch': ('protocols', 'DataBatch'),
    'DataError': ('protocols', 'DataError'),
    'EventQuery': ('protocols', 'EventQuery'),
    'IngestBatch': ('protocols', 'IngestBatch'),
    'OperationResult': ('protocols', 'OperationResult'),
    'QueryError': ('protocols', 'QueryError'),
    'QuerySpec': ('protocols', 'QuerySpec'),
    'SourceResponseError': ('live_client', 'SourceResponseError'),
    'TushareHttpClient': ('live_client', 'TushareHttpClient'),
    'UpdateRequest': ('protocols', 'UpdateRequest'),
    'adjust_prices': ('derived', 'adjust_prices'),
    'project_review_display': ('review_display', 'project_review_display'),
    'save_review_display': ('review_display', 'save_review_display'),
    'load_review_display': ('review_display', 'load_review_display'),
    'audit_snapshot': ('verification', 'audit_snapshot'),
    'apply_saved_raw': ('updates', 'apply_saved_raw'),
    'bounded_weight_observations': ('universe_sources', 'bounded_weight_observations'),
    'bulk_job_status': ('bulk_jobs', 'bulk_job_status'),
    'collect_event_response': ('event_sources', 'collect_event_response'),
    'estimate_bulk_job': ('bulk_jobs', 'estimate_bulk_job'),
    'estimate_full_sources': ('full_sources', 'estimate_full_sources'),
    'full_source_status': ('full_sources', 'full_source_status'),
    'event_source_profile': ('event_sources', 'event_source_profile'),
    'export_bundle': ('portable', 'export_bundle'),
    'export_qlib': ('qlib_export', 'export_qlib'),
    'verify_qlib_export': ('qlib_export', 'verify_qlib_export'),
    'import_bundle': ('portable', 'import_bundle'),
    'listing_identity_candidates': ('universe_sources', 'listing_identity_candidates'),
    'plan_bulk_job': ('bulk_jobs', 'plan_bulk_job'),
    'plan_full_sources': ('full_sources', 'plan_full_sources'),
    'plan_full_listing_requests': ('universe_sources', 'plan_full_listing_requests'),
    'plan_weight_months': ('universe_sources', 'plan_weight_months'),
    'run_bulk_job': ('bulk_jobs', 'run_bulk_job'),
    'run_full_sources': ('full_sources', 'run_full_sources'),
    'single_quarter': ('financial', 'single_quarter'),
    'ttm': ('financial', 'ttm'),
    'validate_identity_bindings': ('universe_sources', 'validate_identity_bindings'),
    'verify_bundle': ('portable', 'verify_bundle'),
    'verify_bulk_job': ('bulk_jobs', 'verify_bulk_job'),
    'verify_full_sources': ('full_sources', 'verify_full_sources'),
}

__all__ = list(_EXPORTS)


def __getattr__(name):
    try:
        module, attribute = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    value = getattr(import_module('.' + module, __name__), attribute)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
