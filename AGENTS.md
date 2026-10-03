# Working on Axiom Data

The target design is `../axiom-docs/docs/design/02_axiom_data.md`. The executed real tutorials in `../axiom-docs/notebooks/` explain it.
Run `../axiom-docs/examples/real_tutorials.py` to rebuild both via Jupyter; synthetic boundary
fixtures remain separate and are not production evidence.

The local public entry is `axiom_data.Data`. New storage is Raw observations,
typed Parquet and inline-domain Snapshot JSON. Read-only queries never require a
materialized View, full admission or frozen subprocess. Keep only current code
and documentation in this repository. Do not overwrite or automatically migrate old data.

Keep source units, stable security identities, first observations, revision order,
knowledge cutoffs, missing states and immutable old snapshots explicit. Narrow
checks to the requested dependency scope. Only declared source calls contact the
network. Never use a Label outcome as a decision input.

Public methods and helpers document inputs, time semantics, side effects, failure
behavior and cache identity. Cross-repo adapters belong to the consumer; Data
does not import Engine or Research. No new service/registry framework is needed.

Use Python >=3.11 and install the package dependencies. Small local checks:

```sh
PYTHONPATH=src:tests python3 -m unittest discover -s tests -p 'test_local_*.py' -v
PYTHONPATH=src:tests:../axiom-engine/src:../axiom-research/src:../axiom-ui/src python3 -m unittest discover -s tests -p 'test_completion_*.py' -v
```

Do not add compatibility shims that reintroduce publication gates into current
queries. Use saved source bundles in a separate environment for old roots. Current tests use temporary
synthetic roots; full-history supplier collection remains a separate operation.


Delivery CLI: `prepare → plan → run → verify → audit`; configs live in `examples/`.
Default bulk is whole-market daily fetch with frozen canonical selection, bounded
workers and monthly output. Reuse the same plan after interruption; never rewrite
Raw receipt times or turn a capped response into complete coverage. See
`docs/delivery-validation.json` for measured one-year evidence, not twelve-year proof.

The current complete-delivery scope is `../axiom-docs/docs/completion-checklist.md`: financial
sources, PIT semantics, vendor lifecycle boundaries, historical membership,
Reader and Qlib consumer protocols and both executed tutorials are required. Market-only results
must not be described as final delivery. No new registry or admission framework.

Production sources are Tushare only, as explicitly decided by the user on
2026-10-03. Accept supplier content. External documents and comparisons are
optional warnings: never overwrite vendor facts or gate prepare/run/current on
an official archive, delisting table, PDF or exact historical change chain.
Preserve observed snapshot dates and actual receipts; do not claim daily member
precision that the supplier response does not provide. Local request, mapping,
units, types and file-integrity errors still require correction.

Qlib is required in the current delivery: explicit immutable native daily export,
actual Research consumer reads, Reader equivalence and relocation, and executed
teaching in both notebooks. Ordinary ingestion/Reader use does not require Qlib.
Do not implicitly normalize prices, fill financial events or compute strategy
features. Actual Qlib acceptance uses the optional requirements-qlib.lock runtime;
the base-only test environment can skip those optional-runtime integration tests.
