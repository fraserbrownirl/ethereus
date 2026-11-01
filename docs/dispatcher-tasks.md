# Dispatcher task list

Single list for the core repo and Route A, since the Cursor agent and Muse are retired (Fraser, 2026-11-01). It replaces `route-a-brief.md`, which gets no more orders. Mark items done with the source of the number.

## Done

- ELF `b320d31b` (`c0853b4`, source `880e37d`), confirmed on the box.
- rv-store port on `main` (`b2d7049`, `b011c7c`, `7fe997c`), fresh-clone tests pass.
- Snapshot against `b320d31b` (`c68e3aa`), verify 136/136 on real kernels.
- 9B merge `dist/q9-rv2`. Gates 1, 2 on fla, 3, 4 pass; numbers in `docs/route-a.md`.
- `serve.py` on `route-a` (`ac6245c`).

## Now

1. Serve test rerun on `d3fd855` code: passes only with replay clean on all three traces. Step 3 stays open until then.
2. Harness VM 58640793: confirm Docker runs. Install uv and the harness at the same commit as the box (`cc3daaf`), and record it.
3. Route from the VM to the box's `serve.py`, because the box cannot run Docker. Base on port 8000, `q9-rv2` on 8001, one at a time if VRAM needs it.
4. One Internet trial on base, end to end, then one on Ethereus. Record provider config and web search setup in `docs/ethevals-submission.md`.
5. Internet on the 28 no-key tasks, default attempts, both arms. Trajectories in `route-a/trajectories/<arm>/internet/`.

## Then

6. Store merge `q9-store-rv`, gates, snapshot verify, smoke test (`scripts/smoke_knowledge.py`).
7. The +Skills arm.
8. 4B evals on a rented L4, up to $0.50/h, once the 9B evals are running.
9. After both eval configurations finish: `eth_in` firmware change, its gate 4, the spec's spill test.
10. `docs/ethevals-submission.md` final draft.

## Waiting on Fraser

- Whether to supply `ANTHROPIC_API_KEY` for the 9 judged tasks.
