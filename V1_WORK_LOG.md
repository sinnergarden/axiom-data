# PR8 P1 / Gate A review node — 2026-09-12

The three requested P1 fixes and minimal Gate A validator are implemented.
Coverage identity retains empty source observations; public security-session
scope uses the same builder/loader guards; shared profile admission blocks
indicator truncation across manual Raw, all operations and replay. Bootstrap
source collection deterministically splits capped indicator requests, retaining
parents and resuming only unfinished children. Official exchange Raw parsing
and previously accepted source/domain semantics remain.

Bounded independent Astra cross-review approved the implementation by separate
file ownership. Luna ran the full suite: 291/291 PASS in 93.744 seconds.
`reports/pr8/P1_GATE_A_REVIEW.md` contains the review map, evidence and boundaries.
The actual Gate A CLI probe returns GATE_A_BLOCKED: missing established source
completeness policies and formal full-admission/daily/Notebook/sparse-history
capabilities. It checks all 56 bindings and preserves the 469 registry; it does
not establish full-target payload admission. Overall V1 remains Stage 3/5.
NO_BULK_BUILD remains in force. I/O PR2 was merged only into the development
branch at cb20745d7ac9a6946757abf4b31986fff7f53240. PR8 was not merged to main.

# Admission-plan review node — 2026-09-12

Astra implemented `validate_admission_plan` and its CLI. The preflight binds
the frozen packaged registry and explicit Snapshot manifest, validates complete
identity/calendar Raw/parent/dependency closures, and checks each View kind's
planned scope with per-security exchange-session bitsets. It rejects geometry
holes, duplicate overlapping work, semantic substitution, malformed manifest
composition and impossible adjusted anchors. Source availability, actual View
payloads and full Snapshot closure remain pending. No unavailable declaration
waives a gap, and the result never establishes consumption readiness.

Independent Astra bounded code review passed after the cited counterexamples
were fixed; Luna targeted tests pass 9/9 in 0.250s. See
`reports/pr8/CODE_REVIEW_NODES.md` for review paths and remaining overall gates.
Full suite: 255/255 PASS in 75.394s, executed by Luna; `git diff --check` PASS.
Log: `/tmp/pr8-admission-plan-fullsuite-20260912.log`.
Stage 3/5 is still active; full V1 admission and terminal validation are not
complete. No new bulk build or pointer update was performed.

# Recovery execution code checkpoint — 2026-09-11

Astra owns implementation, scripts and test code; Luna high performs execution
and evidence work. Workspace and repo working agreements now record this split.
The new public `verify_recovery` operation and CLI validate an explicitly
restored Snapshot/View closure offline, rebuild the catalog, and revalidate on
resume. Published PR6 v1 uses its frozen loader. Market/adjusted Views support
exact rebuild against declared IDs and manifest digests; PR6/PR7 require their
original published bytes because current builders cannot pin creation time.
The independent recovery review identified that distinction and the entrypoint
now rejects unsupported exact rebuild requests before recording a run.
Independent Astra recovery review approved this bounded increment after the fix.
Luna ran the full suite: 246/246 PASS in 75.398s; `git diff --check` passed.
Execution log: `/tmp/pr8-recovery-fullsuite-20260911.log`.

Stage 3/5 remains active. This is recovery execution code, not a full-root
recovery result or baseline admission. Full-scope admission, terminal validation
and the remaining V1 artifact gates are outstanding; `NO_BULK_BUILD` remains.

# DM1 security session scope checkpoint — 2026-09-11

