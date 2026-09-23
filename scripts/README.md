# Script ownership

| Category | Entry | Use |
| --- | --- | --- |
| Current operation | `axiom-data` CLI (`src/axiom_data/cli.py`) | Bootstrap, daily, View materialization, admission and recovery through public operations. |
| Maintenance | `bootstrap_references.py` | Explicit reference-source bootstrap on an isolated or authorized root. |
| Historical reproduction | `run_pr3_market_slice.py`, `build_pr5_d01_evidence.py`, `run_pr5_dm1_slice.py`, `run_pr6_pit_financial.py` | Reproduce earlier slice evidence. |
| Historical reproduction | `collect_pr7.py`, `run_pr7_dm2.py`, `resolve_pr7_scope.py`, `validate_pr7_dm2.py`, `check_pr7_evidence.py`, `pr8_gate_a_evidence.py` | Reproduce earlier source, scope and gate evidence. |

Historical scripts retain exact names and source references so their recorded
evidence can be checked. They are not alternate production entrypoints. For a
new run, use the public operation and an explicit immutable Snapshot/run ID.
