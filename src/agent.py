import argparse
import asyncio
import json
import os
import re
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from mcp import Client, StdioServerParameters

from catalog import FLAG_REASONS, ITEMS, REASONS, canonical_item, canonical_reason
from ollama_llm import MODEL, ollama_host, ollama_think, tool_catalog
from reflect import (
    combined_decision,
    draft_response,
    eligibility_turns,
    last_observation,
    observation_dict,
    reconcile_finish,
    reflect,
)

ROOT = Path(__file__).resolve().parent.parent
MAX_TURNS = 16
_MISSING = object()


def _rewrite(fields: dict) -> None:
    if fields.get("item"):
        fields["item"] = canonical_item(str(fields["item"]))
    if fields.get("reason"):
        fields["reason"] = canonical_reason(str(fields["reason"]))


def normalize_step(step: dict) -> dict:
    if step.get("kind") == "finish":
        return step
    arguments = dict(step.get("arguments") or {})
    if step.get("action") == "check_request_eligibility":
        _rewrite(arguments)
    elif step.get("action") == "flag_for_human_review" and isinstance(arguments.get("request"), dict):
        request = dict(arguments["request"])
        _rewrite(request)
        arguments["request"] = request
    updated = dict(step)
    updated["arguments"] = arguments
    return updated


def apply_flag_code(step: dict, trace: list) -> dict:
    if step.get("action") != "flag_for_human_review":
        return step
    arguments = dict(step.get("arguments") or {})
    if str(arguments.get("reason") or "") in FLAG_REASONS:
        return step
    codes = []
    for turn in eligibility_turns(trace):
        observation = observation_dict(turn.get("observation"))
        if observation.get("outcome") == "unknown" and observation.get("code"):
            codes.append(observation["code"])
    unique = list(dict.fromkeys(codes))
    if len(unique) != 1:
        return step
    arguments["reason"] = unique[0]
    updated = dict(step)
    updated["arguments"] = arguments
    return updated


def repeated_flag(step: dict, trace: list) -> bool:
    if step.get("action") != "flag_for_human_review":
        return False
    return any(
        turn.get("action") == "flag_for_human_review" and turn.get("arguments") == step.get("arguments")
        for turn in trace
    )


def unrecognized_reason(lower: str) -> str:
    match = re.search(r"\bfor\s+(.+)$", lower)
    if not match:
        return ""
    phrase = re.sub(r"\bquantity\s+\d+\b", "", match.group(1))
    phrase = re.sub(r"^my\s+", "", phrase.strip(" .,"))
    return phrase.strip(" .,")


def parse_request(text: str) -> dict:
    lower = text.lower()
    match = re.search(r"\bE\d+\b", text, re.IGNORECASE)
    employee_id = match.group(0).upper() if match else ""
    item = next((name for name in ITEMS if name in lower or name.replace("_", " ") in lower), "")
    reason = next((name for name in REASONS if name.replace("_", " ") in lower), "")
    if not reason:
        reason = unrecognized_reason(lower)
    qty = re.search(r"\bquantity\s+(\d+)\b", lower)
    return {
        "employee_id": employee_id,
        "item": item,
        "reason": reason,
        "quantity": int(qty.group(1)) if qty else 1,
        "text": text,
    }


def payload(result) -> object:
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(result.content[0].text)


def _call(thought: str, action: str, arguments: dict) -> dict:
    return {"thought": thought, "kind": "call", "action": action, "arguments": arguments}


def _finish(thought: str, decision: str, code: str, review_id: object = _MISSING) -> dict:
    step = {"thought": thought, "kind": "finish", "decision": decision, "code": code}
    if review_id is not _MISSING:
        step["review_id"] = review_id
    return step


def think(request: dict, trace: list, _tools: list | None = None) -> dict:
    employee = last_observation(trace, "get_employee_info")
    if employee is None:
        return _call(
            f"Look up employee {request['employee_id']}.",
            "get_employee_info",
            {"employee_id": request["employee_id"]},
        )
    if employee.get("found") and last_observation(trace, "get_policy_limits") is None:
        role = employee["employment_type"]
        return _call(f"Load policy limits for {role}.", "get_policy_limits", {"role": role})

    eligibility = last_observation(trace, "check_request_eligibility")
    if eligibility is None:
        return _call(
            "Ask the server if this request is eligible. Do not decide locally.",
            "check_request_eligibility",
            {
                "employee_id": request["employee_id"],
                "item": request["item"],
                "reason": request["reason"],
                "quantity": request["quantity"],
            },
        )

    outcome = eligibility.get("outcome")
    code = eligibility.get("code", "")
    if outcome == "unknown" and last_observation(trace, "flag_for_human_review") is None:
        return _call(
            f"Eligibility is unknown ({code}). Escalate instead of guessing.",
            "flag_for_human_review",
            {
                "employee_id": request["employee_id"],
                "request": {
                    "employee_id": request["employee_id"],
                    "item": request["item"],
                    "reason": request["reason"],
                    "quantity": request["quantity"],
                },
                "reason": code,
            },
        )
    if outcome == "unknown":
        flagged = last_observation(trace, "flag_for_human_review")
        return _finish(
            "Review queue record exists. Stop with escalate.",
            "escalate",
            code,
            flagged.get("review_id"),
        )
    if outcome == "eligible":
        return _finish("Eligibility is eligible. Approve.", "approve", code)
    return _finish(f"Eligibility is denied ({code}). Deny.", "deny", code)


