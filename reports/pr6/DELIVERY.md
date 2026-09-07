# PR6 delivery: PIT reference and financial facts

Branch: `phase1/pr6-pit-financial`. Baseline: `81e3048cc0c11b0826609db66575f44c069ad07d`.

This deliverable is PR6 only. PR7 and total D-M2 acceptance remain deferred.

## Resolved scope

- slice_financial_as_reported: `balance.accounts_receivable`, `balance.current_assets`, `balance.current_liabilities`, `balance.equity`, `balance.inventory`, `balance.total_assets`, `cashflow.operating`, `income.net_income`, `income.oper_cost`, `income.revenue`, `indicator.current_ratio`, `indicator.debt_ratio`, `indicator.gross_margin`, `indicator.roe`
- slice_financial_stable_derived: `financial.single_quarter_oper_cost`, `financial.single_quarter_revenue`, `financial.ttm_net_income`, `financial.ttm_revenue`
- slice_pit_industry: `industry.membership`
- slice_pit_universe: `universe.membership`
- slice_valuation: `valuation.pb`, `valuation.pe`, `valuation.ps`

PR4 r5 package: all 1,518 manifest files passed SHA-256 checks before scope resolution.
The resolved source/semantic/fallback decisions are in the scope JSON and ADR 0005.

## Source and contracts

40 frozen PR6 supplier RawBatches across income, balancesheet, cashflow, fina_indicator, daily_basic, index_weight and bak_basic. Financial sampling uses 688981.SH over 2024Q1–2025Q1 and 600036.SH over selected periods; valuation/industry covers two symbols over 2025-06-10–2025-06-13. Index snapshots cover May/June 2025, with eight explicitly selected security identities.

Four new domains contain 29 financial revisions, 12 membership spans, 8 industry intervals and 8 valuation observations. All 57 rows are best_effort; observed=0, verified=0. Actual first observation is retained and operational/safe policies cannot backfill older dates. Simultaneous ambiguous variants fail selection.

Membership uses half-open intervals, independent observation time and boundary RawBatch lineage. Industry is the source classification label with explicit View encoding. Financial logical events include endpoint/report period/report type and retain content revisions, nulls and source refs. Single-quarter and TTM use exact visible components with max availability; type 1 consolidated cumulative reports are the supported derivation basis.

Valuation PE/PB/PS keep distinct supplier denominators. Market values and total/circulating shares reuse PR5 canonical owners. Financial indicator ratios use supplier_field_only.v1; no accounting formula silently fills a missing supplier field.

## Reader and evidence

SnapshotReader supports as-of, financial derived, historical union and per-date members/classifications. FactView consumes a prebuilt PR6 materialization. QlibViewReader consumes that same fixed artifact with its field and industry maps. Revision/event/derived lineage stays in the same artifact, without a live sidecar.

Snapshot: `snapshot-da421c097632fc1b2d80234d7ffb76cd59dc067125c399061f1675e167cebf3c`.

Fact/Qlib View: `pr6-fact-29352750a024a4956f26dab72fa96803259f09fe53f780fa69da3cf81512f79f`.

Independent numeric RawBatch mapping checks: 37 rows. Direct/Qlib equivalence: 184 field values and nulls across 8 rows. Full real TTM for 688981.SH: revenue 61,502,873,000 CNY; net income 7,243,873,000 CNY. Four quarter values and exact component revision refs are preserved.

Membership reconciliation: 4/4 bounded index/date sets match frozen Qsys. PR4 all-history union reconstructs 3,601 securities and exact digest `5bb93d10bf5c01d6b2962decb48356578bb8942f787c5746c77d0b3a09b1df93`. Inclusive Qsys ends are explicitly converted to exclusive Axiom ends.

Income comparison: 10 field comparisons match, 5 differ between retained terminal variants and the one frozen Qsys variant, and 15 lack a matching frozen report period. Legacy Qlib: 40 match and 40 differ (including missing fields). These are recorded comparisons, not a claim of complete legacy equality.

The frozen adapter contains current_ratio, margin, debt and ROE fallback paths and PE/PS-to-TTM substitutions. Axiom does not adopt those source substitutions. Missing frozen quarterly coverage prevents reconstructing a complete Qsys TTM for this sample; the Axiom real TTM is independently checked against supplier cumulative components. These gaps remain explicit rather than being filled from a live Qsys source.

Frozen industry integer values lack their frozen taxonomy map. Supplier labels match the Axiom RawBatch mapping, but a semantic label-level Qsys industry comparison remains unresolved. Monthly index snapshot dates are not certified rebalance dates. Historical revisions have no typed public-vintage evidence; verified remains unavailable.

## Offline recovery

Source root: `/home/liuming/workspace/axiom/data/forensic/pr6-pit-financial-20260908-r1/validated-root-v3`.

Recovery root: `/home/liuming/workspace/axiom/data/forensic/pr6-pit-financial-20260908-r1/recovered-root-v3`.

Frozen implementation bundle: `16f33772f0da6a05004942f200df224e8476199e5b2ed4ddc148fb25e0188943`.

Executed from the frozen package. Both reconstructions started from raw plus frozen forensic inputs; Python socket creation and connections were blocked during builds. Raw→all baseline/PR6 commits→Snapshot→Fact/Qlib→catalog completed with equal identities and logical contents. Actual-root evidence validation passed all six gates.

Integration and adversarial tests cover before-entry market reads, entry/exit/reentry cohorts, changing industry, old-announcement late revisions, cumulative missingness, TTM future components, simultaneous conflicts, missing preferred supplier fields, individual RawBatch scope/profile forgery, actual-root substitution and coordinated fake evidence refs.

## Deferred PR7 leaves

`forecast.announcement`, `forecast.period_end`, `forecast.type`, `holder.number`, `holder.top10_ratio`, `margin.balance`, `margin.buy`, `margin.lend_balance_volume`, `margin.lend_repay_volume`, `margin.lend_sell_volume`, `margin.repay`, `margin.total_balance`, `moneyflow.big_buy`, `moneyflow.big_sell`, `moneyflow.net`

No Research Feature, Label, Model, Trade/account logic or UI implementation is included. No merge is performed.

## Tests and final validation

Full suite: 96/96 PASS (75.205 seconds). `git diff --check`: PASS.
The repository implementation files match the frozen validation bundle byte-for-byte.
All six actual-root evidence gates passed. See `tests.txt`, `run_manifest.json` and
`validation_attempt.json` for exact results and references.
