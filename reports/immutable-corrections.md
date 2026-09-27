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

The existing source mapper proves logical keys, actual economic identity and
observation/availability bindings. A correction can change economic interpretation using valid revision
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

## Source identity review repair

The reviewed head `a3872d1d464b8a282783ab5ce2c5aa2192b0609b` accepted a
holder/financial row with its report period changed to `1999-12-31` while retaining
the original logical key and source times. Recomputed content and observation
fingerprints passed, and the unsupported identity survived Raw replay.
The shared source signature now compares actual canonical identity with the same
existing mapper output. Its source/time/observation signature is unchanged;
numeric interpretation corrections keep their valid new fingerprints.
No second normalizer, key generator, source authority or persistent schema is added.

One bounded inventory covered all existing source key constructions:

| Family | Source-backed canonical identity |
| --- | --- |
| financial_events | endpoint, symbol, report_period, report_type |
| valuation_daily | daily_basic domain mapping, symbol, session |
| holder_count_events / top_holders_reports / forecast_observations | domain/endpoint, symbol, report_period |
| margin_daily / moneyflow_daily | domain/endpoint, symbol, session |
| universe_membership / legacy industry_membership | group_id, symbol, effective_from |
| SW2021 industry_membership | existing validator binds SW2021 classification/group and symbol to logical_event_key |
| corporate_actions | symbol, action_type, announcement_date; mapper's action_id additionally binds Raw end_date and div_proc, which have no separate canonical fields |
| remaining daily/reference builders | exact contract primary_key fields already compared with mapper output |

The comparison does not recompute those keys. Value fields, membership interval
ends and corporate action effective/record/payment dates retain their existing
interpretation and domain validation rules. All source observation/time proof
still applies, including earliest known observations. SW2021's existing
key/classification validator is reused; no duplicate identity formula is added.
Legacy industry contracts remain read-only and retain their existing loader.

Integrated base: PR32 merge `c3b75a8ac1aeed0c636ae205d2729670b42868ad`, merged
into the same branch without conflicts or edits to its source files.

Repair validation:

- PASS: 19 targeted tests, 46.198 seconds: all 16 immutable-correction tests,
  two existing SW2021 tests and PR32's Raw-damage/source-closure regression.
  The new identity regression covers nine currently writable source domains and
  verifies unchanged supported rows build, validate closure and replay in each.
  Both reviewed report-period mutations fail. Additional security, endpoint,
  report-type, daily-session, membership group/start and action identity mutations
  fail with valid row/revision/observation structure. The legacy industry fixture
  exercises the same shared source boundary directly without publishing a
  read-only contract. Financial and holder numeric corrections still build and
  replay; a different actual mapper-supported logical identity can be inserted
  at an absent exact key and survives later replay.
- PASS: real frozen patch-only repair/resume, followed by PR32's
  `SnapshotReader(validate_sources=True)` reading the corrected Snapshot.
  The prepared Reader rejects later patch-file changes; a new source-validated
  Reader rejects the damaged patch digest. This is included in the 19-test run.
- PASS: unchanged independent review reproduction script rerun against the
  repaired worktree: both numeric corrections `ACCEPTED_AND_REPLAYED`; both
  unsupported report periods `REJECTED` with ArtifactError.
- PASS: final `git diff --check`. NOT RUN: full suite, full-history validation,
  formal-data repair/bulk, migration or promotion.

Workspace evidence: `identity-targeted-final.log`, `identity-reviewed-probe.log`.
The first repair harness run passed the financial positive test but stopped at
the fixture's read-only forecast v1 contract. The harness was corrected to use
current forecast v2 / corporate action v2 mappings; the old industry mapping is
checked without publication. `identity-targeted-second.log` then records two
PASS tests before the final combined run. These are synthetic isolated mutations
of real immutable fixtures, not supplier amendments.

Status: IMPLEMENTED and targeted TESTED. Independent re-review and merge remain
pending. No DATA_ACCEPTED or PROMOTED claim.
