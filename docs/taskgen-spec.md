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

Fraser, 2026-11-02: several MIT sources, none over 30% of the tasks (15 of 50); contracts, functions and their parameters renamed per task so the original is not recognisable; training runs with no network.

Sources so far: OpenZeppelin Contracts v5.7.0, solady 258f67c, PRBMath v4.1.0, all MIT, at most 15 tasks each; a fourth is needed to reach 50. For a test file, the files it imports are copied: project sources into `workspace/src`, test helpers into `tests/`. One function in one of those sources is emptied, the library holding it, the function and its parameters get new names, and comment lines that name the source project are dropped. The container holds only these files: no clone, no git history, no original. `[agent] network_mode = "no-network"`; the image compiles once at build time so the compiler is present offline. A candidate needs the renamed original to pass every test and the emptied version to fail at least one. Documentation text stays, since it defines the behaviour.

## Layout

`experimental/taskgen/`: `kind1.py`, `kind2.py`, `kind3.py`, `validate.py` (local pre-filter in the verifier's layout), `out/` (generated tasks, gitignored), `sources/` (pinned clones, gitignored), `manifest.json` per run.

## Held-out test set

Fraser, 2026-11-02: 2 Kind 1 tasks per source (8 of 50) are never trained on. Chosen as the two lowest `sha256("holdout:" + name)` per source, so the choice is reproducible and not by hand. Ids and the 42 training tasks: `experimental/taskgen/kind1-split.json`. Held out: `k1-oz-02d959d2-tracea4e2`, `k1-oz-d05b3326-foldt724`, `k1-dragonfly-db3ab910-packfe6b`, `k1-dragonfly-da4fc229-foldw7c0`, `k1-solady-e4437fda-decode`, `k1-solady-83643ee8-siftce1e`, `k1-prb-303d2ff1-tracei693`, `k1-prb-b5715c51-carveu882`. Training tasks may come from the same source file as a held-out one (up to 3 per file); a stricter split would drop those.
