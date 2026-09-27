# Repeated request coverage reads

On the frozen PR7 Snapshot in evidence.json, 100 identical margin scope checks
previously loaded 300 RawBatch envelopes/payloads (0.196s). The Reader now indexes
its verified request intervals once: three Raw loads (0.0026s). Timings exclude
Snapshot startup and measure this bounded component only.

The index belongs to one already validated Reader and is populated only after
all relevant Raw reads succeed. Scope checks still reject uncovered dates and
unknown securities. A new Reader validates the complete Snapshot/Raw closure;
a regression corrupting a copied Raw payload confirms rejection. There is no
persistent correctness cache. Holder period/revision, PIT and exchange selection
are unchanged.

Full-root daily, View build and offline recovery costs remain separate gates.
