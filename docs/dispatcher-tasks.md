# Dispatcher task list

Single list for the core repo and Route A; the Cursor agent and Muse are retired. `scripts/status_page.py` reads this table. State is done, running, queued or blocked. An empty cell means unknown.

| Task | State | Blocker | Started | Finished | Result |
|---|---|---|---|---|---|
| Fork batch: 7 mainnet-fork tasks, Internet, both arms | done | | 2026-11-01 17:30 | 2026-11-02 01:30 | 0 of 7 in both arms; totals 6 of 37 in both |
| Judged batch: 6 judged tasks plus witness-string rerun, Internet, both arms | done | | 2026-11-01 13:28 | 2026-11-01 17:30 | 0 of 7 in both arms; totals 6 of 30 in both |
| Ethereus test, before the spill fix, three arms | done | | 2026-11-02 01:30 | 2026-11-02 01:44 | final answer exact, of 15: base 4, circuit 2, ceiling 5 |
| Rerun three-call `serve.py` test on the eval VM, then Fraser can destroy 58623317 | done | | 2026-11-02 | 2026-11-02 | RESULT PASS, 3 of 3, 0 wrong on replay |
| Environment pin check: gates log torch, transformers, fla, triton and driver; Results row goes amber on a difference from the pinned venv | done | | 2026-11-01 | 2026-11-01 | |
| Canary: one command runs the three-call conversation with replay after any firmware, snapshot, driver or venv change | done | | 2026-11-01 | 2026-11-02 | PASS on ELF 8f07bfe3 |
| Artefact provenance in CI: rebuild every committed binary and compare loaded sections | done | | 2026-11-01 | 2026-11-01 | loaded bytes same, locally and on GitHub's runner; 130 author's files hashed |
| Oracle independence test, by hand: one flipped keccak round-constant bit must fail gate 1 | done | | 2026-11-01 | 2026-11-01 | caught: keccak 40/40, create 37/40, selector 15/40 |
| Malformed-input fuzz per command in gate 1, must give `error (...)` | done | | 2026-11-01 | 2026-11-01 | no crash; 6 lenient forms go with the spill fix |
| `eth_in` firmware change, its gate 4, the spec's spill test | done | | 2026-11-01 | 2026-11-02 | ELF 8f07bfe3: gate 4 6 of 6, both spec cases spilled and exact |
| Ethereus test, after the spill fix | done | | 2026-11-02 | 2026-11-02 | final exact, of 15: circuit 3, base 4, ceiling 5; tool right: circuit 2 |
| Regenerate `run` and calldata fine-tune families with real addresses | done | On branch `spill-eth-in`; regenerate if the firmware changes again | 2026-11-01 | 2026-11-01 | 763 spilled rows, all match an independent check; 56 create2 rows |
| GPU test of `serve.py` store support | done | | 2026-11-02 | 2026-11-02 | RESULT PASS, including an exact `get`, 0 wrong on replay |
| +Skills arm on `q9-store-rv` | blocked | Stopped for training, 8 of 74 trials; resume after | 2026-11-02 | | |
| 4B evals on a rented L4, up to $0.50/h | queued | After the 9B evals | | | |
| `docs/ethevals-submission.md` final draft | queued | Needs both backbones' numbers | | | |
| Fine-tune data generator, `scripts/gen_ft_data.py` | done | | 2026-11-01 | 2026-11-02 | sample approved by Fraser; 60 `run_limit` rows added after |
| Fine-tune training and evaluation, `docs/finetune-spec-v1.md` | running | Short probe first, then the full run | 2026-11-02 | | |
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
