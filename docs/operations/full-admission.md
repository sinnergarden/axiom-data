# Complete Data dependency admission

`full_admission(root, *, run_id, snapshot_id, plan)` inspects a fixed target and
writes `operations/<run_id>/full-admission.json`. It does not promote a Snapshot.
`validate_full_admission(root, report, *, expected_views, target_digest)` reloads
that report, reopens the exact immutable inputs, repeats its scope and value/state
checks, and rejects changed results. A `PASS` is Data dependency validation;
consumer business acceptance remains an independent decision.

## Explicit common-policy plan v1

The explicit `full_admission_plan.v1` object has these fields:

- `schema_version`: `full_admission_plan.v1`.
- `target`: the existing Gate A source scope: `symbols`, `start_session`,
  `end_session`, `financial_observation_start`, `benchmarks`, `universe_ids`.
- `scope_registry_digest`: `requirement_registry_digest()`.
- `snapshot_manifest_digest`: the fixed Snapshot digest.
- `required_view_configs`: all five family semantics as in
  `validate_admission_plan`, without the three target geometry fields.
- `views`: the exact `materialize_views` label → `{kind, config}` plan.
- `view_refs`: matching labels → `{kind, view_id, manifest_digest}` returned by
  `materialize_views`. Reference kinds use their stored names, including
  `financial_fact` and `event_fact`.

The entire target must be covered. Actual manifest scopes, policies, cutoffs,
anchors, universes and Snapshot refs must match the plan. Benchmark IDs are
explicit. Financial observation coverage is checked from actual ancestor Raw
request `start_date`/`end_date` interval unions for every security and endpoint;
quarter-only requests cannot establish that window. Raw completeness is
requalified with the current existing source policy. More recent data or an
otherwise complete four-day View cannot prove a larger target.

The operation shares one checked Snapshot and financial preparation, processes
compatible single-security fact shards in batches of at most 50, and consumes
canonical daily inputs in monthly slices. Reports retain per-leaf counts,
original quality-state counts, bounded missing examples and logical digests,
plus actual source commit refs. They do not persist another certificate store or
calculate any of the 469 Features. Full-history performance has not been proven
by these small tests.

## Per-security historical plan v2

`full_admission_plan.v2` has the same `schema_version`, `target`,
`scope_registry_digest`, `snapshot_manifest_digest` and `view_refs` fields.
Its `historical_plan` field holds the complete return value from
`plan_historical_views(..., data_root=root, snapshot_id=snapshot_id)`; it replaces
v1's `required_view_configs` and `views`. No second copy of the View plan is
stored. The planner's returned `views` go unchanged to `materialize_views`.

Admission reopens the fixed Snapshot's identities, calendars and factor sources,
re-runs that authoritative planner, and requires equality with the complete
saved plan. This preserves each security's applicable interval, requested anchor
upper bound, actual anchor, source rows and identity exclusions. A common anchor
is not imposed on historical shards. Changed anchor evidence, omitted shards,
wrong cutoffs and target substitution fail. The historical planner currently
requires its declared SW2021 industry policy; an old differently classified
fixture does not establish a historical v2 end-to-end result.

## Missing values and qualification

Design 02 §9 requires correct source, scope, PIT and missing classification; it
does not require a numeric value in every cell. The same distinction applies to
the 56 dependency leaves and 469 dependency mappings:

- `AVAILABLE`: the value is present with a known PIT qualification.
- `SOURCE_MISSING`: contract-defined `vendor_null`/`not_provided` (or the
  financial public `source_value_missing`) with actual source and revision refs.
  The original fact, null and `quality_state` are preserved. A financial fact
  whose own quality is `BLOCKED` remains `BLOCKED`; Data validation does not
  rewrite it. The report emits a warning and marks dependency values missing.
- `NOT_VISIBLE`: the family's coverage checks passed, but the PIT projection
  reports no visible observation/component at that cutoff.
- `NOT_APPLICABLE`: an existing source contract explicitly classifies a missing
  value, such as no price limit or suspension, and supplies its source ref.
- `BLOCKED`/`UNKNOWN`: unclassified missing values, gaps, ambiguities, unknown
  qualification or incomplete target coverage. These cannot validate.

Report `status` measures the checks; `value_availability`, `states`, original
quality counts and missing examples describe the actual facts. A Feature's
mapping can validate while its input values remain unavailable. Consumers that
require non-null values must inspect those states; this API does not waive a
consumer's non-null requirement or claim Feature computation succeeded.

## Daily evidence

`execute_daily_case(root, *, case, run_id, snapshot_id, source_requests,
domain_inputs, client=None, observed_raw_batch_ids=None)` calls the existing
`daily` operation. It retains the real partial checkpoint bytes before resuming
and writes `daily-case.json`; it never edits old Raw or invents interruption
history. Retry the same case/run/input plan after a supplier failure.

`validate_daily_evidence(root, evidence, *, expected_baseline)` accepts the
registered baseline and four distinct `{run_id, content_digest}` refs:
`t_plus_1`, `no_change`, `late_data`, `interrupted_resume`. It checks collection,
frozen plan, candidate build record, immutable before/after Snapshot refs, Raw
consumption, actual new historical facts and partial-work reuse. Old runs
without captured interruption evidence cannot prove interrupted resume.
Successful daily candidates still await their own Views and full admission;
this validator does not declare those candidates consumable.

## Notebook and final terminal evidence

`execute_notebook_smoke(root, *, snapshot_id, views, target, output_path)` runs
a fixed two-cell notebook using public read APIs and saves its actual outputs.
It imports `nbformat` and `nbclient` lazily, with a clear error when unavailable;
there are no mandatory new dependencies. A Python Jupyter kernel must be
available. Output must be a new `.ipynb` path under the supplied root. The
notebook makes no collection/build calls and denies network and external data
reads while loading its exact refs.

`validate_notebook_smoke(root, report, *, expected_views)` checks the packaged
cell sources, execution counters, parameters, output digest and actual read
results. Counting populated cells alone is insufficient. It reopens every fixed
View and rejects omitted family/scope, wrong root, changed refs and missing or
altered outputs.

Assemble these records with the existing baseline, View and offline recovery
records and call `gate_a.validate_terminal_evidence`. Its successful result is
`EVIDENCE_VALIDATED`, `gate_b_status=REVIEW_REQUIRED`,
`ready_for_consumption=False`. The separate reviewer and promotion decisions
remain outstanding.

## Evidence limits

The regression suite uses the immutable 2025-06-10–13 real fixture for
688981.SH. Its real financial observation-window test copies nine explicitly
identified actual Raw records from the fixed bootstrap collection into a
throwaway root and rebuilds through public `repair`; original bytes and
observation times are unchanged. The historical null holder count remains null.
The old period-only financial fixture is a negative coverage case.

A separate synthetic empty observation-window case exercises the same public
collection/build path; its qualification is explicitly synthetic. Daily change
and interruption tests simulate supplier responses. None of these tests is a
production full-universe/full-history run, daily live-supplier acceptance,
`DATA_ACCEPTED` or `PROMOTED`.

The two-security historical v2 regression exercises the real planner with an
explicitly synthetic Reader/source boundary: one security ends before the target
end and uses an earlier observed factor; the other starts inside the target and
uses a later anchor. Positive geometry admission and anchor/evidence/omission/
target negatives pass. This is **not** a v2 complete artifact end-to-end test.
Real multi-security v2 full admission remains a separate representative workload
validation with fixed SW2021 inputs.
