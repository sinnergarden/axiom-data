# Required View stage

`axiom_data.materialize_views(root, run_id=..., snapshot_id=..., views=...)`
materializes an explicit nonempty plan through the same implementations as the existing public View builders.
Each label has `kind` and `config`. Public kinds are `adjusted_price`, `market_replay`,
`market_qlib`, `financial_fact`, and `event_fact`; config uses that builder's arguments.
The operation resolves public names to immutable View kinds before freezing the
plan. Previously published plans remain readable under their original names;
see [historical identifiers](../history/compatibility-identifiers.md).
Snapshot identity must be concrete. Cross-View references, such as an adjusted
price View supplied to Qlib, must also be explicit artifact IDs.

```sh
PYTHONPATH=src python3 -m axiom_data.cli --data-root /var/lib/axiom-data \
  materialize-views --snapshot snapshot-EXPLICIT_ID --run-id REQUIRED_VIEW_RUN \
  --plan required-views.json
```

The plan binds installed Python/JSON implementation content. Resume validates each
completed View and skips its builder when the artifact and inputs still match.
A missing, damaged or mismatched View is rebuilt; a changed implementation or
plan requires a new run.

A failed required View leaves prior immutable outputs intact and returns FAILED.
VIEWS_BUILT means this stage passed and full admission remains pending;
ready_for_consumption stays false. It does not move a pointer or accept a baseline.
Reports include individual View build times. Full-root cost must be measured
separately from fixture validation.

One operation constructs a fully validated Reader and shares it among its View
builds and their written-artifact checks. Individual public builders still create
a fresh Reader. A new operation or public load revalidates the Snapshot closure;
there is no cross-call correctness cache.

Event View v3 and later keep their requested calendar interval while planning each source
against that DomainCommit's frozen `builder_config.end_session`. For example,
a View can end on 2026-09-11 while margin input ends on 2026-09-10. The 09-11
margin facts are null with `source_scope_not_available`; 09-10 values are not
carried forward. Moneyflow with a 09-11 source bound still supplies that day's
facts. PIT visibility continues to use the requested knowledge cutoff.

The frozen bound does not replace request coverage: a missing security or a gap
inside the required source interval still fails. Commits without an explicit
source end retain strict coverage checks. Published Event Views v1/v2 continue
to load with their original projection rules; source contracts are unchanged.

Current financial and event Views store field state intervals instead of one copy
of an unchanged fact for every trading day. The public Readers still return daily
rows: for example, a report visible on Monday remains available on Tuesday
without storing the same report again. A new revision, missing reason, membership,
or industry state starts a new interval. Financial Views reference the Snapshot's
immutable universe and industry commits; they do not contain complete member
sets. Previously published daily financial/event Views remain readable.
