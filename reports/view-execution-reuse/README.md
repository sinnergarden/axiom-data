# Required View preflight and immutable computation reuse

Worker ticket: `review/data-prebulk-20260926/tickets/view-execution-reuse.md`,
including the confirmed-suspension OHLC classification correction.
Base: reviewed PR31 merge `feb97f8d7bae7b166db3f31d20fa6e7722c9dbb8`.

The public materialization operation now assesses all five families' deterministic
prerequisites before publishing its first View. Request/identity/calendar/PIT,
anchor and source coverage checks call the builders' existing preparation owners;
financial admission keeps its operation-local shared preparation. Reports distinguish
READY prerequisites, BLOCKED items, and items NOT_ASSESSED after Snapshot/anchor
failure. A READY prerequisite result is not full data acceptance. Shared preparation
errors retain message, stage and affected labels.

A label may include an optional `reuse_candidate` alongside `kind` and `config`:

```python
views['price']['reuse_candidate'] = {
    'kind': 'adjusted_price',
    'view_id': prior_ref.view_id,
    'manifest_digest': prior_ref.manifest_digest,
}
```

The candidate is frozen with the operation plan. Its existing loader validates the
artifact and request; relevant DomainCommit refs from its checked Snapshot must
match the target, including parent/raw provenance. Executed code, contracts,
configuration, scope and PIT remain bound. A changed or corrupt candidate uses the
ordinary builder. The operation shares one target Reader and a bounded one-entry
candidate Reader slot; no candidate index or persistent cache is introduced.

Dependency sets are explicit: adjusted uses market/factors; replay uses market,
status, limits and actions; Qlib uses market; financial uses the four fundamental
domains; event uses the five event domains. All include identity/calendar. Referenced
DomainCommits carry their own dependency identities. Financial manifests still
bind all target Snapshot domain refs; membership/industry refs are rebound.
Event compressed states retain values/source metadata while rebinding Snapshot
and holder derived identity through the same metadata binding function as fresh
construction. Reuse therefore produces the same new View identity as fresh build,
while keeping the old View unchanged. Adjusted Qlib candidates conservatively use
fresh construction with the caller's validated explicit target-Snapshot Derived
reference; no recursive reference substitution occurs.

Confirmed full-day suspension now carries status source/qualification into null
canonical OHLC. A valid adjusted anchor plus a suspended no-price session can
interpret missing per-session factor as not applicable to OHLC, using the weaker
qualification. Original View `missing_factor` and values remain intact. Separately
requested factor, limits, turnover and capital leaves retain their requirements.
Unknown/unqualified status is not promoted; existing Snapshot contracts reject
conflicting price/status facts. No source profile, identity date, or canonical
representation changes.

## Validation

`targeted-tests.log` records 86 PASS in 40.756 seconds on the final affected-suite run (Python 3.12). Coverage includes all five
later-item blockers; shared error attribution; unchanged dependencies across an
unrelated benchmark parent-link correction; same-valued relevant factor provenance
change; config/scope/PIT/code invalidation; corrupted candidate fallback; interrupted
resume; existing publication target; and the actual public frozen subprocess entry.
The suspension fact-construction matrix tests price/factor/source/qualification
boundaries, with existing full-admission and market/suspension contract regressions.

Fixtures are copies of the existing real PR7 input. Parent-link-only corrections
are explicitly synthetic, preserve canonical bytes, and pass the public Snapshot
validator. Test roots are temporary. No formal collection, repair, bulk or promotion
was performed.

`benchmark.py` / `benchmark.json` record four-session, one/two-security five-family
comparisons. Five Views: reuse 0.331s / zero projection calls, fresh 0.383s / five.
Ten Views: reuse 0.546s / zero projections, fresh 0.645s / ten. Logical session-read
counts are recorded per domain. The five-family test also asserts exactly two
Reader constructions, one target and one shared candidate Snapshot. Preflight and
fresh construction can each read a bounded batch; expensive financial preparation
is shared, and a hit skips build-batch preparation. No second View projection runs
as preflight.

These small measurements are not a production census, full-history performance
claim, physical I/O measurement or peak-memory estimate. The known production
validation cost (about 30 minutes / 18–19 GiB sampled previously) remains relevant;
representative final integrated measurement is a separate authorized stage.

Compatibility: existing plans without candidates and historical loaders remain
supported. Same-Snapshot resume validates artifacts as before. New View binding
uses existing formats and immutable publication; no old artifact is rewritten.
Status: IMPLEMENTED / TESTED, pending independent remote review. No DATA_ACCEPTED
or PROMOTED claim.
