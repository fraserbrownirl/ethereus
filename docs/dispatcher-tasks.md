# Dispatcher task list

Single list for the core repo and Route A; the Cursor agent and Muse are retired. `scripts/status_page.py` reads this table. State is done, running, queued or blocked. An empty cell means unknown.

| Task | State | Blocker | Started | Finished | Result |
|---|---|---|---|---|---|
| Fork batch: 7 mainnet-fork tasks, Internet, both arms | queued | Waits for the judged batch | | | |
| Judged batch: 6 judged tasks plus witness-string rerun, Internet, both arms | running | | 2026-11-01 13:28 | | |
| Ethereus test, before the spill fix, three arms | queued | Needs the GPU after the eval batches | | | |
| `eth_in` firmware change, its gate 4, the spec's spill test | queued | ELF frozen until both eval configurations finish | | | |
| Ethereus test, after the spill fix | queued | Needs the spill fix | | | |
| GPU test of `serve.py` store support | queued | Needs the GPU after the eval batches | | | |
| +Skills arm on `q9-store-rv` | queued | Needs the GPU after the eval batches | | | |
| 4B evals on a rented L4, up to $0.50/h | queued | After the 9B evals | | | |
| `docs/ethevals-submission.md` final draft | queued | Needs both backbones' numbers | | | |
| Status console, `scripts/status_page.py` | done | | 2026-11-01 | 2026-11-01 | |
| Clean `.env`, `push_secrets.py` | done | | 2026-11-01 | 2026-11-01 | `eba5d52` |
| No-key Internet run, circuit arm | done | | 2026-11-01 | 2026-11-01 | 6/24 tasks, 12/48 trials |
| No-key Internet run, base arm | done | | 2026-11-01 | 2026-11-01 | 6/23 tasks scored, 12/46 trials |
| Store merge, gates, verify, smoke | done | | 2026-11-01 | 2026-11-01 | 1.07x, 18/18, 6/6, verify ok, smoke 4/20 vs 3/20 |
| Migrate to eval VM 58642243, gates on pinned venv, stop old boxes | done | | 2026-11-01 | 2026-11-01 | 1.0001x, 18/18, 6/6 |
| `serve.py` + `spill.py`, three-call test | done | | 2026-11-01 | 2026-11-01 | RESULT PASS |
| Snapshot against `b320d31b` | done | | 2026-11-01 | 2026-11-01 | verify 136/136 |
| rv-store port on `main` | done | | 2026-11-01 | 2026-11-01 | store and sealed tests pass |
| ELF `b320d31b` with `file_buf` | done | | 2026-11-01 | 2026-11-01 | `c0853b4` |
