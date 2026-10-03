import json
import os
import re
import urllib.error
import urllib.request

DEFAULT_HOST = "http://host.docker.internal:11434"
MODEL = "qwen3:8b"
SYSTEM_PROMPT = """You are an agent handling one request. The connected server's tools are in the user message under "tools". Call only those tools.

Read request_text yourself. It is ordinary language. A number written next to an item is the quantity, even when the word quantity is absent. Send the catalog key for an item: singular, lowercase, underscores. external ssd is external_ssd. If the person says it broke, the reason is broken. Use broken, lost, new_hire, preference, or performance only when the words mean that. Otherwise send the person's own phrase.

If request_text names more than one item, check each item on its own with the quantity written next to that item. Finish once, after every named item has an observation. Do not stop after the first item.

Do not invent policy or facts a tool is supposed to look up. Finish only after a tool observation supports the decision.

When an observation includes an outcome:
- eligible: finish with decision approve and that code
- denied: finish with decision deny and that code
- unknown: call the tool whose description says it flags the case for human review, pass that observation's code as the reason, then finish with decision escalate and the review id. Flag each unknown outcome once. Do not flag the same case again.

Reply with one JSON object and nothing else.
Call: {"thought": "...", "kind": "call", "action": "TOOL_NAME", "arguments": {}}
Finish: {"thought": "...", "kind": "finish", "decision": "approve|deny|escalate", "code": "...", "review_id": "..."}
"""


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
    starts = [index for index in (blob.find("{"), blob.find("[")) if index >= 0]
    if not starts:
        raise ValueError(f"model did not return JSON: {text!r}")
    start = min(starts)
    try:
        step, _ = json.JSONDecoder().raw_decode(blob[start:])
    except json.JSONDecodeError as exc:
        raise ValueError(f"model did not return JSON: {text!r}") from exc
    if not isinstance(step, dict):
        raise TypeError(f"model did not return a JSON object: {text!r}")
    if step.get("kind") in allowed and not step.get("action"):
        step["action"] = step["kind"]
        step["kind"] = "call"
    if step.get("kind") == "call" or (step.get("kind") is None and step.get("action")):
        step["kind"] = "call"
        if step.get("action") not in allowed:
            raise ValueError(f"unknown action: {step.get('action')!r}")
        step.setdefault("arguments", {})
        if not isinstance(step["arguments"], dict):
            raise ValueError("arguments must be an object")
        return step
    if step.get("kind") == "finish" or (step.get("kind") is None and step.get("decision")):
        step["kind"] = "finish"
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


def chat(host: str, messages: list, timeout: int = 180) -> dict:
    body: dict = {
        "model": MODEL,
        "messages": messages,
        "stream": False,
        "think": False,
        "options": {"temperature": 0},
    }
    return _post(host, "/api/chat", body, timeout)


def ollama_think(
    request: dict,
    trace: list,
    *,
    host: str,
    tools: list,
) -> dict:
    allowed = {tool["name"] for tool in tools}
    payload = {
        "tools": tools,
        "request_text": request.get("text", ""),
        "trace": [
            {
                "thought": turn.get("thought"),
                "action": turn.get("action"),
                "arguments": turn.get("arguments"),
                "observation": turn.get("observation"),
            }
            for turn in trace
        ],
        "next": "Read request_text and return the next JSON step.",
    }
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload)},
    ]
    data = chat(host, messages)
    message = data.get("message") or {}
    content = message.get("content") or ""
    try:
        step = parse_step(content, allowed)
    except (ValueError, TypeError, json.JSONDecodeError):
        messages.append({"role": "assistant", "content": content})
        messages.append(
            {
                "role": "user",
                "content": "Put only the JSON object in the answer field. No markdown.",
            }
        )
        data = chat(host, messages)
        message = data.get("message") or {}
        content = message.get("content") or ""
        try:
            step = parse_step(content, allowed)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"The model did not return one JSON step. Last reply: {content!r}") from exc
    return step
