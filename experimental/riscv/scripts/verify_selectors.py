"""Verify env/eth/selectors.txt against 4byte.directory (one-time QA + reuse).

For each signature: keccak -> selector -> 4byte reverse lookup must list the
signature. Typos here would poison the DROM cache (a wrong cached selector
shadows the keccak fallthrough), so every line must verify or be dropped.

Usage (needs network): .venv/bin/python scripts/verify_selectors.py
"""

import json
import sys
import time
import urllib.request
from pathlib import Path

from eth_hash.auto import keccak


def sigs(path: Path) -> list[str]:
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def main() -> int:
    root = Path(__file__).parents[1]
    lines = sigs(root / "chipformer" / "rv" / "env" / "eth" / "selectors.txt")
    print(f"{len(lines)} signatures to verify")
    bad: list[str] = []
    for i, sig in enumerate(lines):
        sel = "0x" + keccak(sig.encode()).hex()[:8]
        texts: list[str] = []
        for endpoint in ("signatures", "event-signatures"):
            url = f"https://www.4byte.directory/api/v1/{endpoint}/?hex_signature={sel}"
            req = urllib.request.Request(url, headers={"User-Agent": "ethereus-build/1.0"})
            data = None
            for attempt in range(3):
                try:
                    with urllib.request.urlopen(req, timeout=20) as r:
                        data = json.load(r)
                    break
                except Exception as e:  # noqa: BLE001
                    if attempt == 2:
                        print(f"  ERROR {sig} [{endpoint}]: {e}")
                    else:
                        time.sleep(3)
            if data is None:
                break
            texts += [x["text_signature"] for x in data.get("results", [])]
            time.sleep(0.3)
        else:
            if sig not in texts:
                print(f"  MISS {sig} ({sel}): 4byte has {texts[:3]}")
                bad.append(sig)
            if (i + 1) % 50 == 0:
                print(f"  ... {i + 1}/{len(lines)}")
            continue
        bad.append(sig)
        time.sleep(0.3)
        if (i + 1) % 50 == 0:
            print(f"  ... {i + 1}/{len(lines)}")
    print(f"{len(lines) - len(bad)}/{len(lines)} verified")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
