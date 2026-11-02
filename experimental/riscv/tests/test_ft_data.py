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
    assert {r["family"] for r in rows} == {"abi", "u256", "create", "run", "files", "negative", "knowledge", "run_limit"}
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


def test_prompt_variants_are_consistent(data):
    for r in data[0]:
        assert ("bash" in r["tools"]) == (r["prompt_variant"] != "plain")


def test_run_final_reads_return_not_gas(data):
    for r in data[0]:
        if r["family"] == "run":
            lines = r["tool_outputs"][-1].splitlines()
            assert lines[0] in r["messages"][-1]["content"]
            assert len(lines) < 2 or f"which is {lines[1]}." not in r["messages"][-1]["content"] or lines[0].endswith(f"{int(lines[1]):x}")


def test_spilled_rows_train_the_full_call_and_answer_on_the_rewritten_one(data):
    rows = [r for r in data[0] if r.get("spilled")]
    assert rows, "no row spilled"
    for r in rows:
        roles = [s["role"] for s in r["segments"]]
        assert "spill_prefill" in roles
        pre = roles.index("spill_prefill")
        assert not r["segments"][pre]["train"]
        assert len(r["segments"][pre + 1]["ids"]) == 1 and r["segments"][pre + 1]["train"]  # </tool_call>
        call = r["call_segments"][-1]
        assert call["role"] == "assistant_call" and call["train"] and len(call["ids"]) - 2 > 80
        assert all(not s["train"] for s in r["call_segments"][:-1])
        assert "@spill/" in r["messages"][-3]["tool_calls"][0]["function"]["arguments"]["cmd"]
        assert not r["tool_outputs"][-1].startswith("error")


def test_run_limit_rows_call_nothing_and_say_why(data):
    rows = [r for r in data[0] if r["family"] == "run_limit"]
    assert rows
    for r in rows:
        assert all(s["role"] != "assistant_call" for s in r["segments"])
        assert "built-in" in r["messages"][-1]["content"] and "`run`" in r["messages"][-1]["content"]


def test_negatives_call_nothing(data):
    for r in data[0]:
        if r["family"] == "negative":
            assert all(s["role"] != "assistant_call" for s in r["segments"])


def test_knowledge_answers_appear_in_the_tool_response(data):
    for r in data[0]:
        if r["family"] in ("files", "knowledge"):
            assert r["answer"] in r["messages"][-2]["content"].replace("**", "").replace("\n", " ")


def test_knowledge_final_is_a_checked_restatement_not_the_raw_text(data):
    sys.path.insert(0, str(RV / "scripts"))
    sys.path.insert(0, str(RV))
    import gen_ft_data as g

    rows = [r for r in data[0] if r["family"] in ("files", "knowledge")]
    assert rows
    for r in rows:
        assert r["messages"][-1]["content"] == r["final"] != r["answer"]
        assert not g.restatement_problems(r["final"], r["answer"], r["source"]), r["id"]
        assert r["answer"] in r["messages"][-2]["content"].replace("**", "").replace("\n", " ")  # the tool response stays raw


def test_a_why_question_never_gets_a_yes_or_no(data):
    sys.path.insert(0, str(RV / "scripts"))
    sys.path.insert(0, str(RV))
    import gen_ft_data as g

    assert g.fit_final("Why does it revert?", "No, `es/a.md` says it reverts.") == "`es/a.md` says it reverts."
    assert g.fit_final("What is it?", "Yes: per `es/a.md` it is x.") == "Per `es/a.md` it is x."
    assert g.fit_final("Is it safe?", "No, `es/a.md` says not.") == "No, `es/a.md` says not."
    assert g.fit_final("What is it?", "Notably `es/a.md` says x.") == "Notably `es/a.md` says x."
    for r in data[0]:
        if r["family"] in ("files", "knowledge") and r["messages"][1]["content"].split()[0] in ("What", "Why", "How", "Which", "When"):
            assert not r["final"].startswith(("Yes", "No,", "No.", "No:"))


def test_restatement_checker():
    sys.path.insert(0, str(RV / "scripts"))
    sys.path.insert(0, str(RV))
    import gen_ft_data as g

    fact, f = "`maxMint` must not use balanceOf; stETH fell to 0.93 ETH in 2022. Look for: x. [SP O-12]", "es/a.md"
    assert not g.restatement_problems("According to `es/a.md`, `maxMint` must not use it, and stETH fell to 0.93 ETH in 2022.", fact, f)
    assert g.restatement_problems("`maxMint` must not use it.", fact, f) == ["does not name the file"]
    assert g.restatement_problems("Per `es/a.md`, stETH fell to 0.90 ETH.", fact, f)
    assert g.restatement_problems("Per `es/a.md`, use `minMint`.", fact, f)
    assert g.restatement_problems("Per `es/a.md`, it fell. Look for: x.", fact, f)
    assert g.restatement_problems("Per `es/a.md`, a. b. c.", fact, f) == ["over two sentences"]


def test_smoke_questions_are_held_out(data):
    asked = {m["content"].lower() for r in data[0] for m in r["messages"] if m["role"] == "user"}
    for q in SMOKE:
        assert q["q"].lower() not in asked
    for r in data[0]:
        if r["family"] in ("files", "knowledge"):
            assert all(q["answer"].lower() not in r["answer"].lower() for q in SMOKE)


def test_dmind_filter_drops_what_the_skills_cover():
    sys.path.insert(0, str(RV / "scripts"))
    sys.path.insert(0, str(RV))
    import gen_ft_data as g

    corpus = "use eip-7702 for delegation. the gas limit rose."
    assert g.in_skills("What does EIP-7702 add?", "Delegation for accounts", corpus)
    assert g.in_skills("What is 'gas' used for?", "Paying fees", corpus)
    assert g.in_skills("Pick one.", "the gas limit rose", corpus)
    assert not g.in_skills("What is 'Quorum' in a vote?", "A minimum turnout", corpus)


def test_question_filter():
    sys.path.insert(0, str(RV / "scripts"))
    sys.path.insert(0, str(RV))
    import gen_ft_data as g

    assert not g.question_ok("Is it true that use a timelock for upgrades?", "use a timelock for upgrades", [])
    assert not g.question_ok("What does the reference say about oracles?", "Check staleness on every read", [])
    assert g.question_ok("How should I guard against stale prices?", "Check staleness on every read", [])

