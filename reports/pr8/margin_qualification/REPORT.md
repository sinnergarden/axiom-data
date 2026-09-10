# Signed supplier repayment observations

The exact 3,601-request / 4,880,379-row acquisition contains 361 rows with
negative repayment values: 343 rqchl and 20 rzche cells (two rows have both).
All original rows, indexes and Raw IDs are retained in source_scan.json.

Tushare's margin_detail documentation identifies rzche as financing repayment
amount and rqchl as securities-lending repayment quantity. It supplies no
revision/adjustment discriminator that establishes the required nonnegative
repayment fact for these signed observations. Documentation:
https://tushare.pro/document/2?doc_id=59 .

The explicit margin_negative_repayment.v1 policy qualifies only those two
fields when finite and negative: canonical value null, missing_reason
source_repayment_unresolved. Other fields retain their original mapping.
Negative balances, buys and sells still fail. The policy never clips, takes
absolute values, or reconstructs repayments from changes in balances.

Each actual observation retains negative_source_values, the qualification
profile, source_ref and actual observed_at. Profile bytes and implementation
are bound to builder identity/config. Raw remains unchanged. Removing the
qualification evidence fails validation. Old configurations continue rejecting
the negative input. Daily economic session and PIT observation times are intact.

This is explicit unavailable-data evidence, not an economic explanation of
supplier adjustments and not a certification of those repayment values.
Full canonical/admission/V1 acceptance remain outstanding.

Full source mapping, row contract and exchange-session scan: 3,601 requests /
4,880,379 rows PASS, no failures, 204.352s. Full suite 200/200 PASS, zero skips,
67.678s. Canonical retry and full V1 gates remain due.
