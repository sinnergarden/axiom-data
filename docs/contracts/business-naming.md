# Business names and historical reads

Current Python entrypoints describe their responsibility: reference sources, fundamentals sources, event sources, financial Views and event Views. Historical Python aliases, original resources and published View readers live in `axiom_data.deprecated`. Historical run records are reachable through the [evidence index](../../deprecated/history/index.md).

New writes use `financial_fact` and `event_fact` storage kinds, `financial-fact-` and `event-fact-` ID prefixes, and the corresponding `financial_fact_view` and `event_fact_view` artifact types. The public View builders continue to use one calculation path. Existing immutable IDs are loaded through the same business entrypoints and retain their original identity and provenance.

The current writable contracts are `security_master.v2`, `market_daily.v2` and `corporate_actions.v3` for the three domains whose packaged contract text/source names changed. The other domains keep their writable contract versions. The new contracts preserve field, unit, key, missing-value and time semantics. Contract digests change because their declared versions and descriptions change. A build using one of these contracts against a parent with an older contract requires explicit `new_lineage`; it must not attach the new contract to an old parent chain. Published artifacts are not migrated in place.

Current source profiles are:

- `tushare_market.v1` and `tushare_reference.v1`;
- `tushare_fundamentals.v1` and `tushare_fundamentals.v2`;
- `tushare_events.v1`, `tushare_holder_reports.v2` and `tushare_holder_reports.v3`;
- `tushare_fina_indicator.v2`.

`source_completeness.v2` binds these profiles for current collection. The data dependency registry is `data_dependency_scope.v1`; the readiness contract is `gate_a_contract.v5`. Profile revisions preserve the existing endpoint mappings and source authority. New source/profile identifiers produce new digests and artifact identities; these are not claimed to equal historical identities.

Reading an older Raw observation checks its original profile bytes, digest, request fields and source binding. A new artifact may retain an immutable reference to such an observation. Frozen runs retain their original executable package and invocation; compatibility resolution does not rewrite stored manifests, code packages or checkpoints.

A collection run started with the current API records business profile names. Resuming an existing historical collection run keeps its original requests, request keys and plan digest, and selects its archived profile through the compatibility mapping. The same collector continues pending work under that original protocol, including the original requests and keys of already frozen split-child graphs. Previously successful Raw observations keep their original timestamps; a pending request first succeeding on resume records the new observation time. This exception continues an existing run and does not migrate it.
