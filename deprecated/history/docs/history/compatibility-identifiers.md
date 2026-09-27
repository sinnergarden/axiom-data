# Historical identifiers

Current plans use `financial_fact` and `event_fact`; published View kinds and
IDs remain `pr6_fact` / `pr6-fact-*` and `pr7_fact` / `pr7-fact-*`. The public
View operation and readers resolve these names before applying the frozen
artifact schema and identity rules.

Current source plans name `reference`, `fundamentals`, `financial_indicator`,
`events` and `holder_reports_v3`. Collection checkpoints retain the corresponding
`dm1`, `pr6`, `pr6_indicator`, `pr7` and `pr7_holder_v3` names. Requests are
normalized before request identity and checkpoint comparison, so either spelling
resumes the same request without another supplier call.

Packaged source profile versions, the frozen requirement registry, schema names
and old `*_v1` loaders retain their published bytes and names. The small
`dm1_*`, `pr6_*` and `pr7_*` Python modules are import shims for historical
callers; current implementation lives in the reference, financial and event
modules. The earlier Gate A contract remains packaged for historical evidence;
old work logs remain under their original release evidence.
