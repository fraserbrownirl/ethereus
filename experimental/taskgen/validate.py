"""Local pre-filter in the verifier's layout: root/{workspace, tests, foundry.toml}, as ETH Evals' forge_checks.py
builds it. Not the proof: Harbor on x86 runs each task's reference solution for that (docs/taskgen-spec.md)."""

import json
import re
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).parent
FORGE_STD = HERE / "sources/forge-std/src"
VERIFY = '[profile.default]\nsrc = "tests"\ntest = "tests"\nlibs = []\nffi = false\nauto_detect_remappings = false\nauto_detect_solc = true\n'


def forge_layout(root: Path, oz: Path, lib: str, overrides: dict[str, str], tests: dict[str, str]) -> Path:
    """A persistent layout under root, so Forge's cache carries between candidates. Overrides are applied and the
    previous run's are undone, so the library is the original except for these files."""
    contracts = root / "workspace" / lib / "contracts"
    if not contracts.exists():
        contracts.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(oz / "contracts", contracts)
    state = root / ".overrides.json"
    for rel in json.loads(state.read_text()) if state.exists() else []:
        shutil.copyfile(oz / rel, root / "workspace" / lib / rel)
    for rel, text in overrides.items():
        (root / "workspace" / lib / rel).write_text(text)
    state.write_text(json.dumps(list(overrides)))
    t = root / "tests"
    if t.exists():
        shutil.rmtree(t)
    t.mkdir(parents=True)
    for name, text in tests.items():
        (t / name).write_text(text)
    remaps = [f"workspace/:@openzeppelin/contracts/=workspace/{lib}/contracts/", "workspace/:src/=workspace/src/",
              f"forge-std/={FORGE_STD}/"]
    (root / "foundry.toml").write_text(VERIFY + "remappings = " + json.dumps(remaps) + "\n")
    return root


def run(root: Path, timeout: int = 1800) -> dict:
    """{compiled, reason, checks: {name: {passed, reason}}}, the way forge_checks.py reads Forge's JSON."""
    try:
        r = subprocess.run(["forge", "test", "--json", "--no-storage-caching"], cwd=root, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"compiled": False, "reason": f"forge timed out after {timeout} s", "checks": {}}
    checks = {}
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict):
        for suite, result in data.items():
            for name, test in (result.get("test_results") or {}).items():
                passed = test["status"] == "Success"
                checks[name.partition("(")[0]] = {"passed": passed, "reason": "" if passed else " ".join(str(test.get("reason") or test["status"]).split())}
    reason = None
    if not checks:
        lines = (r.stdout + "\n" + r.stderr).splitlines()
        reason = next((ln.strip() for ln in lines if re.match(r"^\s*(?:Compiler)?Error", ln)), (r.stderr or r.stdout).strip()[:300])
    return {"compiled": bool(checks), "reason": reason, "checks": checks}
