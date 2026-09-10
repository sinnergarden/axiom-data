# Required View stage

`axiom_data.materialize_views(root, run_id=..., snapshot_id=..., views=...)`
materializes an explicit nonempty plan through the existing public View builders.
Each label has `kind` and `config`. Kinds are `adjusted_price`, `market_replay`,
`market_qlib`, `pr6_fact`, and `pr7_fact`; config uses that builder's arguments.
Snapshot identity must be concrete. Cross-View references, such as an adjusted
price View supplied to Qlib, must also be explicit artifact IDs.

```sh
PYTHONPATH=src python3 -m axiom_data.cli --data-root /home/liuming/workspace/axiom/data \
  materialize-views --snapshot snapshot-EXPLICIT_ID --run-id REQUIRED_VIEW_RUN \
  --plan required-views.json
```

The plan binds installed Python/JSON implementation content. Resume replays the
same builders, validates the published results and checks previously recorded
IDs/digests. A changed implementation or plan requires a new run. This preserves
identity validation but does not yet avoid recomputation of completed Views.

A failed required View leaves prior immutable outputs intact and returns FAILED.
VIEWS_BUILT means this stage passed and full admission remains pending;
ready_for_consumption stays false. It does not move a pointer or accept a baseline.
Reports include individual View build times. Full-root cost must be measured
separately from fixture validation.
