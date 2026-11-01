# Dispatcher working rules

From Fraser, 2026-11-01. Each rule exists because that class of error happened this week. These sit alongside `docs/dispatcher-addendum-autonomous.md`.

1. **Oracle independence.** Whenever an oracle changes, and once now by hand: inject a deliberate bug into the firmware (for example flip one bit in keccak's round constants), rebuild, and run gate 1. If the gate does not fail, the oracle is not independent. Catches a harness that mirrors the firmware.
2. **Claims carry their source.** Every number in a report has a log path beside it, and the status page links the log. This applies to my own reports as well as anything I read. A number with no log says so.
3. **Fuzz the error paths.** Gate 1 includes a slice of malformed inputs per command: bad hex, wrong arity, over-window, empty. Each must give an `error (...)` line, never a crash, a hang or garbage. Folds into `eth.py`.
4. **Fixtures proven first.** Any new built-in contract, test vector or snapshot gets its own gate against py-evm or eth_abi before anything else uses it.
5. **By-design rejections are asserted, not compared.** Inputs the firmware rejects by our decision (stricter than `eth_abi` or the oracle's libraries) are asserted as errors in the gate. The oracle does not model them and they are never compared.
