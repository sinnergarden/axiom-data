# PR7 bounded D-M2 review report

PR6 e23d8234dd110afd2972cc5647bfea14df4b019b was fast-forwarded into main and
pushed. PR7 is delivered on `phase1/pr7-dm2-completion` for independent review.
The completion claim is limited to frozen PR4 V1 dependencies and actual PIT
qualification. PR7 is not merged.

1. Branch: `phase1/pr7-dm2-completion`; the delivery commit is the commit containing
   this report (reported explicitly in the handoff message).
2. Exact PR7 leaves: `forecast.announcement`, `forecast.period_end`, `forecast.type`, `holder.number`, `holder.top10_ratio`, `margin.balance`, `margin.buy`, `margin.lend_balance_volume`, `margin.lend_repay_volume`, `margin.lend_sell_volume`, `margin.repay`, `margin.total_balance`, `moneyflow.big_buy`, `moneyflow.big_sell`, `moneyflow.net`.
   PR5 18 + PR6 23 + PR7 15 = 56, disjoint. PR4 package: 1518 verified files.
   No remaining standalone stable-derived leaf is added.
3. [56 requirement coverage and 469 dependency matrix](coverage_and_feature_admission.json)
   binds each actual owner, contract, artifact, public field, qualification, scope
   and consumer count. No unknown owner or legacy-only requirement remains.
4. New contracts: holder_count_events.v1, top_holders_reports.v1, margin_daily.v1,
   moneyflow_daily.v1, forecast_observations.v1. Snapshot v4 composes 18 domains.
   Prior Snapshot and View schemas retain their original dispatch paths.
5. Profile `tushare_pr7.v1`: stk_holdernumber, top10_holders, margin_detail,
   moneyflow, forecast. Every collection is a per-request validated RawBatch v2.
   Raw profile digests and transitive source mapping are checked on publication
   and load. Request identities are in the frozen build plan.
6. [PIT qualification](pit_qualification.json): new canonical rows are 100%
   historical bootstrap best_effort. Operational selection uses real observation
   time. Verified remains fail-closed. Same-retrieval publication order is explicit;
   conflicting identical observation/publication times fail.
7. Holder: 67 count events, 15 reports retaining 150 holder rows. Complete-group
   aggregation requires ten distinct valid holders. Partial report, null holder,
   disappeared holder, report-period revisions and recurrence are tested. Group
   component provenance is exposed in typed facts. Holder-only extension reuses
   all other domain refs and performs no supplier recollection.
8. Margin/lending: 12 real rows and all seven required leaves, with CNY/shares
   units and zero distinct from vendor_null/not_provided.
9. Moneyflow: 12 real rows. ELG buy/sell are separately preserved, values convert
   from 10,000 CNY to CNY, and the supplier L2 net retains its sign. The independent
   checker does not derive net by summing size buckets.
10. Forecast: three real 000401.SZ company performance forecast observations,
    using fiscal report period as horizon and the source classification enum as
    metric. Announcement, period and type are public typed leaves. They are not
    analyst consensus or numerical profit predictions. Empty responses for the
    other two sampled symbols remain missing evidence.
11. [Artifact identities](run_manifest.json): Snapshot-bound PR6 and PR7 Fact/Qlib
    views plus explicit-anchor adjusted prices. PR7 has 12 numeric Qlib fields and
    three event/date/text forecast leaves in the same typed metadata artifact.
    Public access uses SnapshotReader/FactView and QlibViewReader, with no sidecar.
12. [Reconciliation](reconciliation.json): 211 independent raw field checks and
    144 direct/Qlib numeric checks pass. Frozen immutable shareholder bytes match
    24 count events and all 15 Top10 aggregates. The remaining 43 count events
    have no matching frozen rows. PR4 provides no immutable margin/moneyflow/
    forecast fact rows for comparison; these are explicitly unavailable historical
    evidence, not claimed matches. Mutable Qsys canonical data was not adopted.
13. 469/469 authoritative Feature Data dependencies resolve through the 56 public
    requirements. Feature calculations and predictive validity are outside admission.
14. [Legacy dependency kill test](legacy_kill.json): explicit old SysQ, Qlib and
    sidecar read probes are denied; the complete 56-leaf consumer succeeds with
    no attempted legacy read. Guards cover the Python I/O used by the consumer.
15. [Offline rebuild](offline_rebuild.json): all frozen PR5/6/7 raw is rebuilt into
    a separate root with network and external legacy reads denied. The frozen code
    bundle is actually executed in another interpreter. Domain/Snapshot/View IDs,
    schema/values, PIT, union/lookback, derived results, 56/469 admission and catalog
    deletion/rebuild agree. Both roots are independently reopened and checked.
16. [D-M2 acceptance](dm2_acceptance.json): D01–D15 are mapped to passing tests and
    bounded real evidence. Independent PR7 review remains the final release gate.
17. Full suite: 136/136 PASS, zero skipped, 100.969 seconds; [test log](full_tests.log).
    Diff and commit whitespace checks are recorded at delivery.
18. Limitations: bounded dates/symbols, terminal best_effort history, unavailable
    strict historical adjusted prices (explicit research_non_pit view used), missing
    frozen comparisons described above, and formal S180 income offline replay still
    unavailable. No production scheduler or automatic current adoption is introduced.
19. D-M3 and Research/Core/Trade/UI integration have not started. PR7 awaits review.

## Exact bounded artifacts

- snapshot_id: `snapshot-fa3d8b80c729edd2e139a49c82b3dea868bb5639998ba7cc84f540c0623fe786`
- pr6_view_id: `pr6-fact-128ce659c9eeef38922d7e762ec56bb4ad353b337ab4fe5ffc7de29f9a0e754f`
- pr7_view_id: `pr7-fact-bbd7326584cd74667cc3de6d964ee10cbd4e2b434cffcd94222ecee7a2aedcae`
- adjusted_view_id: `adjusted-price-5722f89cb9640a0caaeb1c653d6bda330e8ffecd7e5f0987a895634051c2c47b`

Source: `/home/liuming/workspace/axiom/data/forensic/pr7-dm2-20260909-r1/admission-r3/source`

Recovery: `/home/liuming/workspace/axiom/data/forensic/pr7-dm2-20260909-r1/admission-r3/recovery`

## Reproduction

Run `scripts/check_pr7_evidence.py --reports reports/pr7` with `PYTHONPATH=src` to
independently validate the terminal report against actual artifacts. The frozen
build plan and executed code bundle paths are recorded in offline_rebuild.json.
Collection uses `scripts/collect_pr7.py --run-root <explicit run root>`; rebuilding
uses `scripts/run_pr7_dm2.py --target <new root> --plan <explicit frozen plan>`,
after copying precisely the raw identities named by that plan.
