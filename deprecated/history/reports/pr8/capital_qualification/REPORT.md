# Security capital source conflict

Full 3,601-request scan identified 15 contradictory rows in 603882.SH: positive
float_share 45,948.7577 exceeds total_share 45,788.4577 (ten-thousand shares).
The exact supplier observations are frozen in tests/fixtures/capital_conflict.json.
No evidence identifies which value should be corrected.

Explicit capital_conflict.v1 mapping binds its profile digest to a new
security_capital.v2 root. Both canonical counts are null with
source_capital_conflict, original numeric values and Raw provenance. Other
invalid counts still fail. Published v1 schema and strict validation remain
unchanged. Formal v2 loading replays its Raw mapping; forged or incomplete
qualification fails. This is unavailable data, not repaired economic values.

202/202 tests PASS (70.563s). Actual 15-row fixture, unchanged Raw, repeat identity,
v1/v2 dispatch, missing mapping and malformed proof are tested. Full qualified
scan and canonical build remain pending. V1 stage 3/5 remains incomplete.
