#!/usr/bin/env python3
"""Copy secrets from the repo's .env to the eval VM. Prints names only, never values.

    python3 route-a/gates/push_secrets.py

Empty values are skipped, so what is already on the VM stays.
"""
import json
import os
import pathlib
import subprocess
import sys

FILES = {"ANTHROPIC_API_KEY": "anthropic.key", "MAINNET_RPC_URL": "mainnet_rpc"}
# The eval VM's address lives in config/vm.json (gitignored; copy config/vm.example.json and fill it in).
VM = json.loads((pathlib.Path(__file__).resolve().parents[2] / "config/vm.json").read_text())
SSH = ["ssh", "-i", os.path.expanduser(VM["key"]), "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=20",
       "-o", "BatchMode=yes", "-p", str(VM["port"]), f"{VM['user']}@{VM['host']}"]

env = pathlib.Path(__file__).resolve().parents[2] / ".env"
values = {}
for line in env.read_text().splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        k, v = line.split("=", 1)
        values[k.strip()] = v.strip().strip('"').strip("'")
for name, target in FILES.items():
    if not values.get(name):
        print(f"{name}: empty, skipped")
        continue
    cmd = f"umask 077; mkdir -p /root/.harness; cat > /root/.harness/{target}"
    r = subprocess.run(SSH + [cmd], input=(values[name] + "\n").encode(), capture_output=True)
    print(f"{name}: {'copied to the VM' if r.returncode == 0 else 'FAILED, ssh exit ' + str(r.returncode)}")