Added the explicit opt-in `security_session_scope=exchange_security.v1` mapping
for adjustment factors and security capital. The frozen identity/calendar rule
keeps only open sessions within the half-open security identity interval; known
pre-list, delist/post-delist, and closed-calendar supplier rows produce no
ordinary canonical fact, while missing/unknown identity or calendar fails
closed. The complete diagnostic inventory records 16,747 on/after-delist factor
rows plus 2 closed-calendar factor rows (16,749 exact exclusion keys total) and
0 capital rows; refs and digests are recorded in
`docs/dm1_security_session_scope.v1.md` and the external report it names.
Raw batches and previously published artifacts remain unchanged; no baseline
capital rebuild was performed, and full-scope reconstruction remains pending.
The new policy accepts a full parent followed by a one-day/subset incremental
patch after validating the full frozen parent history. Targeted tests pass 7/7;
the one full suite run passes 238/238 in
`/tmp/dm1-security-session-scope-fullsuite-20260911.log`. The targeted test
compares single-symbol mapper calls; it does not claim public partitioned-path
equivalence.

# New-server reproducibility checkpoint — 2026-09-11

Acceptance pending. Frozen Raw/profile/config/code/Snapshot/View restore must
preserve `first_observed_at`, identities, and pass offline fresh-root validation;
repo-only recollection may create new best-effort observations and lineage under
the same scope/source/PIT inputs, but cannot backfill historical `first_observed_at`
or upgrade verified evidence. Full baseline is failed, fresh-root recovery is
`NOT_VALIDATED`, and the public CLI exists while full-run orchestration is not yet
portable from the repo. See `docs/operations/reproducibility.md`.

# Acceptance read checkpoint — 2026-09-10

229/229 tests PASS (76.906s). FactView construction shares its checked Snapshot
with current PR6 and adjusted Views; published PR6 v1 keeps LegacyReader dispatch.
The public PR7 direct/Qlib comparison also validates one complete closure per
call and decodes the checked View through the existing Qlib reader. Real copied
fixture calls drop to 18 canonical loads for each operation; fresh calls still
reject subsequent Raw corruption. No artifact schema or projection changes.

Stage 3/5 continues. Daily source collection resumed after a transient DNS
failure, preserving 18,174 successful request observations. New SW reference
collection validates all four mapping conditions again (511 taxonomy / 7,908
membership rows). Two index-weight responses contain 800 and 1,000 August 31
members. Incremental reference checkpoints and full daily source/revision scans
are separate from baseline acceptance. Full V1 gates remain incomplete.

# Explicit observed Raw reuse checkpoint — 2026-09-10

228/228 tests PASS (72.577s). Public daily/collect_requests accept explicit
request-ID-to-RawBatch bindings, verify them before collection, preserve actual
observation timestamps and reject substituted resume refs. The real copied PR7
closure passes candidate then no-change replay without supplier calls or new Raw.
Full-root daily performance remains to be validated.

Stage 3/5: all 18 canonical domains have completed terminal validation. Candidate
r2 attempt 1 stopped before publication because execution inventory used
commit_id for market's market_commit_id. The inventory binding is corrected and
checks manifest row count/logical digest. Failure records are preserved; attempt
2 has imported the frozen closure and is validating the public bootstrap
checkpoint. Scope audit and required Views wait in sequence. The separate full
September 9 session source capture is running against 3,601 securities. Baseline,
full admission, daily/recovery and Notebook gates remain incomplete.

# Catalog verification checkpoint — 2026-09-10

227/227 tests PASS (65.868s). Catalog rebuild shares verified closures only within
its own call, checks every Snapshot composition and View, and preserves legacy
PR6 dispatch. Real fixture canonical loads fall from 103 to 18 for the same 130
entries. Mismatched fixed dependencies and later corruption fail; failed rebuild
keeps the existing catalog. Evidence: reports/pr8/catalog_validation_reuse.
Stage 3/5; adjustment canonical active. V1 terminal acceptance remains pending.

# Valuation projection checkpoint — 2026-09-10

225/225 tests PASS (65.159s). After full PR6 scope admission, valuation projection
reads the target month and uses the existing revision selector. Future/ABA tests
and direct equivalence pass; the real full valuation six-row probe drops from
153 objects/8.45 GB/65.86s to one object/19.27 MB/0.122s. Structural validation
cost is reported separately. Full PR6 payload digest is unchanged. Evidence:
reports/pr8/valuation_session_projection. Stage 3/5; adjustment builds continue;
V1 terminal gates remain incomplete.

