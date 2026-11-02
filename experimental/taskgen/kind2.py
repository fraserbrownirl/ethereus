"""Kind 2: find the planted vulnerability (docs/taskgen-spec.md).

One check in a renamed source file is removed or weakened, in a class real audits report: a deleted `require` or
`if (...) revert` (SWC-123, missing input validation), a dropped access-control or reentrancy modifier (SWC-105,
SWC-106, SWC-107), or a bound made off by one (`<` for `<=`). The checks are the source project's own Foundry tests:
the ones that fail on the planted version are the exploit tests, and they are hidden from the agent. The reference
solution is the original file, renamed. The generator writes no test and no solution of its own.
"""

import argparse
import hashlib
import json
import re
import shutil
import textwrap
from pathlib import Path

import validate
from kind1 import (AGENT_WORDS, HERE, IMAGE, IMPORT, SOURCES, build_task, closure, functions, resolve, strip_comments,
                   task_files)

MODIFIER = re.compile(r"\b(only[A-Z]\w*|whenNotPaused|whenPaused|nonReentrant)\b(\s*\([^()]*\))?")
CLASSES = {"check": "a missing check (SWC-123)", "access": "missing access control (SWC-105, SWC-106, SWC-107)",
           "bound": "an off-by-one bound"}


def close_paren(s: str, i: int) -> int:
    """Index just after the parenthesis that closes the one at s[i]."""
    depth = 0
    while i < len(s):
        depth += {"(": 1, ")": -1}.get(s[i], 0)
        i += 1
        if depth == 0:
            return i
    return -1


def mutants(text: str, fn: dict) -> list[tuple[str, str, str]]:
    """(class, description, mutated text) for one function, offsets from `functions(text)`."""
    clean = strip_comments(text)
    out = []
    header = (fn["fstart"], fn["start"])
    for m in MODIFIER.finditer(clean, *header):
        out.append(("access", m.group(1), text[:m.start()] + text[m.end():]))
    body_lo, body_hi = fn["start"], fn["end"]
    for m in re.finditer(r"\brequire\s*\(", clean[:body_hi]):
        if m.start() < body_lo:
            continue
        end = close_paren(clean, m.end() - 1)
        semi = clean.find(";", end)
        if end > 0 and semi > 0 and clean[end:semi].strip() == "":
            out.append(("check", "require", text[:m.start()] + text[semi + 1:]))
            for b in re.finditer(r"(?<![<>=])(<=|>=|<|>)(?![<>=])", clean[m.end():end]):
                lo = m.end() + b.start()
                flip = {"<=": "<", ">=": ">", "<": "<=", ">": ">="}[b.group(1)]
                out.append(("bound", b.group(1), text[:lo] + flip + text[lo + len(b.group(1)):]))
    for m in re.finditer(r"\bif\s*\(", clean[:body_hi]):
        if m.start() < body_lo:
            continue
        end = close_paren(clean, m.end() - 1)
        if end < 0:
            continue
        rest = clean[end:body_hi]
        r = re.match(r"\s*(\{\s*)?revert\b[^;]*;\s*(\})?", rest)
        if r and bool(r.group(1)) == bool(r.group(2)):
            out.append(("check", "if-revert", text[:m.start()] + text[end + r.end():]))
            for b in re.finditer(r"(?<![<>=])(<=|>=|<|>)(?![<>=])", clean[m.end():end]):
                lo = m.end() + b.start()
                flip = {"<=": "<", ">=": ">", "<": "<=", ">": ">="}[b.group(1)]
                out.append(("bound", b.group(1), text[:lo] + flip + text[lo + len(b.group(1)):]))
    return out


def instruction_for(rel: str) -> str:
    return textwrap.dedent(f"""\
        One check in `{rel}` was removed or weakened, so the code now accepts something it must refuse. Find it and put it back.

        Change only what the fix needs; everything else in the project stays as it is. `forge build` works in the project; there is no network access.
        """)


