# Immutable canonical corrections validation

Base: reviewed main `feb97f8d7bae7b166db3f31d20fa6e7722c9dbb8` (PR31).
Scope: canonical patch publication/loading, build/closure replay and repair input/resume.
No Views, full admission, collection, formal-data repair, bulk or promotion.

The new `axiom_data.patches` module publishes `canonical_patch.v1` artifacts in the
existing patches directory. Domain commits explicitly bind that field protocol,
ordered immutable refs and inherited correction semantics. Old empty-patch
commits retain their interpretation. Source-replaying families retain complete
Raw ancestry even when every canonical row is tombstoned. Patch-only builds reuse
untouched partitions. Source evidence is separate from ingestion coverage.

The existing source mapper proves logical keys and observation/availability
bindings. A correction can change economic interpretation using valid revision
fingerprints, but cannot invent source knowledge. Unsupported mapper/key/time
corrections, synthetic scope pointers and inconsistent membership group-state
changes fail; see `docs/operations/patches.md` for the supported boundary.

## Verification

48 distinct targeted tests passed across the following runs:

- `test_immutable_corrections`: 13 PASS. Real immutable fixture-derived daily,
  reference, event and financial examples; insert/replace/tombstone, old digest,
  collision/key/domain, corrupt evidence/patch, revision/PIT rejection,
  unsupported source key/time, numeric revision correction, actual later
  observation conflict, complete tombstone ancestry, partition reuse,
  explicit protocol/legacy behavior, no-change preservation, actual frozen
  patch-only repair/resume and unrelated Snapshot domain reuse.
- `test_public_source_scope` and `test_dm1_security_session_scope`: 13 PASS,
  together with the first 11 patch tests: 24 tests, 56.525 seconds, exit 0.
- Final full-tombstone/no-change probes: 2 PASS, 1.603 seconds, exit 0.
- `test_build`, `test_v1_candidate`, `test_financial_source_revision`,
  `test_universe_acquisition`: 22 PASS in the preceding combined run. That
  run had two test-harness errors (exclusive manifest writer used on an
  existing synthetic copy; misspelled scope test module). Both were corrected
  and covered by the green runs above; it was not a green combined run.

Initial fixture setup required the market builder's existing constructor
arguments; one corruption probe required making its isolated copied Raw file
writable. These harness failures are preserved in the workspace evidence logs.
No product validator was relaxed to make tests pass. Ordinary mapping tests use
one shared prepared context; the repair test executes the real frozen process.
All intentional data perturbations are synthetic probes, not supplier amendments.

Workspace logs: `review/data-prebulk-20260926/immutable-corrections/`:
`targeted-first.log`, `targeted-second.log`, `targeted-third.log`,
`targeted-final.log`, `targeted-confirmed.log`, `final-boundaries.log`.
`git diff --check`: PASS. Full suite and production-scale runtime: NOT RUN.
This evidence establishes the finite correction ticket, not DATA_ACCEPTED or
whole-Design readiness. Independent PR review and merge are separate.
