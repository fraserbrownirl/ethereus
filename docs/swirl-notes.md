# SWiRL notes (for the Muse)

Ethereus-9B-v0.2 is trained on multi-step trajectories (SWiRL-style), not single-turn rows (Fraser, 2026-11-04;
single-turn training work is stopped). The Muse works on branch `swirl-wip` in `~/ethereus-swirl` on the eval VM.
These notes collect what the dispatcher has established for that work; each line says where it comes from.

## Trajectory generator: Qwen3.6-27B

- **Tool-call format: Qwen's `qwen3_coder` XML.** The generator writes
  `<tool_call>\n<function=NAME>\n<parameter=ARG>\nvalue\n</parameter>\n</function>\n</tool_call>`, with tools listed as
  JSON inside `<tools>…</tools>` in the system prompt and each result returned in `<tool_response>…</tool_response>`
  (its own `chat_template.jinja`; vLLM serves it with `--tool-call-parser qwen3_coder`). This is the same shape
  `serve.py` parses and the circuit's own calls use, so a generated call can be executed and replayed through the
  circuit without a format change.
- **Non-string arguments arrive as JSON.** Qwen3.6's template renders a non-string argument with `tojson` (a boolean
  is `true`, a number `42`, a list `[1, 2]`); Qwen3.5's used Python's `string` (`True`). The `ref.py` adapter must
  parse typed parameter values as JSON (and keep a string parameter as its raw text), or typed arguments are
  misread.
- Fit and serving: `docs/q36-27b-config.md` (bf16 fits one VM GPU, about 72 GiB at the full 262,144 context,
  estimate; `vllm>=0.19.0` in its own venv; one model server on the GPU at a time, so not alongside an eval arm).

## What the evals say v0.2 must teach (Step 3 and the line test)

- v0.1 uses its in-weights store and then stops: no OpenCode tool calls in any agent arm (`docs/ethevals-submission.md`).
- The untrained model works in the workspace but almost never calls `eth`; a hint line raises that from 0 to 1 call
  in 20 trials, and every trial that did call an in-weights tool kept working afterwards (line test,
  `docs/dispatcher-log.md`, 2026-11-04).
- So the trajectories need both kinds of tool in one task: in-weights calls where they compute the value, and the
  agent's tools for the workspace, continuing after each in-weights result.

## The SWiRL brief (Fraser, 2026-11-04, verbatim)

Copied from Fraser's brief so the VM Muse reads it from the repo. Amendments follow in their own section as Fraser sends them.

### SWiRL brief: multi-step training data for Ethereus v0.2

**Owner:** VM Muse. **Branch:** `swirl-wip` in `~/ethereus-swirl`. Builds code only; the GPU runs go through the dispatcher.

#### Why

v0.1 was trained on single-turn data and learned "look up, restate, stop", making 0 agent tool calls. The one-line hint got only 2 of 20 in the line test, with 1 eth call.

v0.2 needs real multi-step trajectories: call a tool, read the result, act again, recover from errors, and stop when done. The reference is SWiRL (Stanford CS329A, part 5): generate one step at a time, score each step, filter by process, then RL.

#### 1. Environment

- **Tasks:** Kind 1, 2 and 3, training split only.
  - The sha256 hold-outs stay out.
  - Nothing from ETH Evals or eth-evals.
- **Tools:** use the exact tool-call format and chat template the model uses at inference:
  - `eth`, with results from `ref.py` (bit-exact with the chip);
  - `files`;
  - forge build/test;
  - workspace read/write;
  - for Kind 3, the filtered chain RPC.
- **Step cap:** 20 steps.

#### 2. Step loop

At each step the generator sees the task plus the full history, and emits reasoning plus exactly one action: a tool call or the final answer. The tool runs and its result is appended to the history.

#### 3. Generator

- **Model:** Qwen3.6-27B is the first choice (Apache-2.0).
  - The dispatcher confirmed it fits one VM GPU in bf16.
  - It can't share the GPU with an eval server, so generation and evals take turns.
- **Allowed:** only open, Apache-licensed models.
- **Never:** Claude, Opus or Muse output.
- **Recording:** put the model, version and sampling settings in every row.
- **Serving:** vLLM ≥0.19.0 in its own venv, with `--tool-call-parser qwen3_coder --reasoning-parser qwen3 --language-model-only`.
- **Tool-call format:** `<tool_call><function=NAME><parameter=ARG>value</parameter></function></tool_call>`. Tools are listed as JSON in `<tools>`, and results go in `<tool_response>`.
  - Non-string arguments arrive as JSON, so the `ref.py` adapter parses typed values as JSON and keeps string parameters as raw text.