# Required View shared validation checkpoint — 2026-09-10

223/223 tests PASS (63.096s). All five View kinds share one fully checked Reader
inside materialize_views; public single builders use the same implementation
with a fresh Reader. The real 18-domain fixture loads 18 commits for all five
Views together and rejects a corrupted Snapshot on resume. No selector or
artifact version changes. Stage 3/5: status terminal validation, then adjustment;
full-scope audit waits on candidate r2. Remaining V1 gates are incomplete.

# Market Qlib validation checkpoint — 2026-09-10

222/222 tests PASS (63.461s). Market Qlib v1/v2 publication reuses its same-call
checked Reader, including adjusted-price dependency validation. Each fresh public
load checks a full Snapshot once; both versions reject later source corruption.
The copied real 18-domain fixture records exactly 18 canonical loads per build
and per public load. View schema dispatch and formulas remain unchanged.
Stage 3/5 and the remaining full-root acceptance gates continue.

# D-M1 View validation checkpoint — 2026-09-10

221/221 tests PASS (64.011s). Adjusted and replay View publication reuse the
same-call checked Reader; each new public load validates a fresh complete
Snapshot. Real fixture identities are unchanged, and later source corruption
still fails. Canonical-load calls drop to 18 per build/public load from 25–54.
See reports/pr8/dm1_view_validation_reuse. Stage 3/5 and V1 gates continue.

# Required View operation checkpoint — 2026-09-10

220/220 tests PASS (69.117s). Public materialize_views and thin materialize-views
CLI freeze Snapshot/config/implementation inputs and execute existing View
builders. Required failures preserve prior outputs and keep the candidate
unready; resume replays and verifies exact results. A substituted execution-record
View ID fails. See docs/operations/materialize-views.md. Full admission remains
a separate gate; stage 3/5 and all V1 terminal work continue.

# PR7 projection batching checkpoint — 2026-09-10

219/219 tests PASS (66.642s). PR7 View selection runs once per domain/session;
metadata is shared with direct leaf_fact. On the frozen real three-security,
four-session fixture, as_of calls drop from 180 to 20 and the complete payload
digest is unchanged. Both PIT policies at two cutoffs match all direct metadata.
See reports/pr8/pr7_projection_batching. Stage 3/5 and full V1 gates continue.

# Request coverage reuse checkpoint — 2026-09-10

218/218 tests PASS (67.525s). Repeated PR7 scope checks reuse verified request
intervals within a single Reader; 100 checks on the frozen real fixture load
three Raw batches instead of 300. New Readers still validate their complete
closure and reject corrupted Raw. Evidence in reports/pr8/request_coverage_reuse;
real calendar append evidence in reports/pr8/calendar_incremental.
Stage 3/5: 16/18 canonical domains validated; status and adjustment remain.
Full-scope audit r1 (session 44224, bundle_r13) waits on candidate r2, audits the
entire requested canonical scope and explicitly leaves admission unassessed.
Full-root operations, Views/admission and all remaining terminal gates continue.

# Calendar incremental boundary checkpoint — 2026-09-10

217/217 tests PASS (69.125s). Calendar append/overlap seeds each exchange
predecessor from the validated parent before the incoming range. Old rows and
Raw remain unchanged; conflicting existing open-day corrections still fail.
The real September 9–10 calendar build r1 failed closed; r2 will use the
corrected builder with the same frozen Raw and parent. Stage 3/5 continues;
full baseline/admission and the remaining V1 terminal gates are pending.

# Public bootstrap checkpoint — 2026-09-10

216/216 tests PASS (68.138s). Public bootstrap API/CLI accepts complete frozen Raw
build inputs or 18 explicit canonical checkpoint IDs. Both use the normal
publisher/closure validators and leave Views/admission pending. Real fixture
resume keeps the exact Snapshot identity and rejects later corruption.
Stage 3/5; valuation and remaining queue continue. No baseline decision yet.

