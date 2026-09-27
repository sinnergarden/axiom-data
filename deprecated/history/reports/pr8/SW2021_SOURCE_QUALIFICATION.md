# PR8 SW2021 source qualification

Decision: **pilot REJECTED for full historical bootstrap admission**. SW2021
remains the requested taxonomy. The endpoint returns useful historical rows,
but three representative securities have reproducible unexplained history gaps.
Under the user's pilot gate, full industry bootstrap and new canonical lineage
have not started. No source fallback or explicit unclassified state was created.
The original V1 delivery remains incomplete.

## Scope and actual collection

40 securities, 15 SSE / 25 SZSE, include pre-2014 listings, later IPOs, multiple
industry transitions, 001289.SZ and four currently delisted securities. Exact
scope and all immutable RawBatch refs are in [sw2021_qualification.json](sw2021_qualification.json).

- 3 index_classify requests: explicit SW2021 × L1/L2/L3.
- 120 index_member_all requests: each security × default/Y/N.
- 3 stock_basic requests: L/D/P, including industry for reference comparison.
- 14 category crosschecks: seven actual L3 categories × Y/N.
- 9 repeated requests: 000506.SZ, 000975.SZ, 001289.SZ × default/Y/N.
- Reused 12 explicit pre-existing bak_basic date RawBatches for comparisons;
  no repeated day×security collection.

Total: **149 requests / 149 RawBatches / zero collection failures**, 7,049
supplier rows, including repeated/batch observations (not unique memberships).
By endpoint: index_classify 511, index_member_all 639, stock_basic 5,899.
The initial and crosscheck observation spans are 15.558 and 2.186 seconds;
these are first-to-last retrieval spans, not measured end-to-end bootstrap times.
Published raw files total 1,269,026 bytes; all were checked read-only. Frozen
collector code digests passed. Independent qualification took 0.290 seconds.
Exact metrics and code/report hashes: [pilot_metrics.json](pilot_metrics.json).

