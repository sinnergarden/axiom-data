# Real calendar append

The first build rejected a missing predecessor at the parent/incoming boundary.
The corrected builder seeds each exchange from the parent before the requested
range. With the identical Raw IDs and parent, r2 validates 9,272 rows: four new
rows, zero changed existing rows, 152 reused partitions and one touched partition
in 0.582 seconds. Raw payloads remain supplier observations. The original
bootstrap Snapshot inputs continue to reference their original calendar.

Append, overlap and conflicting historical open-day fixtures pass. The complete
217-test suite passed in 69.125 seconds at cbc6a62. This is a calendar component
check; full-root daily acceptance remains pending.
