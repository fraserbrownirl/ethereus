#!/usr/bin/env python3
"""Kind 1: write a function so the source project's own Foundry tests pass.

Fraser, 2026-11-02: several MIT sources, none over 30% of the tasks; contracts, functions and their parameters
renamed per task so the original is not recognisable; the agent runs with no network.

For a test file, the files it imports are copied: project sources into workspace/src, test helpers into tests/.
One function in one of those sources is emptied. The library holding it, the function and its parameters get
new names, and comment lines that name the source project are dropped. A candidate counts only when, in the
verifier's layout, the renamed original passes every test and the emptied file fails at least one
(validate.py). Harbor's free-check on x86 is the proof (docs/taskgen-spec.md). No copy of the original is in
the agent's container: only the copied, renamed, emptied files.

    kind1.py --out out --work work [--per-source 15] [--per-file 3]
"""

import argparse
import hashlib
import json
import posixpath
import re
import shutil
import textwrap
from pathlib import Path

import validate

HERE = Path(__file__).parent
IMAGE = "ghcr.io/buidlguidl/ethevals-chain:inputs-d0ef1955a98ec678"  # the pin ETH Evals' own contract tasks use
HOLLOW = '{\n        revert("unimplemented");\n    }'

SOURCES = [
    {"key": "oz", "repo": "OpenZeppelin/openzeppelin-contracts", "ref": "v5.7.0", "licence": "MIT", "root": "sources/oz",
     "src": "contracts", "test": "test", "remap": {"@openzeppelin/contracts/": "contracts/"}},
    {"key": "solady", "repo": "Vectorized/solady", "ref": "258f67c", "licence": "MIT", "root": "sources/solady",
     "src": "src", "test": "test", "remap": {}},
    {"key": "prb", "repo": "PaulRBerg/prb-math", "ref": "v4.1.0", "licence": "MIT", "root": "sources/prb-math",
     "src": "src", "test": "test", "remap": {"src/": "src/"}},
    # solmate (AGPL) is not fetched, so a test that needs it fails to resolve and is dropped: no AGPL code in a task.
    {"key": "dragonfly", "repo": "dragonfly-xyz/useful-solidity-patterns", "ref": "c989fc2", "licence": "MIT",
     "root": "sources/dragonfly", "src": "patterns", "test": "test", "remap": {}},
]

# Comment lines that name a source project are dropped from every copied file.
PROVENANCE = re.compile(r"openzeppelin|solady|vectorized|prb|razvan|dragonfly|github\.com|@author|@custom:security-contact", re.I)
# Function names too common to rename safely: a global rename would also hit builtins and other contracts.
STOP = {"encode", "decode", "push", "pop", "length", "call", "transfer", "send", "balance", "max", "min", "add", "sub",
        "mul", "div", "mod", "log", "set", "get", "remove", "contains", "at", "keys", "values", "toString"}
ADJ = ["Amber", "Basalt", "Cobalt", "Dune", "Ember", "Flint", "Garnet", "Harbor", "Indigo", "Juniper", "Kestrel", "Lumen",
       "Marble", "Nimbus", "Onyx", "Pebble", "Quartz", "Russet", "Slate", "Tidal", "Umber", "Vellum", "Willow", "Zephyr"]
NOUN = ["Ledger", "Kit", "Toolkit", "Helpers", "Codec", "Ops", "Works", "Utils", "Lib", "Bench", "Forge", "Mill"]
VERB = ["derive", "compute", "resolve", "settle", "render", "pack", "fold", "shape", "trace", "carve", "blend", "sift"]
PARAMS = ["alpha", "beta", "gamma", "delta", "omega", "sigma", "kappa", "theta", "lambda_", "rho", "tau", "phi"]


def pick(seed: str, words: list[str], n: int = 1) -> list[str]:
    h = hashlib.sha256(seed.encode()).digest()
    return [words[h[i] % len(words)] for i in range(n)]


def strip_comments(src: str) -> str:
    """Same length as src, with comments and string contents blanked, so offsets still line up."""
    out, i, n = list(src), 0, len(src)
    while i < n:
        if src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j < 0 else j
            out[i:j] = " " * (j - i)
            i = j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out[i:j] = [c if c == "\n" else " " for c in src[i:j]]
            i = j
        elif src[i] in "\"'":
            q, j = src[i], i + 1
            while j < n and src[j] != q:
                j += 2 if src[j] == "\\" else 1
            out[i + 1:j] = " " * (j - i - 1)
            i = j + 1
        else:
            i += 1
    return "".join(out)


