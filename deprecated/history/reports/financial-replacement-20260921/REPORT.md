# Financial v3 replacement candidate

Status: **RUNTIME_VERIFIED / CANDIDATE_BUILT**. This report records publication and targeted validation; it does not declare baseline acceptance or promotion.

## Published artifacts

- Code: `b9b23b5439cb4599ea0461a1bae441415d288028` (main, clean at execution).
- Public operation: `axiom_data.repair`, run `financial-v3-replacement-20260921-r1`.
- Data root: `/var/lib/axiom-data`.
- Old Snapshot: `snapshot-aba8944e426923db7a9f3e08a977c0e8a3a6fe12ba8b92561dfffd74ac285420`.
- New financial DomainCommit: `financial_events-ff83e5efc1f4c805281c556030b219de225fc2dba16e3a16ccf57cab6d8b17a7`.
- New candidate Snapshot: `snapshot-aa48fb719f5db092e81a24b188e469e38d3ef05bde77f4ec39282e55b5ebb650`.
- Financial contract: `financial_events.v3`, new lineage with no parent.
- Raw input: all **230,464** existing immutable Raw IDs, in the original order; original requested scope retained.
- Wall time through terminal validation: **8895.576 seconds** (148.26 minutes).

Exact manifest digests, complete reused reference objects, and stage results are in [manifest-references.json](manifest-references.json). Formal manifests are under `canonical/financial_events/commits/<commit ID>/manifest.json` and `snapshots/<Snapshot ID>/manifest.json` in the stated root.

## Validation

| Check | Result |
| --- | --- |
| Parent Snapshot: full 18-domain contract/closure validation | PASS |
| New financial v3: full contract/closure validation | PASS |
| New Snapshot: complete referenced closure through public publication | PASS |
| Exact old/new Snapshot reference comparison | PASS: only financial_events differs |
| All 17 unchanged domain refs, including contract/identity/content digests | PASS: exact equality |
| Original ordered financial Raw references | PASS: unchanged |
| Old Snapshot and old financial manifest bytes | PASS: digests unchanged |
| current/default pointer bytes and published View reference inventory | PASS: unchanged |
| Targeted financial source revision suite | PASS: 7/7, 0.293 seconds |

The public operation first verified the original immutable closure, then reused its checked commits within the same invocation for candidate composition. There were 19 recorded successful domain closure checks: 18 original commits and the new financial commit. No development-scoped validation substituted for publication checks. A separate post-run metadata check re-read the published Snapshot manifest, verified its digest, confirmed all new references appear in the successful closure records, and compared old/new references again.

### Real 002010.SZ regression

Report period `2026-06-30`, `supplier_indicator`; Raw `pr6-480cfa7d0400011c3adfcb532a724f202e65fbe15663730d840bbe37bd6a5b9c` contains `update_flag=1 / roe=2.4916` and `update_flag=0 / roe=2.4915`. The new canonical partition contains one matching observation with `roe=0.024916` and the same Raw source reference.

Both `best_effort_vendor_v1` at `2026-09-11T15:59:59+00:00` and `operational_pit_v1` at `2026-09-18T23:59:59+08:00` select revision `b150b4200cb22dd4f79aca0d2121d44b143491a924bc10ff4f553ed8faa71f9c` with that value.

The targeted suite verifies conflicting `1/1`, `0/0`, and unknown flags remain rejected; identical duplicates, row-order independence, observation boundaries, historical contract reading, and contract upgrade boundaries remain valid.

Command (temporary test roots):

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tests python3 -m unittest -v test_financial_source_revision
```

## Reused 17 commits

| Domain | Exact reused DomainCommit ID |
| --- | --- |
| adjustment_factors | `adjustment_factors-f668c7b41f28f6440c9cbddc3f8c8b54184340113555b6fe1e8c7ab9b1c163aa` |
| benchmark_daily | `benchmark_daily-f0c738958e27ee88f8d74512153b6d7351c93b9c6506976b3bf65846d70abbdc` |
| corporate_actions | `corporate_actions-bd6799108ee43cf237a366cb97eb0b6f855c629d38bb324d34556e5a51c61f50` |
| forecast_observations | `forecast_observations-fbc31264711758176b1dca3645bf37f9461b048a0b90652a5dd7025422a6467e` |
| holder_count_events | `holder_count_events-2bd13147f0b2f8bd7484f4b7b9efa0d40172624d187039f73a5241229ad7f968` |
| industry_membership | `industry_membership-a47f87e0431af950fd4135fce5f50e9f3bf5ba010906daa96ceae05e86c97a42` |
| margin_daily | `margin_daily-74fc981f2f7ca96d2cfbea10cfbbf5bba6a0a5dbfb595efa45171a4b395478a5` |
| market_daily | `market_daily-9ea11e3b19db8dcaf55c7e235b620e9fab4fc9239495279c45849ad887cf2ed9` |
| moneyflow_daily | `moneyflow_daily-6fc789a379c4038c8abf7aaaf8124b9321839f8232fbfa0a6ae44b9a0103f894` |
| price_limits | `price_limits-37ff0a91daeb3e1d6ec472f357a8d9bd2b4c1189706164ac1ed19003cd80ade1` |
| security_capital | `security_capital-6e9f56b079bb1f57c73ff9a74ae7fad75c004701d4e1e3e5f59be5813a6682fe` |
| security_master | `security_master-5540f406637d950766e036a973de7a4a8a96a7f3638b98e97fe5d96d28ac493c` |
| security_status | `security_status-7b4634ce532171696a0c1819c7f97fd4380583ec53775e8c0c6fbb8e19251c89` |
| top_holders_reports | `top_holders_reports-d2a42c310c88c74652cd8544924588530bf6faa647cd4887cf7b5d44949e1de0` |
| trading_calendar | `trading_calendar-b24314e06a79828a9ce9ff9ca4f2e75a7f12d5f4576c1961c01a68a644b694d7` |
| universe_membership | `universe_membership-7b5d90c08a8ea80f59ea9e23fc3e650db25910150e51c11f444d039c33b2f5d8` |
| valuation_daily | `valuation_daily-3888719d42d9e7f5a1a15b14b51a2fd79ca9caec2c4f1a7554de20442c13f858` |

## Execution boundaries and evidence

Publication used existing Raw only. No supplier requests, Views, bulk resume, or current/default promotion occurred. Published historical artifacts were retained. Product code remained unchanged; this PR contains only this report and its manifest-reference evidence.

Local detailed evidence: `/home/liuming/workspace/axiom/review/financial-v3-replacement-20260921/` (`repair-plan.json`, `publication.log`, `closure-completed.json`, `repair-result.json`, `validation-result.json`, `targeted-tests.log`). The formal operation checkpoint is `operations/financial-v3-replacement-20260921-r1/build.json` under the data root.

Scope of this evidence is replacement candidate publication and the checks above. Full Views and Gate B were not run.
