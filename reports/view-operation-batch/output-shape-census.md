# View output shape census

Scope: the five current per-security View builders, their files and manifests. This is a bounded code and fixture inspection; the paused full-history preflight was not resumed.

| Family | Per-security output | Repeated shared context | Finding |
|---|---|---|---|
| adjusted_price | This security's dated adjusted rows | Snapshot/domain refs and the requested calendar interval | No full-market row duplication |
| market_replay | This security's replay rows | Snapshot/domain refs | No full-market row duplication |
| market_qlib | This security's feature files and instrument line | Trading-day axis for the requested interval | Small shared calendar axis; no full-market fact rows |
| financial, through v3 | This security's financial/valuation rows, plus **every member of each requested universe on every session** | Full-scope availability summary, industry code mapping, and implementation bundle | Blocking full-index membership duplication |
| event | This security's event rows and feature files | Trading-day axis and implementation bundle | No full-market event rows; bundle is repeated but bounded |

The financial path calls `reader.members(group, session)` without a security projection and appends every returned row to `payload.memberships`. `_files` then writes the complete payload into each security's `rows.json`. The candidate's two groups have 800 and 1,000 members in their frozen intervals; the requested full history has 3,088 SZSE open sessions. In the small immutable fixture, a selected member row occupies 1,576 JSON bytes on average. One 800-member group over only 3,000 sessions therefore suggests about 3.78 GB **per financial View**, or 13.54 TB for 3,580 Views, before the second group or other fields. This is a scale estimate from the frozen shape, not an actual production write measurement. The source counts and calculation are in `financial-output-size-evidence.json`.

Version 4 keeps the legacy inline versions readable and writes only this security's `wide`, `events`, `derived`, and `industries` rows plus its session axis. Its manifest references the existing immutable Universe Membership DomainCommit with the concrete Snapshot, group IDs, PIT policy, cutoff, and date interval. The full-scope membership selector still runs before security projection. The public Reader resolves the reference through the existing `members()` path.

Other repeated context is small by comparison: the fixture's implementation bundle is 954,234 bytes per financial or event View, and the financial availability summary is 3,568 bytes in the fixture. These are recorded for Design; this ticket does not alter their representation.
