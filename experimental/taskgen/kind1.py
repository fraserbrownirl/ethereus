#!/usr/bin/env python3
"""Kind 1: write a function so the source project's own Foundry tests pass.

Source: OpenZeppelin Contracts v5.7.0 (MIT) and its test/**/*.t.sol. Each candidate empties one function body
in the library file a test file covers. A candidate becomes a task only when, in the verifier's layout,
the original passes every test and the emptied file fails at least one (validate.py). Harbor on x86 is the
final proof (docs/taskgen-spec.md).

    kind1.py --out out --work work [--max-per-file 4] [--limit 0]
"""

import argparse
import json
import re
import shutil
import textwrap
from pathlib import Path

import validate

HERE = Path(__file__).parent
OZ = HERE / "sources/oz"
OZ_TAG = "v5.7.0"
IMAGE = "ghcr.io/buidlguidl/ethevals-chain:inputs-d0ef1955a98ec678"  # the pin ETH Evals' own contract tasks use
LIB = "lib/openzeppelin-contracts"
HOLLOW = '{\n        revert("unimplemented");\n    }'


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
    """Every function with a body: name, the body's [start, end) offsets, its line count."""
    clean = strip_comments(src)
    found = []
    for m in re.finditer(r"\bfunction\s+([A-Za-z_]\w*)\s*\(", clean):
        depth, i = 1, m.end()
        while i < len(clean) and depth:  # the parameter list
            depth += {"(": 1, ")": -1}.get(clean[i], 0)
            i += 1
        j = i
        while j < len(clean) and clean[j] not in "{;":
            j += 1
        if j >= len(clean) or clean[j] == ";":
            continue  # declaration only
        depth, k = 0, j
        while k < len(clean):
            depth += {"{": 1, "}": -1}.get(clean[k], 0)
            k += 1
            if depth == 0:
                break
        header = clean[m.start():j]
        found.append({"name": m.group(1), "start": j, "end": k, "lines": src.count("\n", j, k),
                      "header": " ".join(src[m.start():j].split()), "pure_or_view": bool(re.search(r"\b(pure|view)\b", header))})
    return found


def rewrite_test(text: str, contract_rel: str) -> str | None:
    """Imports pointed at the verifier's layout. None if the test needs files beyond the library and forge-std."""
    out = []
    for line in text.splitlines():
        m = re.match(r'(\s*import\s+(?:[^"]*\s+from\s+)?)"([^"]+)"(.*)', line)
        if m:
            path = m.group(2)
            if path.startswith("@openzeppelin/contracts/"):
                path = f"../workspace/{LIB}/contracts/" + path.removeprefix("@openzeppelin/contracts/")
            elif path.startswith("forge-std/"):
                pass
            else:
                return None
            line = f'{m.group(1)}"{path}"{m.group(3)}'
        out.append(line)
    return "\n".join(out) + "\n"


def test_names(text: str) -> list[str]:
    return re.findall(r"\bfunction\s+(test\w*)\s*\(", text)


