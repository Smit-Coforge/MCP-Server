import json
import os
import re
import urllib.error
import urllib.request

DEFAULT_HOST = "http://host.docker.internal:11434"
MODEL_ALIASES = {
    "qwen": "qwen3:8b",
    "qwen3": "qwen3:8b",
    "mistral": "mistral:7b",
}
SYSTEM_PROMPT = """You are an agent handling one request. The connected server's tools are in the user message under "tools". Call only those tools.

Do not invent policy or facts a tool is supposed to look up. Finish only after a tool observation supports the decision.

When an observation includes an outcome:
- eligible: finish with decision approve and that code
- denied: finish with decision deny and that code
- unknown: call the tool whose description says it flags the case for human review, pass the code as the reason, then finish with decision escalate and the review id

Reply with one JSON object and nothing else.
Call: {"thought": "...", "kind": "call", "action": "TOOL_NAME", "arguments": {}}
Finish: {"thought": "...", "kind": "finish", "decision": "approve|deny|escalate", "code": "...", "review_id": "..."}
"""


def resolve_model(name: str) -> str:
    return MODEL_ALIASES.get(name.lower(), name)


def ollama_host() -> str:
    raw = os.environ.get("OLLAMA_HOST", DEFAULT_HOST).strip()
    if "://" not in raw:
        raw = f"http://{raw}"
    return raw.rstrip("/")


def tool_catalog(tools) -> list[dict]:
    return [
        {
            "name": tool.name,
            "description": tool.description or "",
            "input_schema": tool.input_schema,
        }
        for tool in tools
    ]


def parse_step(text: str, allowed: set[str]) -> dict:
    blob = text.strip()
    if blob.startswith("```"):
        blob = re.sub(r"^```(?:json)?\s*", "", blob)
        blob = re.sub(r"\s*```$", "", blob)
    start = blob.find("{")
    end = blob.rfind("}")
    if start < 0 or end < 0:
        raise ValueError(f"model did not return JSON: {text!r}")
    step = json.loads(blob[start : end + 1])
    if step.get("kind") == "call":
        if step.get("action") not in allowed:
            raise ValueError(f"unknown action: {step.get('action')!r}")
        step.setdefault("arguments", {})
        if not isinstance(step["arguments"], dict):
            raise ValueError("arguments must be an object")
        return step
    if step.get("kind") == "finish":
        if step.get("decision") not in {"approve", "deny", "escalate"}:
            raise ValueError(f"unknown decision: {step.get('decision')!r}")
        return step
    raise ValueError(f"unknown kind: {step.get('kind')!r}")


def _post(host: str, path: str, body: dict, timeout: int) -> dict:
    req = urllib.request.Request(
        f"{host}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError:
        raise
    except urllib.error.URLError as exc:
        raise ConnectionError(
            f"Could not reach Ollama at {host}. "
            "From this container use http://host.docker.internal:11434."
        ) from exc


def chat(host: str, model: str, messages: list, think: bool | None, timeout: int = 180) -> dict:
    body: dict = {"model": model, "messages": messages, "stream": False}
    if think is not None:
        body["think"] = think
    try:
        return _post(host, "/api/chat", body, timeout)
    except urllib.error.HTTPError:
        if think is None:
            raise
        body.pop("think", None)
        return _post(host, "/api/chat", body, timeout)


def ollama_think(
    request: dict,
    trace: list,
    *,
    host: str,
    model: str,
    think: bool,
    tools: list,
) -> dict:
    allowed = {tool["name"] for tool in tools}
    payload = {
        "tools": tools,
        "request_text": request.get("text", ""),
        "parsed": {
            "employee_id": request.get("employee_id"),
            "item": request.get("item"),
            "reason": request.get("reason"),
            "quantity": request.get("quantity"),
        },
        "trace": [
            {
                "thought": turn.get("thought"),
                "action": turn.get("action"),
                "arguments": turn.get("arguments"),
                "observation": turn.get("observation"),
            }
            for turn in trace
        ],
        "next": "Return the next JSON step.",
    }
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload)},
    ]
    data = chat(host, model, messages, think)
    message = data.get("message") or {}
    content = message.get("content") or ""
    try:
        step = parse_step(content, allowed)
    except (ValueError, json.JSONDecodeError):
        messages.append({"role": "assistant", "content": content})
        messages.append(
            {
                "role": "user",
                "content": "Put only the JSON object in the answer field. No markdown.",
            }
        )
        data = chat(host, model, messages, think)
        message = data.get("message") or {}
        content = message.get("content") or ""
        step = parse_step(content, allowed)
    if message.get("thinking"):
        step["thinking"] = message["thinking"]
    return step
