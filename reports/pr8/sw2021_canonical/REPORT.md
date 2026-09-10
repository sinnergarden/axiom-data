# SW2021 canonical industry stage

The authorized source correction passed all four automatic checks over 511
taxonomy nodes and 7,908 global membership rows. Raw retains 850401.SI;
canonical classification uses 850412.SI with the exact versioned correction,
source Raw references and mapping-profile digest. No general name alias exists.

`industry_membership.v3` represents each security's complete observed history,
then selects its revision before interval projection. A zero-span history is a
valid source-availability observation. Known coverage gaps remain explicit;
source collection incompleteness and contradictory taxonomy fail the build.
The existing v1/v2 contracts and View loaders retain their version dispatch.

Full target projection: 3,601 securities, 2014-01-01 through the explicitly
frozen available endpoint 2026-09-08. 9,035,545 classified sessions include all
32,458 restored special-steel sessions. The remaining 125,648 supplier gaps and
1,180 ambiguous boundaries match the independently qualified history. No
forward fill or CITIC substitution was used. See projection_validation.json.

The current-code canonical commit is
`industry_membership-355a5dfcbbea56edec2773bf11dd375d8dc5c8a1ed8f0e940fc698798df18e04`.
Raw-only recovery into `/tmp/axiom-sw2021-offline-20260910-r1`, with network
disabled, reproduced the security and industry commit identities and logical
digest. See offline_validation.json. The projection validation also retains the
first published intermediate identity; that artifact was not overwritten.

172/172 tests passed in 127.319 seconds, zero skips. Added coverage includes
mapping conflicts/removal, unchanged Raw, actual source golden rows, complete
history correction/return and operational prefix, real Snapshot/Fact/Qlib
metadata and float32 equivalence, and no-change commit reuse. Existing universe,
financial, PR7 exchange-calendar and v1 View tests pass.

This completes the SW industry implementation/validation stage. The full V1
baseline, all-domain admission, daily profiles, full-root recovery and final
acceptance Notebook remain required. No baseline pointer moved; no merge/push.
