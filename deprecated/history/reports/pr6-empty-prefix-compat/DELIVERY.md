# PR6 empty-state, PIT-prefix and v1 compatibility delivery

Branch: `phase1/pr6-pit-financial`; base: `c6c09ceef09c495f601387c68fbfda0df61834d0`.
The corrective commit SHA is returned with this report in the final response.
This work contains only the three requested blockers. No merge is performed.

1. **Commit:** one corrective commit on the existing PR6 branch, without rewriting
   the previous commits or published artifacts.
2. **Universe representation:** the same universe domain now has an explicit v3
   contract. Its DomainCommit stores group_states separately from member rows.
   Each state binds universe/effective intervals, observation identity/time,
   qualification, source/raw refs, counts, set digests and member revision refs.
   Zero-member complete observations are valid (empty_group_state_example.json). There is no fake security row.
   The complete empty assertion is bound to the RawBatch; absent completeness
   means SOURCE_GAP, not an empty set. Group-state closure is replay-validated.
3. **Empty correction:** tests verify {A,B}→{}, two universes with only one cleared,
   {}→nonempty, an empty genesis parent, clean raw-only recovery, both operational
   and vendor selection, and source-gap rejection. SnapshotReader returns an empty
   cohort with its group observation envelope. A v3 row-only selector rejects
   missing group_states instead of falling back to an old positive row.
4. **PIT prefix algorithm:** select observations visible under the explicit policy
   and cutoff first. All single-quarter/TTM component existence, missing reasons,
   arithmetic and logical source provenance use only that selected world. The
   financial available-scope projection is also cutoff-filtered. Future Q2
   existence is not emitted in historical missing metadata.
5. **Old/new Snapshot comparison:** integration tests publish one Snapshot with Q1,
   Q3, Q4 and another after Q2 is observed on 2025-07-01. At cutoff 2025-06-01,
   their full derived logical records compare equal (snapshot_prefix_probe.json), including null value,
   missing_quarter, visible component revisions and source lineage. At cutoff
   2025-08-01, TTM revenue becomes 1000. Financial ABA tests remain in the full suite.
6. **Derived identity:** formal View identities continue to bind their Snapshot and
   full immutable dependencies. Different Snapshots may produce different View IDs
   while their historical logical PIT result is identical. No cross-Snapshot
   artifact-ID equality rule was introduced; row calculation keys are not artifact IDs.
7. **Version dispatch:** pr6_fact_view.v1 uses its original packaged projection,
   Reader filtering, PIT arithmetic, manifest projection and validation semantics.
   pr6_fact_view.v2 retains coverage and typed metadata admission. FactView is the
   reading facade, not an additional persisted fact_view schema. Existing
   adjusted_price_view.v1, market_replay_view.v1 and qlib_view.v1/v2 remain supported.
   No artifact code is executed, no v1 manifest is upgraded, and v2 fields are not
   demanded of v1. Requests for metadata absent from v1 have an explicit contract error.
8. **Real v1:** the original reported View
   pr6-fact-29352750a024a4956f26dab72fa96803259f09fe53f780fa69da3cf81512f79f
   loads through the current loader and FactView/Qlib readers. All 8 wide rows match
   the original stored logical payload; original identity/digests verify. The old
   v1 and previous v2 evidence validators both pass. Tampered copied v1 payload or
   schema rejects; v1/v2 identities remain distinct. Published original bytes remain intact.
9. **Validation:** probes_before.txt records all four initial failures;
   probes_after.txt records passing probes. tests.txt contains the complete suite: 107/107 PASS in 93.196 seconds, executed from the frozen code bundle.
   The real bounded supplier slice was rebuilt with frozen code into fresh source
   and network-disabled recovery roots, with equal identities and six actual-root
   gates passing. run_manifest.json and validation_attempt.json bind the final
   artifacts and code bundle. Source/PIT qualifications and prior forensic
   reconciliation limits remain unchanged; verified stays fail-closed.
10. **Scope:** PR7 was not started; SysQ, Research, Trade and UI are unchanged.
    No total D-M2 acceptance is claimed.