def task_files(name, rel, fn, original, hollowed, test_rel, test_text, failing, tests):
    """The Harbor folder: path -> text."""
    short = rel.removeprefix("contracts/")
    instruction = textwrap.dedent(f"""\
        In our copy of OpenZeppelin Contracts, the body of `{fn['name']}` in `{LIB}/{rel}` has been deleted and the function now just reverts. Write it back.

        Keep the signature, the visibility and the NatSpec as they are, and change nothing else in the library. The function should do exactly what its documentation and the rest of the file expect of it, including its edge cases.
        """)
    quoted = "\n>\n".join("\n".join("> " + line for line in para.splitlines()) for para in instruction.strip().split("\n\n"))
    rows = "\n".join(f"| `{t}` | {'fails until the function is written back' if t in failing else 'keeps the rest of the library working'} | Solidity |"
                     for t in tests)
    readme = textwrap.dedent(f"""\
        # Write back `{fn['name']}` in OpenZeppelin's `{Path(rel).name}`

        The agent restores one function of OpenZeppelin Contracts {OZ_TAG}. The checks are OpenZeppelin's own Foundry tests for that file, so the function must behave exactly as the project specifies.

        ## The instruction

        {{QUOTED}}

        ## What it touches

        - Solidity: `{fn['header']}`, in `{short}`. Its documentation and its callers in the same file define the behaviour; the tests also probe edge cases the documentation states only briefly.

        ## The checks

        | Check | What it proves | Area |
        | --- | --- | --- |
        | `compile` | the library still compiles | Solidity |
        {{ROWS}}

        ## Why it matters

        OpenZeppelin Contracts is the most deployed Solidity library; code that reimplements one of its functions has to match it exactly, edge cases included.

        ## Notes

        Generated by `experimental/taskgen/kind1.py` from OpenZeppelin Contracts {OZ_TAG} (MIT). The reference solution is the original function.
        """).replace("{QUOTED}", quoted).replace("{ROWS}", rows)
    toml = textwrap.dedent(f"""\
        schema_version = "1.3"
        artifacts = ["/workspace"]

        [task]
        name = "ethereus/{name}"
        description = "Write back OpenZeppelin's {fn['name']} in {Path(rel).name} so the project's own tests pass."
        authors = [{{ name = "Ethereus task generator" }}]
        keywords = ["ethereum", "solidity", "openzeppelin"]

        [metadata]
        pillar = "build"
        layer = "contracts"
        modes = ["internet", "skills"]

        [verifier]
        environment_mode = "separate"
        user = "root"

        [environment]
        cpus = 1
        memory_mb = 2048
        """)
    docker = textwrap.dedent(f"""\
        FROM {IMAGE}
        WORKDIR /workspace
        COPY --chown=agent:agent foundry.toml /workspace/foundry.toml
        RUN git clone --depth 1 --branch {OZ_TAG} https://github.com/OpenZeppelin/openzeppelin-contracts.git /workspace/{LIB} \\
            && rm -rf /workspace/{LIB}/.git /workspace/{LIB}/test \\
            && chown -R agent:agent /workspace/lib
        COPY --chown=agent:agent {Path(rel).name} /workspace/{LIB}/{rel}
        ENTRYPOINT []
        CMD ["sleep", "infinity"]
        """)
    foundry = '[profile.default]\nsrc = "src"\nlibs = ["lib"]\nremappings = ["@openzeppelin/contracts/=lib/openzeppelin-contracts/contracts/"]\n'
    solve = f"#!/bin/sh\nset -eu\ncat > /workspace/{LIB}/{rel} <<'SOLIDITY_EOF'\n{original}SOLIDITY_EOF\n"
    test_sh = "#!/bin/sh\nset -eu\npython3 /opt/forge_checks.py\ncp /opt/check_records.py /tests/check_records.py\nrewardkit /tests\n"
    reward = '[scoring.check_records]\naggregation = "all-pass"\n\n[[reward]]\nname = "reward"\naggregation = "all-pass"\n'
    return {"task.toml": toml, "README.md": readme, "instruction.md": instruction, "environment/Dockerfile": docker,
            "environment/foundry.toml": foundry, f"environment/{Path(rel).name}": hollowed, "solution/solve.sh": solve,
            f"tests/{Path(test_rel).name}": test_text, "tests/test.sh": test_sh, "tests/reward.toml": reward}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE / "out"))
    ap.add_argument("--work", default=str(HERE / "work"))
    ap.add_argument("--max-per-file", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0, help="stop after this many validated tasks")
    ap.add_argument("--only", default="", help="comma-separated test files, relative to test/")
    a = ap.parse_args()
    out, work = Path(a.out) / "build", Path(a.work)
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"kind": 1, "source": f"OpenZeppelin/openzeppelin-contracts {OZ_TAG}", "licence": "MIT", "tasks": [], "rejected": {}}
    tests = sorted((OZ / "test").rglob("*.t.sol"))
    if a.only:
        tests = [OZ / "test" / t for t in a.only.split(",")]
    made = 0
    for tpath in tests:
        test_rel = str(tpath.relative_to(OZ))
        rel = "contracts/" + str(tpath.relative_to(OZ / "test")).removesuffix(".t.sol") + ".sol"
        if not (OZ / rel).exists():
            manifest["rejected"][test_rel] = "no library file of the same name"
            continue
        test_text = rewrite_test(tpath.read_text(), rel)
        if test_text is None:
            manifest["rejected"][test_rel] = "imports beyond the library and forge-std"
            continue
        names = test_names(test_text)
        original = (OZ / rel).read_text()
        base = validate.forge_layout(work / "base", OZ, LIB, {}, {Path(test_rel).name: test_text})
        r0 = validate.run(base)
        if not r0["compiled"] or not r0["checks"] or not all(c["passed"] for c in r0["checks"].values()):
            manifest["rejected"][test_rel] = f"original does not pass: {r0['reason'] or [k for k, c in r0['checks'].items() if not c['passed']][:5]}"
            continue
        kept = 0
        for fn in sorted(functions(original), key=lambda f: -f["lines"]):
            if kept >= a.max_per_file or (a.limit and made >= a.limit):
                break
            if fn["lines"] < 2:
                continue
            hollowed = original[:fn["start"]] + HOLLOW + original[fn["end"]:]
            layout = validate.forge_layout(work / "cand", OZ, LIB, {rel: hollowed}, {Path(test_rel).name: test_text})
            r = validate.run(layout)
            key = f"{rel}:{fn['name']}"
            if not r["compiled"]:
                manifest["rejected"][key] = f"emptied file does not compile: {r['reason']}"
                continue
            failing = sorted(k for k, c in r["checks"].items() if not c["passed"])
            if not failing:
                manifest["rejected"][key] = "no test fails when the body is gone"
                continue
            name = f"oz-{Path(rel).stem.lower()}-{fn['name'].lower()}"
            if (out / name).exists():
                name += f"-{fn['start']}"
            files = task_files(name, rel, fn, original, hollowed, test_rel, test_text, set(failing), names)
            d = out / name
            if d.exists():
                shutil.rmtree(d)
            for p, text in files.items():
                (d / p).parent.mkdir(parents=True, exist_ok=True)
                (d / p).write_text(text)
            (d / "solution/solve.sh").chmod(0o755)
            (d / "tests/test.sh").chmod(0o755)
            manifest["tasks"].append({"name": name, "file": rel, "function": fn["name"], "tests": len(names),
                                      "failing_without_it": len(failing), "local_prefilter": "pass"})
            kept += 1
            made += 1
            print(f"TASK {name} fails {len(failing)} of {len(names)} without it", flush=True)
        if a.limit and made >= a.limit:
            break
    (Path(a.out) / "manifest-kind1.json").write_text(json.dumps(manifest, indent=1))
    print(f"KIND1 tasks {made}, rejected {len(manifest['rejected'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
