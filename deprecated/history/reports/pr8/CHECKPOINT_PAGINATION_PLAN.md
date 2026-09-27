# PR8 checkpoint recovery / pagination selector node

Base: aa190103245b205b89bbf9f9be4723d33919ef42. NO_BULK_BUILD.

1. **Complete.** Reproduce and implement current-validation checkpoint normalization and durable split supersession; implement shared per-page request/payload validation.
2. **Complete.** Independently review both changes and exercise legacy completed-to-split, child failure/resume, selector mismatch and mapper/public parity.
3. **Complete.** Run the full suite and unchanged Gate A validator; persist logs and exact remaining blockers.
4. **Publication verification.** Validate the committed code/evidence and remote reference; deliver the requested report.

Done means: same-run legacy 100-row checkpoint actually executes children; completed/failed never overlap after normalization; split graph and completed children are stable across resume; invalid parent cannot become canonical. Every evidence page must pass the existing profile/selector/payload semantics as well as page-sequence checks. Full tests and diff/show checks pass, independent review findings are resolved, Gate A is honestly recomputed, and commit/evidence are delivered. No bulk, baseline, new domain or final acceptance work.
