# 688981.SH source cutoff regression

`event_source_cutoff_688981.zip` contains seven exact immutable RawBatch directories
for one security, taken from candidate
`snapshot-1d69dc236a358f1627ae91080c33cf993b9e2acb55bbd2c2cb7a37129ce6b5a5`.
`fixture.json` lists each original DomainCommit, contract, Raw ID and the relevant
frozen builder settings. Raw manifest, payload and digest bytes are unchanged.
The existing Raw loader validates these files during tests. No supplier access
or production data root is needed to run the new regression.

Margin request ends on 2026-09-10; moneyflow ends on 2026-09-11. Report sources
end on 2026-09-13. The test builds isolated source commits from the complete
responses, then exercises public View build/load, direct leaf queries and Qlib.
Security/calendar and the bounded Reader's Snapshot binding are test scaffolding;
this is not a full production candidate validation or performance benchmark.
