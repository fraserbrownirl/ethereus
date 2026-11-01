"""serve.py without a model: auth, OpenAI shape, traces, consent, async, the 408 path."""

import json
import time

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from chipformer.rv import serve  # noqa: E402

TEXT = ("<tool_call>\n<function=eth>\n<parameter=cmd>\nselector transfer(address,uint256)\n"
        "</parameter>\n</function>\n</tool_call>\n<tool_response>\n0xa9059cbb\n</tool_response>\n"
        "<think>\n\n</think>\n\nThe selector is 0xa9059cbb.<|im_end|>")


class Fake:
    client_call = None

    def __init__(self, text=TEXT, slow=False):
        self.text, self.slow, self.seen = text, slow, []

    def run(self, messages, max_new, deadline=None, client_tools=()):
        self.seen.append(messages)
        self.client_tools = client_tools
        if self.slow:
            raise serve.Deadline
        return serve.Turn(segments=[{"prompt_ids": [1], "gen_ids": [2], "owner_mask": [True],
                                     "files": {}}],
                          text=self.text, prompt_tokens=1, completion_tokens=1,
                          client_call=self.client_call,
                          finish_reason="tool_calls" if self.client_call else "stop")


@pytest.fixture
def client(tmp_path):
    return TestClient(serve.create_app(Fake(), ["k1", "k2"], tmp_path, hmac_key=b"s"))


A = {"Authorization": "Bearer k1"}
BODY = {"messages": [{"role": "user", "content": "selector?"}]}


