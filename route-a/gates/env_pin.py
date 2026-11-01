#!/usr/bin/env python
"""What a gate ran on, against the pinned venv. Reports drift; never stops a gate.

    env_pin.py OUT.json [--lock FILE]     write the record, print ENV_PIN OK or ENV_PIN DRIFT
    env_pin.py --fingerprint FILE...      one hash over the files, the driver and the installed packages
"""
import hashlib
import importlib.metadata as md
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
WATCH = ("torch", "transformers", "fla-core", "flash-linear-attention", "triton")


def driver():
    try:
        return subprocess.run(["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"], capture_output=True, text=True, timeout=30).stdout.strip().splitlines()[0]
    except Exception:
        return None


def installed():
    return {d.metadata["Name"].lower().replace("_", "-"): d.version for d in md.distributions()}


def locked(path):
    out = {}
    for line in Path(path).read_text().splitlines():
        if "==" in line and not line.startswith("#"):
            n, v = line.split("==", 1)
            out[n.strip().lower().replace("_", "-")] = v.strip()
    return out


def compare(have, want, drv, want_drv):
    d = [f"{n}: {have.get(n)}, pinned {v}" for n, v in sorted(want.items()) if have.get(n) != v]
    d += [f"{n}: installed, not pinned" for n in sorted(set(have) - set(want))]
    if want_drv and drv != want_drv:
        d.append(f"driver: {drv}, pinned {want_drv}")
    return d


def fingerprint(paths):
    h = hashlib.sha256()
    for p in paths:
        h.update(hashlib.sha256(Path(p).read_bytes()).digest())
    h.update(str(driver()).encode())
    h.update(json.dumps(sorted(installed().items())).encode())
    return h.hexdigest()


def main(a):
    if a[0] == "--fingerprint":
        print(fingerprint(a[1:]))
        return 0
    lock = a[a.index("--lock") + 1] if "--lock" in a else HERE / "venv.lock.txt"
    drv_file = HERE / "driver.lock"
    want_drv = drv_file.read_text().strip() if drv_file.exists() else None
    have, want, drv = installed(), locked(lock), driver()
    drift = compare(have, want, drv, want_drv)
    rec = {"versions": {n: have.get(n) for n in WATCH}, "driver": drv, "pinned_driver": want_drv, "drift": drift}
    Path(a[0]).write_text(json.dumps(rec, indent=1) + "\n")
    print("ENV_PIN", "DRIFT" if drift else "OK", json.dumps(rec["versions"]), "driver", drv, *drift[:6])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