def format_turn(turn: dict) -> str:
    args = json.dumps(turn["arguments"], sort_keys=True)
    obs = json.dumps(turn["observation"], default=str)
    return f"Thought: {turn['thought']}\nAction: {turn['action']}({args})\nObservation: {obs}"


def server_params() -> StdioServerParameters:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "server"],
        cwd=str(ROOT),
        env=env,
    )


def make_turn(step: dict, observation: object) -> dict:
    return {
        "thought": step["thought"],
        "action": step["action"],
        "arguments": step["arguments"],
        "observation": observation,
    }


async def server_tools(client) -> list[dict]:
    listed = await client.list_tools()
    return tool_catalog(listed.tools)


async def run_once(text: str, thinker: Callable = think) -> dict:
    request = parse_request(text)
    async with Client(server_params()) as client:
        tools = await server_tools(client)
        step = normalize_step(thinker(request, [], tools))
        result = await client.call_tool(step["action"], step["arguments"])
        turn = make_turn(step, payload(result))
    return {"request": request, "trace": [turn]}


def finish_result(request: dict, trace: list, step: dict) -> dict:
    step, note = reconcile_finish(step, trace)
    draft = draft_response(request, step, trace)
    checked = reflect(draft, trace)
    return {
        "request": request,
        "trace": trace,
        "decision": step.get("decision"),
        "code": step.get("code"),
        "review_id": step.get("review_id"),
        "draft": draft,
        "dropped": checked["dropped"],
        "reflection_note": note,
        "response": checked["response"],
    }


def stopped_result(request: dict, trace: list) -> dict:
    count = len(trace)
    note = f"Stopped after {count} step." if count == 1 else f"Stopped after {count} steps."
    if not eligibility_turns(trace):
        return {
            "request": request,
            "trace": trace,
            "decision": None,
            "code": None,
            "review_id": None,
            "draft": "",
            "dropped": [],
            "response": note,
        }
    result = finish_result(request, trace, {"decision": combined_decision(trace), "code": ""})
    result["response"] = f"{result['response']} {note}".strip()
    return result


async def run_agent(text: str, thinker: Callable = think) -> dict:
    request = parse_request(text)
    trace: list[dict] = []
    async with Client(server_params()) as client:
        tools = await server_tools(client)
        for _ in range(MAX_TURNS):
            try:
                step = thinker(request, trace, tools)
            except (ValueError, TypeError):
                return stopped_result(request, trace)
            if step["kind"] == "finish":
                return finish_result(request, trace, step)
            step = apply_flag_code(normalize_step(step), trace)
            if repeated_flag(step, trace):
                return finish_result(
                    request,
                    trace,
                    {"decision": combined_decision(trace), "code": ""},
                )
            result = await client.call_tool(step["action"], step["arguments"])
            trace.append(make_turn(step, payload(result)))
    return stopped_result(request, trace)


def render_result(result: dict) -> str:
    lines = [format_turn(turn) + "\n" for turn in result["trace"]]
    decision = result.get("decision")
    note = result.get("reflection_note") or ""
    if decision in {"approve", "deny", "escalate"} or result.get("response") or result.get("draft") or note:
        if decision in {"approve", "deny", "escalate"}:
            lines.append(f"Decision: {decision.upper()}")
        if result.get("draft"):
            lines.append(f"Draft: {result['draft']}")
        if result["dropped"]:
            lines.append("Reflection dropped:")
            lines.extend(f"- {sentence}" for sentence in result["dropped"])
        elif result.get("draft") and not note:
            lines.append("Reflection: draft matches the tool observations.")
        if note:
            lines.append(f"Reflection: {note}")
        if result.get("response"):
            lines.append(result["response"])
    return "\n".join(lines).rstrip() + "\n"


def print_result(result: dict) -> None:
    print(render_result(result), end="")


def save_run(result: dict, directory: Path | None = None) -> Path:
    folder = directory or (ROOT / "runs")
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    path = folder / f"{stamp}.txt"
    path.write_text(render_result(result))
    return path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="IT equipment ReAct agent")
    parser.add_argument(
        "text",
        nargs="?",
        default="E100 wants a monitor, reason preference, quantity 1",
    )
    parser.add_argument("--once", action="store_true", help="one tool call, then stop")
    parser.add_argument(
        "--scripted",
        action="store_true",
        help="use the keyword thinker instead of qwen",
    )
    return parser.parse_args(argv)


def model_thinker() -> Callable:
    host = ollama_host()

    def _think(request: dict, trace: list, tools: list | None = None) -> dict:
        return ollama_think(request, trace, host=host, tools=tools or [])

    return _think


def main() -> None:
    args = parse_args()
    if args.scripted:
        thinker = think
        print("Thinker: scripted", file=sys.stderr)
    else:
        thinker = model_thinker()
        print(f"LLM: {MODEL}  host: {ollama_host()}", file=sys.stderr)
    runner = run_once if args.once else run_agent
    result = asyncio.run(runner(args.text, thinker))
    print_result(result)
    sys.stdout.flush()
    path = save_run(result)
    print(f"Saved: {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
