# Gate A before bulk execution

`plan-gate-a --scope <explicit scope.json>` freezes target dates and securities.
`validate-gate-a --plan <plan.json>` is read-only with respect to the data root.
It returns GATE_A_READY_FOR_BULK_BUILD or GATE_A_BLOCKED. READY still requires
the independent reviewer; the command never starts collection or publication.
`gate-a-completeness-matrix` emits the original 13 source bindings and all of
their endpoint rules, directly from the installed SourceProfile extension and
the frozen 56-requirement registry.

Gate A checks source contracts, complete deterministic request planning,
historical sparse execution, shared public admission routes, reviewed industry
and security qualification, historical View geometry and future evidence schemas.
It binds the current Git revision, installed Python/JSON bytes, profile/policy
digests, planner/config identity and validator version. `validate_gate_a_report`
recomputes this binding; an earlier PASS cannot stand in for changed code.

The executable admission routes have frozen function-body digests in the Gate A
contract, including bootstrap's canonical-checkpoint branch and shared Raw
admission. The AST call inventory alone is not acceptance: retaining a validator
name in dead code does not preserve the reviewed executable binding. Changes to
these routes require an updated binding and independent review.

Gate B has a separate execution-status field. Its immutable artifact/ref schema
is checked before bulk; missing future admission/daily/Notebook executors remain
visible as CAPABILITY_BLOCKED. No absent baseline or unexecuted final Notebook
is treated as a Gate A failure. `validate_terminal_evidence` still refuses final
acceptance while any required executor or bound evidence is missing.

Historical sparse execution uses the existing collection checkpoints and split
graph. Complete coverage refers to an exhausted source request under its declared
contract. Empty event responses, unknown observations, source snapshots and
unqueried intervals retain different semantics. Parent intervals aggregate only
after every admitted child covers its exact portion. A listing snapshot is not
official historical delist-boundary evidence; the accepted exchange source and
boundary contract remain required. No source observation establishes verified
historical PIT merely because it was collected later.

New SourceProfile completeness rules are an explicit versioned extension of the
base mapping profile. New Raw provenance and build configuration bind its digest.
Raw payloads and old base profile bytes remain unchanged. Coverage v1 is read
under its frozen projection; coverage v2 records current policy revalidation,
including an empty response or the same Raw under a changed validation contract.

New v2 builds also requalify inherited parent and dependency sources before
mapping. This applies to direct BuildApplication calls as well as public
bootstrap/repair. Paginated observations are validated within each commit's
ordered input group, so separate historical revisions cannot supply each other's
missing pages. Successful current-policy checks are reused only within one
candidate invocation and data root; a fresh invocation verifies them again.

Writable contracts are explicitly frozen in `contracts/writable_contracts.v1.json`.
Superseded domain versions and `source_observations.v1` remain readable but
cannot publish new DomainCommits. The shared public build boundary enforces the
domain policy, and the actual publisher requires current complete v2 coverage,
including when called directly. Bootstrap, daily, repair and resumed build plans
use the same admission. Versions still declared current, such as
`market_daily.v1`, remain writable. A domain or coverage-contract upgrade requires
a new lineage; a same-v2 completeness-policy revision retains current revalidation.

Sparse readiness now executes an offline behavioral pack through the public
planner, executor, aggregation and admission paths. Evidence records expected
and actual outcomes for exact child closure and destructive missing, gap,
truncated, partial, selector, overlap and wrong-scope cases. A separate controlled
split-provenance isolation checks the same aggregate body's interval guard.
Fixture setup errors block readiness. Function digests record execution identity;
they do not substitute for these behavioral results. Complete empty coverage
means a particular source/request returned empty under its declared contract,
not proof that the economic world never had an event.
