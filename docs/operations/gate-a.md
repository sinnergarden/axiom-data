# Gate A code and source readiness

`axiom_data.gate_a.make_gate_a_plan(scope)` freezes an explicit source scope and
the future acceptance requirements. Scope contains `symbols`, `start_session`,
`end_session`, `financial_observation_start`, `benchmarks`, and `universe_ids`.
The financial start is declared separately to retain lookback observations.
No wall-clock end date, default Snapshot pointer, or shortened probe scope is
substituted. Save the returned JSON and pass it to `validate_gate_a(plan)`.

The validator returns `GATE_A_READY_FOR_BULK_BUILD` or `GATE_A_BLOCKED`, with
individual findings and code/profile/plan digests. It never contacts a source,
loads the full data root, builds artifacts, or promotes a baseline. Even READY
has `external_review_required=true` and `bulk_authorized=false`: an independent
reviewer must approve the exact code and plan before source preflight and bulk.

The packaged `scope/gate_a.v3.json` binds each of the frozen 56 public Data
requirements to actual public fields, canonical/derived owners, source profiles,
endpoints, planners, coverage policy, and historical PIT qualification. The
469 dependency edges remain bound to the original registry digest. The source
planner actually runs without I/O for the requested range; request unions are
checked separately for every security/index and endpoint. A first/last date or
one representative source request cannot fill a hole. Reference acquisition is
explicitly reused through `validate_domain_commit_closure` before the source
preflight; this step does not recollect already accepted industry/security data.

Every required source endpoint must have an established policy returned by the
shared `source_completeness.completeness_policy`. The validator exercises its
empty-result and unproven at-cap boundary. A missing profile/planner/policy, or
an at-cap gate that silently accepts the payload, blocks readiness. Unestablished
supplier limits remain findings; this module does not invent limits to make the
gate pass. It binds the accepted SW2021 anomaly and gap policies, exclusive
official delist boundary, and the coverage-state contract. Public bootstrap,
daily, repair, inspect and catalog entry paths are checked; current code digests
are recorded for provenance. Gate A exercises the public write and View boundaries
and the sparse behavior pack. It does not require current Python source to match
a frozen function-body digest. Independent review and operation regressions still
assess broader behavioral equivalence.

## Historical View execution

After identity/calendar validation,
`plan_historical_views(target=..., security_rows=..., calendar_rows=...,
universe_ids=..., knowledge_cutoff=...)` generates ordinary `materialize_views`
requests for all five families. Each security uses its own eligible interval and
its **last eligible target session** as the adjusted-price anchor. This works
when an early delisted security and a later listed security share no possible
common anchor. Prices use the existing `research_non_pit` anchored formula;
market Qlib remains unadjusted. Financial and event Facts retain their complete fixed field sets
and existing `best_effort_vendor_v1` projection.

The complete requested target, eligible sessions and every identity exclusion
remain in the output. Unknown identities or securities with no eligible target
session block geometry. An eligible anchor does not prove its factor is present.
Source gaps, missing financial endpoint observations, and other strict View
admission failures require actual source evidence and qualification. No source
gap is waived here and no published View/Reader contract changes. The existing
single-anchor `validate_admission_plan` remains a separate geometry contract;
do not feed this per-security-anchor plan into it as if their semantics matched.

## Future terminal evidence

`validate_terminal_evidence_plan(plan)` checks the exact required evidence
categories, fields, producer/validator signatures and code digests. Symbolic
values such as `$baseline.snapshot_id` describe future explicit bindings; they
are never passed to builders by this validator. Future artifacts are not needed
for Gate A. Missing execution capabilities are reported as
`CAPABILITY_BLOCKED`, and block Gate A.

The evidence plan requires:

- A baseline Snapshot ID, manifest digest and full target digest.
- Explicit Snapshot-bound refs and manifest digests for all required Views.
- Full 56-requirement admission and the original 469 dependency mappings,
  source-backed coverage qualifications, PIT and artifact refs.
- Distinct T+1, no-change, late-data and interrupted-resume daily executions.
- An offline copied-root recovery record and revalidated Snapshot/View refs.
- An executed, parameterized Notebook bound to that baseline, target and Views.

`validate_terminal_evidence(root, evidence, plan=...)` fails closed on missing
categories and on unavailable formal verifiers. When those capabilities exist,
it reloads actual Snapshot/View artifacts and hash-bound execution records before
delegating full admission, daily scenario semantics and Notebook execution
bindings to their formal validators. Report `PASS` flags are insufficient. The
output still requires independent Gate B review and never sets consumption
readiness or changes a pointer.

The bounded admission script and Notebook are historical fixture evidence;
they are not registered as full-range execution capabilities. Until the formal
full-admission, daily-evidence and parameterized Notebook entries are present,
the gate reports their absence. This is an explicit code capability gap, not a
request to generate future baseline artifacts during Gate A.

## CLI and reproducible code probe

`plan-gate-a --scope scope.json` emits the frozen plan, and
`validate-gate-a --plan plan.json` emits readiness (exit 0 for READY, 1 for
BLOCKED). Both share the exported public services. The global `--data-root`
argument is accepted for CLI consistency; these commands do not read or write
that root.

[归档范围证据](../../deprecated/history/index.md) is a single-security 2014–2026 date-range
code probe with explicit earlier financial lookback. Its result checks all 56
requirement bindings but is not 3,601-security source availability/admission
or a bulk-build plan approval. Missing code/profile capabilities block every
scope; reducing a probe does not waive those blockers.
