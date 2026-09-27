# PR8 legacy publication and sparse behavior

Base: `8ef2ce12de47626a57a74f7dc2c9ace7a2cc69e5`.
Scope is the two reported P1 issues. Earlier Gate A evidence is superseded.
No bulk bootstrap, new domain, or other V1 capability is part of this change.

## Writable admission

`contracts/writable_contracts.v1.json` explicitly records current domain contracts
and superseded read-only versions. It also records writable coverage v2 and
read-only coverage v1. Versions that are still current, such as market_daily.v1,
remain usable. No new domain schema is invented and no highest-file-version
heuristic selects a contract.

BuildApplication checks the requested domain contract before invoking its
executor. MarketDomainBuilder independently checks it at the publisher boundary,
defaults every new build to coverage v2 and requires current complete admission.
Selecting the base builder or omitting coverage cannot admit an unestablished
source. Public operation plans share the same contract guard before execution
or resume. Errors explicitly identify LEGACY_CONTRACT_READ_ONLY.

An upgrade of either the domain contract or legacy/absent coverage to v2 requires
a new lineage. Immutable Raw may be reused after current requalification. A
same-v2 completeness-policy revision still follows the existing revalidation
and identity rules; it does not require an unrelated new lineage.

Historical loaders retain their artifact contracts. Legacy v1 coverage uses
legacy validation; artifacts predating coverage also use their frozen legacy
source rules. Manifest, payload, mapping, dependency and closure checks remain.
Readable historical input does not authorize a legacy publication or waive
current source requalification.

## Behavioral Gate A

The public planner and executor produce an exact four-day forecast split with
the actual declared source cap. Both empty children are admitted; resume must
make no new source calls. Fixed destructive fixtures then exercise the current
public aggregate with a missing child, gap, truncated child, partial child,
selector mismatch, conflicting overlap and empty child bound to another scope.

Each case records an expected and actual outcome. Setup failures are separately
reported and cannot count as successful rejection. Because canonical split
provenance also rejects malformed intervals, an additional controlled resolver
fixture tests the same aggregate body's gap guard independently of that
redundant check. It is identified as an isolation fixture, not public source
availability evidence.

Always-COMPLETE, lost child-completeness behavior and a removed interval guard
must cause the pack and Gate A to block. A non-function Mock validator is also
actually exercised rather than rejected by reflection before the behavior pack.
Function/module identities record execution provenance; they do not establish
correctness. Gate A records writable/read-only policy versions, all 13 source
policies, fixture IDs/outcomes and current code/config bindings.

Empty coverage means this source request returned empty under its documented
scope and capability. It does not prove economic history had no event, and does
not establish verified historical PIT.

## Test compatibility and review

Synthetic unit sources have explicit payload-bound proofs installed only in
test scopes and restored by cleanup. Production contains no fixture exception.
Real-source and Gate A tests run with the production validators. Incremental
tests that need a current baseline rebuild a clean lineage from validated Raw
and assert equal canonical rows. Legacy read tests use published artifacts or
an explicitly frozen coverage-v1 test manifest, not a writable legacy flag.

Independent Astra approved the production guards and behavioral pack, then
approved the scoped test adaptation. Final test results and commit-bound
acceptance evidence are recorded at delivery after validation.

## Frozen-code validation

Luna executed the affected suite: 134/134 PASS in 98.583 seconds, and full
suite: 366/366 PASS in 164.602 seconds. Logs: p1_write_behavior_affected.log
and p1_write_behavior_fullsuite.log. No implementation changes followed these
runs. Independent r2 Astra code review: APPROVE. Final full-target Gate A
production and replay receipts are stored outside the repository under
../review/pr8-p1-<full-commit-sha>/ so the report binds the exact reviewed commit.
No bulk bootstrap was started.
