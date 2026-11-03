"""pytest plugin: point every eth-firmware test at NEW_ELF_DIR/{eth,eth-bridge}.elf without
touching tests/data/rv (the tracked images stay as they are)."""
import os
from pathlib import Path
D = Path(os.environ["NEW_ELF_DIR"])
def pytest_configure(config):
    from chipformer.rv import eth
    eth.ELF = D / "eth.elf"
    eth.BRIDGE_ELF = D / "eth-bridge.elf"
    print(f"[newelf] eth.ELF={eth.ELF} BRIDGE_ELF={eth.BRIDGE_ELF}")
def pytest_collection_modifyitems(session, config, items):
    seen = set()
    for it in items:
        mod = getattr(it, "module", None)
        if mod is None or mod in seen: continue
        seen.add(mod)
        for name in ("ELF", "BRIDGE"):
            v = getattr(mod, name, None)
            if v is None: continue
            s = str(v)
            if s.endswith("eth-bridge.elf"): setattr(mod, name, D / "eth-bridge.elf")
            elif s.endswith("eth.elf"): setattr(mod, name, D / "eth.elf")
            else: continue
            print(f"[newelf] {mod.__name__}.{name} -> {getattr(mod, name)}")
