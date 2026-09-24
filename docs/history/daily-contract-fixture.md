# Daily contract admission fixture

`src/axiom_data/scope/daily_contract_snapshot.zip` is the exact immutable closure
of `snapshot-fa3d8b80c729edd2e139a49c82b3dea868bb5639998ba7cc84f540c0623fe786`,
already recorded in [the historical run manifest](../../reports/pr7/run_manifest.json).
It contains 18 DomainCommits, 105 RawBatches and the Snapshot: 389 files, 480,034
compressed bytes. Original manifest, payload and digest bytes are unchanged.
Catalog, pointers, operations and Views are excluded.

Gate A restores the archive only into a temporary root. The regular loaders
validate the fixture; no special loader or admission exception is installed.
A local fixture client supplies one empty indicator response for `688981.SH`,
covering `2025-01-01` through `2025-03-31`, observed at `2025-06-14T00:00:00Z`.
The regular collector publishes that temporary Raw, and both daily calls bind
the exact Raw ID through `observed_raw_batch_ids`. They never contact a supplier.

The legal request with `financial_events.v1` must raise
`LEGACY_CONTRACT_READ_ONLY` before operation writes. The same request with the
current writable contract must build a candidate whose real closure loads and
declares that contract. Neither case claims production readiness. All temporary
files are removed after the probe; the formal data root is not accessed.
