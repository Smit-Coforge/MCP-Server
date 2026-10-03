import json
import re

from catalog import ITEMS

_FIELD_CLAIMS = (
    ("employment_types", re.compile(r"Employment type is (\w+)"), str),
    ("issued_on", re.compile(r"Issued on (\d{4}-\d{2}-\d{2})"), str),
    ("max_quantity", re.compile(r"Max quantity is (\d+)"), int),
    ("cadence_years", re.compile(r"Cadence is (\d+)"), int),
    ("codes", re.compile(r"Outcome code is (\w+)"), str),
    ("review_ids", re.compile(r"Review id (\w+)"), str),
)
_OUTCOMES = {"approved": "eligible", "denied": "denied", "escalated": "unknown"}
_DECISION = {"eligible": "approve", "denied": "deny", "unknown": "escalate"}
_WORD = {"approve": "Approved", "deny": "Denied", "escalate": "Escalated"}


def observation_dict(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        return json.loads(value)
    return {"value": value}


def last_observation(trace: list, tool: str) -> dict | None:
    for turn in reversed(trace):
        if turn.get("action") == tool:
            return observation_dict(turn["observation"])
    return None


def eligibility_turns(trace: list) -> list[dict]:
    return [turn for turn in trace if turn.get("action") == "check_request_eligibility"]


def combined_decision(trace: list) -> str | None:
    decisions = []
    for turn in eligibility_turns(trace):
        outcome = observation_dict(turn.get("observation")).get("outcome")
        if outcome:
            decisions.append(_DECISION.get(outcome, "escalate"))
    if not decisions or len(set(decisions)) != 1:
        return None
    return decisions[0]


def _facts() -> dict:
    return {key: set() for key in (
        "employment_types",
        "issued_on",
        "max_quantity",
        "cadence_years",
        "codes",
        "outcomes",
        "review_ids",
        "employee_ids",
        "items",
    )}


def _add_observation(obs: dict, facts: dict) -> None:
    for key, bucket in (
        ("employee_id", "employee_ids"),
        ("employment_type", "employment_types"),
        ("outcome", "outcomes"),
        ("code", "codes"),
        ("review_id", "review_ids"),
    ):
        if obs.get(key):
            facts[bucket].add(str(obs[key]))
    if obs.get("review_id") and obs.get("reason"):
        facts["codes"].add(str(obs["reason"]))
    for entry in obs.get("equipment") or []:
        if entry.get("item"):
            facts["items"].add(entry["item"])
        if entry.get("issued_on"):
            facts["issued_on"].add(entry["issued_on"])
    for entry in obs.get("items") or []:
        if entry.get("item"):
            facts["items"].add(entry["item"])
        if entry.get("max_quantity") is not None:
            facts["max_quantity"].add(entry["max_quantity"])
        if entry.get("cadence_years") is not None:
            facts["cadence_years"].add(entry["cadence_years"])
    request = obs.get("request")
    if isinstance(request, dict):
        if request.get("item"):
            facts["items"].add(request["item"])
        if request.get("employee_id"):
            facts["employee_ids"].add(str(request["employee_id"]))


def _add_call_arguments(turn: dict, facts: dict) -> None:
    if turn.get("action") != "check_request_eligibility":
        return
    args = turn.get("arguments") or {}
    item = str(args.get("item") or "")
    if item:
        facts["items"].add(item)
        lower = item.lower()
        for name in ITEMS:
            if name in lower or name.replace("_", " ") in lower:
                facts["items"].add(name)
    if args.get("employee_id"):
        facts["employee_ids"].add(str(args["employee_id"]).upper())


def collect_facts(trace: list) -> dict:
    facts = _facts()
    for turn in trace:
        _add_call_arguments(turn, facts)
        _add_observation(observation_dict(turn.get("observation")), facts)
    return facts


def _issued_on(trace: list, item: str) -> str | None:
    employee = last_observation(trace, "get_employee_info")
    if not employee or not employee.get("found"):
        return None
    dates = [
        entry["issued_on"]
        for entry in employee.get("equipment") or []
        if entry.get("item") == item and entry.get("issued_on")
    ]
    return max(dates) if dates else None


def _policy_row(trace: list, item: str) -> dict | None:
    policy = last_observation(trace, "get_policy_limits")
    if not policy:
        return None
    for entry in policy.get("items") or []:
        if entry.get("item") == item:
            return entry
    return None


def _item_lines(turn: dict) -> list[str]:
    obs = observation_dict(turn.get("observation"))
    args = turn.get("arguments") or {}
    item = str(args.get("item") or "").strip()
    employee_id = str(args.get("employee_id") or "").strip()
    word = _WORD.get(_DECISION.get(obs.get("outcome"), "escalate"), "Escalated")
    if item and employee_id:
        lines = [f"{word} {item} for {employee_id}."]
    elif employee_id:
        lines = [f"{word} request for {employee_id}."]
    else:
        lines = [f"{word}."]
    if obs.get("code"):
        lines.append(f"Outcome code is {obs['code']}.")
    return lines


def _drop_decision(step: dict, claimed: object, reason: str) -> tuple[dict, str]:
    step["decision"] = None
    step["code"] = ""
    if not claimed:
        return step, ""
    return step, f"Dropped decision {str(claimed).upper()}. {reason}"


def reconcile_finish(step: dict, trace: list) -> tuple[dict, str]:
    updated = dict(step)
    claimed = updated.get("decision")
    claimed_code = updated.get("code")
    agreed = combined_decision(trace)
    flagged = last_observation(trace, "flag_for_human_review")
    if flagged and flagged.get("review_id"):
        updated["review_id"] = flagged["review_id"]
    if not eligibility_turns(trace):
        return _drop_decision(updated, claimed, "No eligibility result confirmed it.")
    if agreed is None:
        return _drop_decision(updated, claimed, "The checks do not agree on one decision.")
    updated["decision"] = agreed
    turns = eligibility_turns(trace)
    if len(turns) == 1:
        code = observation_dict(turns[0].get("observation")).get("code")
        if code:
            updated["code"] = code
    else:
        updated["code"] = ""
    notes = []
    if claimed and claimed != agreed:
        notes.append(
            f"Replaced decision {str(claimed).upper()} with {agreed.upper()} from the eligibility result."
        )
    if claimed_code and updated.get("code") and claimed_code != updated["code"]:
        notes.append(f"Replaced outcome code {claimed_code} with {updated['code']}.")
    return updated, " ".join(notes)


def draft_response(request: dict, step: dict, trace: list) -> str:
    turns = eligibility_turns(trace)
    decision = step.get("decision")
    if len(turns) > 1:
        sentences = []
        for turn in turns:
            sentences.extend(_item_lines(turn))
    elif decision not in _WORD:
        return ""
    else:
        item = request["item"]
        employee_id = request["employee_id"]
        sentences = [f"{_WORD[decision]} {item} for {employee_id}."]
        if decision == "escalate":
            sentences.append(f"Review id {step.get('review_id', '')}.")
        employee = last_observation(trace, "get_employee_info")
        if employee and employee.get("employment_type"):
            sentences.append(f"Employment type is {employee['employment_type']}.")
        issued = _issued_on(trace, item)
        if issued:
            sentences.append(f"Issued on {issued}.")
        row = _policy_row(trace, item)
        if row:
            sentences.append(f"Max quantity is {row['max_quantity']}.")
            if row.get("cadence_years") is not None:
                sentences.append(f"Cadence is {row['cadence_years']} years.")
        if step.get("code"):
            sentences.append(f"Outcome code is {step['code']}.")
        return " ".join(sentences)
    employee = last_observation(trace, "get_employee_info")
    if employee and employee.get("employment_type"):
        sentences.append(f"Employment type is {employee['employment_type']}.")
    return " ".join(sentences)


def _sentences(draft: str) -> list[str]:
    return [f"{part.strip()}." for part in draft.split(".") if part.strip()]


def claims_in(sentence: str) -> list[tuple[str, object]]:
    claims: list[tuple[str, object]] = []
    lower = sentence.lower()
    for name in ITEMS:
        if name in lower or name.replace("_", " ") in lower:
            claims.append(("items", name))
    for employee_id in re.findall(r"\bE\d+\b", sentence, re.IGNORECASE):
        claims.append(("employee_ids", employee_id.upper()))
    for kind, pattern, cast in _FIELD_CLAIMS:
        matched = pattern.search(sentence)
        if matched:
            claims.append((kind, cast(matched.group(1))))
    for word, outcome in _OUTCOMES.items():
        if lower.startswith(word):
            claims.append(("outcomes", outcome))
            break
    return claims


def reflect(draft: str, trace: list) -> dict:
    facts = collect_facts(trace)
    kept = []
    dropped = []
    for sentence in _sentences(draft):
        claims = claims_in(sentence)
        if claims and all(value in facts[kind] for kind, value in claims):
            kept.append(sentence)
        else:
            dropped.append(sentence)
    return {"response": " ".join(kept), "dropped": dropped}
