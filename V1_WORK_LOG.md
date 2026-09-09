# Active source qualification (2026-09-09 latest user request)

Compare SW2021 and CITIC over the explicit PR4 union of 3,601 securities,
2014-01-01 through the frozen available endpoint 2026-09-08. Keep both Raw
lineages separate. The old SW pilot rejection is historical evidence under the
superseded strict gap gate, not the new acceptance policy.

Qualification stages:
1. Validate source profile, pagination, repeatability and batch completeness.
2. Score both complete histories identically; resolve source gaps, boundary
   ambiguity and delist mapping, then select exactly one authority.
3. Implement the new contract/root and continue original V1 stages 3–5 below.
4. Independently validate full artifacts, requested evidence and final report.

Small verified supplier gaps and uncertain boundary sessions use the user's
explicit availability/ambiguity semantics; incomplete collection still fails.
No canonical industry promotion or full bootstrap has started. Existing pilot
artifacts and the 161-test checkpoint remain validated; final V1 is incomplete.

Qualification checkpoint validated:
- 1,821 new immutable Raw refs; SW 7,908 / CITIC 6,740 unique membership
  rows, repeat equality, 1,250 category comparisons and 508 stock comparisons.
- With official exchange delist dates: 9,162,373 expected sessions; SW interval
  coverage 98.615773%, CITIC 79.350426%. SW 125,648 source-gap sessions,
  1,180 ambiguous boundaries, zero interior conflicts; CITIC 42 conflict sessions.
- Prefer SW2021. A newly reproduced taxonomy contradiction remains: metadata
  850401.SI versus member 850412.SI for 特钢Ⅲ. User was asked whether the
  affected 32,458 target sessions may use taxonomy_code_unresolved availability;
  no answer received yet, no alias or canonical promotion performed.
- Official SSE/SZSE full delist tables locate all 228 target D securities;
  211 supplier dates match and 17 differ. Exact exchange boundary precedence is
  supported by evidence; the new canonical adapter has not yet been published.
- Full suite 164/164 PASS, zero skips, 113.487 seconds. All scores independently
  recomputed, fixture matched actual source rows, 1,821 Raw refs and primary
  documents revalidated; published Raw files readonly.
- Full report: reports/pr8/industry_authority/QUALIFICATION.md. The original
  five-stage V1 terminal outcome remains incomplete; full bootstrap has not begun.

# Axiom Data V1 operational release

## Historical SW-only pilot decision (superseded by full-union qualification)

SW2021 is the formal industry candidate. Qualify index_classify and
index_member_all on 40 representative securities before full industry bootstrap.
The prior null-classification proposal is suspended. Existing bak_basic raw is
superseded forensic/comparison evidence, retained unchanged; no SW promotion.
The next industry contract preserves all L1/L2/L3 code/name relationships, exact
source observations and honest best_effort historical knowledge, on a new root.

Pilot stages within the original V1 plan:
1. Freeze profile, explicit 40-symbol scope and request plan; collect immutable
   default/Y/N observations, taxonomy and current stock_basic comparison.
2. Independently validate request binding, taxonomy joins, in/out boundaries,
   intervals, gaps, listing relation, old missing cases and batch equivalence.
3. Only on all pilot gates PASS, implement the versioned SW contract/new lineage,
   full SW bootstrap and remaining original V1 gates. If historical capability is
   insufficient, submit the new source decision without fallback.
Pilot run: data/operations/sw2021-pilot-20260909-r1. Frozen code and plan exist;
126 initial and 23 crosscheck requests completed with 149 validated RawBatches.
Pilot REJECTED: 000506.SZ / 000975.SZ / 001289.SZ have 1,000 / 119 / 14
unexplained open-session gaps. All taxonomy joins, repeated observations and
applicable category crosschecks agree. Source raw success is not pilot acceptance.
Evidence and next source decision: reports/pr8/SW2021_SOURCE_QUALIFICATION.md.
Final suite: 161/161 PASS, zero skips, 110.825 seconds; all new/forensic Raw refs
reloaded successfully. No merge/push or pointer change.
No SW canonical lineage or full bootstrap has started. The two original source
blockers remain open under the corrected source analysis.
The previous industry v3 approval question is superseded by this user decision.

