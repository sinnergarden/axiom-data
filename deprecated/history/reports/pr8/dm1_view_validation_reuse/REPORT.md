# D-M1 View validation reuse

Adjusted-price and MarketReplay builders reuse their same-call checked Reader
when validating their written View. Each new public load validates a fresh full
Snapshot closure once; copied-source corruption is still rejected. Adjusted
validation also uses the existing bounded partition reader for its scoped facts.

On the frozen PR7 closure, adjusted build canonical-load calls fall from 43 to
18, and replay from 54 to 18. Public loads now use 18 calls each (previously 25
and 36). Both View IDs remain identical. Full suite: 221/221 PASS, 64.011s.
These call-count/component measurements do not establish full-root daily costs.
