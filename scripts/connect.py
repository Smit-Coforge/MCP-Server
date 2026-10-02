import asyncio
import os
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters

ROOT = Path(__file__).resolve().parents[1]


def payload(result) -> object:
    return result.structured_content or result.content[0].text


def server_params(module: str) -> StdioServerParameters:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", module],
        cwd=str(ROOT),
        env=env,
    )


async def ping() -> None:
    async with Client(server_params("minimal_server")) as client:
        print("registered tools:", [tool.name for tool in (await client.list_tools()).tools])
        print("ping:", payload(await client.call_tool("ping", {})))


async def tools() -> None:
    async with Client(server_params("server")) as client:
        print("registered tools:", [tool.name for tool in (await client.list_tools()).tools])
        print(
            "get_employee_info E100:",
            payload(await client.call_tool("get_employee_info", {"employee_id": "E100"})),
        )
        print(
            "get_employee_info E999:",
            payload(await client.call_tool("get_employee_info", {"employee_id": "E999"})),
        )
        print(
            "get_policy_limits intern:",
            payload(await client.call_tool("get_policy_limits", {"role": "intern"})),
        )
        print(
            "check_request_eligibility:",
            payload(
                await client.call_tool(
                    "check_request_eligibility",
                    {
                        "employee_id": "E200",
                        "item": "laptop",
                        "reason": "preference",
                        "quantity": 1,
                    },
                )
            ),
        )
        print(
            "flag_for_human_review:",
            payload(
                await client.call_tool(
                    "flag_for_human_review",
                    {
                        "employee_id": "E999",
                        "request": {
                            "employee_id": "E999",
                            "item": "mouse",
                            "reason": "broken",
                            "quantity": 1,
                        },
                        "reason": "not_found",
                    },
                )
            ),
        )


if __name__ == "__main__":
    asyncio.run(ping() if sys.argv[-1] == "ping" else tools())
