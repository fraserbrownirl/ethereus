"""Fine-tune data on a small generated sample: machine-made responses, loss flags, held-out smoke."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("transformers")
RV = Path(__file__).parents[1]
SMOKE = json.loads((RV / "scripts/smoke_questions.json").read_text())["questions"]


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    out = tmp_path_factory.mktemp("ft")
    r = subprocess.run([sys.executable, str(RV / "scripts/gen_ft_data.py"), "--scale", "0.02", "--workers", "2", "--out", str(out)],
                       capture_output=True, text=True, timeout=1500)
    assert r.returncode == 0, r.stderr[-2000:]
    rows = [json.loads(ln) for p in out.glob("*.jsonl") for ln in p.read_text().splitlines()]
    return rows, json.loads((out / "manifest.json").read_text())


def test_every_family_written(data):
    rows, man = data
    assert {r["family"] for r in rows} == {"abi", "u256", "create", "run", "files", "negative", "knowledge"}
    assert len(man["firmware_sha256"]) == 64


def test_loss_is_only_on_generated_tokens(data):
    for r in data[0]:
        for s in r["segments"]:
            assert s["train"] == (s["role"] in ("assistant_call", "assistant_final"))
        assert r["segments"][-1]["role"] == "assistant_final"


def test_tool_output_is_never_trained_and_final_restates_it(data):
    for r in data[0]:
        if r["family"] in ("abi", "u256", "create"):
            assert any(s["role"] == "tool_output" for s in r["segments"])
            assert r["tool_outputs"][-1].splitlines()[0] in r["messages"][-1]["content"]


def test_run_final_reads_return_not_gas(data):
    for r in data[0]:
        if r["family"] == "run":
            lines = r["tool_outputs"][-1].splitlines()
            assert lines[0] in r["messages"][-1]["content"]
            assert len(lines) < 2 or f"which is {lines[1]}." not in r["messages"][-1]["content"] or lines[0].endswith(f"{int(lines[1]):x}")


def test_negatives_call_nothing(data):
    for r in data[0]:
        if r["family"] == "negative":
            assert all(s["role"] != "assistant_call" for s in r["segments"])


def test_knowledge_answers_appear_in_the_tool_response(data):
    for r in data[0]:
        if r["family"] in ("files", "knowledge"):
            assert r["answer"] in r["messages"][-2]["content"]


def test_smoke_questions_are_held_out(data):
    asked = {m["content"].lower() for r in data[0] for m in r["messages"] if m["role"] == "user"}
    for q in SMOKE:
        assert q["q"].lower() not in asked
    for r in data[0]:
        if r["family"] in ("files", "knowledge"):
            assert all(q["answer"].lower() not in r["answer"].lower() for q in SMOKE)
