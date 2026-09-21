# Tushare financial revisions

New financial publication uses `financial_events.v4` and its embedded
`tushare_financial_revision.v2` source contract. It applies to `income`,
`balancesheet`, `cashflow` and `fina_indicator`.

Within one immutable RawBatch, group rows by security, report period, report
type and **both** publication-date fields (`ann_date`, `f_ann_date`). Indicator
responses do not provide `f_ann_date`; its absence does not supply another date.
Only inside that exact group does string flag `"1"` supersede `"0"`.

For example, two income rows with the same report and both dates equal may
contain flag `"0"` / revenue 100 and flag `"1"` / revenue 120. V4 retains 120.
If `f_ann_date` differs, both versions remain: an earlier cutoff must still see
the earlier publication under the existing best-effort policy. The flag never
orders separate Raw observations or separate publication dates.

Different values with the same preferred flag remain separate canonical
revisions. The existing PIT selector rejects a simultaneous tie with
`ambiguous simultaneous revisions`; publication does not claim that every PIT
query is available. Unknown flags fail source admission. Identical content
continues to use the existing deduplication and observation history.

Raw bytes, PIT qualification and generic PIT selection are unchanged. The
canonical observations retain their Raw references. No security filter can
remove a conflict before full-scope admission. Content fingerprints and source
observations retain their existing shapes; v4 has a new contract and lineage.

## Historical contracts

Financial v1/v2/v3 remain readable and replay with their declared semantics;
all three are read-only for new publication. V3's
`tushare_fina_indicator_revision.v1` applies only to indicator rows with the
same security/report/ann_date in one Raw and rejects conflicting equal flags
at normalization. V4 does not retroactively alter that behavior.

Rebuild v4 from explicit existing Raw IDs and a security-master dependency,
with no parent from an older contract. Do not recollect or modify historical
artifacts. Snapshot replacement and View materialization are separate operations.

## Rebuild execution

Financial normalization consumes Raw batches one at a time. The earliest
observation and its deterministic source reference are independent of input
order; final revisions and observations keep their existing ordering. Canonical
rows and observation metadata still occupy memory proportional to the output.

The writer reuses Raw refs already captured during source admission and obtains
input IDs from the explicit build request. It does not reload payloads merely
to recover IDs/refs. Mapping replay, source completeness, staged validation and
published closure validation remain in place; this creates no persistent cache
or alternate financial calculation path.

## Unorderable leaf facts

`financial_leaf_resolution.v1` applies at consumption, after the existing PIT
policy has found its highest-ranked candidates. It does not change canonical
observations or supplier ordering. `SnapshotReader.as_of('financial_events')`
uses this policy; generic `select_revisions` retains strict tie rejection.

For example, two equally ranked balance-sheet observations may both report
assets of 500, while inventory is null in one and 20 in the other. Assets remains
500. Inventory becomes null with `missing_reason=AMBIGUOUS_SOURCE_REVISION` and
Fact metadata `validity=unavailable`. A null/value difference is a conflict;
matching nulls retain a normal missing reason.

There is no selected source winner. The combined consumption result has null
`revision_id`/`source_ref`, all competing `component_revisions`, and a separate
`resolution_id`. `ambiguous_fields` names only fields with differing values.
An equally valued leaf still retains the competing sources as provenance.

Single-quarter and TTM outputs propagate this unavailable reason when an input
for that specific field is ambiguous. An unrelated field remains usable.
The new derived contract is `financial_stable.v3`.

Full-scope View admission records `financial_ambiguities` before security
projection. Each entry contains the logical record, conflicting fields, source
components and half-open PIT interval `[from,to_exclusive)`. The census includes
historical conflicts even when a later visible revision resolves them. An open
end means no ending transition is known at the query cutoff; future observations
do not change an earlier cutoff's evidence. Equal-valued revision ties are also
recorded, with no conflicting fields, rather than silently discarding sources.

New Fact/Qlib artifacts declare `pr6_fact_view.v3`, `typed_fact.v2` and the
resolution policy. Published v1/v2 Views replay their original semantics and
identity projection. Financial canonical v1–v4 loaders and Raw stay unchanged;
this consumption change requires no financial DomainCommit rebuild.
