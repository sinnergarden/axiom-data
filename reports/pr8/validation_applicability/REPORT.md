# Validation applicability

The exact 214-test regression run passed in 64.388 seconds on code 3ede5e1.
report.json binds the ruleset, implementation bytes, contract/profile versions,
semantic scope and individual checks. It has no validated-until date.

Normal new-day data does not invalidate unchanged version-level golden behavior.
New data still needs request/schema/PIT/conflict/coverage validation and affected
Derived/Qlib checks. Semantic version changes, a new supplier counterexample or
expanded semantic scope require reassessment.

This report does not certify full bootstrap coverage. Actual data availability,
full-scope admission, daily performance and full-root recovery remain separate
terminal gates. See docs/operations/version-model.md for the version model.
