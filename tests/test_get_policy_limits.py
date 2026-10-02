from services import get_policy_limits


def test_fulltime_includes_laptop_and_omits_nothing_allowed():
    result = get_policy_limits("fulltime")
    items = {row["item"]: row for row in result["items"]}
    assert result["role"] == "fulltime"
    assert items["laptop"] == {
        "item": "laptop",
        "kind": "tracked",
        "max_quantity": 1,
        "cadence_years": 2,
    }
    assert items["external_ssd"]["max_quantity"] == 2
    assert items["external_ssd"]["cadence_years"] is None


def test_intern_omits_laptop_dock_and_webcam():
    names = {row["item"] for row in get_policy_limits("intern")["items"]}
    assert "headset" in names
    assert "laptop" not in names
    assert "dock" not in names
    assert "webcam" not in names


def test_unknown_employment_type_returns_no_items():
    assert get_policy_limits("contractor")["items"]
    assert get_policy_limits("unknown") == {"role": "unknown", "items": []}
