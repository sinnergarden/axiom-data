# Source labels and zero limit pairs

Full price-limit scan: 3,601 requests, three offending records: 000520.SZ
2015-08-18, 600206.SH 2014-01-27, 600637.SH 2015-06-12, each up/down=0.
The supplier documentation (https://tushare.pro/document/2?doc_id=183) provides
no zero-pair no-limit discriminator. Explicit zero_limit_pair.v1 maps only
zero/zero to the existing unknown/null contract, records the rule reference,
and keeps Raw. Other invalid bounds fail. The profile digest enters builder
identity and formal qualified loading replays the source mapping.

Forecast source full scan: 69,931 rows across 3,601 requests. The original
8 documented labels remain unchanged. Actual extra labels are 增亏 (167),
减亏 (160), 不确定 (928), 其他 (47). New forecast_observations.v2 and explicit
forecast_source_types.v1 retain the 12 observed strings verbatim, with no
category merge or inferred economic direction. Unknown future labels fail.
Published v1 retains its original enum validation and identity. Raw, revision
selection and observation times are unchanged. Source documentation remains
https://tushare.pro/document/2?doc_id=45; extra values come from the actual
frozen Raw samples, not a claim that documentation lists them.

Actual fixtures include exact Raw identities. 204/204 tests PASS (68.509s),
including malformed/equal-positive limit bounds, new-label rejection, version
and mapping guards, actual retained values, immutable Raw and repeat identity.
The first suite found two synthetic lineage nodes touched by an overbroad
qualified replay condition; the condition is now limited to price_limits and
the full rerun passes. Public candidate config accepts these explicit mappings
and capital_conflict.v1 with domain guards.

Stage 3/5 remains incomplete. Full qualified price-limit scan, canonical recovery,
full admission/baseline and all remaining V1 terminal gates are still pending.
