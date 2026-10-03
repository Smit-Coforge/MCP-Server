import json
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest

from ollama_llm import (
    MODEL,
    SYSTEM_PROMPT,
    chat,
    ollama_host,
    ollama_think,
    parse_step,
    tool_catalog,
)


def test_prompt_does_not_name_tools():
    assert MODEL == "qwen3:8b"
    for name in (
        "get_employee_info",
        "get_policy_limits",
        "check_request_eligibility",
        "flag_for_human_review",
    ):
        assert name not in SYSTEM_PROMPT
    listed = tool_catalog(
        [SimpleNamespace(name="ping", description=None, input_schema={"type": "object"})]
    )
    assert listed == [{"name": "ping", "description": "", "input_schema": {"type": "object"}}]


def test_parse_step_keeps_the_first_json_object():
    allowed = {"get_employee_info", "ping"}
    call = parse_step(
        "```json\n"
        '{"thought": "look up", "kind": "call", "action": "get_employee_info", '
        '"arguments": {"employee_id": "E100"}}\n'
        '{"thought": "again"}\n```',
        allowed,
    )
    assert call["arguments"] == {"employee_id": "E100"}
    assert parse_step('{"kind": "ping", "arguments": {}}', {"ping"})["action"] == "ping"
    untyped = parse_step('{"action": "ping"}', {"ping"})
    assert untyped["kind"] == "call" and untyped["arguments"] == {}
    assert parse_step('{"decision": "deny"}', set())["kind"] == "finish"


def test_parse_step_rejects_a_bad_reply():
    cases = [
        ('{"kind": "call", "action": "delete_all"}', ValueError, "unknown action"),
        ("no json", ValueError, "did not return JSON"),
        ("{", ValueError, "did not return JSON"),
        ("[1]", TypeError, "JSON object"),
        ('{"kind": "call", "action": "ping", "arguments": []}', ValueError, "arguments must be an object"),
        ('{"kind": "finish", "decision": "maybe"}', ValueError, "unknown decision"),
        ('{"kind": "shrug"}', ValueError, "unknown kind"),
    ]
    for text, error, match in cases:
        with pytest.raises(error, match=match):
            parse_step(text, {"ping"})


def test_host_reads_the_environment(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "example.test:11434")
    assert ollama_host() == "http://example.test:11434"
    monkeypatch.setenv("OLLAMA_HOST", "http://example.test/")
    assert ollama_host() == "http://example.test"


class _Body:
    def __init__(self, payload: bytes):
        self.payload = payload

    def read(self):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _urlopen(replies):
    def urlopen(req, timeout=0):
        if not replies:
            raise urllib.error.URLError("down")
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return _Body(reply)

    return urlopen


def test_think_retries_once_then_returns_the_step(monkeypatch):
    replies = [
        json.dumps({"message": {"content": "not json"}}).encode(),
        json.dumps(
            {"message": {"content": '{"thought": "ok", "kind": "finish", "decision": "approve", "code": "ok"}'}}
        ).encode(),
        b'{"message": {"content": "ok"}}',
    ]
    monkeypatch.setattr(urllib.request, "urlopen", _urlopen(replies))
    step = ollama_think({"text": "E100"}, [], host="http://example.test", tools=[{"name": "ping"}])
    assert step["decision"] == "approve"
    assert chat("http://example.test", [])["message"]["content"] == "ok"


def test_http_failures_surface(monkeypatch):
    replies = [
        b'{"message": {"content": "still bad"}}',
        b'{"message": {"content": "still bad"}}',
        urllib.error.HTTPError("http://example.test", 500, "no", {}, None),
    ]
    monkeypatch.setattr(urllib.request, "urlopen", _urlopen(replies))
    with pytest.raises(ValueError, match="did not return one JSON step"):
        ollama_think({"text": "E100"}, [], host="http://example.test", tools=[])
    with pytest.raises(urllib.error.HTTPError):
        chat("http://example.test", [])
    with pytest.raises(ConnectionError, match="Could not reach Ollama"):
        chat("http://example.test", [])