Authority: user V1 request dated 2026-09-09; accepted PR7
95efe78b734ebf9783c7a59d3b81c19a8abb445e; PR4 final authoritative
469 Feature / 56 requirement package. Data root remains
/home/liuming/workspace/axiom/data. No merge authorized.

## Definition of done

Real 2014-01-01 through source-available-date bootstrap is COMPLETE with
56 resolved requirements, honest qualification and no admission blocker; exact
baseline, full offline recovery, multiple incremental daily cases, late revision,
no-change, old Snapshot immutability, long lineage, readonly publication and
Notebook smoke all independently validated. Deliver branch/commit and all 24
requested report items. A stage is not terminal completion.

## Stages

1. Authority, source availability, existing artifacts and implementation preflight.
2. Public operations, immutable partition storage, iterative lineage, validation
   applicability, readonly publication and acceptance Notebook.
3. Resumable real full bootstrap and full-scope admission/reconciliation.
4. Real-root daily/revision/no-change and offline recovery; performance evidence.
5. Independently validate every terminal artifact, Notebook and full suite;
   commit for review with exact refs and explicit accepted/rejected decision.

## Current state

Stage 2/5 stopped at the real source-to-contract blockers recorded below.
Local main and origin/main both e23d8234; PR7 approved
head 95efe78 is present on its remote review branch. V1 branch starts at that
exact head. No baseline has been accepted. No running V1 job existed.

Physical implementation preflight: current DomainCommit v1 stores one rows.json
per complete state; partition objects and incremental APIs require implementation.
Parent closure currently uses recursion and must become iterative. These are
requested operational changes, not changes to accepted business semantics.

## Verified implementation checkpoint

Stage 2/5 has a tested implementation checkpoint; source collection for stage 3
has run, but full canonical bootstrap is blocked. No V1 baseline exists.

- Iterative closure: 2,501-node synthetic chain, repeated-ref cache and cycle
  rejection pass. Original 20 artifact tests pass.
- Opt-in DomainCommit v2 complete time-partition maps preserve v1 loader dispatch.
  A new month reuses the exact old month object; shared-object corruption fails.
  This currently optimizes writes; Reader/build memory and historical reads still
  need full-scale profiling and optimization before daily acceptance.
- Public collect_requests has a fixed request/profile binding, single-writer lock,
  per-success immutable refs, resumable failures, credentials excluded from durable
  metadata, and read-only publication. It is a collection stage, not bootstrap
  COMPLETE or baseline acceptance.
- inspect_snapshot and compare_pr7_projection are public read-only acceptance
  services. notebooks/data_acceptance.ipynb uses only public Data reads; all six
  code cells passed a network-denied Python smoke using the explicit accepted PR7
  fixture. It has not passed the requested full-baseline acceptance yet.
- The final checkpoint full suite passed 158/158 in 110.891 seconds, with zero
  skips, including both real-source blocker regressions. Exact logs and fixture
  binding validation are retained under reports/v1/.
- Public assemble_candidate builds/resumes explicit canonical inputs and marks
  its result CANDIDATE_BUILT, never ready-for-consumption. It does not implement
  the complete bootstrap/daily/repair or full admission lifecycle.
- Validation applicability compares explicit semantic signatures independently
  of dates; actual full-scope certification remains required.

## Explicit real preflight artifacts

Run: v1-bootstrap-20260909-r1. Registry:
/home/liuming/workspace/axiom/data/operations/v1-bootstrap-20260909-r1/preflight.json
This is a source/storage probe, not a formal full bootstrap run.