def functions(src: str) -> list[dict]:
    """Every function with a body: name, header, the body's [start, end) offsets, its line count, parameter names."""
    clean = strip_comments(src)
    found = []
    for m in re.finditer(r"\bfunction\s+([A-Za-z_]\w*)\s*\(", clean):
        depth, i = 1, m.end()
        while i < len(clean) and depth:
            depth += {"(": 1, ")": -1}.get(clean[i], 0)
            i += 1
        params = clean[m.end():i - 1]
        j = i
        while j < len(clean) and clean[j] not in "{;":
            j += 1
        if j >= len(clean) or clean[j] == ";":
            continue
        depth, k = 0, j
        while k < len(clean):
            depth += {"{": 1, "}": -1}.get(clean[k], 0)
            k += 1
            if depth == 0:
                break
        header = clean[m.start():j]
        rets = re.search(r"returns\s*\((.*)\)", header, re.S)
        names = [p.split()[-1] for p in (params + "," + (rets.group(1) if rets else "")).split(",")
                 if len(p.split()) >= 2 and re.fullmatch(r"[A-Za-z_]\w*", p.split()[-1])
                 and p.split()[-1] not in ("memory", "calldata", "storage", "payable")]
        found.append({"name": m.group(1), "fstart": m.start(), "start": j, "end": k, "lines": src.count("\n", j, k),
                      "header": " ".join(src[m.start():j].split()), "params": names})
    return found


def resolve(spec: dict, importer: str, path: str) -> str | None:
    """A repo-relative path for an import, or None for forge-std, which the verifier provides."""
    if path.startswith(("forge-std/", "ds-test/")):
        return None
    if path.startswith("."):
        return posixpath.normpath(posixpath.join(posixpath.dirname(importer), path))
    for prefix, target in sorted(spec["remap"].items(), key=lambda kv: -len(kv[0])):
        if path.startswith(prefix):
            return target + path[len(prefix):]
    return path


IMPORT = re.compile(r'(\bimport\s+(?:[^";]*?\s+from\s+)?)"([^"]+)"')


def closure(spec: dict, root: Path, start: str) -> list[str] | None:
    """Every repo file the test imports, transitively; None if one is missing."""
    seen, todo = [], [start]
    while todo:
        f = todo.pop()
        if f in seen:
            continue
        if not (root / f).exists():
            return None
        seen.append(f)
        for m in IMPORT.finditer((root / f).read_text()):
            r = resolve(spec, f, m.group(2))
            if r is not None:
                todo.append(r)
    return seen


def placed(spec: dict, f: str, rename_file: dict) -> str:
    """Where a repo file lands in the verifier's layout."""
    f = rename_file.get(f, f)
    if f.startswith(spec["src"] + "/"):
        return "workspace/src/" + f[len(spec["src"]) + 1:]
    if f.startswith(spec["test"] + "/"):
        return "tests/" + f[len(spec["test"]) + 1:]
    return "workspace/vendor/" + f


def rewrite_imports(spec: dict, f: str, text: str, rename_file: dict) -> str:
    here = posixpath.dirname(placed(spec, f, rename_file))

    def sub(m):
        path = m.group(2)
        r = resolve(spec, f, path)
        if r is None:
            return m.group(1) + '"' + path.replace("forge-std/src/", "forge-std/") + '"'
        rel = posixpath.relpath(placed(spec, r, rename_file), here)
        return m.group(1) + '"' + (rel if rel.startswith(".") else "./" + rel) + '"'
    return IMPORT.sub(sub, text)


def scrub(text: str) -> str:
    """Drop comment lines that name the source project; keep the SPDX line."""
    out = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(("//", "*", "/*")) and "SPDX" not in s and PROVENANCE.search(s):
            continue
        out.append(line)
    return "\n".join(out) + "\n"


def contract_names(text: str) -> list[str]:
    return re.findall(r"\b(?:contract|library|abstract\s+contract)\s+([A-Za-z_]\w*)", strip_comments(text))


