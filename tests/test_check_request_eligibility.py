from datetime import date

from services import POLICY_CLOCK, check_request_eligibility, years_since


def test_years_since_counts_whole_years_on_the_policy_clock():
    assert years_since(date(2023, 10, 1), POLICY_CLOCK) == 3
    assert years_since(date(2023, 10, 2), POLICY_CLOCK) == 2


def test_approve_monitor_refresh():
    assert check_request_eligibility("E100", "monitor", "preference", 1) == {
        "outcome": "eligible",
        "code": "ok",
    }


def test_deny_early_laptop_preference():
    assert check_request_eligibility("E200", "laptop", "preference", 1) == {
        "outcome": "denied",
        "code": "too_soon",
    }


def test_escalate_missing_employee():
    assert check_request_eligibility("E999", "mouse", "broken", 1) == {
        "outcome": "unknown",
        "code": "not_found",
    }


def test_escalate_broken_laptop_inside_cadence():
    assert check_request_eligibility("E200", "laptop", "broken", 1) == {
        "outcome": "unknown",
        "code": "early_replacement",
    }


def test_deny_intern_laptop():
    assert check_request_eligibility("E300", "laptop", "preference", 1) == {
        "outcome": "denied",
        "code": "not_allowed",
    }


def test_deny_intern_over_ssd_quantity():
    assert check_request_eligibility("E300", "external_ssd", "preference", 2) == {
        "outcome": "denied",
        "code": "over_quantity",
    }


def test_deny_invalid_quantity():
    assert check_request_eligibility("E100", "keyboard", "broken", 0) == {
        "outcome": "denied",
        "code": "invalid_quantity",
    }


def test_deny_reason_conflict():
    assert check_request_eligibility("E100", "headset", "new_hire", 1) == {
        "outcome": "denied",
        "code": "reason_conflict",
    }


def test_unknown_item_is_no_rule():
    assert check_request_eligibility("E100", "chair", "broken", 1) == {
        "outcome": "unknown",
        "code": "no_rule",
    }


def test_contractor_ssd_is_eligible():
    assert check_request_eligibility("E400", "external_ssd", "preference", 2) == {
        "outcome": "eligible",
        "code": "ok",
    }


def test_personal_use_is_ambiguous():
    assert check_request_eligibility("E100", "laptop", "personal use", 1) == {
        "outcome": "unknown",
        "code": "ambiguous",
    }


def test_missing_reason_is_ambiguous():
    assert check_request_eligibility("E100", "laptop", "", 1) == {
        "outcome": "unknown",
        "code": "ambiguous",
    }


def test_starter_laptop_for_new_hire():
    assert check_request_eligibility("E201", "laptop", "new_hire", 1) == {
        "outcome": "eligible",
        "code": "ok",
    }