Both APIs are documented at the 2,000-point level; the user's 5,000-point account
successfully executed this pilot. The documented membership response ceiling is
2,000 rows. The collector retains possible-truncation or request mismatch as a
qualification failure in Raw metadata, without admitting it as canonical data.
Sources: [taxonomy](https://tushare.pro/document/2?doc_id=181),
[membership](https://tushare.pro/document/2?doc_id=335).

## Taxonomy, request behavior and repeatability

All 511 taxonomy records declare SW2021: L1=31, L2=134, L3=346. Parent relations
join by industry_code/parent_code; membership index codes join through index_code.
The 40-security current and historical sample's L1/L2/L3 codes, names and levels
all match this tree. No unmatched taxonomy code or name was found.

For all 40 securities, omitted is_new equals Y (40 rows each). N returns 20
retired membership rows across 12 securities, excluding their current rows. Historical retrieval requires
both Y and N. All three repeated securities returned identical observations;
19 applicable stock/category comparisons matched exactly. These measurements
prove bounded repeatability, not complete historical coverage.

Y is a current *membership record*, not proof of a currently listed security:
000005.SZ, 000018.SZ, 000024.SZ and 600687.SH are delisted in stock_basic yet
still have Y membership records with null out_date. The accepted security
identity boundary remains a separate requirement.

## Real intervals and missing coverage

Both candidate out_date conventions were inspected without normalizing the raw
boundary. No overlapping intervals were found in the inspected scope. Treating
out_date as exclusive creates 12 additional uncovered exchange sessions compared
with an inclusive-out interpretation. Examples:

- 000100.SZ: old classification ends 2019-04-19; next begins 2019-04-22.
- 002352.SZ: old ends 2017-02-28; next begins 2017-03-01.
- 601888.SH: old ends 2021-07-29; next begins 2021-07-30.

These are empirical evidence consistent with a last-included source date;
independent SW effective-date evidence is still required before adopting a
canonical conversion. Neither convention repairs the longer gaps below.

| Security | Uncovered open dates even with inclusive out_date | Count | Batch/repeat result |
| --- | --- | ---: | --- |
| 000506.SZ | 2018-06-19 through 2022-07-28 | 1,000 | Same history gap |
| 000975.SZ | 2014-01-02 through 2014-06-30 | 119 | Same history gap |
| 001289.SZ | 2022-01-24 through 2022-02-17 | 14 | Same history gap |

Counts use the frozen security's own exchange calendar and reported listing
bounds as source diagnostics. They do not promote vendor delist dates as legally
verified boundaries. These three securities are currently listed with no
reported delist date; the gaps cannot be explained by current delisting.
Their gaps are unresolved source membership coverage, not evidence of a genuine
unclassified observation. They are also not failed network requests or mismatched
batch selectors. No canonical missing rows are manufactured.

in_date is demonstrably not a listing-date proxy: 22 of 40 first membership dates
precede supplier list_date, including 688981.SH (2020-07-06 vs 2020-07-16) and
300015.SZ (2009-01-12 vs 2009-10-30). Raw origins are retained. Pre-2014 origins
can describe a crossing interval without inventing a 2014 start, but 000975.SZ
still lacks the required beginning-of-2014 membership.

## Three-way comparison and 001289.SZ

SW2021 has an explicit taxonomy and interval records. stock_basic industry is a
current coarse reference label; bak_basic supplies date-specific coarse labels
from its available history. Strings across these systems are not equated.

For 001289.SZ:

- stock_basic currently labels it 新型电力 and reports listing on 2022-01-24.
- Frozen bak_basic is null on 2022-01-24, then 新型电力 on 2022-01-25 and
  2022-02-17.
- SW has a valid hierarchy, with L3 风力发电 (851617.SI), beginning 2022-02-18.
- Y/N and L3 batch observations agree. SW therefore supplies a clear subsequent
  classification, but does not explain the 14-session initial gap. The old
  null case is **not fully closed** by the source switch.

For 000506.SZ, bak_basic contains 区域地产 on 2018-06-19 and 黄金 on several
later dates inside SW's gap. This shows the security/reference observations
exist; it cannot supply the missing SW taxonomy history. For 000975.SZ,
bak_basic starts too late to resolve its 2014 gap. On 2019-07-24 its coarse
label remains 铅锌 while SW changes to 黄金, illustrating that category strings
and change dates from different systems need not be identical.

Delisted stock_basic reference rows have null industry while SW retains their
industry histories. This explains some current-reference nulls as distinct from
historical SW membership; it does not establish contemporary tradability.

## PIT and old-source disposition

All historical SW mapping remains **best_effort**. Retrospective dates under
SW2021 do not prove knowledge in 2014. Raw first-observed times are actual
retrieval times; future ongoing observations can form observed history.

The 2,596 prior bak_basic RawBatches / 10,712,417 rows are retained unchanged and
explicitly marked **superseded_forensic** in [source_dispositions.json](source_dispositions.json),
with the exact raw ID list and collection-manifest hash. Permitted uses are
coverage/listing sanity and legacy comparison; they cannot become SW canonical
truth. The previous null-classification promotion proposal is suspended by the
user's source decision.

## Acceptance gates and source decision

| Gate | Result |
| --- | --- |
| SW2021 identity and hierarchy | PASS |
| Historical rows retrievable and bounded repeated requests stable | PASS for pilot observations |
| Complete required historical membership | FAIL: three unexplained gaps |
| in/out boundary independently established | OPEN; empirical conventions measured |
| Unexplained interval overlap | None in pilot |
| Key old missing case explained throughout required scope | FAIL: 001289.SZ initial gap |
| Pre-2014 origins preserved | PASS at raw level |
| Required 2014 coverage | FAIL: 000975.SZ |
| Request/payload/profile binding | PASS, zero structural issues |
| Honest PIT / no old-source promotion | PASS |

**Next source decision:** retain SW2021 as the intended authority, but obtain
complete supplier history or primary SW effective-date evidence for these exact
counterexamples before qualifying this endpoint for full scope. Do not assume
category batching recovers missing records: the measured batches agree with the
incomplete stock histories. This report does not authorize another source,
interpolation, narrower baseline scope, or promotion with unresolved gaps.

No industry_membership.v3 canonical contract has been published. Once the source
gates pass, it must express classification_system/level, codes/names, security,
intervals and observation/PIT provenance on a clean lineage, preserving v1/v2
read compatibility. Other accepted domain semantics remain unchanged.

The delist blocker is also **open**: one primary announcement corroborates
000005.SZ's 2024-04-26 removal date, but no complete 228-security boundary evidence
set is accepted. bak_basic and SW Y flags do not change the exclusive security
identity boundary.

## Validation and remaining V1 work

Public collector: axiom_data.sw_source.SwQualificationCollector, integrated into
existing collect_requests / thin collect CLI. Public read-only inspector:
axiom_data.sw_qualification.inspect_sw_pilot, receiving explicit immutable refs.
Collection code was frozen before the real requests. Tests cover scoped requests,
truncation/anomaly retention, old-builder rejection, and all three real gaps.
The latter uses clearly marked source projections to test qualification, while
real artifact binding/digest checks ran separately against the full RawBatches.

Full suite: **161/161 PASS**, zero skips, 110.825 seconds; exact log is in
full_tests.log. Final audit reloaded all 149 new and 2,596 forensic RawBatches. No merge,
push, new canonical Snapshot or pointer change. SW full bootstrap, the remaining
2014-to-current domains, 56-requirement full admission, baseline, daily/T+1,
revision/no-change, offline recovery and final Notebook remain pending. No
Research/Core/Trade/UI work was performed.
