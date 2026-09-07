# PR6 review correction delivery

This is the four-blocker correction on `phase1/pr6-pit-financial`, based on
`0f5ec5967ae6b2f17b43911088dbfde81950eb90`. The delivery commit is the commit
containing this report; its exact SHA is returned in the final response.
No merge is performed. The PR6 domain/leaf scope is unchanged.

1. **Commit:** one corrective commit on the existing review branch; no history rewrite.
2. **Financial model:** v2 content identity remains `(logical_event_key, revision_id)`.
   `observations` stores independent retrieval events. The content fingerprint
   excludes observation metadata and preserves earliest first_observed_at.
   Operational/safe selection uses the last eligible observation. Vendor policy
   orders vendor availability then actual observation; incompatible ties fail.
3. **ABA:** tests cover A=100 → B=200 → A=100 → A=100: two content rows, four
   observations. Cutoffs return 100/200/100/100. Reversed raw replay and an empty-root
   raw-only rebuild reproduce the identical rows and selection.
4. **Membership algorithm:** load parent raw refs and new raw inputs, order by
   actual observation time, replace effective snapshots inside each new request's
   bounds, and derive consecutive half-open intervals. Retain complete state
   identities/observations. Select exactly one state per group under each policy.
   A child exit closes the old open span while the parent's bytes remain unchanged.
5. **Membership tests:** genesis/open interval, exit, re-entry, correction from
   August 1 to July 15, old-cutoff prefix stability, raw-order-independent replay,
   operational/vendor unique results, and pre-entry absence. Empty exact-date
   snapshots express exit; an entirely empty history fails closed.
6. **Coverage:** required calendar/domain date and symbol holes reject; unknown
   universe/classification IDs reject explicitly. Formal View and read envelopes
   distinguish requested, actual available and validated scope. No partial default.
   Tests include 2020 start, late end, unknown IDs/symbols/fields, complete scope,
   and missing middle calendar/valuation/classification date or security.
7. **FactView schema:** numeric rows plus typed facts containing value, unit,
   validity, missing_reason, PIT qualification/usable_at/policy/cutoff, quality_state,
   source/revision/observation/derived refs and component lineage. Universe typed
   facts bind IDs, domain version and policy; membership_facts returns the query
   envelope, including for an empty cohort.
8. **TTM example:** `ttm_missing_example.json` is an actual 600036.SH FactView result:
   value=null, unit=CNY, validity=missing, missing_reason=missing_quarter,
   quality_state=BLOCKED, with derived_ref, expected_quarters and component refs.
   Separate tests distinguish source_value_missing, incompatible_report_type and
   PIT_component_not_visible. Financial derived values consume selected observations.
9. **Industry mapping:** the formal View binds classification system, mapping version
   (industry commit), and code→industry identity. FactView and Qlib metadata return it.
   Deleting the mapping rejects load. No external mutable sidecar is introduced.
10. **Real slice:** reran the same 40 immutable supplier RawBatches (no recollection).
    Canonical contents: financial 29, valuation 8, universe 18, industry 8.
    All 63 content rows remain best_effort; observed=0, verified=0. Direct/Qlib:
    184 values/nulls match; 37 independent raw mapping checks; bounded membership
    4/4 matches; frozen historical union exactly 3,601. Real 688981.SH TTM remains
    revenue 61,502,873,000 CNY and net income 7,243,873,000 CNY.
    Existing source limitations remain: income 10 MATCH/5 DRIFT/15 missing frozen
    references; legacy Qlib 40 MATCH/40 DRIFT; missing frozen industry taxonomy and
    incomplete frozen financial quarters. No verified PIT or full legacy equality claim.
11. **Offline rebuild:** both source and recovery built from raw plus frozen reference
    inputs with socket creation/connections disabled, using the frozen code bundle.
    Snapshot/View identities and logical values match; all six actual-root gates pass.
    Exact roots, IDs and bundle are in validation_attempt.json and run_manifest.json.
    Earlier published artifacts remain unchanged; new records/View use v2 contracts.
12. **Tests:** 100/100 PASS, 65.769 seconds. tests.txt contains the full run.
    Repo implementation hashes match the frozen validation bundle; diff whitespace
    validation passes. PR1–PR5 regressions remain in the full suite.
13. **Scope:** PR7 is not started. SysQ, Research, Trade and UI are unchanged.
    This report does not declare total D-M2 acceptance.