def kind2_files(name, spec, names, orig, mutant, tests, cls):
    """Kind 1's task layout with Kind 2's instruction, README and solution: the workspace holds the planted version."""
    files = task_files(name, spec, names, orig, mutant, tests)
    rel = names["file"].removeprefix("workspace/")
    instruction = instruction_for(rel)
    quoted = "\n>\n".join("\n".join("> " + line for line in para.splitlines()) for para in instruction.strip().split("\n\n"))
    rows = "\n".join(f"| `{t}` | {'fails while the flaw is there' if tests[t] else 'keeps the rest of the code working'} | Solidity |" for t in tests)
    files["instruction.md"] = instruction
    files["README.md"] = textwrap.dedent(f"""\
        # Restore a removed check in `{rel}`

        The agent finds and fixes a planted flaw in a Solidity source from a widely used MIT project, renamed so it is not recognisable. The flaw is {CLASSES[cls]}. The checks are that project's own Foundry tests, renamed the same way; the ones that fail on the planted version act as the exploit tests.

        ## The instruction

        {{QUOTED}}

        ## What it touches

        - Solidity: `{rel}`. The agent is not told which function holds the flaw.

        ## The checks

        | Check | What it proves | Area |
        | --- | --- | --- |
        | `compile` | the project still compiles | Solidity |
        {{ROWS}}

        ## Why it matters

        A missing or weakened check is among the most common findings in Solidity audits.

        ## Notes

        Generated by `experimental/taskgen/kind2.py` from {spec['repo']} {spec['ref']} ({spec['licence']}); names changed per task. The reference solution is the original file, renamed.
        """).replace("{QUOTED}", quoted).replace("{ROWS}", rows)
    files["task.toml"] = files["task.toml"].replace(
        'description = "Write back one deleted library function so the project\'s own Foundry tests pass."',
        'description = "Find and restore one removed or weakened check so the project\'s own Foundry tests pass."')
    return files


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE / "out2"))
    ap.add_argument("--work", default=str(HERE / "work2"))
    ap.add_argument("--per-source", type=int, default=15, help="30% of 50")
    ap.add_argument("--per-file", type=int, default=2)
    ap.add_argument("--sources", default=",".join(s["key"] for s in SOURCES))
    a = ap.parse_args()
    out = Path(a.out) / "build"
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"kind": 2, "tasks": [], "rejected": {}}
    seen_files = set()
    for spec in [s for s in SOURCES if s["key"] in a.sources.split(",")]:
        root = HERE / spec["root"]
        made = 0
        for test_file in sorted(str(p.relative_to(root)) for p in (root / spec["test"]).rglob("*.t.sol")):
            if made >= a.per_source:
                break
            files = closure(spec, root, test_file)
            if files is None or not re.findall(r"\bfunction\s+test\w*", (root / test_file).read_text()):
                continue
            direct = {resolve(spec, test_file, m.group(2)) for m in IMPORT.finditer((root / test_file).read_text())}
            targets = [f for f in files if f in direct and f.startswith(spec["src"] + "/") and "interface" not in f.lower()]
            for target in sorted(targets):
                if made >= a.per_source or (spec["key"], target) in seen_files:
                    continue
                kept, base, used = 0, None, set()
                for fn in sorted(functions((root / target).read_text()), key=lambda f: -f["lines"]):
                    if kept >= a.per_file or made >= a.per_source:
                        break
                    if fn["name"].rstrip("0123456789_") in used:  # overloads and numbered twins are near copies
                        continue
                    seed = f"k2:{spec['key']}:{test_file}:{target}:{fn['name']}:{fn['start']}"
                    try:
                        orig, _, names = build_task(spec, root, test_file, files, target, fn, seed)
                    except Exception as e:  # noqa: BLE001 - counted, not fatal
                        manifest["rejected"][seed] = f"build: {type(e).__name__}"
                        continue
                    tgt = names["file"]
                    found = [f for f in functions(orig[tgt]) if f["name"] == names["function"]]
                    if not found:
                        continue
                    base = validate.run(validate.layout(Path(a.work) / spec["key"], orig))  # renames differ per function
                    if not base["compiled"] or not all(c["passed"] for c in base["checks"].values()):
                        manifest["rejected"][seed] = f"renamed original does not pass: {base['reason']}"
                        continue
                    for cls, what, text in mutants(orig[tgt], found[0]):
                        mutant = {**orig, tgt: text}
                        r1 = validate.run(validate.layout(Path(a.work) / spec["key"], mutant))
                        failing = {k for k, c in r1["checks"].items() if not c["passed"]}
                        if not r1["compiled"] or not failing:
                            continue
                        name = re.sub(r"[^a-z0-9]+", "-", f"k2 {spec['key']} {hashlib.sha256((seed + what + text).encode()).hexdigest()[:8]} {cls}").strip("-")
                        tests_map = {t: t in failing for t in sorted(base["checks"])}
                        tf = kind2_files(name, spec, names, orig, mutant, tests_map, cls)
                        if any(AGENT_WORDS.search(t) for p, t in tf.items() if p == "instruction.md" or p.startswith("environment/")):
                            manifest["rejected"][seed + what] = "agent-visible text holds a word the lint forbids"
                            continue
                        d = out / name
                        if d.exists():
                            shutil.rmtree(d)
                        for p, t in tf.items():
                            (d / p).parent.mkdir(parents=True, exist_ok=True)
                            (d / p).write_text(t)
                        (d / "solution/solve.sh").chmod(0o755)
                        (d / "tests/test.sh").chmod(0o755)
                        manifest["tasks"].append({"name": name, "source": spec["repo"], "ref": spec["ref"], "licence": spec["licence"],
                                                  "test": test_file, "file": target, "function": fn["name"], "class": cls, "mutation": what,
                                                  "checks": len(base["checks"]), "failing_with_flaw": len(failing)})
                        print(f"TASK {name} {target}:{fn['name']} {cls}/{what} fails {len(failing)} of {len(base['checks'])}", flush=True)
                        kept += 1
                        made += 1
                        used.add(fn["name"].rstrip("0123456789_"))
                        break
                if kept:
                    seen_files.add((spec["key"], target))
        print(f"SOURCE {spec['key']} tasks {made}", flush=True)
    (Path(a.out) / "manifest-kind2.json").write_text(json.dumps(manifest, indent=1))
    print(f"KIND2 tasks {len(manifest['tasks'])}, rejected {len(manifest['rejected'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
