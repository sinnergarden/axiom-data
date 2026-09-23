# Holder-count source admission

For `tushare_pr7_holder.v3` / `stk_holdernumber`, `end_date` is required
for the canonical holder-count logical event identity. A null `end_date`
is never inferred from `ann_date`, surrounding records, or a quarter end.
The complete supplier response remains in RawBatch, including non-null
`holder_num` values on rows with a null report date.

Such rows are `unmaterializable` with reason `MISSING_REPORT_DATE` and
affected field `end_date`. Raw summary `canonical_admission` records counts,
row indexes and fingerprints; `event_source.canonicalization_report(raw)`
returns that admission result with its immutable `raw_ref`. They produce no
canonical holder-count event. This is source-to-canonical admission, not PIT
qualification. Existing null-date/null-count exclusion remains compatible.
Invalid non-null dates and conflicting canonical revisions remain errors.

Supplier request completeness is evaluated separately: a complete response
may contain unusable rows. Downstream requirement coverage must assess the
reported gap; collection completion alone does not admit a requirement or
declare the entire endpoint history incomplete. Existing RawBatches and
checkpoints are preserved on resume.

This admission clarification supersedes the older v3 profile prose that
rejected missing report dates with non-null counts. Frozen profile bytes and
digests are retained for existing Raw compatibility. Existing valid and
null-date/null-count responses keep their original summary shape; their
admission report is computed from immutable Raw when requested. New summary
metadata is added only for the newly admitted response shape.