# Daily execution checkpoint — 2026-09-10

215/215 tests PASS (65.964s). Public daily API/CLI execute per-source collection,
resume retained Raw after partial failure, bind frozen build templates and produce
an immutable candidate through assemble_candidate. Simulated T+1 alignment and
NO_CHANGE pass on a copied real PR7 closure. View/admission acceptance and the
full-root daily performance gate remain outstanding. See docs/operations/daily.md.
Stage 3/5: 15/18 domains validated; valuation active, then status and adjustment.

# Validation applicability checkpoint — 2026-09-10

reports/pr8/validation_applicability/report.json binds the actual 214-test PASS
run, implementation and test/fixture digests, contract/profile versions, ruleset,
semantic scope, reassessment triggers and limitations. Golden applicability is
separate from actual coverage and does not expire solely on the next calendar day.
This does not accept bootstrap data. Stage 3/5: limits is in terminal validation;
the full candidate continuation and remaining V1 gates are still pending.

# Scope inspection checkpoint — 2026-09-10

214/214 tests PASS (64.388s). Public inspect_scope and inspect --scope report
actual canonical session gaps/nulls/zeros against exchange calendars and security
lifecycles. Integer bit masks retain interior gaps. This is current audit of
canonical revisions, explicitly NOT_ASSESSED for admission; historical PIT
semantics remain separate. Full-scope admission and all V1 terminal gates remain.
Stage 3/5, limits canonical active; candidate continuation waits on the exact queue.

# Daily PIT query checkpoint — 2026-09-10

213/213 tests PASS (63.542s). Daily PR7 as_of optionally bounds economic sessions;
margin/moneyflow leaf projection uses that bound before the unchanged revision
selector. All revisions for the selected session stay together. Three-month
fixture results equal full-scan results before/after a late revision, and corrupted
selected objects fail. Financial, holder and membership bounds are rejected.
Stage 3/5 continues. Candidate publication waits on the exact canonical queue
in v1_candidate_r1, frozen bundle_r11; candidate validation is not admission.

# Fact publication / canonical checkpoint — 2026-09-10

212/212 tests PASS (65.756s). PR6/PR7 Fact publication reuses its same-call checked
Reader for independent written-View replay. Each public load still validates a
fresh full Snapshot closure; real fixture tests verify one build validation,
later-load validation and corruption rejection. Legacy version dispatch remains.

Moneyflow independently validated: 8,871,536 rows / 153 partitions / 3,305.183s,
17,133,240 KiB peak RSS. Main capital closure validated in 430.128s. Fourteen of
18 domains validated; limits, valuation, status and adjustment remain queued.
Stage 3/5 and all remaining V1 terminal gates continue.

# Public repair checkpoint — 2026-09-10

211/211 tests PASS (64.350s). Public repair API and thin CLI use the existing
candidate builder with explicit Snapshot and frozen Raw identities. Incremental
and clean lineage decisions remain explicit; resume and failure checks are shared.
Successful repair still requires Views and admission. See docs/operations/repair.md.
Stage 3/5 continues; no baseline or pointer promotion.

# Contract metadata checkpoint — 2026-09-10

210/210 tests PASS (63.327s). PR7 leaf metadata now uses the Snapshot domain's
actual contract version and units, including forecast_observations.v2 and missing
facts. Existing v1 domain output remains unchanged. Full-market query evidence
is in reports/pr8/session_read_performance; startup validation is excluded.
Canonical queue and all remaining V1 gates continue, stage 3/5.

# Session read checkpoint — 2026-09-10

209/209 tests PASS (61.971s). After complete Reader validation, bounded session
reads select whole month objects and verify their bytes/counts before projection.
Membership and PIT selectors are unchanged. Scoped-object corruption and new
Reader full-closure corruption rejection are tested. Actual full-market query
comparison is prepared; no performance number is claimed until it validates.
Stage 3/5, moneyflow and the remaining canonical queue continue.