- **Delivery:** write generation as a GPU script with a run-command for the dispatcher to run.

#### 4. Exact step scoring, wherever possible

- The tool call is well-formed.
- Its arguments are correct wherever the task has a known intermediate (selector, slot, base units, address).
- The tool result is used correctly in the next step or in the final answer, with no copying errors.
- The number of passing forge tests goes up, not down.
- There are no forbidden actions: editing tests, cheat RPC methods, network access.
- Stopping before the verifier passes counts as a bad step.

Use an open-model judge only for steps that can't be scored exactly, and report what share of steps needed it.

#### 5. Outcome

The outcome is the task's own verifier result: pass or fail.

#### 6. Two datasets

- **SFT:** only trajectories where every step is good and the outcome passes.
- **RL:** per-step records (context, action, step reward) from process-filtered trajectories, including trajectories whose outcome failed.

For both datasets:
- Keep recovery examples (an error, then a fix).
- Keep the per-source caps: 15 per source, and no source above 30%.
- Dedupe.

#### 7. Schema (JSONL)

`task_id, kind, source, licence, provenance, generator{model,version,sampling}, steps[{action, tool, args, response, checks{…}, step_reward}], outcome, holdout:false`

#### 8. Provenance rule

- Every task and trajectory records who wrote it: a source repo and its licence, a human, or the generator model.
- **Nothing written by Muse goes into training data or into any environment we might license out.** This covers trajectories, solutions and task text. Meta Model API terms §10.1(ix) forbid using outputs to train competing models or to build training datasets for them.
- Muse-written content is for scorer tests only.
- Flag any existing Kind 1–3 tasks whose prompts or solutions Muse wrote.

#### 9. Prove the scorers before any GPU run

- Convert the existing proven solutions into scripted "golden" trajectories. They must score all-good and pass.
- Plant bad steps: a wrong selector, a copying error, an early stop, an edited test. Each must be caught.
- Report the scorers' precision on these tests.

#### 10. Pilot

Prepare a script for 50 tasks × 4 samples, ready for the GPU, with the expected run time.

#### 11. Comparison to plan

v0.2 will be trained two ways:
- with the built-in circuit;
- with the same tools called externally.

Design the data so both are possible: the same trajectories, with only the tool executor swapped. This tests whether the in-weights computer adds anything.

#### Report

- the scorer test results;
- the schema;
- one sample trajectory;
- the pilot command;
- the list of flagged Muse-written tasks;
- anything you couldn't score exactly.

## Amendments to the brief

None separate: Fraser's `swirl-brief.md` already includes every amendment (generator, tool format, provenance,
section 11). The copy above matches that file line for line (headings shifted two levels), sections 8 and 11 included.

## Provenance rule

Every task and every trajectory records who wrote it: a source repo and its licence, a human, or the generator model
(with version and sampling). **Nothing written by Muse goes into training data or into any environment we might
license out** — trajectories, solutions and task text alike — because the Meta Model API terms §10.1(ix) forbid
using its outputs to train competing models or to build training datasets for them. Muse-written content is for
scorer tests only. "Muse" here means Meta's Muse models however they were run: the Cursor Muse lanes (Kind 2,
Kind 3) and the sandboxed Solana Muse.

**The same rule covers Claude, including the dispatcher** (Fraser, 2026-11-04): Anthropic's terms restrict using
outputs to train competing models, and no Claude or Opus output goes into training. Upstream code (sources and their
own tests, under their licences) is clean; anything Claude or Muse wrote — instructions, READMEs, flaw descriptions,
tests, solutions — is not.

## Existing Kind 1–3 tasks: who wrote them (from the commit history, 2026-11-04)

Method: every dispatcher commit carries a `Co-Authored-By: Claude` trailer; the Muse lanes' commits (also
authored "Fraser Brown") carry none. Task text was traced with `git blame` on the generator templates and checked
against each task's own files. A task is flagged if Muse wrote its prompt (instruction, README, description), its
solution, or the flaw it plants.

- **Kind 1 (50 proven + 8 held out): none flagged.** `kind1.py` on main has 6 commits, all the dispatcher's; the
  tasks were built and proven by the dispatcher and frozen as built. Solutions are the upstream MIT sources. The
  Muse's later `kind1.py` edits are on `kind2` only and did not rebuild these tasks.
