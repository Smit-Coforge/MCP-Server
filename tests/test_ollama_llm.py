from types import SimpleNamespace

import pytest

from agent import make_thinker, parse_args, think
from ollama_llm import SYSTEM_PROMPT, parse_step, resolve_model, tool_catalog


def test_resolve_model_aliases():
    assert resolve_model("qwen") == "qwen3:8b"
    assert resolve_model("mistral") == "mistral:7b"
    assert resolve_model("qwen3:8b") == "qwen3:8b"


def test_system_prompt_does_not_name_server_tools():
    for name in (
        "get_employee_info",
        "get_policy_limits",
        "check_request_eligibility",
        "flag_for_human_review",
    ):
        assert name not in SYSTEM_PROMPT


def test_tool_catalog_uses_server_definitions():
    tools = [
        SimpleNamespace(
            name="ping",
            description="Return ok.",
            input_schema={"type": "object", "properties": {}},
        )
    ]
    assert tool_catalog(tools) == [
        {
            "name": "ping",
            "description": "Return ok.",
            "input_schema": {"type": "object", "properties": {}},
        }
    ]


def test_parse_step_call_and_fenced_finish():
    allowed = {"get_employee_info"}
    call = parse_step(
        '{"thought": "look up", "kind": "call", '
        '"action": "get_employee_info", "arguments": {"employee_id": "E100"}}',
        allowed,
    )
    assert call["action"] == "get_employee_info"
    finish = parse_step(
        "```json\n"
        '{"thought": "done", "kind": "finish", "decision": "approve", "code": "ok"}\n'
        "```",
        allowed,
    )
    assert finish["decision"] == "approve"


def test_parse_step_rejects_unknown_tool():
    with pytest.raises(ValueError, match="unknown action"):
        parse_step(
            '{"thought": "x", "kind": "call", "action": "delete_all"}',
            {"get_employee_info"},
        )


def test_default_thinker_is_scripted():
    assert make_thinker(None, False) is think


def test_parse_args_model_and_think():
    args = parse_args(
        ["--model", "qwen", "--think", "on", "E100 wants a monitor, reason preference, quantity 1"]
    )
    assert args.model == "qwen"
    assert args.think == "on"
    assert "E100" in args.text
