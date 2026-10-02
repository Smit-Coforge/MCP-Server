import json
from datetime import date
from pathlib import Path

STORE_PATH = Path(__file__).resolve().parents[1] / "data" / "store.json"
STORE = json.loads(STORE_PATH.read_text())
POLICY_CLOCK = date.fromisoformat(STORE["policy_clock"])
EMPLOYEES = STORE["employees"]
CATALOG = STORE["catalog"]
REVIEW_QUEUE: list[dict] = []

DENY_INSIDE_CADENCE = {"preference", "performance"}
KNOWN_REASONS = {"broken", "lost", "new_hire", "preference", "performance"}


def years_since(issued_on: date, clock: date = POLICY_CLOCK) -> int:
    years = clock.year - issued_on.year
    if (clock.month, clock.day) < (issued_on.month, issued_on.day):
        years -= 1
    return years


def get_employee_info(employee_id: str) -> dict:
    record = EMPLOYEES.get(employee_id)
    if record is None:
        return {"found": False, "employee_id": employee_id}
    return {"found": True, **record}


def get_policy_limits(role: str) -> dict:
    items = []
    for item, row in CATALOG.items():
        limit = row["limits"].get(role)
        if limit is None:
            continue
        items.append(
            {
                "item": item,
                "kind": row["kind"],
                "max_quantity": limit["max_quantity"],
                "cadence_years": limit["cadence_years"],
            }
        )
    return {"role": role, "items": items}


def check_request_eligibility(
    employee_id: str, item: str, reason: str, quantity: object
) -> dict:
    employee = get_employee_info(employee_id)
    if not employee["found"]:
        return {"outcome": "unknown", "code": "not_found"}

    row = CATALOG.get(item)
    if row is None:
        return {"outcome": "unknown", "code": "no_rule"}

    limit = row["limits"].get(employee["employment_type"])
    if limit is None:
        return {"outcome": "denied", "code": "not_allowed"}

    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 1:
        return {"outcome": "denied", "code": "invalid_quantity"}

    if quantity > limit["max_quantity"]:
        return {"outcome": "denied", "code": "over_quantity"}

    if reason not in KNOWN_REASONS:
        return {"outcome": "unknown", "code": "ambiguous"}

    if reason == "new_hire" and employee["tenure_years"] >= 1:
        return {"outcome": "denied", "code": "reason_conflict"}

    if row["kind"] == "untracked":
        return {"outcome": "eligible", "code": "ok"}

    units = [entry for entry in employee["equipment"] if entry["item"] == item]
    if not units:
        return {"outcome": "eligible", "code": "ok"}

    newest = max(date.fromisoformat(entry["issued_on"]) for entry in units)
    if years_since(newest) >= limit["cadence_years"]:
        return {"outcome": "eligible", "code": "ok"}

    if reason in DENY_INSIDE_CADENCE:
        return {"outcome": "denied", "code": "too_soon"}
    return {"outcome": "unknown", "code": "early_replacement"}


def flag_for_human_review(employee_id: str, request: dict, reason: str) -> dict:
    review_id = f"R{len(REVIEW_QUEUE) + 1:03d}"
    record = {
        "review_id": review_id,
        "employee_id": employee_id,
        "request": request,
        "reason": reason,
    }
    REVIEW_QUEUE.append(record)
    return record


def reset_review_queue() -> None:
    REVIEW_QUEUE.clear()
