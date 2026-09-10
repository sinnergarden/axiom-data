# New-server reproducibility boundary

This is an acceptance boundary, not a completion claim. The formal target must
be executable from the repository: scripts and explicit configuration must be
available there; Tushare credentials remain local machine configuration and are
never archived. A clone supplies code, not the archived dataset. Inputs use
explicit immutable IDs; `current`, directory order, and mtimes cannot select
artifacts.

There are two valid paths. **Frozen restore** carries Raw payloads and manifests,
source profiles, contracts, configs, code commit, and Snapshot/View references
into a new offline root. Byte digests, identities, closure, and Views are checked;
the original `first_observed_at` and artifact identities are retained. **Repo-only
recollection** uses the same scope, source revisions, vendor times, code, and PIT
policy; historical best-effort logical values should agree. If `vendor_available_at`
is absent and fallback uses a new observation, values may differ. New Raw IDs,
observations, and Snapshot lineage are allowed, but old `first_observed_at` must not
be backfilled and verified status must not be fabricated. A later retrieval alone
cannot establish verified historical availability.

Current status: full baseline is failed; fresh-root recovery is `NOT_VALIDATED`.
The public CLI exists, but the full-run orchestration is not yet a portable repo
entry point. This checkpoint remains pending.
