import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import agent
from agent import run_agent
from catalog import canonical_item, canonical_reason
from reflect import draft_response, observation_dict, reconcile_finish, reflect


def _call(thought, action, arguments):
    return {"thought": thought, "kind": "call", "action": action, "arguments": arguments}


def test_scripted_monitor_is_approved():
    result = asyncio.run(run_agent("E100 wants a monitor, reason preference, quantity 1"))
    assert result["decision"] == "approve"
    assert result["dropped"] == []
    assert "Issued on 2022-01-15." in result["response"]
    assert "Cadence is 3 years." in result["response"]
    assert [turn["action"] for turn in result["trace"]] == [
        "get_employee_info",
        "get_policy_limits",
        "check_request_eligibility",
    ]
    shown = agent.render_result(result)
    assert "Decision: APPROVE" in shown
    assert "draft matches the tool observations." in shown


def test_scripted_laptop_preference_is_denied():
    result = asyncio.run(run_agent("E200 wants a laptop, reason preference, quantity 1"))
    assert (result["decision"], result["code"]) == ("deny", "too_soon")


def test_missing_employee_is_escalated():
    result = asyncio.run(run_agent("E999 wants a mouse, reason broken, quantity 1"))
    assert (result["decision"], result["code"]) == ("escalate", "not_found")
    assert result["trace"][-1]["action"] == "flag_for_human_review"


def test_early_replacement_is_escalated():
    result = asyncio.run(run_agent("E200 wants a laptop, reason broken, quantity 1"))
    assert (result["decision"], result["code"]) == ("escalate", "early_replacement")


def test_personal_use_is_ambiguous():
    result = asyncio.run(run_agent("E100 wants a laptop for my personal use"))
    assert (result["decision"], result["code"]) == ("escalate", "ambiguous")


def test_reflection_drops_unconfirmed_claims():
    checked = reflect(
        "Approved monitor for E100. Employment type is manager. Cadence is 9 years. Outcome code is ok.",
        [
            {
                "action": "get_employee_info",
                "observation": {
                    "found": True,
                    "employee_id": "E100",
                    "employment_type": "fulltime",
                    "equipment": [{"item": "monitor", "issued_on": "2022-01-15"}],
                },
            },
            {
                "action": "check_request_eligibility",
                "observation": {"outcome": "eligible", "code": "ok"},
            },
        ],
    )
    assert checked["dropped"] == ["Employment type is manager.", "Cadence is 9 years."]
    assert checked["response"] == "Approved monitor for E100. Outcome code is ok."
    assert "Reflection dropped:" in agent.render_result(
        {
            "trace": [],
            "decision": "approve",
            "draft": checked["response"],
            "dropped": checked["dropped"],
            "response": checked["response"],
        }
    )


def test_finish_decision_follows_eligibility():
    def wrong_finish(request, trace, tools=None):
        if not trace:
            return _call(
                "Check the laptop.",
                "check_request_eligibility",
                {"employee_id": "E200", "item": "laptop", "reason": "preference", "quantity": 1},
            )
        return {"thought": "Approve anyway.", "kind": "finish", "decision": "approve", "code": "ITEM_NOT_ALLOWED"}

    result = asyncio.run(run_agent("E200 wants a laptop, reason preference, quantity 1", wrong_finish))
    assert (result["decision"], result["code"]) == ("deny", "too_soon")
    assert "ITEM_NOT_ALLOWED" not in result["response"]


def test_finish_without_eligibility_is_dropped():
    def no_check(request, trace, tools=None):
        if not trace:
            return _call("Look up E101.", "get_employee_info", {"employee_id": "E101"})
        return {"thought": "Approve the name.", "kind": "finish", "decision": "approve", "code": "E101_NAME_FOUND"}

    result = asyncio.run(run_agent("what is the name of employee E101", no_check))
    assert result["decision"] is None
    assert "No eligibility result confirmed it." in agent.render_result(result)


def test_each_item_gets_its_own_line():
    assert observation_dict('{"found": false}') == {"found": False}
    assert observation_dict(5) == {"value": 5}
    lines = draft_response(
        {"item": "laptop", "employee_id": "E100"},
        {"decision": "approve"},
        [
            {
                "action": "get_employee_info",
                "observation": {"found": True, "employee_id": "E100", "employment_type": "fulltime"},
            },
            {
                "action": "check_request_eligibility",
                "arguments": {"item": "webcam", "employee_id": "E300"},
                "observation": {"outcome": "denied", "code": "not_allowed"},
            },
            {
                "action": "check_request_eligibility",
                "arguments": {"employee_id": "E100"},
                "observation": {"outcome": "denied", "code": "over_quantity"},
            },
            {
                "action": "check_request_eligibility",
                "arguments": {},
                "observation": {"outcome": "eligible"},
            },
        ],
    )
    assert "Denied webcam for E300." in lines
    assert "Employment type is fulltime." in lines
    assert "Denied request for E100." in lines
    assert "Approved." in lines
    assert "Max quantity" not in draft_response(
        {"item": "laptop", "employee_id": "E100"},
        {"decision": "approve", "code": "ok"},
        [
            {
                "action": "get_policy_limits",
                "observation": {"items": [{"item": "monitor", "max_quantity": 2}]},
            },
            {
                "action": "check_request_eligibility",
                "arguments": {"item": "laptop", "employee_id": "E100"},
                "observation": {"outcome": "eligible", "code": "ok"},
            },
        ],
    )


