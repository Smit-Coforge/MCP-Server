from services import REVIEW_QUEUE, flag_for_human_review, reset_review_queue


def setup_function() -> None:
    reset_review_queue()


def test_flag_appends_queue_record_and_returns_its_id():
    request = {
        "employee_id": "E999",
        "item": "mouse",
        "reason": "broken",
        "quantity": 1,
    }
    result = flag_for_human_review("E999", request, "not_found")
    assert result["review_id"] == "R001"
    assert result["reason"] == "not_found"
    assert result["request"] == request
    assert REVIEW_QUEUE == [result]


def test_second_flag_gets_the_next_id():
    flag_for_human_review("E999", {}, "not_found")
    second = flag_for_human_review("E200", {}, "early_replacement")
    assert second["review_id"] == "R002"
    assert len(REVIEW_QUEUE) == 2
