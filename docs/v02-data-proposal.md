# Ethereus-9B-v0.2: data proposal

Draft for Fraser's review, 2026-11-03. Nothing is generated or trained until he approves it.

## What v0.1 shows

The tool in the weights works, and v0.1 learned to decide to use it: 45 of 48 right calls on held-out tool questions,
13 of 15 on the Ethereus test, 0 unneeded calls on 50 unrelated questions. In ETH Evals it over-applies the habit. In
arms 1 and 2 (Internet + Skills, Internet) every trial is one turn: it calls its in-weights `get` or `list`, restates a
line, and stops. It never calls the agent's own tools (bash, read, write, edit), so build and act tasks score 0. It
even asks its store for the task's own `chain_context.json`. The serving path is not the cause: asked plainly, v0.1
called OpenCode's `write` tool correctly on both probe requests.

The data explains it. `gen_ft_data.py` had families for `eth` calls, `list`/`get` reads, a `run` limit and plain
answers with no tool. None used a client tool, and none had more than one turn of work. The training system line
("before answering, list them and get whatever is relevant") pushed the same way.

## What v0.2 must learn

1. **Which tool, not just whether.** In-weights tools for what they do best (hashes, ABI, addresses, u256, the
   reference notes); the agent's tools for the workspace (read files, write code, run `forge`, send transactions).
2. **Keep working.** A task is done when its checks would pass, not after the first answer.
3. **Keep what v0.1 has.** Its tool decision on Ethereum questions, its 0 unneeded calls, and the gates (KL to stock
   at most 0.05, MMLU-500 within 1 point).

## New families

| Family | Rows | Built from | What a row shows |
|---|---|---|---|
| `agent_build` | about 400 | Kind 1 proven tasks (42 for training; the 8 held out never) and Kind 2 and 3 when proven | read the instruction and files with `read`, write the solution with `write` or `edit`, run the task's tests with `bash`, read the result, finish with a short report |
| `agent_mixed` | about 300 | the same tasks plus Ethereum facts they need | the steps above with an in-weights call where it is the right tool (a selector, a keccak, a CREATE2 address, a store note), and `read` for workspace files such as `chain_context.json` |
| `agent_recover` | about 150 | the same tasks, with a test that fails first | a wrong first attempt, the failing test output, a fix, the passing run |
| existing families | as v0.1, rebalanced | `gen_ft_data.py` | v0.1's `eth`, files, knowledge, `run_limit` and no-tool rows, down-weighted so agent rows are about half the data |

**Where the trajectories come from.** Mechanically, not from a model. Each step's tool output is real: the generator
runs the reference solution inside the task's own container, step by step, and records what `read`, `bash` and
`forge test` actually return. The words between steps are short templates filled from the task (file names, test
names, results). No ETH Evals task, no Claude output, as for Kind 1. `agent_recover` uses Kind 1's emptied version
as the wrong first attempt, since its tests are known to fail.

**Format.** OpenCode's own tool schema and system prompt, as recorded in our Step 3 trajectories, so training sees
what evaluation sees. Multi-turn rows, each tool result in its own tool message.

**System line.** Drop it, or replace it with one that does not force a look-up: "Reference notes are under es/ in
memory; workspace files are on disk."

## Evaluation before any ETH Evals run

- The v0.1 tool evaluation (48 rows) and the Ethereus test: no regression.
- Held-out agent tasks: the 8 Kind 1 hold-outs, run through ETH Evals' runner with OpenCode, as Step 3 runs.
- The fine-tune gates: KL to stock at most 0.05, MMLU-500 within 1 point of stock.
- Then ETH Evals, the same arms as Step 3.

## Cost

Generation runs on CPU and Docker: about 850 rows, each a few task-container runs. Training as ft1b: a LoRA on
stock 9B with the KL term, about 4 hours on the eval GPU at the larger row count. One decision for Fraser: the
generator needs task containers, so it runs where Docker works (the eval VM or the Cursor Muse's machine).