# Action reobservation checkpoint — 2026-09-10

208/208 tests PASS (63.154s). Explicit corporate_action_reobservation.v1 fixes
repeated dividend scans by retaining earliest evidence for identical revisions;
new revisions and conflicting-content rejection remain intact. Raw remains
retained, and equal canonical state can reuse a parent. Old configs stay strict.
See reports/pr8/action_reobservation/REPORT.md. This is a daily prerequisite,
not completion of daily execution. Stage 3/5 and all terminal V1 gates remain.

# Snapshot publication validation checkpoint — 2026-09-10

207/207 tests PASS (63.815s). Snapshot publication validates domain composition
once, then independently validates its published manifest against those checked
refs. Public loads always validate the full closure; no cross-call cache exists.
Exact identity and later-corruption rejection remain tested. This removes one
redundant full source replay during baseline publication. Stage 3/5 continues.

# Daily dry-run checkpoint — 2026-09-10

206/206 tests PASS (65.571s). Public plan_daily / plan-daily CLI inspect explicit
per-source economic windows, exact parent commits, T+1 policies and dependency
review candidates without writing to the data root. Source availability remains
unconfirmed until collection. This completes dry-run inspection only, not the
daily execution/admission service. See docs/operations/daily-plan.md.

Capital r1 independently validated: 8,878,683 rows / 153 partitions / 1,417.789s,
10,720,984 KiB peak RSS. Thirteen canonical domains are validated. The remaining
five-domain queue is executing moneyflow. Capital byte transfer to the main root
is separate from destination closure validation; do not treat that transfer as
admission. Full V1 terminal gates remain incomplete, stage 3/5.

# Full canonical continuation — 2026-09-10

205/205 tests PASS (69.885s). Public inspection now performs one complete Reader
validation per call and streams its inventory; repeated calls still revalidate
and later corruption fails. It reports no full-scope admission claim.

Margin r2 independently validated 4,880,379 rows / 153 partitions / 2,106.894s;
forecast v2 69,931 rows / 15 partitions / 62.842s. Both exact refs are in the run
progress files. Full qualified limit scan passes 9,162,373 rows (477.371s).
Capital canonical and main-root margin import validation continue. The frozen
canonical_tail_queue plan runs moneyflow, limits, valuation, status, adjustment
serially after those memory-heavy prerequisites validate. Its progress is durable
and each child performs independent closure validation. Stage 3/5 remains
incomplete; all baseline/admission and stages 4–5 gates still apply.

# Source label checkpoint — 2026-09-10

204/204 tests PASS (68.509s). Explicit zero_limit_pair.v1 retains three source
zero pairs as unknown. Forecast v2 preserves the four additional observed labels
verbatim under a frozen 12-label contract; v1 remains unchanged. Full source is
complete; canonical recovery and all later V1 gates remain due. Stage 3/5.

# Capital qualification checkpoint — 2026-09-10

202/202 tests PASS (70.563s). security_capital.v2 explicitly retains 15 source
conflicts as null counts with unchanged evidence; v1 stays strict. All source
collection is now complete, including forecast 3,601 requests / 69,931 Raw rows.
Full canonical/admission/baseline and stages 4–5 remain incomplete. No merge/push.

# Corporate action observations checkpoint — 2026-09-10

201/201 tests PASS (63.474s). New corporate_actions.v2 root represents undated
source observations without substituting dates. Affected date-bounded queries
fail closed; v1 remains unchanged. Full source scan: 35,068 dated / 30 undated /
one unresolved-terms observation, no failures. Full market is independently
validated: 9,151,217 rows / 153 partitions / 1,453.834s. Financial canonical:
696,217 rows / 90 partitions / 793.077s. Imports into the main root and remaining
canonical work continue. Stage 3/5, V1 terminal incomplete, no merge/push/pointer.

