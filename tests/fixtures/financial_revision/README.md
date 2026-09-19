# Financial source revision fixture

The enclosed RawBatch is a byte-for-byte copy of the immutable response for
`002010.SZ / 2026-06-30`, observed at `2026-09-14T06:40:58.094391+00:00`.
It is bundled with the tests; installation or supplier access is unnecessary.

- Raw ID: `pr6-480cfa7d0400011c3adfcb532a724f202e65fbe15663730d840bbe37bd6a5b9c`
- Payload SHA-256: `b268a9c5610c1b8eb01e3aa528d3de694864a26bad1bd43125e96c1611369269`
- Manifest SHA-256: `ee2d97f620093ecd0e21791c2c078f9f679761b5046decc512208edca152f4c5`
- Source rows differ only in `roe` (`2.4916` / `2.4915`) and string
  `update_flag` (`"1"` / `"0"`).

`test_financial_source_revision.FinancialSourceRevisionTest` uses the public Raw
loader, builds financial v3 in a temporary root, and validates its closure.
Expected canonical ROE is `0.024916`, with original visibility and Raw reference.

The separately named `HistoricalFinancialArtifactTest` needs the existing
immutable fixture identified in `reports/pr6/run_manifest.json`, relocated by
`tests/fixture_locations.py`. It loads only the financial DomainCommit closure,
not a Snapshot. Missing external evidence is a failure, not a skipped pass.
