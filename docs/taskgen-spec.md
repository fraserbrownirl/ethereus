# Task generator for step 3: training the model to build

Fraser, 2026-11-02. Step 3 is training the model to build: Solidity tasks scored by machines, not by labels. It is what moves a large backbone (K3) on ETH Evals toward the frontier. Runs in parallel with the K3 merge; no artefact crosses between them.

## Kinds

| Kind | The agent | The check | Reference solution |
|---|---|---|---|
| 1. Pass a test suite | writes a contract or function | the source project's own Foundry tests | the original code |
| 2. Find the planted vulnerability | finds and fixes a planted bug | an exploit test that fails on the planted version and passes after the fix | the original code |
| 3. Reach an outcome on a fork | produces transactions on a mainnet fork | Forge tests reading the fork's end state | a transaction script |

## Rules

- Sources: real verified contracts and real audit findings. Never ETH Evals tasks, never Claude outputs as solutions or targets. The generator's code is ours; what a task asks for and how it is checked come from the source project or a real finding.
- Licence: MIT or Apache-2.0 sources only, recorded per task.
- Output: Harbor task folders in ETH Evals' format (`task.toml` schema 1.3, `README.md`, `instruction.md`, `environment/`, `tests/`, `solution/solve.sh`), so the same harness runs them. Instructions avoid the words the ETH Evals lint forbids.
- A task counts only when it is proven: the reference solution passes every check through Harbor on x86, and the unsolved starting state fails at least one. Local Forge runs are a pre-filter, not the proof.
- First pass: 50 validated tasks per kind.
- Held apart from ETH Evals: no task shares a source contract with an ETH Evals task.

## Kind 1

Source: OpenZeppelin Contracts v5.7.0 (MIT), the most deployed Solidity library, and its Foundry tests (`test/**/*.t.sol`). Each task empties one function body in the library file a test file covers. The container clones v5.7.0, as ETH Evals' own tasks do, and copies the emptied file over it. The checks are that test file, with imports pointed at the workspace. A candidate needs the original to pass every test and the emptied version to fail at least one.

## Layout

`experimental/taskgen/`: `kind1.py`, `kind2.py`, `kind3.py`, `validate.py` (local pre-filter in the verifier's layout), `out/` (generated tasks, gitignored), `sources/` (pinned clones, gitignored), `manifest.json` per run.
