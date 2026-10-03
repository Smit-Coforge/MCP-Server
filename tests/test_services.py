import runpy
from datetime import date

import pytest

from services import (
    POLICY_CLOCK,
    REVIEW_QUEUE,
    check_request_eligibility,
    flag_for_human_review,
    get_employee_info,
    get_policy_limits,
    reset_review_queue,
    years_since,
)


def test_years_since_counts_only_complete_years():
    assert years_since(date(2023, 10, 1), POLICY_CLOCK) == 3
    assert years_since(date(2023, 10, 2), POLICY_CLOCK) == 2


def test_known_employee_record():
    employee = get_employee_info("E100")
    assert employee["name"] == "Alex Chen"
    assert employee["tenure_years"] == 4


def test_missing_employee_record():
    assert get_employee_info("E999") == {"found": False, "employee_id": "E999"}


def test_policy_limits_follow_employment_type():
    fulltime = {row["item"]: row for row in get_policy_limits("fulltime")["items"]}
    assert fulltime["laptop"]["cadence_years"] == 2
    assert fulltime["external_ssd"]["cadence_years"] is None
    intern = {row["item"] for row in get_policy_limits("intern")["items"]}
    assert "headset" in intern
    assert "laptop" not in intern and "dock" not in intern and "webcam" not in intern
    assert get_policy_limits("unknown") == {"role": "unknown", "items": []}


@pytest.mark.parametrize(
    ("employee_id", "item", "reason", "quantity", "outcome", "code"),
    [
        ("E100", "monitor", "preference", 1, "eligible", "ok"),
        ("E200", "laptop", "preference", 1, "denied", "too_soon"),
        ("E999", "mouse", "broken", 1, "unknown", "not_found"),
        ("E200", "laptop", "broken", 1, "unknown", "early_replacement"),
        ("E300", "laptop", "preference", 1, "denied", "not_allowed"),
        ("E300", "external_ssd", "preference", 2, "denied", "over_quantity"),
        ("E100", "keyboard", "broken", 0, "denied", "invalid_quantity"),
        ("E100", "keyboard", "broken", True, "denied", "invalid_quantity"),
        ("E100", "headset", "new_hire", 1, "denied", "reason_conflict"),
        ("E100", "chair", "broken", 1, "unknown", "no_rule"),
        ("E400", "external_ssd", "preference", 2, "eligible", "ok"),
        ("E100", "laptop", "personal use", 1, "unknown", "ambiguous"),
        ("E201", "laptop", "new_hire", 1, "eligible", "ok"),
    ],
    ids=[
        "monitor_ok",
        "too_soon",
        "not_found",
        "early_replacement",
        "not_allowed",
        "over_quantity",
        "quantity_zero",
        "quantity_bool",
        "reason_conflict",
        "no_rule",
        "contractor_ssd",
        "ambiguous",
        "new_hire_laptop",
    ],
)
def test_eligibility(employee_id, item, reason, quantity, outcome, code):
    assert check_request_eligibility(employee_id, item, reason, quantity) == {
        "outcome": outcome,
        "code": code,
    }


def test_review_ids_count_up():
    reset_review_queue()
    first = flag_for_human_review("E999", {"item": "mouse"}, "not_found")
    second = flag_for_human_review("E200", {}, "early_replacement")
    assert first["review_id"] == "R001"
    assert second["review_id"] == "R002"
    assert REVIEW_QUEUE == [first, second]
    reset_review_queue()


def test_server_tools_delegate_to_the_records(monkeypatch):
    import minimal_server
    import server

    reset_review_queue()
    assert server.get_employee_info("E100")["found"] is True
    assert server.get_policy_limits("intern")["role"] == "intern"
    assert server.check_request_eligibility("E200", "laptop", "preference", 1)["code"] == "too_soon"
    assert minimal_server.ping() == {"ok": True}
    assert server.flag_for_human_review("E999", {"item": "mouse"}, "not_found")["review_id"] == "R001"
    reset_review_queue()

    monkeypatch.setattr("mcp.server.MCPServer.run", lambda self: None)
    runpy.run_module("server", run_name="__main__")
    runpy.run_module("minimal_server", run_name="__main__")