def build_task(spec, root, test_file, files, target, fn, seed):
    """{path in the verifier's layout: text} for the renamed original and for the emptied version, plus names."""
    adj, noun = pick(seed, ADJ, 2)[0], pick(seed + "n", NOUN)[0]
    libs = contract_names((root / target).read_text())
    lib_map = {old: f"{pick(seed + old, ADJ)[0]}{pick(seed + old + 'n', NOUN)[0]}" for old in libs}
    new_fn = fn["name"]
    if fn["name"] not in STOP and len(fn["name"]) > 3:
        new_fn = pick(seed + "v", VERB)[0] + fn["name"].lstrip("_")[:1].upper() + hashlib.sha256((seed + fn["name"]).encode()).hexdigest()[:3]
        if fn["name"].startswith("_"):
            new_fn = "_" + new_fn
    new_file = posixpath.join(posixpath.dirname(target), (lib_map.get(Path(target).stem) or f"{adj}{noun}") + ".sol")
    rename_file = {target: new_file}

    def rename(text: str, f: str) -> str:
        for old, new in lib_map.items():
            text = re.sub(rf"\b{old}\b", new, text)
            text = re.sub(rf"\b{old}(?=\w*Test\b)", new, text)  # test contracts named after the library
        if new_fn != fn["name"]:
            old = re.escape(fn["name"])
            # Names this file reaches the target by: renamed libraries, and aliases of an import of the target file
            # (free functions, as in `import "./Helpers.sol" as Helpers` or `import * as Helpers from ...`).
            aliases, imports_target = list(lib_map.values()), False
            for m in IMPORT.finditer(text):
                if resolve(spec, f, m.group(2)) == target:
                    imports_target = True
                    al = re.search(r"\bas\s+([A-Za-z_]\w*)", m.group(0) + text[m.end():m.end() + 40].split(";")[0])
                    if al:
                        aliases.append(al.group(1))
            qual = "|".join(map(re.escape, aliases))
            if qual:
                text = re.sub(rf"\b({qual})\.{old}\b", rf"\1.{new_fn}", text)
            if imports_target:  # `import {sqrt} from "..."` and its direct calls
                text = re.sub(rf"(import\s*\{{[^}}]*?)\b{old}\b", rf"\g<1>{new_fn}", text)
            if f == target or imports_target or (qual and re.search(rf"\busing\s+({qual})\b", text)):
                text = re.sub(rf"(?<![\w.]){re.escape(fn['name'])}(?=\s*\()", new_fn, text)
                text = re.sub(rf"\.{re.escape(fn['name'])}(?=\s*\()", f".{new_fn}", text)
        return text

    out_orig, out_hollow = {}, {}
    for f in files:
        text = (root / f).read_text()
        if f == target:
            pmap = dict(zip(fn["params"], pick(seed + "p", PARAMS, len(fn["params"]))))
            pmap = {k: v + str(i) for i, (k, v) in enumerate(pmap.items()) if len(k) > 1}
            body = text[fn["fstart"]:fn["end"]]
            doc_start = text.rfind("\n", 0, text.rfind("function", 0, fn["fstart"] + 1)) + 1
            prev = text[:fn["fstart"]]
            natspec_start = max(prev.rfind("/**"), prev.rfind("///")) if prev.rstrip().endswith(("*/",)) or prev.rstrip().split("\n")[-1].strip().startswith("///") else fn["fstart"]
            natspec_start = min(natspec_start, fn["fstart"]) if natspec_start >= 0 else fn["fstart"]
            seg = text[natspec_start:fn["end"]]
            hollow_seg = text[natspec_start:fn["start"]] + HOLLOW  # emptied before renaming, so the cut is exact
            for old, new in pmap.items():
                seg = re.sub(rf"\b{re.escape(old)}\b", new, seg)
                hollow_seg = re.sub(rf"\b{re.escape(old)}\b", new, hollow_seg)
            orig_text = text[:natspec_start] + seg + text[fn["end"]:]
            hollow_text = text[:natspec_start] + hollow_seg + text[fn["end"]:]
            del body, doc_start
        else:
            orig_text = hollow_text = text
        for which, t, store in (("o", orig_text, out_orig), ("h", hollow_text, out_hollow)):
            t = rename(t, f)
            t = rewrite_imports(spec, f, t, rename_file)
            t = scrub(t)
            store[placed(spec, f, rename_file)] = t
    return out_orig, out_hollow, {"lib_map": lib_map, "function": new_fn, "file": placed(spec, target, rename_file)}


