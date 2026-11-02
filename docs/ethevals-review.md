# ETH Evals: what a proper submission requires

Read 2026-11-02 from BuidlGuidl/ethevals at `cc3daaf` (current `main`): README, AGENTS.md, CONTEXT.md, the runner
reference, `docs/harbor.md` (board jobs), ADRs 0001, 0006, 0009, 0015, 0020, `runner/ethevals/job.yaml`, the skills
pack and the How it works page. Each point names its source.

## How results reach the board

- The board is filled by ETH Evals' own CI, not by submitted numbers. A model joins as "one entry in `job.yaml`" by
  pull request; after merge, `results.yml` plans and runs the missing trials, checks every trial folder for secrets,
  uploads trajectories to Harbor Hub and opens a results PR (runner reference, "CI and publication"; How it works).
- CI runs OpenCode agents on OpenRouter with Exa search (`job.yaml`; secrets `OPENROUTER_API_KEY`, `EXA_API_KEY`).
  Ethereus needs its own runtime (circuit, firmware, file store) behind an OpenAI-compatible endpoint, so a board entry
  needs an endpoint CI can reach and a key the maintainers hold. That is a conversation with the maintainers before
  any PR.
- Locally run trials can be uploaded only to the configured Hub org (`buidlguidl`) with its key (`--upload-hub`).
  Our own runs are evidence for that conversation, not board rows.

## What each mode means

| Board label | Mode | Definition (CONTEXT.md, How it works) |
|---|---|---|
| Model only | vanilla | the bare model answers in one API call, no tools, no web; Concepts quizzes only |
| Internet | internet | an agent with a shell, the web and the task's sidecars; no Ethereum skills installed |
| Internet + Skills | skills | the same agent plus the repo's skills pack, installed in the agent's skill directory by the harness |

- The skills pack is four ethskills guides pinned at `06ea4efa0807`: addresses, building-blocks, security, standards
  (`skills/README.md`). Our store holds 16 files from ethskills and evm-audit-skills. Files in our weights are part
  of the model, so a run with them loaded is the Internet mode for Ethereus-9B-v0.1, disclosed; "Internet + Skills"
  is the harness's skills mode on top of that.
- So the plan's two configurations should be: **Internet** = Ethereus-9B-v0.1 served as trained (training system
  line, file store loaded); **Internet + Skills** = the same model with the harness's skills mode on. The earlier
  plan (Internet without files) is withdrawn.

## Settings that must match the board's OpenCode entries

| Setting | Board (`job.yaml`) | Ours (`route-a/gates/job-ethereus.yaml`) |
|---|---|---|
| OpenCode version | 1.18.33 | 1.18.33 |
| Trials per task | 2 (ADR 0020) | 2 |
| Retries | `max_retries: 1`; judge errors only by `--trial N --retry-judge-error` | same |
| Web search | Exa (`EXA_API_KEY`) | none |
| Output limit | `OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX: '+131072'` (ADR 0015) | not set; our provider entry says output 8,192 |
| Effort | high, passed to OpenCode as the model variant | none |
| Agent time limit | none; Harbor default none, CI stops a trial step at 190 minutes; a task sets one only if it needs it | 45 minutes on our earlier capped copies |
| Judge | Claude Opus 5.5, high, $2 cap | same |
| RPC | `MAINNET_RPC_URL` for fork tasks | same |

## Consequences for Step 3

1. Add Exa search and the output-limit setting to our OpenCode entries, so "Internet" means what it means on the
   board. This needs an Exa key.
2. Run without our 45-minute cap; bound each trial at 190 minutes, as CI does. Arms take longer than estimated.
3. The earlier stock rows ran without Exa and with our cap, so they no longer match: reuse is withdrawn, stock
   reruns in full.
4. Model only (vanilla) is the cleanest test of a tool in the weights: one API call, no agent tools, 25 Concepts
   quizzes, 2 trials each. Cheap, and it is where the built-in tool is the only tool.
5. The training system line is set by our server, not by the agent; the submission states it.
6. The submission draft (Step 4) is an evidence pack for the maintainers plus the proposed `job.yaml` entry, not a
   results PR.