# Margin qualification checkpoint — 2026-09-10

200/200 tests PASS (67.678s). Explicit negative-repayment qualification preserves
361 source rows' signed repayment evidence, with canonical null values and
source_repayment_unresolved. Full 4,880,379-row mapping/contract/calendar scan
passes (204.352s). Market and financial canonical continue; moneyflow source is
complete. Corporate-action missing ex-date input is under diagnosis. Stage 3/5
and V1 terminal gates remain incomplete. No merge/push/default pointer move.

# Identity validation checkpoint — 2026-09-10

199/199 tests PASS (66.294s). Complete security schema validation now occurs
once per market/D-M1 cross-domain pass, followed by unchanged interval checks.
Actual 7,202 identity/date results match, with 0.468215s → 0.011136s.
The older market job is interrupted with Raw/objects retained; next explicit
recovery plan reuses the already validated fixed calendar identity. Stage 3/5
and V1 terminal gates remain incomplete. No merge/push/default pointer move.

# Top10 source qualification checkpoint — 2026-09-10

198/198 tests PASS (100.147s). Full 10,803-request source/row scan passes with
explicit top10_ambiguity.v1: conflicting holders and totals above 100.01% retain
source evidence and produce incomplete reports with null concentration. Old
configurations remain fail-closed. See reports/pr8/top10_qualification/REPORT.md.
Universe canonical is independently validated (265,400 rows / 14 partitions).
Stage 3/5 continues; V1 admission/baseline and stages 4–5 remain incomplete.

# Universe acquisition checkpoint — 2026-09-10

195/195 tests PASS (139.910s). Explicit complete initial acquisition reduces the
full 306-request universe mapping to 264,600 observations / two group states,
27.078s and 759,404 KiB RSS. Source gaps do not create empty membership.
See reports/pr8/universe_acquisition/REPORT.md. Top10 source is complete;
market canonical and other source work continue. Stage 3/5, terminal V1
incomplete; no pointer/merge/push.

# Market source scan and holder canonical checkpoint — 2026-09-10

192/192 tests PASS (134.099s). Full market scan found four failing security
histories; v3 passes all four after exact issuer-qualified timing cases and
R-event filtering before daily halt keys. Raw/older profiles unchanged.
Holder canonical is validated: 338,076 rows / 22 partitions; exact ID and
measurements in reports/pr8/full_scale_preparation/REPORT.md.
Stage 3/5 continues; market v3 full canonical, all other domain canonical,
56/469 admission, baseline and stages 4–5 remain due. No pointer/merge/push.

# D-M1 materialization checkpoint — 2026-09-10

190/190 tests PASS (137.532s). Six reference mappers now support bounded
security batches with unchanged logical values/full-scope provenance.
Stage 3/5 remains active. Market source is retrying the final failed batch
under its original identity; remaining source domains retain their checkpoints.
Full canonical/admission/baseline and stages 4–5 are still due.

# Active checkpoint — 2026-09-10 source/storage follow-up

Stage 3/5 remains active. SW correction and industry stage are validated.
189/189 tests PASS (138.450s). Holder v3 source collection is COMPLETE:
3,601 requests / 339,598 Raw records. Other source jobs continue with exact
checkpoint identities. Full baseline, admission and stages 4–5 are incomplete.
See reports/pr8/full_scale_preparation/REPORT.md for explicit holder/indicator/
halt source qualification, bounded partition reads and the 100-security mapper
probe: 295,050 identical rows, 79.75% lower peak RSS. No merge/push/pointer move.

# Active decision — 2026-09-10

Full bootstrap run v1-full-bootstrap-20260910-r1 is active in stage 3/5.
Market Raw collection, remaining domain collection, holder timestamp v2 recovery
and Top10 checkpoint recovery have separate source roots and exclusive run
identities. Use their explicit progress.json/collection.json records; never
launch a duplicate active run. See reports/pr8/bootstrap_sources/REPORT.md.
179/179 tests PASS (129.822s). Ordinary source format/temporary throttling fixes
are implemented; full canonical/admission/baseline and stages 4–5 remain due.