def test_matching_checks_keep_one_decision():
    assert reconcile_finish({}, [])[1] == ""
    agreed, note = reconcile_finish(
        {"decision": "approve", "code": "ok"},
        [
            {"action": "check_request_eligibility", "observation": {"outcome": "eligible", "code": "ok"}},
            {"action": "check_request_eligibility", "observation": {"outcome": "eligible", "code": "ok"}},
        ],
    )
    assert agreed["decision"] == "approve"
    assert agreed["code"] == ""
    assert note == ""


def test_checks_that_disagree_drop_the_decision():
    mixed, note = reconcile_finish(
        {"decision": "approve"},
        [
            {"action": "check_request_eligibility", "observation": {"outcome": "eligible", "code": "ok"}},
            {"action": "check_request_eligibility", "observation": {"outcome": "denied", "code": "too_soon"}},
        ],
    )
    assert mixed["decision"] is None
    assert "do not agree" in note


def test_phrases_are_rewritten_before_the_tool_call():
    assert canonical_item("external ssd") == "external_ssd"
    assert canonical_item("monitors") == "monitor"
    assert canonical_item("not-a-real-item") == "not-a-real-item"
    assert canonical_reason("it broke") == "broken"
    assert canonical_reason("play games") == "play games"
    rewritten = agent.normalize_step(
        {
            "kind": "call",
            "action": "check_request_eligibility",
            "arguments": {"item": "external ssd", "reason": "it broke"},
        }
    )
    assert rewritten["arguments"] == {"item": "external_ssd", "reason": "broken"}
    flagged = agent.normalize_step(
        {
            "kind": "call",
            "action": "flag_for_human_review",
            "arguments": {"reason": "unknown", "request": {"item": "monitors", "reason": "it broke"}},
        }
    )
    assert flagged["arguments"]["request"]["item"] == "monitor"
    assert agent.normalize_step({"kind": "finish", "decision": "approve"})["decision"] == "approve"
    assert agent.payload(SimpleNamespace(structured_content={"ok": True})) == {"ok": True}
    same = {"action": "flag_for_human_review", "arguments": {"reason": "unknown"}}
    assert (
        agent.apply_flag_code(
            same,
            [
                {"action": "check_request_eligibility", "observation": {"outcome": "unknown", "code": "no_rule"}},
                {"action": "check_request_eligibility", "observation": {"outcome": "unknown", "code": "ambiguous"}},
            ],
        )
        is same
    )
    one = agent.apply_flag_code(
        {"action": "flag_for_human_review", "arguments": {"reason": "unknown"}},
        [{"action": "check_request_eligibility", "observation": {"outcome": "unknown", "code": "ambiguous"}}],
    )
    assert one["arguments"]["reason"] == "ambiguous"


def test_the_same_flag_is_not_repeated():
    flag = {
        "employee_id": "E999",
        "reason": "not_found",
        "request": {"employee_id": "E999", "item": "mouse", "reason": "broken", "quantity": 1},
    }

    def repeat(request, trace, tools=None):
        if not any(turn["action"] == "check_request_eligibility" for turn in trace):
            return _call(
                "Check.",
                "check_request_eligibility",
                {"employee_id": "E999", "item": "mouse", "reason": "broken", "quantity": 1},
            )
        return _call("Flag again.", "flag_for_human_review", flag)

    result = asyncio.run(run_agent("E999 wants a mouse, reason broken, quantity 1", repeat))
    assert [turn["action"] for turn in result["trace"]].count("flag_for_human_review") == 1
    assert result["decision"] == "escalate"


def test_a_bad_reply_keeps_the_trace(monkeypatch):
    def bad(request, trace, tools=None):
        if not trace:
            return _call("Look up.", "get_employee_info", {"employee_id": "E100"})
        raise ValueError("bad json")

    stopped = asyncio.run(run_agent("E100 wants a monitor, reason preference, quantity 1", bad))
    assert stopped["response"] == "Stopped after 1 step."

    monkeypatch.setattr(agent, "MAX_TURNS", 2)

    def keep_going(request, trace, tools=None):
        if not trace:
            return _call(
                "Check.",
                "check_request_eligibility",
                {"employee_id": "E100", "item": "monitor", "reason": "preference", "quantity": 1},
            )
        return _call("Look up.", "get_employee_info", {"employee_id": "E100"})

    partial = asyncio.run(run_agent("E100 wants a monitor, reason preference, quantity 1", keep_going))
    assert partial["decision"] == "approve"
    assert "Stopped after 2 steps." in partial["response"]


def test_cli_runs_scripted_and_the_model_path(monkeypatch, capsys):
    monkeypatch.setattr(
        agent,
        "ollama_think",
        lambda request, trace, host, tools: _call("lookup", "get_employee_info", {"employee_id": "E100"}),
    )
    monkeypatch.setattr(sys, "argv", ["agent", "--once", "E100 wants a monitor, reason preference, quantity 1"])
    agent.main()
    monkeypatch.setattr(
        sys, "argv", ["agent", "--scripted", "--once", "E100 wants a monitor, reason preference, quantity 1"]
    )
    agent.main()
    import runpy

    runpy.run_path(agent.__file__, run_name="__main__")
    err = capsys.readouterr().err
    assert "LLM:" in err and "Thinker: scripted" in err
    for line in err.splitlines():
        if line.startswith("Saved:"):
            Path(line.removeprefix("Saved:").strip()).unlink()
