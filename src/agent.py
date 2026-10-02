import argparse
import asyncio
import json
import os
import re
import sys
from collections.abc import Callable
from pathlib import Path

from mcp import Client, StdioServerParameters

from ollama_llm import ollama_host, ollama_think, resolve_model, tool_catalog

ROOT = Path(__file__).resolve().parent.parent
ITEMS = (
    "external_ssd",
    "monitor",
    "laptop",
    "dock",
    "headset",
    "keyboard",
    "webcam",
    "mouse",
)
REASONS = ("new_hire", "preference", "performance", "broken", "lost")
MAX_TURNS = 8


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
    item = ""
    for name in ITEMS:
        if name in lower or name.replace("_", " ") in lower:
            item = name
            break
    reason = next((name for name in REASONS if name.replace("_", " ") in lower), "")
    if not reason:
        reason = unrecognized_reason(lower)
    qty = re.search(r"\bquantity\s+(\d+)\b", lower)
    quantity = int(qty.group(1)) if qty else 1
    return {
        "employee_id": employee_id,
        "item": item,
        "reason": reason,
        "quantity": quantity,
        "text": text,
    }


def observation_dict(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        return json.loads(value)
    return {"value": value}


def payload(result) -> object:
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(result.content[0].text)


def last_observation(trace: list, tool: str) -> dict | None:
    for turn in reversed(trace):
        if turn.get("action") == tool:
            return observation_dict(turn["observation"])
    return None


def think(request: dict, trace: list, _tools: list | None = None) -> dict:
    employee = last_observation(trace, "get_employee_info")
    if employee is None:
        return {
            "thought": f"Look up employee {request['employee_id']}.",
            "kind": "call",
            "action": "get_employee_info",
            "arguments": {"employee_id": request["employee_id"]},
        }

    if employee.get("found") and last_observation(trace, "get_policy_limits") is None:
        role = employee["employment_type"]
        return {
            "thought": f"Load policy limits for {role}.",
            "kind": "call",
            "action": "get_policy_limits",
            "arguments": {"role": role},
        }

    eligibility = last_observation(trace, "check_request_eligibility")
    if eligibility is None:
        return {
            "thought": "Ask the server if this request is eligible. Do not decide locally.",
            "kind": "call",
            "action": "check_request_eligibility",
            "arguments": {
                "employee_id": request["employee_id"],
                "item": request["item"],
                "reason": request["reason"],
                "quantity": request["quantity"],
            },
        }

    outcome = eligibility.get("outcome")
    code = eligibility.get("code", "")
    if outcome == "unknown":
        if last_observation(trace, "flag_for_human_review") is None:
            return {
                "thought": f"Eligibility is unknown ({code}). Escalate instead of guessing.",
                "kind": "call",
                "action": "flag_for_human_review",
                "arguments": {
                    "employee_id": request["employee_id"],
                    "request": {
                        "employee_id": request["employee_id"],
                        "item": request["item"],
                        "reason": request["reason"],
                        "quantity": request["quantity"],
                    },
                    "reason": code,
                },
            }
        flagged = last_observation(trace, "flag_for_human_review")
        return {
            "thought": "Review queue record exists. Stop with escalate.",
            "kind": "finish",
            "decision": "escalate",
            "review_id": flagged.get("review_id"),
            "code": code,
        }
    if outcome == "eligible":
        return {
            "thought": "Eligibility is eligible. Approve.",
            "kind": "finish",
            "decision": "approve",
            "code": code,
        }
    return {
        "thought": f"Eligibility is denied ({code}). Deny.",
        "kind": "finish",
        "decision": "deny",
        "code": code,
    }


def format_turn(turn: dict) -> str:
    action = turn["action"]
    args = json.dumps(turn["arguments"], sort_keys=True)
    obs = json.dumps(turn["observation"], default=str)
    parts = []
    if turn.get("thinking"):
        parts.append(f"Thinking: {turn['thinking']}")
    parts.append(f"Thought: {turn['thought']}")
    parts.append(f"Action: {action}({args})")
    parts.append(f"Observation: {obs}")
    return "\n".join(parts)


def draft_response(request: dict, step: dict) -> str:
    decision = step["decision"]
    code = step.get("code", "")
    if decision == "approve":
        return (
            f"Approved {request['item']} for {request['employee_id']}. "
            f"check_request_eligibility returned eligible/{code}."
        )
    if decision == "deny":
        return (
            f"Denied {request['item']} for {request['employee_id']}. "
            f"check_request_eligibility returned denied/{code}."
        )
    review_id = step.get("review_id", "")
    return (
        f"Escalated {request['item']} for {request['employee_id']} "
        f"({code}). Review id {review_id}."
    )


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
    turn = {
        "thought": step["thought"],
        "action": step["action"],
        "arguments": step["arguments"],
        "observation": observation,
    }
    if step.get("thinking"):
        turn["thinking"] = step["thinking"]
    return turn


async def server_tools(client) -> list[dict]:
    listed = await client.list_tools()
    return tool_catalog(listed.tools)


async def run_once(text: str, thinker: Callable = think) -> dict:
    request = parse_request(text)
    async with Client(server_params()) as client:
        tools = await server_tools(client)
        step = thinker(request, [], tools)
        result = await client.call_tool(step["action"], step["arguments"])
        turn = make_turn(step, payload(result))
    return {"request": request, "trace": [turn]}


async def run_agent(text: str, thinker: Callable = think) -> dict:
    request = parse_request(text)
    trace: list[dict] = []
    async with Client(server_params()) as client:
        tools = await server_tools(client)
        for _ in range(MAX_TURNS):
            step = thinker(request, trace, tools)
            if step["kind"] == "finish":
                return {
                    "request": request,
                    "trace": trace,
                    "decision": step["decision"],
                    "code": step.get("code"),
                    "review_id": step.get("review_id"),
                    "response": draft_response(request, step),
                }
            result = await client.call_tool(step["action"], step["arguments"])
            trace.append(make_turn(step, payload(result)))
    raise RuntimeError("ReAct loop hit MAX_TURNS without a decision")


def print_result(result: dict) -> None:
    for turn in result["trace"]:
        print(format_turn(turn))
        print()
    if "decision" in result:
        print(f"Decision: {result['decision'].upper()}")
        print(result["response"])


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="IT equipment ReAct agent")
    parser.add_argument(
        "text",
        nargs="?",
        default="E100 wants a monitor, reason preference, quantity 1",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="one tool call, then stop",
    )
    parser.add_argument(
        "--model",
        help="Ollama model, e.g. qwen, qwen3:8b, mistral. Default is the scripted thinker.",
    )
    parser.add_argument(
        "--think",
        choices=("on", "off"),
        default="off",
        help="qwen3 thinking mode. Ignored for the scripted thinker.",
    )
    return parser.parse_args(argv)


def make_thinker(model: str | None, think_on: bool) -> Callable:
    if not model:
        return think
    host = ollama_host()
    resolved = resolve_model(model)

    def _think(request: dict, trace: list, tools: list | None = None) -> dict:
        return ollama_think(
            request,
            trace,
            host=host,
            model=resolved,
            think=think_on,
            tools=tools or [],
        )

    return _think


def main() -> None:
    args = parse_args()
    think_on = args.think == "on"
    thinker = make_thinker(args.model, think_on)
    if args.model:
        print(
            f"LLM: {resolve_model(args.model)}  think: {args.think}  "
            f"host: {ollama_host()}",
            file=sys.stderr,
        )
    runner = run_once if args.once else run_agent
    print_result(asyncio.run(runner(args.text, thinker)))


if __name__ == "__main__":
    main()
