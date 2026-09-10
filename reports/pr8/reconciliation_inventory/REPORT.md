# PR8 reconciliation inventory

Status: `NOT_ASSESSED`. This is a read-only evidence inventory for a future FULL
baseline reconciliation. It does not perform the baseline comparison and does not
make a Research-ready claim.

The admission boundary separates canonical source/Axiom evidence from comparison
with an immutable Qsys snapshot. A missing or unverifiable Qsys comparison limits
the reconciliation report; it does not, by itself, mean that canonical Axiom data
is missing or that canonical admission fails. Any source↔Axiom mismatch must still
be classified and retained.

## Existing market evidence

`reports/pr3/run_manifest.json` declares a Qsys reference at
`SysQ/data/raw_panels/raw_panel_csi800_20200101_20251231.parquet`, 89,478,083 bytes,
content digest
`sha256:4f9ce6d340ccfa90979f048e9a7463b42c695f79ed2ae11d31bcc2fa8d49bbe4`.
Its reported scope is 20 symbols, 2025-01-01..2025-12-31. The role is
`frozen-forensic-reference-only`.

`reports/pr5/run_manifest.json` declares the same size and digest using the
external absolute path `/home/liuming/.openclaw/workspace/SysQ/data/raw_panels/raw_panel_csi800_20200101_20251231.parquet`.
Its bounded scope is six symbols (`600000.SH`, `600036.SH`, `688981.SH`,
`603072.SH`, `000001.SZ`, `301581.SZ`), 2025-06-01..2025-09-05, benchmark
`000300.SH`.

These are report-level declarations only. No market parquet or market
`reference_manifest.json` was found in the explicitly checked Axiom forensic
roots. Other roots were not searched, and the prohibited mutable/legacy SysQ root
was not opened. Therefore the market artifact is
`qsys_reference_metadata_only / physical_frozen_artifact_unverified`; its declared
digest is not a content hash verified in this inventory.

Reusable Axiom-side outputs are `reports/pr3/direct_qlib_equivalence.json`
(20 symbols, 2025 full year, 14 fields, 4,858/4,858 keys) and
`reports/pr5/direct_qlib_equivalence.json` (six symbols, 2025-06-01..09-05,
14 fields, 414/414 keys). These establish direct↔Qlib equivalence, not Qsys
equivalence. The bounded Qsys probes are
`reports/pr3/tushare_axiom_qsys_reconciliation.json` (four symbols, 2025-06-10..13:
179 all-equal, 32 Qsys-missing, 13 Axiom/Tushare-versus-Qsys differences) and
`reports/pr5/qsys_reconciliation.json` (six symbols, same four days: 179 all-equal,
144 Qsys-missing, 13 differences). They must not be extrapolated to FULL
3601-security 2014-01-01..2026-09-08 coverage.

## Frozen Qsys evidence with local binding

PR6 reference root:
`/home/liuming/workspace/axiom/data/forensic/pr6-empty-prefix-compat-20260908-r4/source/evidence/pr6_reference/`.
Its `reference_manifest.json` is schema `pr6_frozen_reference.v1`; observed
manifest SHA256 is `c08cfb69d5da2130a9d9db8ea278d46ba71997c1edea593cfdc8af9defb66f00`.
The two relevant declared artifacts were locally found, size-checked, and their
full streaming SHA256 values matched the declared manifest values:

* `pr4/frozen_income/income.parquet`: 3,677,614 bytes; declared SHA256
  `6ef941166aaeed8658ee5dd796056d728303d23e0d28703e5b089ac26214fa42`.
  Its bound manifest declares 93,878 rows, csi1800, 2014-03-13..2026-08-21,
  required history start 2014-03-13.
* `pr4/frozen_cohort/universe/csi1800_pit_v2/membership.parquet`: 43,162 bytes;
  declared SHA256
  `567137db93fb9b2bbdb9220f6d0ed813fec233da87948a953a255b2e08b386df`.
  Its bound manifest declares 3,601 instruments, 235 snapshots, 2,081 validated
  trading dates, snapshot range 2007-01-31..2026-07-31 and registry window
  2018-01-01..2026-07-31.

`reports/pr6/DELIVERY.md` records 4/4 bounded membership matches, the 3,601
security historical union digest, bounded income comparisons, and legacy Qlib
comparisons. It also records unresolved industry integer→taxonomy label semantics.
This is financial/universe/legacy-Qlib evidence, not a full market panel.

PR7 shareholder root:
`/home/liuming/workspace/axiom/data/forensic/pr7-dm2-20260909-r1/admission-r5/source/evidence/shareholder/`.
Its `reference_manifest.json` observed SHA256 is
`eee983775881fdf079c12ee4b8be6ddb93f4cfaa31836d859131f891b554b84a`; the bound
source artifact is `0baf4e0fc24e5f95f691fb3f332badb8e8d32ed708118c0f1e019a28ce049800`.
Both parquet files exist, were size-checked, and their full streaming SHA256
values matched the declared manifest values: `holder_num.parquet` is
921,813 bytes with declared SHA256
`8bb111f2e9d979bb604c155e4a211dd7087949fa1ecabc317128621c92dc5eb3`;
`top10_holder_ratio.parquet` is 1,103,350 bytes with declared SHA256
`8690a233c1bb5daeddd54ad88a7c8bdef68be3f1a9dde25e513a517935fc45fb`.
The bound manifest declares csi1800, 3,131 symbols and 2017-01-01..2026-08-21.
`reports/pr7/reconciliation.json` records 24 exact frozen holder-count matches,
15 exact Top10 matches, 43 holder events without frozen rows, and no immutable
Qsys fact rows for margin, moneyflow, or forecast.

## Full-scope gaps and admission interpretation

No existing artifact provides a verified Qsys market comparison across 3,601
securities and 2014-01-01..2026-09-08. PR6 income begins 2014-03-13; PR6
membership ends 2026-07-31; PR7 shareholder begins 2017-01-01. The market Qsys
parquet is report metadata only. Consequently the future reconciliation panel
must separate `canonical_coverage`/`source_current` results from
`qsys_comparison_available`, `qsys_reference_unverified`, and
`unavailable_historical_evidence`. Full baseline comparison remains
`NOT_ASSESSED`.