- stock_basic: L 5,558 rows; D 339 rows; P 0 rows. These are supplier-wide
  observations, including securities outside the final approved SSE/SZSE scope.
- trade_cal: SSE and SZSE each 4,634 civil days, 2014-01-01 through 2026-09-08.
- 600036.SH: daily 3,080, adj_factor 3,085, daily_basic 3,080, stk_limit 3,085,
  suspend_d 6 rows. Complete market mapping produced 3,085 canonical rows and
  153 monthly objects. This is one-security evidence only.
- Public collection run industry-bulk-preflight-20260909-r1 in that explicit
  data subroot: bak_basic 2014-01-02 -> 0, 2016-01-04 -> 0,
  2026-09-08 -> 5,567 rows, using the new tushare_pr6.v2 bulk profile. The completed
  probe predates addition of explicit SourceProfile bindings to run plan hashes;
  its immutable RawBatch refs remain valid, but it must not be resumed with a
  different run-plan hash.
- Supplier documentation https://tushare.pro/document/2?doc_id=262 states 2016
  start, optional ts_code with trade_date, maximum 7,000. This is documentation,
  not a claim that every 2016 date is available. V2 validates scope and rejects
  an at-limit response; business mapping and v1 dispatch remain unchanged.

## Remaining decisions and work

The unanswered optional security-scope question was resolved by a stated working
assumption: the authoritative PR4 historical union, 3,601 symbols, including exits.
All identities exist in supplier L/D/P responses, but 228 required delisted
securities have a non-null delist_date. The accepted stock_basic v1 SourceProfile
and adapter reject that boundary until exclusive-effective-date compatibility
has evidence. Full security master construction fails at that gate.

The frozen-code industry run v1-industry-bootstrap-20260909-r1 completed 2,596
requests and published 2,596 validated read-only RawBatches, 10,712,417 supplier
rows. First nonempty date is 2016-08-09. This is raw collection, not canonical
coverage or baseline completion. Of 568 required-symbol null industry rows,
158 precede supplier list_date, 375 concern currently listed securities on/after
list_date, and 35 require unresolved delisted-boundary evidence. These are raw
diagnostic categories, not a replacement eligibility selector. Example:
001289.SZ on its 2022-01-24 listing day has industry=null; the existing mapper
rejects it. No classification is filled or silently omitted.

Evidence: reports/v1/source_blockers.json, tests/fixtures/v1_source_blockers.json,
tests/test_v1_source_blockers.py, reports/v1/V1_CHECKPOINT_REPORT.md.
Next checkpoint must resolve source-to-contract mapping through evidence and
versioned fixtures. Existing contracts are unchanged. Full service
bootstrap/daily/repair, full admission, broad reconciliation, full recovery,
real-root daily profiles and final Notebook reconciliation/quality panels remain
unimplemented or unvalidated. Partition writes reuse objects, but readers/builds
still eagerly materialize full states; no full-scale daily performance claim.
Candidate resume request-binding hardening and complete config-value validation
also remain before production use. No current pointer was moved; no merge or push.

## Source decision follow-up

The primary SZSE-hosted issuer announcement 2024-021 corroborates 000005.SZ's
2024-04-26 removal date and distinguishes its 2024-04-11 termination decision.
Original PDF captured read-only by explicit SHA-256; metadata in
reports/v1/primary_boundary_evidence.json. This validates a source distinction
for one security, not a global boundary mapping for 228 securities.

reports/v1/SOURCE_CONTRACT_DECISION.md proposes evidence-backed delisting mapping
under a new SourceProfile while retaining security_master.v1, and a clean-root
industry_membership.v3 for explicit null classification observations. The latter
changes the accepted row contract; a concise contract decision was requested
from the user under request section 1. No answer has arrived at this checkpoint.
No runtime/contract change was applied; two source blocker tests reran 2/2 PASS.
Full bootstrap remains blocked and the terminal goal remains incomplete.
