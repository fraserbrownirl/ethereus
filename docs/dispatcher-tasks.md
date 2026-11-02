# Dispatcher task list

Single list for the core repo and Route A; the Cursor agent and Muse are retired. `scripts/status_page.py` reads this table. State is done, running, queued or blocked. An empty cell means unknown. Crossing artefact: what one task hands the next (a checkpoint, an ELF hash, a dataset); `none` means nothing does, so it runs in parallel on whatever capacity exists. Read `docs/constraints.md` before adding a row.

| Task | State | Blocker | Crossing artefact | Started | Finished | Result |
|---|---|---|---|---|---|---|
| Step 1: Ethereus-9B-v0.1 to a private Hugging Face repo, LoRA and hashes in `lora/`, sha256 check (Fraser) | done |  | the merged checkpoint, `SHA256SUMS` | 2026-11-02 | 2026-11-02 | 27 of 27 files match by sha256; repo private; model card gets eval scores after Step 3 |
| Step 2: eval build, spill on, unary fix, gate 1 (Fraser) | done | | ELF 7c4175e6, main 5f3b67b | 2026-11-02 | 2026-11-02 | spill test PASS 7 cases, 0 wrong on replay; gate 1 7,652/0 |
| Step 3: ETH Evals, 37 tasks, 2 trials, OpenCode 1.18.33, board settings (Exa search, output limit, high effort), real RPC: Ethereus-9B-v0.1 Internet and Internet + Skills, Ethereus-9B (untrained), Qwen3.5-9B, Model only for all three (Fraser) | running | Smoke trials check the set-up; arms in Fraser's order, one at a time | ELF 7c4175e6, `route-a/gates/job-step3.yaml` | 2026-11-02 | | |
| Step 4: `docs/ethevals-submission.md` draft for Fraser's review, nothing posted | queued | Step 3's results | each arm's results folder | | | |
| Unary minus fix in eth firmware (Fraser, priority): port, gate slice, rebuild, gate 1, exposure list | done | | ELF 7c4175e6 (bridge), 7dcd8933 | 2026-11-02 | 2026-11-02 | gate 1 u256 7,652 cases, 0 mismatches; gate 4 6 of 6, 0 wrong on replay; canary PASS; snapshot verify OK; nothing to regenerate |
| K3 merge: compiled layers into Kimi K3 (mixture-of-experts, gated delta), its gates | running | Feasibility note written (`docs/k3-feasibility.md`): four research items (no zero decay, `situ`, attention residuals, tokenizer); stages 0 to 2 on Mac and eval VM now; stage 3 needs 8x B200 class (Fraser: spend) | firmware ELF 7c4175e6 and the circuit; nothing from the 9B checkpoints |  |  |  |
| Task generator for step 3: Solidity build tasks scored by machines, three kinds, Harbor format, 50 proven per kind (Fraser) | running | Kind 1 done: 50 proven, 8 held out. Kind 2 moved to the Cursor Muse (branch kind2, docs/kind2-handover-muse.md); I verify its claims. Kind 3 queued for it after Kind 2's first 40 proven | none; uses OpenZeppelin v5.7.0 and real findings | 2026-11-02 |  |  |
| API and open call | blocked | Wait for K3: the flywheel pays off only on a model builders prefer (Fraser) | the K3 checkpoint | | | |
| Fork batch: 7 mainnet-fork tasks, Internet, both arms | done | | none; needs the GPU only | 2026-11-01 17:30 | 2026-11-02 01:30 | 0 of 7 in both arms; totals 6 of 37 in both |
| Judged batch: 6 judged tasks plus witness-string rerun, Internet, both arms | done | | none | 2026-11-01 13:28 | 2026-11-01 17:30 | 0 of 7 in both arms; totals 6 of 30 in both |
| Ethereus test, before the spill fix, three arms | done | | ELF b320d31b | 2026-11-02 01:30 | 2026-11-02 01:44 | final answer exact, of 15: base 4, circuit 2, ceiling 5 |
| Rerun three-call `serve.py` test on the eval VM, then Fraser can destroy 58623317 | done | | ELF b320d31b, q9-rv2 | 2026-11-02 | 2026-11-02 | RESULT PASS, 3 of 3, 0 wrong on replay |
| Environment pin check: gates log torch, transformers, fla, triton and driver; Results row goes amber on a difference from the pinned venv | done | | none | 2026-11-01 | 2026-11-01 | |
| Canary: one command runs the three-call conversation with replay after any firmware, snapshot, driver or venv change | done | | ELF and snapshot hashes | 2026-11-01 | 2026-11-02 | PASS on ELF 8f07bfe3 |
| Artefact provenance in CI: rebuild every committed binary and compare loaded sections | done | | committed ELFs | 2026-11-01 | 2026-11-01 | loaded bytes same, locally and on GitHub's runner; 130 author's files hashed |
| Oracle independence test, by hand: one flipped keccak round-constant bit must fail gate 1 | done | | none | 2026-11-01 | 2026-11-01 | caught: keccak 40/40, create 37/40, selector 15/40 |
| Malformed-input fuzz per command in gate 1, must give `error (...)` | done | | none | 2026-11-01 | 2026-11-01 | no crash; 6 lenient forms go with the spill fix |
| `eth_in` firmware change, its gate 4, the spec's spill test | done | | ELF 8f07bfe3 | 2026-11-01 | 2026-11-02 | ELF 8f07bfe3: gate 4 6 of 6, both spec cases spilled and exact |
| Ethereus test, after the spill fix | done | | ELF 8f07bfe3 | 2026-11-02 | 2026-11-02 | final exact, of 15: circuit 3, base 4, ceiling 5; tool right: circuit 2 |
| Regenerate `run` and calldata fine-tune families with real addresses | done | On branch `spill-eth-in`; regenerate if the firmware changes again | ELF hash in the data manifest | 2026-11-01 | 2026-11-01 | 763 spilled rows, all match an independent check; 56 create2 rows |
| GPU test of `serve.py` store support | done | | q9-store-rv, snapshot c2f24eca | 2026-11-02 | 2026-11-02 | RESULT PASS, including an exact `get`, 0 wrong on replay |
| +Skills arm on `q9-store-rv` | done |  | q9-store-rv, snapshot c2f24eca | 2026-11-02 | 2026-11-02 | superseded by Step 3, which runs ETH Evals' own skills mode |
| 4B evals on a rented L4, up to $0.50/h | queued | After Step 3; the 9B results come first | none; needs its own merged 4B checkpoint |  |  |  |
| `docs/ethevals-submission.md` final draft | done |  | each arm's results folder |  | 2026-11-02 | merged into Step 4 |
| Fine-tune data generator, `scripts/gen_ft_data.py` | done | | ELF hash, snapshot pack | 2026-11-01 | 2026-11-02 | sample approved by Fraser; 60 `run_limit` rows added after |
| Fine-tune training and evaluation, `docs/finetune-spec-v1.md` | done |  | data/ft1 jsonl, base 9B, then q9-store-rv for apply | 2026-11-02 | 2026-11-02 | Ethereus-9B-v0.1: KL 0.0078, MMLU 78.6 (stock 78.0), knowledge with files 11 of 20 (target 12), right tool call 45 of 48, finals 45 of 48, unneeded calls 0 of 50, Ethereus test 13 of 15 |
| Status console, `scripts/status_page.py` | done | | docs/status.html | 2026-11-01 | 2026-11-01 | |
| Clean `.env`, `push_secrets.py` | done | | none | 2026-11-01 | 2026-11-01 | `eba5d52` |
| No-key Internet run, circuit arm | done | | q9-rv2 | 2026-11-01 | 2026-11-01 | 6/24 tasks, 12/48 trials |
| No-key Internet run, base arm | done | | none | 2026-11-01 | 2026-11-01 | 6/23 tasks scored, 12/46 trials |
| Store merge, gates, verify, smoke | done | | ELF, store pack | 2026-11-01 | 2026-11-01 | 1.07x, 18/18, 6/6, verify ok, smoke 4/20 vs 3/20 |
| Migrate to eval VM 58642243, gates on pinned venv, stop old boxes | done | | none | 2026-11-01 | 2026-11-01 | 1.0001x, 18/18, 6/6 |
| `serve.py` + `spill.py`, three-call test | done | | ELF | 2026-11-01 | 2026-11-01 | RESULT PASS |
| Snapshot against `b320d31b` | done | | ELF b320d31b, store pack | 2026-11-01 | 2026-11-01 | verify 136/136 |
| rv-store port on `main` | done | | none | 2026-11-01 | 2026-11-01 | store and sealed tests pass |
| ELF `b320d31b` with `file_buf` | done | | firmware source | 2026-11-01 | 2026-11-01 | `c0853b4` |