- **Kind 3 (21 tasks): all flagged.** Every commit that created them is the Muse's (696ac9c, f813e81, 22e7f6b,
  7336a42): instructions, solutions (`solve.sh`, `solution/*.sol`, `confirm.py`) and tests. Scorer tests only.
  k3-odd-decimals-01, k3-approve-deposit-01…06, k3-stake-claim-01…06, k3-raw-call-01…04, k3-storage-read-01…04.
- **Kind 2 (52 Docker-proven): 37 flagged, 15 clean.** Every Kind 2 solution is the upstream project's own code and
  every test the project's own; what differs is who wrote the prompt text and the planted flaw.
  - The generator `kind2.py` was written by the dispatcher (f70c2d71); in its task-text templates the Muse rewrote
    the agent-visible instruction's flaw list (eff9555c), the README heading and body and the `task.toml`
    description (8cbedac8); the Muse added the flaw classes return, state, cast, compare, effects, var and replay
    and the code that plants them (e85a232e, 31d1892, eff9555c). check, access and bound are the dispatcher's.
  - Clean (dispatcher-written class and text): k2-oz-3c65c914-bound, k2-oz-29369cec-check, k2-oz-7ca0b5a8-bound,
    k2-oz-31ee332d-check, k2-dragonfly-063abec9-check, k2-dragonfly-0c31350e-access, k2-dragonfly-bbd30160-check,
    k2-dragonfly-6445f713-check, k2-dragonfly-077c2457-check, k2-dragonfly-0c3b5933-check,
    k2-dragonfly-40af734e-check, k2-dragonfly-67984f3b-check, k2-dragonfly-b9471def-check,
    k2-dragonfly-baa7fb23-check, k2-solady-5f2559b0-check. Caveat for Fraser: these were built by runs the Muse
    executed, with its edits to the copy-and-rename code (1757928, 8f2aa3d); the identifier renames are mechanical,
    from the dispatcher's word lists, but the build itself was a Muse run.
  - Flagged (37):
    - k2-dragonfly-46bde01d-return: flaw class by Muse
    - k2-oz-a3b25243-state: flaw class by Muse
    - k2-dragonfly-b17932e6-compare: flaw class by Muse
    - k2-oz-01e89889-compare: flaw class by Muse
    - k2-oz-3bb4c8aa-compare: flaw class by Muse
    - k2-oz-814828c3-compare: flaw class by Muse
    - k2-oz-b379cadc-compare: flaw class by Muse
    - k2-oz-b681c687-compare: flaw class by Muse
    - k2-oz-cadba713-compare: flaw class by Muse
    - k2-solady-20420f63-var: flaw class, instruction by Muse
    - k2-solady-28bbf820-var: flaw class, instruction by Muse
    - k2-solady-a09e5c06-var: flaw class, instruction by Muse
    - k2-solady-b208aec0-var: flaw class, instruction by Muse
    - k2-solady-f34711b5-var: flaw class, instruction by Muse
    - k2-op-0aa710fa-compare: flaw class, instruction, README/description by Muse
    - k2-dragonfly-23b49068-check: instruction, README/description by Muse
    - k2-oz-d2e51bd3-compare: flaw class, instruction, README/description by Muse
    - k2-solady-20d31965-var: flaw class, instruction, README/description by Muse
    - k2-solady-aacddbb1-var: flaw class, instruction, README/description by Muse
    - k2-solady-e9c15fb5-var: flaw class, instruction, README/description by Muse
    - k2-solady-fc0715c4-var: flaw class, instruction, README/description by Muse
    - k2-op-1beb1bed-compare: flaw class, instruction, README/description by Muse
    - k2-op-217ea3ac-compare: flaw class, instruction, README/description by Muse
    - k2-op-55d41203-bound: instruction, README/description by Muse
    - k2-op-9e941d54-replay: flaw class, instruction, README/description by Muse
    - k2-op-9eeb538f-check: instruction, README/description by Muse
    - k2-op-aabbd493-bound: instruction, README/description by Muse
    - k2-op-e3fde736-compare: flaw class, instruction, README/description by Muse
    - k2-op-e54e1ff9-check: instruction, README/description by Muse
    - k2-hyperlane-48b908bc-compare: flaw class, instruction, README/description by Muse
    - k2-hyperlane-d7b7504c-check: instruction, README/description by Muse
    - k2-hyperlane-50bf6bf0-compare: flaw class, instruction, README/description by Muse
    - k2-hyperlane-53eee410-check: instruction, README/description by Muse
    - k2-hyperlane-84a88eda-check: instruction, README/description by Muse
    - k2-hyperlane-92cd9d37-compare: flaw class, instruction, README/description by Muse
    - k2-hyperlane-da761466-bound: instruction, README/description by Muse
    - k2-hyperlane-f92a4734-compare: flaw class, instruction, README/description by Muse

