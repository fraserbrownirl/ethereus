# Dispatcher task list

Single list for the core repo and Route A; the Cursor agent and Muse are retired. `scripts/status_page.py` reads this table. State is done, running, queued or blocked. An empty cell means unknown.

| Task | State | Blocker | Started | Finished | Result |
|---|---|---|---|---|---|
| Fork batch: 7 mainnet-fork tasks, Internet, both arms | running | | 2026-11-01 17:30 | | |
| Judged batch: 6 judged tasks plus witness-string rerun, Internet, both arms | done | | 2026-11-01 13:28 | 2026-11-01 17:30 | 0 of 7 in both arms; totals 6 of 30 in both |
| Ethereus test, before the spill fix, three arms | queued | Needs the GPU after the eval batches | | | |
| Rerun three-call `serve.py` test on the eval VM, then Fraser can destroy 58623317 | queued | Runs automatically after the Ethereus test's before run | | | |
| Environment pin check: gates log torch, transformers, fla, triton and driver; Results row goes amber on a difference from the pinned venv | done | | 2026-11-01 | 2026-11-01 | |
| Canary: one command runs the three-call conversation with replay after any firmware, snapshot, driver or venv change | queued | Script ready; first run needs the GPU free | 2026-11-01 | | |
| Artefact provenance in CI: rebuild every committed binary and compare loaded sections | done | | 2026-11-01 | 2026-11-01 | loaded bytes same, locally and on GitHub's runner; 130 author's files hashed |
| Oracle independence test, by hand: one flipped keccak round-constant bit must fail gate 1 | done | | 2026-11-01 | 2026-11-01 | caught: keccak 40/40, create 37/40, selector 15/40 |
| Malformed-input fuzz per command in gate 1, must give `error (...)` | done | | 2026-11-01 | 2026-11-01 | no crash; 6 lenient forms go with the spill fix |
| `eth_in` firmware change, its gate 4, the spec's spill test | running | Source on branch `spill-eth-in`, not merged; gate 1 there running. Merge, gate 4 and spill test wait for the 37 tasks and the before test (Fraser) | 2026-11-01 | | |
| Ethereus test, after the spill fix | queued | Needs the spill fix | | | |
| Regenerate `run` and calldata fine-tune families with real addresses | done | On branch `spill-eth-in`; regenerate if the firmware changes again | 2026-11-01 | 2026-11-01 | 763 spilled rows, all match an independent check; 56 create2 rows |
| GPU test of `serve.py` store support | queued | Needs the GPU after the eval batches | | | |
| +Skills arm on `q9-store-rv` | queued | Needs the GPU after the eval batches | | | |
| 4B evals on a rented L4, up to $0.50/h | queued | After the 9B evals | | | |
| `docs/ethevals-submission.md` final draft | queued | Needs both backbones' numbers | | | |
| Fine-tune data generator, `scripts/gen_ft_data.py` | running | | 2026-11-01 | | |
| Fine-tune training and evaluation, `docs/finetune-spec-v1.md` | queued | Trainer written, no GPU run. After the eval batches, the Ethereus test and Fraser's sample review | | | |
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
