# PR8 official exchange security boundaries

The 3,601-security master now builds from the frozen stock reference observations
and both original official exchange termination tables. All 228 delisted
identities use the corresponding exchange effective date; 17 supplier date
differences remain preserved in their original stock RawBatch payloads.

`ExchangeSecurityBuilder` is an explicit opt-in builder through the existing
`BuildApplication(...).build(parent, raw_ids, patch_ids, contract_version)` API.
It requires the exact exchange SourceProfile and full exchange coverage,
checks official/supplier listing-date agreement, rejects contradictory live
status and termination evidence, and requires a new root relative to the old
Tushare builder. The accepted `security_master.v1` interval semantics remain
`[list_session, delist_session)`; old artifacts and the legacy non-null source
date rejection remain unchanged. The new source policy is `exchange_security.v1`.

SSE company-level duplicates are resolved by the explicit STOCK_TYPE and
A_STOCK_CODE/B_STOCK_CODE fields, not by overwriting company codes. For example,
600555.SH listed 2001-03-28, whereas 900955.SH listed 1999-01-18. The original
159-row JSON and 208-row XLSX remain intact. XLSX parsing uses Python's standard
library; unsupported shapes, formulas, missing fields and malformed boundaries
fail closed. No dependency was added.

Real immutable commit:
`security_master-d672728d9fc0f45e4fd4a10c1782844f26f979a05c1ca65433428330c4595d02`.
Plan and exact RawBatch refs: `plan.json`. All 3,601 rows and the 228 effective
boundaries were independently checked against the frozen exchange tables.

An isolated offline root containing only those five RawBatches rebuilt the exact
same commit identity and rows with network connections blocked. This validates
the security-master stage, not the complete V1 offline recovery requirement.

The public full-candidate operation selects this builder only when the security
master config explicitly sets `security_boundary_policy=exchange_security.v1`.
Other domains or unknown policy versions fail before candidate execution. No baseline Snapshot or current pointer was published. Industry source
promotion still awaits the previously requested taxonomy-code decision; full
bootstrap, full admission, daily cases and the terminal Notebook remain pending.

Final full suite: 167/167 PASS, zero skips, 111.945 seconds. The five explicit
Raw inputs remain readonly and both primary fixtures match their source SHA256.
