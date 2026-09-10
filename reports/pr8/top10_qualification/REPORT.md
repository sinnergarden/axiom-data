# Top10 same-publication source ambiguity

The complete 10,803-request scan rejects 115 RawBatches containing 192
same-report/same-name duplicate groups. None is an exactly duplicated row.
For example, 000011.SZ / 2014-06-30 / announcement 2014-08-12 has two
深圳市建设投资控股公司 rows with 323,796,320 and 323,796,324 shares, and
incompatible source categories. 000573.SZ / 2025-03-31 has two different
share/ratio observations under the name 张林. The endpoint does not supply
an ordering or a disambiguating holder identity for these alternatives.

The explicit builder policy top10_ambiguity.v1 retains all source alternatives
on one invalid holder entry, with null shares/ratio/category and
ambiguous_source_rows. No alternative wins, and values are never summed.
The accepted v1 completeness rule therefore produces a null top10_ratio with
incomplete_report. The report remains visible in PIT selection, so an older
complete report cannot silently replace this observation. A later unambiguous
observation can resolve it; old cutoffs keep the ambiguity.

Raw payloads are unchanged. The profile bytes and mapper implementation are
bound in builder configuration/identity. Old configurations still reject the
same duplicate-holder input. This qualifies unavailable source facts; it does
not certify their concentration or claim strict historical revision ordering.
Source variants, Raw refs, report revision and qualification are available in
the existing public holder metadata. Report count/completeness rules are intact.

The retained evidence files contain actual source alternatives and immutable
Raw IDs. Canonical bootstrap, full admission and V1 acceptance remain pending.

A second full scan including row validation identified 31 reports in 10 Raw
inputs whose ten individually valid, uniquely named holder ratios total more
than 100.01%. The same explicit policy retains source_ratio on every holder,
sets the canonical ratios null/inconsistent_report_total, and leaves the report
incomplete. It does not rescale percentages or infer missing share classes.
The real totals and original rows are in invalid_totals.json. Current full
source+row rescan: 10,803/10,803 PASS, 45.325 seconds; this validates honest
missing states, not the supplier's conflicting economic values.

Full suite: 198/198 PASS, zero skips, 100.147 seconds. Targeted real fixtures
verify Raw preservation, conflict metadata, null aggregates, later correction
visibility and rejection of fabricated source-variant evidence.