def task_files(name, spec, names, orig, hollow, tests):
    target = names["file"]
    rel = target.removeprefix("workspace/")
    fn = names["function"]
    instruction = textwrap.dedent(f"""\
        The body of `{fn}` in `{rel}` was deleted and the function now just reverts. Write it back.

        Keep its signature, visibility and documentation as they are, and change nothing else in the project. It should do exactly what its documentation and the rest of the code expect of it, edge cases included. `forge build` works in the project; there is no network access.
        """)
    quoted = "\n>\n".join("\n".join("> " + line for line in para.splitlines()) for para in instruction.strip().split("\n\n"))
    rows = "\n".join(f"| `{t}` | {'fails until the function is written back' if tests[t] else 'keeps the rest of the code working'} | Solidity |" for t in tests)
    readme = textwrap.dedent(f"""\
        # Write back `{fn}`

        The agent restores one function in a Solidity library from a widely used MIT project, renamed so it is not recognisable. The checks are that project's own Foundry tests for it, renamed the same way.

        ## The instruction

        {{QUOTED}}

        ## What it touches

        - Solidity: `{fn}` in `{rel}`. Its documentation and its callers define the behaviour; the tests also probe edge cases the documentation states only briefly.

        ## The checks

        | Check | What it proves | Area |
        | --- | --- | --- |
        | `compile` | the project still compiles | Solidity |
        {{ROWS}}

        ## Why it matters

        Reimplementing a library function exactly, from its documentation and its tests' expectations, is daily work in Solidity.

        ## Notes

        Generated by `experimental/taskgen/kind1.py` from {spec['repo']} {spec['ref']} ({spec['licence']}); names changed per task. The reference solution is the original function, renamed.
        """).replace("{QUOTED}", quoted).replace("{ROWS}", rows)
    toml = textwrap.dedent(f"""\
        schema_version = "1.3"
        artifacts = ["/workspace"]

        [task]
        name = "ethereus/{name}"
        description = "Write back one deleted library function so the project's own Foundry tests pass."
        authors = [{{ name = "Ethereus task generator" }}]
        keywords = ["ethereum", "solidity"]

        [metadata]
        pillar = "build"
        layer = "contracts"
        modes = ["internet", "skills"]

        [agent]
        network_mode = "no-network"

        [verifier]
        environment_mode = "separate"
        user = "root"

        [environment]
        cpus = 1
        memory_mb = 2048
        """)
    workspace = {p.removeprefix("workspace/"): t for p, t in hollow.items() if p.startswith("workspace/")}
    docker = textwrap.dedent(f"""\
        FROM {IMAGE}
        WORKDIR /workspace
        COPY --chown=agent:agent workspace /workspace
        RUN cd /workspace && forge build && forge clean
        ENTRYPOINT []
        CMD ["sleep", "infinity"]
        """)
    foundry = '[profile.default]\nsrc = "src"\nlibs = ["vendor"]\nauto_detect_solc = true\n'
    solve = "#!/bin/sh\nset -eu\n" + f"cat > /workspace/{rel} <<'SOLIDITY_EOF'\n{orig[target]}SOLIDITY_EOF\n"
    test_sh = "#!/bin/sh\nset -eu\npython3 /opt/forge_checks.py\ncp /opt/check_records.py /tests/check_records.py\nrewardkit /tests\n"
    reward = '[scoring.check_records]\naggregation = "all-pass"\n\n[[reward]]\nname = "reward"\naggregation = "all-pass"\n'
    files = {"task.toml": toml, "README.md": readme, "instruction.md": instruction, "environment/Dockerfile": docker,
             "environment/workspace/foundry.toml": foundry, "solution/solve.sh": solve, "tests/test.sh": test_sh, "tests/reward.toml": reward}
    files.update({f"environment/workspace/{p}": t for p, t in workspace.items()})
    files.update({p: t for p, t in hollow.items() if p.startswith("tests/")})
    return files


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE / "out"))
    ap.add_argument("--work", default=str(HERE / "work"))
    ap.add_argument("--per-source", type=int, default=15, help="30% of 50")
    ap.add_argument("--per-file", type=int, default=3)
    ap.add_argument("--sources", default=",".join(s["key"] for s in SOURCES))
    ap.add_argument("--only", default="", help="one test file, as source:path")
    a = ap.parse_args()
    out = Path(a.out) / "build"
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"kind": 1, "tasks": [], "rejected": {}}
    for spec in [s for s in SOURCES if s["key"] in a.sources.split(",")]:
        root = HERE / spec["root"]
        tests = sorted(str(p.relative_to(root)) for p in (root / spec["test"]).rglob("*.t.sol"))
        if a.only:
            k, _, p = a.only.partition(":")
            tests = [p] if k == spec["key"] else []
        made = 0
        for test_file in tests:
            if made >= a.per_source:
                break
            files = closure(spec, root, test_file)
            if files is None:
                manifest["rejected"][f"{spec['key']}:{test_file}"] = "an import does not resolve"
                continue
            if not re.findall(r"\bfunction\s+test\w*", (root / test_file).read_text()):
                continue
            direct = {resolve(spec, test_file, m.group(2)) for m in IMPORT.finditer((root / test_file).read_text())}
            targets = sorted((f for f in files if f.startswith(spec["src"] + "/") and "interface" not in f.lower()),
                             key=lambda f: (f not in direct, f))  # the library the test is about comes first
            kept = 0
            for target in targets:
                cands = sorted((fn for fn in functions((root / target).read_text()) if fn["lines"] >= 3), key=lambda f: -f["lines"])
                for fn in cands:
                    if kept >= a.per_file or made >= a.per_source:
                        break
                    seed = f"{spec['key']}:{test_file}:{target}:{fn['name']}:{fn['start']}"
                    key = f"{spec['key']}:{target}:{fn['name']}"
                    try:
                        orig, hollow, names = build_task(spec, root, test_file, files, target, fn, seed)
                    except Exception as e:  # noqa: BLE001 - a candidate that cannot be built is counted, not fatal
                        manifest["rejected"][key] = f"build: {type(e).__name__}"
                        continue
                    r0 = validate.run(validate.layout(Path(a.work) / spec["key"], orig))
                    if not r0["compiled"] or not all(c["passed"] for c in r0["checks"].values()):
                        manifest["rejected"][key] = f"renamed original does not pass: {r0['reason'] or [k for k, c in r0['checks'].items() if not c['passed']][:3]}"
                        continue
                    r1 = validate.run(validate.layout(Path(a.work) / spec["key"], hollow))
                    if not r1["compiled"]:
                        manifest["rejected"][key] = f"emptied version does not compile: {r1['reason']}"
                        continue
                    failing = {k for k, c in r1["checks"].items() if not c["passed"]}
                    if not failing:
                        manifest["rejected"][key] = "no test fails when the body is gone"
                        continue
                    name = re.sub(r"[^a-z0-9]+", "-", f"k1 {spec['key']} {hashlib.sha256(seed.encode()).hexdigest()[:8]} {names['function']}".lower()).strip("-")
                    d = out / name
                    if d.exists():
                        shutil.rmtree(d)
                    tests_map = {t: t in failing for t in sorted(r0["checks"])}
                    for p, text in task_files(name, spec, names, orig, hollow, tests_map).items():
                        (d / p).parent.mkdir(parents=True, exist_ok=True)
                        (d / p).write_text(text)
                    (d / "solution/solve.sh").chmod(0o755)
                    (d / "tests/test.sh").chmod(0o755)
                    manifest["tasks"].append({"name": name, "source": spec["repo"], "ref": spec["ref"], "licence": spec["licence"],
                                              "test": test_file, "file": target, "function": fn["name"], "renamed": names,
                                              "checks": len(r0["checks"]), "failing_without_it": len(failing)})
                    kept += 1
                    made += 1
                    print(f"TASK {name} {spec['key']} {target}:{fn['name']} fails {len(failing)} of {len(r0['checks'])}", flush=True)
        print(f"SOURCE {spec['key']} tasks {made}", flush=True)
    (Path(a.out) / "manifest-kind1.json").write_text(json.dumps(manifest, indent=1))
    print(f"KIND1 tasks {len(manifest['tasks'])}, rejected {len(manifest['rejected'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
