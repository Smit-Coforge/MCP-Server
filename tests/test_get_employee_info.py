from services import get_employee_info


def test_known_employee_returns_type_tenure_and_gear():
    result = get_employee_info("E100")
    assert result["found"] is True
    assert result["employee_id"] == "E100"
    assert result["name"] == "Alex Chen"
    assert result["employment_type"] == "fulltime"
    assert result["tenure_years"] == 4
    assert result["equipment"] == [{"item": "monitor", "issued_on": "2022-01-15"}]


def test_missing_employee_returns_not_found():
    result = get_employee_info("E999")
    assert result == {"found": False, "employee_id": "E999"}
