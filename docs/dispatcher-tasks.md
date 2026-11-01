# Dispatcher task list

Single list for the core repo and Route A, since the Cursor agent and Muse are retired (Fraser, 2026-11-01). It replaces `route-a-brief.md`, which gets no more orders. Mark items done with the source of the number.

## Done

- ELF `b320d31b` (`c0853b4`, source `880e37d`), confirmed on the box.
- rv-store port on `main` (`b2d7049`, `b011c7c`, `7fe997c`), fresh-clone tests pass.
- Snapshot against `b320d31b` (`c68e3aa`), verify 136/136 on real kernels.
- 9B merge `dist/q9-rv2`. Gates 1, 2 on fla, 3, 4 pass; numbers in `docs/route-a.md`.
- `serve.py` on `route-a` (`ac6245c`).

## Now

1. Serve test: final three-call run, replay clean on all three traces.
2. Eval VM from offer 56403068, one box: model, `serve.py` and harness on localhost. Confirm GPU, Docker, and CUDA for the pinned torch.
3. Pinned venv there; lock file committed; torch version recorded in `docs/route-a.md`.
4. Download base 9B, merge, rerun gates 1, 2 on fla, 3, 4 there on the venv and ELF `b320d31b`.
5. Once verified, stop 58623317 and 58640793. Move the grader's API key by relay, without reading it.
6. One Internet trial per arm end to end. Record provider config and web search setup in `docs/ethevals-submission.md`.
7. Internet, both arms, the 28 no-key tasks, then the 9 judged. Default attempts. Trajectories in `route-a/trajectories/<arm>/internet/`.

## Then

6. Store merge `q9-store-rv`, gates, snapshot verify, smoke test (`scripts/smoke_knowledge.py`).
7. The +Skills arm.
8. 4B evals on a rented L4, up to $0.50/h, once the 9B evals are running.
9. After both eval configurations finish: `eth_in` firmware change, its gate 4, the spec's spill test.
10. `docs/ethevals-submission.md` final draft.

## Blocked

- 7 tasks fork mainnet at pinned blocks and need an archive RPC: `act/treasury-eth-sale`, `act/vault-v2-concentration-rebalance`, `act/vault-v2-market-exit`, `build/aave-savings-page`, `build/keeper-deposits`, `build/signed-votes`, `build/swap-page`. Publicnode refuses archive requests at those blocks, so it is not installed on the VM (saved as `mainnet_rpc.publicnode`). Fraser to supply an Alchemy key.
