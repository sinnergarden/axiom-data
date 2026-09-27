# V1 full bootstrap: source stage

Public `plan_bootstrap_sources` and `collect_bootstrap_sources` now expose
explicit date/security/lookback plans, bounded per-domain collection, exclusive
run locks, exact resume binding, Raw validation and conservative truncation
gates. CLI adapters are `plan-bootstrap-sources` and `collect-bootstrap-sources`.
Completion of these APIs means source collection only, never baseline admission.

The frozen initial full plan is referenced by plan_ref.json. It covers 15 source
domains and reuses the 3 completed reference/industry domains. Financial
observation lookback begins in 2013 for 2014 TTM. Index-weight requests use
monthly bounds as described by the [supplier](https://tushare.pro/document/2?doc_id=96).
The [financial source](https://tushare.pro/document/2?doc_id=33) defines its range
on announcement dates; report-period identity remains separate.

Two real source-format cases were fixed under explicit mapping versions:

- `tushare_pr7_holder.v2` accepts the supplier's full Shanghai-local timestamp
  as well as date-only announcements. The original 000001.SZ response includes
  `2025-05-12 15:09:08` for report period 20250507, with a null holder count.
  Raw is unchanged; the canonical announcement day and precise best-effort
  vendor availability remain distinct. v1 retains its original validation.
- `session_suspension.v1` permits a strictly partial-session halt to coexist
  with a daily bar only when the same security/date has positive traded volume.
  Both market and security-status mappings use the same qualifier. Raw timing
  remains intact; the full-day suspension flag is false for the traded bar.
  Missing traded evidence, full-session timing and malformed intervals still
  fail. The first 20-security mapper probe progresses from the preserved
  eight-event counterexample to 56,908 canonical rows. It is a mapper probe,
  not a published full market commit or completeness certification.

PacedSourceClient retries only recognizable transient rate-limit errors, with
bounded attempts; source validation failures are not retried as transport errors.
Top10 recovery reuses and independently validates its first 500 Raw artifacts.
No active run's frozen code or source plan was replaced.

179/179 tests passed in 129.822 seconds, zero skips. This includes v1 artifacts,
industry correction, PIT/revision, source-format, resume and truncation probes.
Mapping versions now participate in validation applicability signatures.

Current stage: 3/5, source acquisition and canonical/storage preparation.
Live execution records under data/operations/v1-full-bootstrap-20260910-r1 are
the authority for collection progress; the copied runners explain continuation.
Full all-domain canonical construction/admission, baseline, daily/T+1/revision/
no-change evidence, full-root offline recovery and final Notebook remain due.
No baseline pointer was moved and no merge/push was performed.
