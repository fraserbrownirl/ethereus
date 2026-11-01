# Dispatcher log

One line per tick: time (UTC), what changed. Spend is omitted because Fraser monitors Vast himself (2026-11-01).

- 2026-11-01 07:38 Gate 1 PASS on 9B `dist/q9-rv2`: KL 6.34e-4, floor 3.77e-4, 1.68x, 12/16 greedy. Fix: keep the untied `lm_head` (`ab930ae` main, `1500fba` route-a).
- 2026-11-01 08:02 Clean checkout at `ab930ae`: store, sealed, filebuf, merge-head tests 32 passed. Main pushed.
- 2026-11-01 08:04 Gate 4 cause found: `chat.py` offered only the calc schema, 0 chip tokens. Fix `d700421` main, `1b9a213` route-a. Reported hang not reproduced; the same CLI finished in 69 s.
- 2026-11-01 08:06 Gate 4 prompt 1 clean: 6,006 chip tokens, 0 head mismatches, 0 wrong predictions on 6,028 owned positions. ELF `b320d31b` confirmed on the box.
- 2026-11-01 08:15 Addendum received and committed. Snapshot rebuild running. Next: `spill.py` + `serve.py`.
- 2026-11-01 08:30 Snapshot step done: `boot_fs` snap 6,091 owned positions exact, bytes unchanged, manifest names `b320d31b`, verify 136/136 on real kernels after fixing its empty reference (`c68e3aa`). `spill.py` and `serve.py` committed (`e30f129`, `8e2f944`), 14 model-free tests pass. GPU acceptance queued; needs a test-only merge from main because the store port changed the vocabulary.