def test_auth(client):
    assert client.post("/v1/chat/completions", json=BODY).status_code == 401
    r = client.get("/v1/models", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "bad_key"
    assert client.get("/health").status_code == 200


def test_completion_shape_and_trace(client):
    r = client.post("/v1/chat/completions", json=BODY, headers=A)
    assert r.status_code == 200 and "X-RateLimit-Remaining-Minute" in r.headers
    j = r.json()
    msg = j["choices"][0]["message"]
    assert msg["content"] == "The selector is 0xa9059cbb."
    args = json.loads(msg["tool_calls"][0]["function"]["arguments"])
    assert args == {"cmd": "selector transfer(address,uint256)"}
    assert j["ethereus"]["tool_messages"][0]["content"] == "0xa9059cbb"
    tid = j["ethereus"]["trace_id"]
    assert client.get(f"/v1/traces/{tid}", headers=A).json()["gen_ids"] == [2]
    other = {"Authorization": "Bearer k2"}
    assert client.get(f"/v1/traces/{tid}", headers=other).status_code == 404


def test_tool_history_arguments_become_mappings(client, tmp_path):
    fake = Fake()
    c = TestClient(serve.create_app(fake, ["k1"], tmp_path / "h"))
    hist = [{"role": "assistant", "content": "", "tool_calls": [{"id": "c", "type": "function",
             "function": {"name": "eth", "arguments": "{\"cmd\": \"u256 1+1\"}"}}]},
            {"role": "tool", "tool_call_id": "c", "content": "2"}]
    assert c.post("/v1/chat/completions", json={"messages": hist}, headers=A).status_code == 200
    assert fake.seen[0][0]["tool_calls"][0]["function"]["arguments"] == {"cmd": "u256 1+1"}


def test_bad_requests(client):
    for body in ({}, {**BODY, "max_tokens": 9000}, {**BODY, "stream": True}):
        r = client.post("/v1/chat/completions", json=body, headers=A)
        assert r.status_code == 422 and r.json()["error"]["code"] == "bad_request"


def test_sync_over_limit_is_408(tmp_path):
    c = TestClient(serve.create_app(Fake(slow=True), ["k1"], tmp_path))
    r = c.post("/v1/chat/completions", json=BODY, headers=A)
    assert r.status_code == 408 and r.json()["error"]["code"] == "use_async"


def test_async_returns_job_then_completion(client):
    r = client.post("/v1/chat/completions", json={**BODY, "mode": "async"}, headers=A)
    assert r.status_code == 202
    jid = r.json()["id"]
    for _ in range(50):
        p = client.get(f"/v1/jobs/{jid}", headers=A)
        if p.status_code == 200:
            break
        time.sleep(0.02)
    assert p.status_code == 200 and p.json()["choices"][0]["finish_reason"] == "stop"
    assert client.get(f"/v1/jobs/{jid}", headers={"Authorization": "Bearer k2"}).status_code == 404


def test_consent_gates_capture_and_hides_the_key(client, tmp_path):
    client.post("/v1/chat/completions", json=BODY, headers=A)
    assert not (tmp_path / "captures").exists()
    assert client.put("/v1/keys/me/consent", json={"consent": "nope"}, headers=A).status_code == 422
    assert client.put("/v1/keys/me/consent", json={"consent": "traces"}, headers=A).status_code == 200
    client.post("/v1/chat/completions", json=BODY, headers=A)
    recs = list((tmp_path / "captures").glob("*/*/*.jsonl"))
    assert len(recs) == 1
    rec = json.loads(recs[0].read_text())
    me = client.get("/v1/keys/me", headers=A).json()
    assert me["consent"] == "traces" and "key_id" not in rec
    assert me["key_id"] not in recs[0].read_text() and me["key_id"] not in str(recs[0])


def test_rate_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(serve, "RATE_MINUTE", 1)
    c = TestClient(serve.create_app(Fake(), ["k1"], tmp_path))
    assert c.post("/v1/chat/completions", json=BODY, headers=A).status_code == 200
    r = c.post("/v1/chat/completions", json=BODY, headers=A)
    assert r.status_code == 429 and r.headers["Retry-After"] == "60"


TCC = 9


def _seg(gen):
    return {"prompt_ids": [1, 2], "gen_ids": gen, "files": {}}


def test_replay_accepts_a_stream_cut_inside_a_run():
    calls = []

    def run(g):
        calls.append(g)
        return {"ok": True} if g == [5, 6] else {"ok": False, "where": 2 + len(g), "got": []}

    r = serve.replay_segment(run, _seg([5, 6, TCC, 7, 8]), TCC)
    assert r["ok"] and r["truncated_run"] and r["checked_tokens"] == 2 and calls[-1] == [5, 6]


def test_replay_still_fails_on_a_wrong_token():
    wrong = {"ok": False, "where": 3, "got": [1], "want": [2]}
    assert serve.replay_segment(lambda g: wrong, _seg([5, TCC, 7]), TCC) == wrong


def test_replay_still_fails_when_short_before_the_end():
    early = {"ok": False, "where": 3, "got": [], "want": [2]}
    assert serve.replay_segment(lambda g: early, _seg([5, TCC, 7]), TCC) == early


def test_replay_cut_keeps_a_failure_visible():
    def run(g):
        return {"ok": False, "where": 2 + len(g), "got": []} if TCC in g else {"ok": False, "where": 2, "got": [1]}

    r = serve.replay_segment(run, _seg([5, TCC, 7]), TCC)
    assert not r["ok"] and r["truncated_run"]


BASH = {"type": "function", "function": {"name": "bash", "parameters": {"type": "object",
        "properties": {"command": {"type": "string"}, "timeout": {"type": "integer"}}}}}


def test_parse_call_decodes_per_schema():
    text = "\n<function=bash>\n<parameter=command>\nls -l\n</parameter>\n<parameter=timeout>\n5\n</parameter>\n</function>\n"
    assert serve.parse_call(text, [BASH]) == {"name": "bash", "arguments": {"command": "ls -l", "timeout": 5}}
    assert serve.parse_call("no call") is None


def test_client_tool_call_goes_back_and_ours_stay_in_the_content(tmp_path):
    fake = Fake(text=TEXT.replace("<|im_end|>", ""))
    fake.client_call = {"name": "bash", "arguments": {"command": "ls"}}
    c = TestClient(serve.create_app(fake, ["k1"], tmp_path))
    r = c.post("/v1/chat/completions", json={**BODY, "tools": [BASH]}, headers=A).json()
    choice = r["choices"][0]
    assert fake.client_tools == [BASH] and choice["finish_reason"] == "tool_calls"
    assert [x["function"]["name"] for x in choice["message"]["tool_calls"]] == ["bash"]
    assert json.loads(choice["message"]["tool_calls"][0]["function"]["arguments"]) == {"command": "ls"}
    assert "<function=eth>" in choice["message"]["content"]  # history for the next request
    assert r["ethereus"]["tool_messages"][0]["content"] == "0xa9059cbb"


def test_stream_is_refused_unless_buffered_then_is_valid_sse(tmp_path):
    body = {**BODY, "stream": True}
    off = TestClient(serve.create_app(Fake(), ["k1"], tmp_path / "a"))
    assert off.post("/v1/chat/completions", json=body, headers=A).status_code == 422
    on = TestClient(serve.create_app(Fake(), ["k1"], tmp_path / "b", buffered_stream=True))
    r = on.post("/v1/chat/completions", json=body, headers=A)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    frames = [ln[6:] for ln in r.text.splitlines() if ln.startswith("data: ")]
    assert frames[-1] == "[DONE]"
    first, last = json.loads(frames[0]), json.loads(frames[-2])
    assert first["choices"][0]["delta"]["content"] == "The selector is 0xa9059cbb."
    assert last["choices"][0]["finish_reason"] == "stop" and last["usage"]["total_tokens"] == 2
