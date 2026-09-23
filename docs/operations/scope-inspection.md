# Canonical session coverage inspection

`axiom_data.inspect_scope(root, snapshot_id, domain=..., symbols=...,
start_session=..., end_session=..., fields=...)` validates one concrete Snapshot
and audits its canonical session records. The CLI accepts the same keyword
arguments from `inspect --snapshot ID --scope scope.json`.

Expected sessions use each security's own exchange calendar and listed interval.
The result distinguishes absent sessions, present nulls, and non-null values
(including zero). Interior gaps are represented by an integer bit mask encoded
as hexadecimal; bit positions refer to `session_mask_axis`. Counts do not infer
continuity from first/last dates. Contract, builder and validation references
accompany the actual scope. No files or source data are written.

For revision domains this is the union of stored canonical revisions, including
later observations. It is explicitly current diagnostic context. It is not a
historical Fact, a PIT result or admission PASS. Historical queries continue to
use the Reader's PIT selectors. Benchmark and membership/financial event grains
require their own scope checks and are not accepted by this session audit.

Tests cover an interior missing session, explicit null, zero, different exchange
sessions, unknown securities, missing calendar coverage and invalid fields.
Earlier suite results are in [historical evidence](../history/V1_WORK_LOG.md).