Stages 1–2 validated: versioned correction, full 3,601-security canonical
history, all 32,458 restored sessions, Snapshot/Fact/Qlib metadata and raw-only
offline identity rebuild. 172/172 tests PASS (127.319s). Evidence is in
reports/pr8/sw2021_canonical/REPORT.md. Proceed to stage 3/5, full-domain
bootstrap/admission. The old source-blocker notes below are historical;
no industry authority/code decision remains pending. V1 is still incomplete.

User selected SW2021 and authorized the sole explicit taxonomy correction
index_classify:850401.SI → canonical:850412.SI. Prior pending-code questions are
resolved. Four automatic conditions, immutable Raw, mapping provenance and a
new industry contract/root are required. No taxonomy_code_unresolved treatment
for these resolved sessions. Preserve actual supplier gaps and boundary ambiguity.

Current stages: (1) validate/version correction and golden cases; (2) complete
canonical industry and View metadata; (3) full-domain bootstrap/admission;
(4) daily/revision/no-change/offline recovery; (5) independently validate baseline,
Notebook, every terminal artifact and final 24-item report. Original DoD below
remains in force. Reuse completed source comparison, security master and resume.

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

Official boundary implementation follow-up:
- New explicit ExchangeSecurityBuilder and exchange_security.v1 source policy
  consume original SSE JSON/SZSE XLSX as immutable Raw inputs, without new deps.
- All 3,601 security identities and 228 official termination boundaries build;
  old Tushare non-null boundary rejection remains intact. A/B share records are
  keyed by the actual security type/code, not company code.
- Exact real commit: security_master-d672728d9fc0f45e4fd4a10c1782844f26f979a05c1ca65433428330c4595d02.
  A raw-only isolated offline rebuild matches its identity and all rows.
- Public candidate config security_boundary_policy=exchange_security.v1 selects
  the new builder explicitly. No baseline Snapshot or pointer promotion.
- This closes the 228-security source mapping/build stage; full pipeline
  admission and operational V1 gates remain. Industry decision is still pending.
- Evidence: reports/pr8/exchange_security/REPORT.md and explicit plan/result refs.

# Axiom Data V1 operational release

## Candidate verification reuse checkpoint — 2026-09-10

231/231 tests PASS (71.322s) in the existing full-suite log. A copied frozen
PR7 candidate build preserves `CANDIDATE_BUILT` while canonical-load calls fall
from 65 to 19 (0.5673211719986284s to 0.2029224029975012s). The cache is scoped
to one writer-controlled `assemble_candidate` call and matching data root;
later calls, another root, and fresh Readers still validate from source, and a
failed call clears its context. Evidence is in
`reports/pr8/candidate_validation_reuse`.

This is bounded validation-reuse evidence only. Full-root performance, full
admission and baseline acceptance remain separate gates; baseline status is
`NOT_ACCEPTED`. Stage 3/5 continues.

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

Boundary follow-up final suite: 167/167 PASS, zero skips, 111.945 seconds;
explicit Raw and primary fixture integrity checks pass. V1 remains incomplete.

Candidate recovery follow-up:
- Resumed commits now bind to the exact parent, ordered Raw refs, contract,
  resolved builder config/implementation and dependency commits; another valid
  commit in the mutable execution record is rejected.
- No-change parent reuse is replayed against explicit inputs. Recovery clears
  stale Snapshot fields before revalidation and cannot report a failed state
  with a prior successful candidate result.
- Real PR7 regression covers normal/no-change resume and substituted valid
  commit rejection. Original Snapshot/Raw inputs remain intact; no pointer move.
- Report: reports/pr8/candidate_resume/REPORT.md. Full V1 remains incomplete,
  with industry code decision and the original bootstrap/admission/daily/
  recovery/Notebook gates outstanding.
