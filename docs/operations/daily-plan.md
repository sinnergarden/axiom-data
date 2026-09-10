# Daily source-plan inspection

`axiom_data.plan_daily(data_root, snapshot_id, source_requests=[...])` and
`axiom-data --data-root ROOT plan-daily --snapshot ID --plan REQUESTS.json`
inspect explicit per-source requests against one validated parent Snapshot.
The CLI prints JSON. Neither entry point collects data, creates run records,
nor changes the data root. `current` is resolved once and the returned concrete
parent ID binds the plan.

Each request uses the same schema as `collect_requests`: collector, domain,
endpoint, params, economic_scope and availability_policy. For example, a market
request can target T while margin targets an earlier economic session. Revision
requests retain their explicit observation/report windows. The existing
`plan_bootstrap_sources` service supplies source request recipes; a scheduler
must choose its economic dates and revision windows explicitly.

The output includes profile bindings, parent commits, source-change candidates,
transitive dependency-review candidates, economic scopes, and T+1 markers.
Every requested source is required. Availability is
`UNCONFIRMED_UNTIL_COLLECTION`; an expected publication policy is not evidence
that the supplier already has a response. Duplicate requests, unexpected
credentials/parameters and domain/policy mismatches fail before execution.

This dry-run is a planning stage. A ready daily result still requires collection,
changed-domain builds, required Views, admission and explicit publication.
