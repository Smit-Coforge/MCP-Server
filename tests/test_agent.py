import asyncio

from agent import parse_request, run_agent, run_once, think


def test_parse_request_extracts_the_four_fields():
    parsed = parse_request("E100 wants a monitor, reason preference, quantity 1")
    assert parsed["employee_id"] == "E100"
    assert parsed["item"] == "monitor"
    assert parsed["reason"] == "preference"
    assert parsed["quantity"] == 1


def test_think_first_action_is_get_employee_info():
    step = think(
        {"employee_id": "E100", "item": "monitor", "reason": "preference", "quantity": 1},
        [],
    )
    assert step["kind"] == "call"
    assert step["action"] == "get_employee_info"
    assert step["arguments"] == {"employee_id": "E100"}


def test_think_flags_unknown_instead_of_guessing():
    request = {
        "employee_id": "E999",
        "item": "mouse",
        "reason": "broken",
        "quantity": 1,
    }
    trace = [
        {
            "action": "get_employee_info",
            "observation": {"found": False, "employee_id": "E999"},
        },
        {
            "action": "check_request_eligibility",
            "observation": {"outcome": "unknown", "code": "not_found"},
        },
    ]
    step = think(request, trace)
    assert step["action"] == "flag_for_human_review"
    assert step["arguments"]["reason"] == "not_found"


def test_once_prints_one_employee_lookup():
    result = asyncio.run(run_once("E100 wants a monitor, reason preference, quantity 1"))
    assert len(result["trace"]) == 1
    turn = result["trace"][0]
    assert turn["action"] == "get_employee_info"
    assert turn["observation"]["found"] is True
    assert turn["observation"]["employee_id"] == "E100"


def test_agent_approves_in_policy_monitor():
    result = asyncio.run(
        run_agent("E100 wants a monitor, reason preference, quantity 1")
    )
    assert result["decision"] == "approve"
    assert [turn["action"] for turn in result["trace"]] == [
        "get_employee_info",
        "get_policy_limits",
        "check_request_eligibility",
    ]


def test_agent_denies_early_laptop():
    result = asyncio.run(
        run_agent("E200 wants a laptop, reason preference, quantity 1")
    )
    assert result["decision"] == "deny"
    assert result["code"] == "too_soon"


def test_parse_request_keeps_personal_use():
    parsed = parse_request("E100 wants a laptop for my personal use")
    assert parsed["employee_id"] == "E100"
    assert parsed["item"] == "laptop"
    assert parsed["reason"] == "personal use"
    assert parsed["quantity"] == 1


def test_agent_escalates_personal_use():
    result = asyncio.run(run_agent("E100 wants a laptop for my personal use"))
    assert result["decision"] == "escalate"
    assert result["code"] == "ambiguous"
    assert result["trace"][-1]["action"] == "flag_for_human_review"
    assert result["trace"][-1]["arguments"]["request"]["reason"] == "personal use"


def test_agent_escalates_missing_employee():
    result = asyncio.run(run_agent("E999 wants a mouse, reason broken, quantity 1"))
    assert result["decision"] == "escalate"
    assert result["code"] == "not_found"
    assert result["review_id"]
    assert result["trace"][-1]["action"] == "flag_for_human_review"
