"""The fine-tune evaluation's scoring, with no model."""

import json
import sys
from pathlib import Path

RV = Path(__file__).parents[1]
sys.path.insert(0, str(RV / "scripts"))
import eval_ft as E  # noqa: E402

CALL = "<tool_call>\n<function=eth>\n<parameter=cmd>\nselector f()\n</parameter>\n</function>\n</tool_call>"
RESP = "\n<tool_response>\n0x26121ff0\n</tool_response>\n"
ROW = {"id": "a", "family": "abi", "tool_outputs": ["0x26121ff0"]}
WANT = ("eth", {"cmd": "selector f()"})


def test_right_call_and_final():
    r = E.score_tool_row(ROW, CALL + RESP + "The selector is `0x26121ff0`.", WANT)
    assert r["call_ok"] and r["final_ok"]


def test_no_call_scores_nothing_even_if_the_answer_is_right():
    r = E.score_tool_row(ROW, "The selector is 0x26121ff0.", WANT)
    assert not r["call_ok"] and not r["final_ok"]


def test_wrong_arguments_fail_the_call():
    assert not E.score_tool_row(ROW, CALL + RESP + "0x26121ff0", ("eth", {"cmd": "selector g()"}))["call_ok"]


def test_altered_hex_in_the_final_fails():
    assert not E.score_tool_row(ROW, CALL + RESP + "The selector is 0x26121ff 0.", WANT)["final_ok"]


def test_a_copy_inside_the_response_alone_is_not_a_final():
    assert not E.score_tool_row(ROW, CALL + RESP + "Done.", WANT)["final_ok"]


def test_a_spilled_call_scores_on_the_full_command():
    text = CALL.replace("selector f()", "keccak @spill/0") + RESP + "0x26121ff0"
    assert E.score_tool_row(ROW, text, ("eth", {"cmd": "keccak 0xab"}), full_cmd="keccak 0xab")["call_ok"]


def test_knowledge_final_needs_the_answer_after_the_response():
    row = {"id": "k", "family": "files", "answer": "Check at 5%", "source": "es/a.md", "final": "Per `es/a.md`, check `x` at 5."}
    text = "<tool_call>\n<function=files>\n<parameter=op>\nget\n</parameter>\n</function>\n</tool_call>\n<tool_response>\nCheck `x` at 5, es/a.md\n</tool_response>\n"
    assert not E.score_tool_row(row, text + "No idea.", ("files", {"op": "get"}))["final_ok"]
    assert not E.score_tool_row(row, text + "`es/a.md` says check it at 6.", ("files", {"op": "get"}))["final_ok"]
    assert E.score_tool_row(row, text + "The file `es/a.md` tells you to check `x` at 5.", ("files", {"op": "get"}))["final_ok"]


def test_summary_thresholds():
    good = {"family": "abi", "call_ok": True, "final_ok": True}
    bad = {"family": "abi", "call_ok": False, "final_ok": True}
    clean = [{"calls": []}]
    assert E.summarize([good] * 9 + [bad], clean)["pass"]
    assert not E.summarize([good] * 8 + [bad] * 2, clean)["pass"]
    assert not E.summarize([good] * 10, [{"calls": ["eth"]}])["pass"]
    assert not E.summarize([good] * 10, [])["pass"]


def test_fifty_red_flag_prompts_none_about_ethereum():
    p = json.loads((RV / "scripts/redflag_prompts.json").read_text())
    assert len(p) == len(set(p)) == 50
    assert not [x for x in p if any(w in x.lower() for w in ("ethereum", "solidity", "token", "contract", "crypto", "blockchain", "keccak"))]


def test_listing_before_the_right_read_is_not_a_miss():
    row = {"id": "f", "family": "files", "answer": "x", "source": "es/a.md", "final": "Per `es/a.md`, x.",
           "messages": [{"role": "assistant", "tool_calls": [{"function": {"name": "list", "arguments": {}}}]},
                        {"role": "assistant", "tool_calls": [{"function": {"name": "get", "arguments": {"name": "es/a.md"}}}]}]}
    want = E.expected_call(row)
    assert want == ("get", {"name": "es/a.md"})
    get = "<tool_call>\n<function=get>\n<parameter=name>\nes/a.md\n</parameter>\n</function>\n</tool_call>\n<tool_response>\nx\n</tool_response>\n"
    lst = "<tool_call>\n<function=list>\n</function>\n</tool_call>\n<tool_response>\nes/a.md\n</tool_response>\n"
    assert E.score_tool_row(row, get + "Per `es/a.md`, x.", want)["call_ok"]
    assert E.score_tool_row(row, lst + get + "Per `es/a.md`, x.", want)["call_ok"]
    assert not E.score_tool_row(row, lst + "Per `es/a.md`, x.", want)["call_ok"]


def test_a_spilled_call_needs_the_right_full_command():
    row = {"id": "r", "family": "run", "tool_outputs": ["0x01"],
           "messages": [{"role": "assistant", "tool_calls": [{"function": {"name": "eth", "arguments": {"cmd": "run erc20 @spill/0"}}}]}]}
    text = "<tool_call>\n<function=eth>\n<parameter=cmd>\nrun erc20 @spill/0\n</parameter>\n</function>\n</tool_call>\n<tool_response>\n0x01\n</tool_response>\n0x01"
    assert E.score_tool_row(row, text, ("eth", {"cmd": "run erc20 0xab"}), full_cmd="run erc20 0xab")["call_ok"]
    assert not E.score_tool_row(row, text, ("eth", {"cmd": "run erc20 0xab"}), full_cmd="run erc20 0xcd")["call_ok"]
