# Tushare financial revisions

New financial publication uses `financial_events.v3`. Its embedded source contract
`tushare_fina_indicator_revision.v1` applies only to `fina_indicator`.

Within one immutable RawBatch, group rows by security, report period and
announcement date. String flag `"1"` supersedes `"0"`. Conflicting rows with the
same flag, missing flags and unknown flags are rejected. Identical duplicates
retain the existing deduplication behavior.

For example, the same response contains ROE `2.4915`, flag `"0"`, and ROE
`2.4916`, flag `"1"`. Canonical ROE is `0.024916`. This preference does not supply
a publication timestamp, change PIT qualification, or order separate Raw
observations. Generic PIT selection is unchanged.

The complete original response remains in Raw. The canonical observation points
to that Raw; the DomainCommit binds the versioned contract and its digest, the
builder revision `tushare-financial-builder.v2`, and implementation content.

Historical financial v1/v2 artifacts still load and replay under their original
semantics. They are read-only. Rebuild financial v3 using the explicit existing
Raw closure and security-master dependency, with no parent (`new_lineage=True`
for the public operation). The old financial commit cannot parent v3. No supplier
request is needed for this correction. Existing Snapshots and Views are unchanged;
any replacement consuming the new lineage requires separate authorization.
