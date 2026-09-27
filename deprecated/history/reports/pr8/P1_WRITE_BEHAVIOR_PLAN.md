# PR8 writable contracts and sparse behavior

Base: 8ef2ce12de47626a57a74f7dc2c9ace7a2cc69e5.
The previous READY report is superseded by the user's two P1 findings.
NO_BULK_BUILD remains in force throughout this task.

1. Complete: freeze explicitly accepted current domain contracts and classify
   superseded versions as read-only; agree the public behavioral fixture paths.
2. Complete: implement shared public writable admission and sparse behavioral
   pack. Focused tests passed 12/12; synthetic fixture migration and legacy-read
   compatibility fixes have been implemented.
3. Complete: independent r2 code review approved; affected tests 134/134 and
   full tests 366/366 PASS, including behavioral fault injection.
4. In progress: validate commit-bound Gate A report and all delivered evidence,
   then hand the bounded change to the independent Reviewer. No bulk execution.

Definition of Done: historical artifacts remain readable; all formal new domain
publication uses current writable contracts and complete v2 source admission;
explicit legacy downgrade and missing-policy bypass fail; contract upgrades do
not attach across lineages. The behavioral pack must show exact child closure
PASS and every missing/gap/truncated/partial/selector/overlap/wrong-scope case
rejected or incomplete. Always-COMPLETE, missing child completeness and missing
gap checks must block Gate A based on output behavior. Full tests and final
code/contract/fixture/report bindings must pass before delivery. Source-empty
evidence remains best-effort evidence about a specific source request.
