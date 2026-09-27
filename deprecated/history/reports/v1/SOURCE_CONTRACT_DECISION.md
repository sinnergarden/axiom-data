# V1 source-to-contract continuation decision

Status: proposed; existing contracts and runtime behavior remain unchanged.
Parent checkpoint: 57c6a567f71e1e5f2bdafc76c2949adcd56d1d46.

The user request section 1 freezes accepted business semantics and requires
counterexample → contract → fixture → validation for real operational blockers.
This document defines the smallest proposed change and its acceptance gates.
It is not approval of a contract or the V1 baseline.

## 1. Security delisting: evidence-backed source mapping

Keep security_master.v1's `[list_session, delist_session)` definition.
The supplier's field name alone is insufficient to establish which delisting
event it denotes. A newly inspected primary disclosure separates those events:

- Security: 000005.SZ; issuer announcement 2024-021, dated 2024-04-25.
- Termination decision: 2024-04-11.
- Removal from listing: 2024-04-26.
- Frozen Tushare stock_basic:D reports delist_date=20240426.

The dates and distinction are stated on page 1 of the issuer announcement
hosted by [SZSE](https://disc.static.szse.cn/disc/disk03/finalpage/2024-04-25/b8989912-ffa8-42a1-9f29-915e833bbb84.PDF).
[Tushare documentation](https://tushare.pro/document/2?doc_id=25) describes
delist_date as the delisting date, but does not establish the contract's interval
boundary or prove every security's mapping.

Inference: for this one security, the supplier date matches removal from listing
and differs from the decision date. This is useful boundary evidence, not a
blanket validation of all 228 securities. Do not use the decision date, last
market row, or the last open session as a substitute boundary.

The original PDF has now been downloaded and saved read-only under the explicit
data root. Its byte digest and retrieval metadata are in
[primary_boundary_evidence.json](primary_boundary_evidence.json). This is a
frozen evidence file; it has not been admitted as a new canonical source input.

Proposed implementation path:

1. Freeze applicable primary evidence as explicit immutable input, including
   source URL, document digest, security, event type and date. Every admitted
   non-null boundary must have the required evidence under a new SourceProfile.
2. The new mapping verifies the supplier date against that evidence, then emits
   the existing canonical exclusive boundary. A mismatch or absent evidence
   still fails. Publish a clean source/builder lineage from explicit inputs.
3. Preserve tushare_phase1.v1 dispatch and its current non-null rejection.
   Keep old security_master artifacts readable under their declared identities.
4. Verify each admitted security's boundary, plus fixtures distinguishing
   decision/removal dates, equality at the exclusive endpoint, mismatching
   evidence, missing evidence and old Snapshot immutability.

No global stock_basic relaxation follows from the one confirmed example.

## 2. Industry: explicit missing observation contract

Counterexample: 001289.SZ has industry=null on 2022-01-24, its supplier listing
date. Exact source row and original RawBatch ref are frozen in
tests/fixtures/v1_source_blockers.json. Current industry_membership.v2 requires
a non-null industry_id. The adapter, validator and View encoding all assume a
real classification; simply removing the adapter exception is incorrect.

Proposed decision: add industry_membership.v3, on a new lineage root, to retain
observed missing classifications. This changes the row contract and requires an
explicit decision under the user's accepted-semantics freeze.

Proposed row states:

| Source observation | industry_id | missing_reason | Interpretation |
| --- | --- | --- | --- |
| Present, valid classification | nonempty string | null | Classification observed |
| Present, industry explicitly null | null | vendor_null | Missing classification observed |
| Present, industry field absent | null | not_provided | Supplier omitted classification |
| No source row / missing request | no canonical observation | no fabricated row reason | Coverage gap |

Empty strings and invalid types remain source-validation failures unless a
separately evidenced SourceProfile defines them. Missing reasons are not fake
industry identifiers. Raw diagnostic rows before listing remain in Raw; their
canonical treatment must respect the existing security identity bounds.

The new row version retains the existing logical event, observation sequence,
revision, PIT policy and interval machinery. Revision fingerprints include
industry_id and missing_reason. A visible missing correction must supersede the
older classification for its interval; there is no fallback or forward fill.
Future observations must preserve old-cutoff logical results.

Direct Fact output and a versioned new View expose value=null, the selected
missing reason, actual usable time and source/observation/revision refs. Numeric
industry encoding includes only valid identifiers; missing observations emit
null, not an artificial code. Old View versions continue their frozen dispatch.

Separate observation coverage from qualified classification coverage. A present
null observation may prove that a request/observation exists; it does not prove
a usable classification or satisfy a consumer's non-null requirement. Existing
required coverage failures remain failures. Full admission must report actual
available/qualified scope; implementing v3 alone cannot accept the baseline.

## 3. Decision validation matrix

Before promoting any output under the proposed versions, require:

- Exact real-source fixture replay for the two counterexamples.
- Industry valid → null → valid, and ABA source observation recurrence.
- Same interval late correction, with old-cutoff prefix stability.
- No-row source gap remains distinct from an observed null.
- Malformed null/reason combinations reject.
- Direct Reader and new Fact/Qlib values and metadata agree.
- Valid industry taxonomy encodes no null or placeholder category.
- v1/v2 artifacts load and pass their original identity/digest checks.
- v2 parent → v3 child fails; v3 clean root rebuild from frozen refs succeeds.
- No full admission until source coverage and consumer readiness are assessed.

Runtime changes have not been made. The two current rejection regression tests
continue to express the accepted contracts while this decision is pending.
They were independently rerun after the evidence capture: 2/2 PASS. The preceding
158-test full-suite result remains the runtime checkpoint; this follow-up changes
only documentation and source evidence, so it does not claim a new full run.
