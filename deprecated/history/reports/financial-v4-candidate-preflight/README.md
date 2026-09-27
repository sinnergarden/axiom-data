# Financial v4 candidate/View preflight

Status: **STOPPED_FOR_DESIGN** under the ticket's stop-on-blocker condition.
No product code or formal data changed. Candidate publication and View comparison
were not attempted after the prerequisite failure.

## Synced code and proposed composition

PR #10 was confirmed merged. Local main was fast-forwarded to origin/main:
`21382552bfa9531ad2c93c55a7beeeb7d6e66b33`; the worktree was clean before
creating the evidence branch.

- Existing candidate: `snapshot-aa48fb719f5db092e81a24b188e469e38d3ef05bde77f4ec39282e55b5ebb650`
- Intended replacement financial: `financial_events-7fe86eee9a5e0b64e9fb8cc47c0446a6677c4d4f0de59bef6979244e4d15c04a`
- All 17 intended unchanged refs are recorded verbatim in preflight-result.json.
- New candidate ID: **NOT CREATED**.

## Real blocking input

The probe verified both manifests' identity/digests, the financial contract digest,
and the complete `balancesheet-2015` partition's integrity before selecting the
full logical record below. It then called the existing production PIT selector
with the frozen real financial View request's policy/cutoff.

| Field | Both observations |
| --- | --- |
| Security | 002961.SZ |
| Report period | 2015-12-31 |
| Endpoint / report type | balancesheet / 1 |
| update_flag | 1 / 1 |
| ann_date | 20161209 |
| f_ann_date | 20161209 |
| first_observed_at | 2026-09-14T14:18:32.295744+00:00 |
| vendor_available_at | 2016-12-09T23:59:59+08:00 |

The sole actual supplier-field difference is `inventories`:
`null` versus `170629689.76`. Both records belong to the same immutable RawBatch.
Canonical `inventory` retains that difference. Raw locators, source reference,
revision IDs and exact rows are in preflight-result.json.

Policy: `best_effort_vendor_v1`.
Cutoff: `2026-09-11T15:59:59+00:00`.
Requested security: `688981.SH`.

Actual selector result:

```text
MarketContractError: ambiguous simultaneous revisions: c272bbb844cc44b1d75c80769bf9c7bcd27a91b1990885f34f92fe308e5116dc
```

This is an intentional unresolved v4 source conflict, not evidence of an
optimization regression. The records have neither a preferred-flag distinction
nor a publication-time distinction. No winner was selected.

In current code, `pr6_views.project` calls `pr6_coverage.admit_view` before
security projection; admission calls `select_revisions` on the complete
financial commit. This observed logical-key conflict therefore blocks financial
View admission even for another requested security. That connection is code
inspection; a View builder was **not** executed in this probe.

## Requested stages

| Stage | This run |
| --- | --- |
| Sync latest main and clean-worktree check | PASS |
| Bounded real financial prerequisite | REJECTED, 6.272 s |
| Publish/validate new replacement candidate | NOT RUN |
| adjusted_price comparison | NOT RUN |
| market_replay comparison | NOT RUN |
| market_qlib comparison | NOT RUN |
| financial comparison | NOT RUN; prerequisite blocked |
| event comparison | NOT RUN |
| completed resume = 0 builder | NOT RUN |
| Five-kind cold/warm elapsed and IO | NOT MEASURED |
| 17,900 full-Views extrapolation | NOT ESTABLISHED |

Earlier market measurements are not presented as results for a new v4 candidate.
A reliable all-five-kind completion estimate cannot be supplied from this run.
No full Snapshot closure, supplier request, Raw/domain rebuild, View publication,
bulk resume, catalog or current/default write occurred.

## Design decision needed

The approved v4 contract preserves truly unorderable observations as ambiguity;
the current financial View admission rejects any such visible conflict across
the full scope. Completing the five-kind benchmark requires Design to decide
how this existing conflict should affect View availability. This evidence PR
proposes no selector, scope, missing-value or source-ordering change.

## Reproduce

On the evidence host with the exact immutable inputs available:

```sh
PYTHONPATH=src python3 reports/financial-v4-candidate-preflight/preflight.py
```

The probe reads formal data and writes only its report beside the script. Its
validation scope is manifest/contract/one partition/exact PIT key, not complete
Snapshot closure. The script intentionally raises if the counterexample becomes
orderable, so an old failure report cannot silently substitute for a new check.
