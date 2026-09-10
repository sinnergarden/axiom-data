# Daily collection and candidate construction

`axiom_data.daily(root, run_id=..., snapshot_id=..., source_requests=...,
domain_inputs=..., client=None, observed_raw_batch_ids=None)` executes the public daily plan and candidate
builder. The Snapshot is explicit; `plan_daily` can resolve a human-facing
pointer beforehand. Each request retains its own economic window and source
availability policy. The collector records actual retrieval time independently.

`domain_inputs` has the same schema as repair/assemble_candidate. Its explicit
Raw IDs are frozen inputs needed in addition to new collection results. An empty
list is allowed for a domain with planned collection. Successfully collected
Raw IDs are appended in request order before calling the shared builder. The
caller must provide every required source domain and any necessary dependency
rebuild inputs. The normal fixed-dependency checks remain enforced.

CLI: `axiom-data --data-root ROOT daily --snapshot ID --run-id RUN --plan plan.json`.
The plan contains `source_requests` and `domain_inputs`. Run files preserve the
frozen source/build plan, actual Raw refs, resolved build inputs, changed/reused
domains and stage. A per-run lock rejects concurrent execution. Resume verifies
the same plan and reuses validated successful collection results. Partial source
failure retains Raw and stops before candidate construction. Caller mutation
during collection cannot change the frozen build plan.

For separately collected observations, `observed_raw_batch_ids` maps planned
request IDs (from `plan_daily`) to explicit RawBatch IDs already present in this
root. The map becomes part of the frozen run plan. Every referenced RawBatch is
loaded and checked against its exact source request before reuse; unknown keys,
wrong requests and substituted completed refs fail. Bound requests make no new
supplier call and retain the original payload and retrieval timestamp. Unbound
requests follow normal collection. This also supports no-change replay of the
same observation without inventing a new observation time.

The current successful stage is `REQUIRED_VIEWS_AND_FULL_ADMISSION`, with
`CANDIDATE_BUILT` or `NO_CHANGE`. These results always retain
`ready_for_consumption=false`; required Views and scope admission must accept
consumption/publication separately. No-change asserts the exact parent Snapshot
identity and creates no new Snapshot identity. This entry point alone is not
completion of the V1 operational acceptance gate.

Validation uses a copied real PR7 closure and explicitly simulated collection:
partial failure/resume, independent economic dates, T+1 timestamp alignment,
no-change reuse, frozen input protection and old-Snapshot stability. Full suite:
215 tests PASS (65.964s). Full-bootstrap-root daily performance and acceptance
remain outstanding.