## Re-audit with Claude included (Fraser, 2026-11-04)

Rule: a task is flagged if its instruction, README, flaw description, test or solution was written by Claude or by
Muse; upstream code and upstream tests are clean. The Muse-only detail above still says which parts were Muse's.

| Kind | Tasks | Flagged | Written by Claude | Written by Muse | Clean (upstream) |
|---|---|---|---|---|---|
| 1 | 58 (50 proven + 8 held out; 110 built) | **58** | instruction, README and description templates (`kind1.py`, dispatcher) | none | solutions (the deleted upstream function), tests (the project's own) |
| 2 | 52 Docker-proven (57 built) | **52** | the generator's instruction, README and description templates and the check/access/bound flaw descriptions (`kind2.py` f70c2d71) | the rewritten instruction flaw list, README and description (37 tasks), flaw classes return/state/cast/compare/effects/var/replay (28) | solutions (the upstream function before the planted flaw), tests (the project's own) |
| 3 | 21 | **21** | none | instructions, READMEs, tests, solutions, chain set-ups | none |

Also outside the task sets: **Ethereus-9B-v0.1's training data contains Claude-written text.** `data/ft1/phrasings.jsonl`
("questions written by a model", committed by the dispatcher in 07e453c and a3b5687) supplies the questions and final
answers of all 663 knowledge and files rows, and the question templates of the abi, u256, create, run, negative,
general and run_limit families are the dispatcher's (`scripts/gen_ft_data.py`). The answers' facts are the ethskills
guides (upstream). v0.2 data must not reuse them.

Mechanical parts made by code that Claude or Muse wrote — identifier renames, deleting or mutating one function,
Dockerfiles and `foundry.toml` — are not prose; whether the rule covers them is Fraser's call. The plan below treats
renames and mutations as mechanical and regenerates all prose.

## Regeneration plan (proposed; not started until Fraser approves)

1. **Generator.** Qwen3.6-27B (Apache-2.0) on the eval VM, bf16, vLLM >=0.19.0 in its own venv
   (`--tool-call-parser qwen3_coder --reasoning-parser qwen3 --language-model-only`), between eval runs. Every
   regenerated file records model, version and sampling.
2. **Kind 1 and 2 text.** Qwen3.6-27B writes each task's `instruction.md`, `README.md` and `task.toml` description
   from facts only (file path, function name, the failing test names, the flaw class), never from the current
   Claude- or Muse-written text. Fraser supplies the flaw-class descriptions (check, access, bound, return, state,
   cast, compare, effects, var, replay; and Kind 1's "deleted function"), which go in verbatim. Solutions and tests
   stay upstream. ETH Evals' lint runs on every regenerated task; a lint failure is regenerated (up to 3 tries).
3. **Kind 3.** Qwen3.6-27B regenerates each task's tests and solution (and instruction and README) from a spec of
   the chain set-up, in a loop: write, run ETH Evals' free check (oracle must score 1, nothing 0), feed back the
   result, up to 4 attempts per task. The chain set-up contracts are Muse-written too and are regenerated the same way.
4. **Re-proof.** All regenerated tasks through `scripts/prove_task.sh` (Kind 3, and Kind 1 and 2 because their text
   changed); Kind 2's fuzz-only rule stays. Splits regenerated by the same sha256 rule.

Estimate (nothing measured yet; throughput from the card's memory bandwidth, 1.79 TB/s over 55.6 GB of weights,
about 32 tokens/s per stream and roughly 300 tokens/s batched at 16 streams):

| Step | Work | GPU time |
|---|---|---|
| Set-up | download 55.6 GB, vLLM venv, load | about 0.5 h, mostly not GPU |
| Kind 1 and 2 text | 110 tasks × 3 files, about 2,000 output tokens a task, 2 lint retries | about 0.3 h |
| Kind 3 | 21 tasks × up to 4 attempts × about 10,000 tokens (with thinking) | about 0.8 h, plus the free checks on CPU |
| Re-proof | 131 tasks on Mac Docker | none (about 1 h on the Mac) |
| **Total** | | **about 1.5–2 h of GPU**, about 3 h elapsed |
