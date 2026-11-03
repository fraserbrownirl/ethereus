# Review: the Cursor Muse's renaming fix (commit 1757928, branch kind2)

Dispatcher, 2026-11-03. Verdict: **do not merge as is.** The fix removes the bug it names but breaks member calls on
contract instances; a narrower change keeps both.

## What the commit changes

`experimental/taskgen/kind1.py`, `build_task.rename`: dotted renames (`.fn(` -> `.newFn(`) now happen only in files
with `using TargetLib`; before, they also happened in the target file and in every file that imports it. The old code
renamed `LibBytes.split` when the target was `LibString.split`, which broke compilation; that is the bug.

## Test

Kind 1 regenerated from scratch twice on the Mac, in separate worktrees, all four sources and the same pinned
sources: once with main's `kind1.py` (the fix's parent), once at `1757928`.

| | main's kind1.py | the fix |
|---|---|---|
| Tasks generated | 50 (OpenZeppelin 15, Solady 15, PRB 5, dragonfly 15) | 49 (dragonfly 14) |
| Candidates rejected | 808 | 852 |
| The 50 proven Kind 1 tasks generated | 50 | 43 |
| Proven tasks whose files change | — | 0 |

- **No proven task was built with the bug.** The 43 that the fix still generates come out byte-identical, so the old
  dotted rename changed nothing wrong in them.
- **The fix loses 7 proven dragonfly tasks** (`07f9f4c3`, `26d4ef24`, `334e36cc`, `4b549429`, `7483785d`,
  `8999f90c`, `da4fc229`): all contract functions (`initialize`, `withdraw`, `transferOut`, `constructTree`, `prove`,
  `createProof`) that the tests call on an instance: `safe.`, `unsafe.`, `wallet.`, `logic.`, `helper.`, `drop.`.
  The old rename of those calls was right; the fix leaves them unrenamed and the renamed original fails to compile.
- 32 of the 50 proven tasks regenerate byte-identical from main's code today; the other 18 come from batches made
  before later generator changes (test-name numbering, the lint-word filter) and are proven as they were built.

## Suggested change

Keep dotted renames in the target file and in files that import it, as before, but skip a call whose qualifier is the
name of another contract or library in the closure (one the target does not define), which is exactly the
`LibBytes.split` case:

```python
others = {n for g in files if g != target for n in contract_names((root / g).read_text())} - set(lib_map)
if f == target or imports_target or (qual and re.search(rf"\busing\s+({qual})\b", text)):
    text = re.sub(rf"(?<![\w.]){re.escape(fn['name'])}(?=\s*\()", new_fn, text)
    text = re.sub(rf"(\b\w+)?\.{re.escape(fn['name'])}(?=\s*\()",
                  lambda m: m.group(0) if m.group(1) in others else f"{m.group(1) or ''}.{new_fn}", text)
```

Accept it when a regeneration keeps all 50 proven Kind 1 tasks byte-identical and the Kind 2 yield gain holds.
