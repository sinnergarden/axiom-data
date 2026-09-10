# PR8 SW2021 authority decision — 2026-09-10

User decision: Axiom Data V1 uses SW2021 via Tushare index_classify and
index_member_all. CITIC remains qualification/comparison evidence only.

The sole authorized anomaly maps index_classify code 850401.SI, name 特钢Ⅲ,
to canonical 850412.SI. The user reports independent external evidence supporting
850412 = 特钢Ⅲ; no external URL was supplied, so this decision does not fabricate
an external citation. Frozen endpoint responses provide local evidence.

Before applying this mapping, automatically verify all four conditions:
1. Taxonomy code 850401.SI names only 特钢Ⅲ.
2. All 特钢Ⅲ member observations use 850412.SI.
3. No actual member observation uses 850401.SI.
4. Code 850412.SI does not identify another node/name.

Any failure is a new taxonomy conflict. Do not infer general name-based aliases.
Keep original Raw payloads unchanged. Canonical membership uses 850412.SI with
source mapping provenance and version-bound builder identity. Future source fixes
require a new profile/build version; published Snapshots stay unchanged.

The 32,458 previously affected sessions use resolved membership. Other proven
source gaps remain classification_unavailable; out-date uncertainty remains
boundary_session_ambiguous. No forward fill and no CITIC fallback.

After validating this correction, continue all original PR8/V1 deliverables.
Do not repeat source selection, delist qualification or candidate recovery work.
