# Corporate action observations v2

Full dividend acquisition contains 31 implementation declarations without
ex_date. Thirty carry supported positive economic terms, and one supplies no
supported economic term. Examples and immutable Raw IDs are in source_rows.json.

corporate_actions.v2 is a new lineage root. It adds observation_state and allows
null effective_date: dated_action, undated_action, or unresolved_terms. The latter
has action_type unresolved and no invented economic quantity. Announcement,
record, payment and share-available dates never substitute for ex/effective date.
Nonzero unsupported stock terms remain a build error. Old v1 content and strict
effective-date validation remain unchanged.

The explicit corporate_action_observations.v1 mapping/profile is bound to builder
configuration and content digest. Formal v2 loading replays Raw mapping and
validates the declared schema; v1 cannot accept v2 rows. Undated observations use
the existing undated partition. Read-only facts without date filters expose all
observations. Date-bounded queries fail closed for requested securities carrying
unresolved observations, so replay/Fact consumers cannot silently treat them as
no action. Source observation dates outside the identity interval are not asserted
as operative action dates; dated actions retain the existing lifecycle checks.

Full 3,601-request mapping/row scan: 35,068 dated actions, 30 undated actions,
one unresolved-terms observation; no failures, 8.175s. 201/201 tests PASS, zero
skips, 63.474s; targeted new-root/Raw/closure/projection regressions pass.
Canonical retry, full admission and V1 terminal gates remain outstanding.
