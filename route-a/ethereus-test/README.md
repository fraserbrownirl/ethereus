# Ethereus test

**This is an Ethereus test, not ETH Evals.** It measures what the circuit adds on exact work, which the ETH Evals row cannot show because the model did not call the circuit there. Do not present these numbers as board results.

Tasks, five instances each, in the style of ETH Evals' Act pillar: byte-exact calldata for a multi-argument call (short arguments), a mapping storage slot (64-byte keccak preimage, given as is), and the return value of an ERC-20 call on the built-in fixture.

Arms: base (stock Qwen3.5-9B, no tool), Ethereus (`q9-rv2`, `eth` in the circuit), host ceiling (stock Qwen with `eth` answered by the host running the same firmware, no 80-token window). Same prompt everywhere, greedy, thinking off, no prompt tuning.

Scored separately: `tool_ok`, the expected answer appears in a tool response; `final_ok`, the exact bytes appear in the final text. `final_ok_despaced` shows how many fail only on stray whitespace. Expected answers come from eth_abi, eth_hash and py-evm (`generate.py`), not from the firmware.

Runs: before the spill fix (ELF `b320d31b`), and again after it.
