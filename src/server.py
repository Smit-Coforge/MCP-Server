from mcp.server import MCPServer

import services

mcp = MCPServer("it-request")


@mcp.tool()
def get_employee_info(employee_id: str) -> dict:
    """Return employment type, tenure, and tracked equipment for an employee."""
    return services.get_employee_info(employee_id)


@mcp.tool()
def get_policy_limits(role: str) -> dict:
    """Return catalog rows that an employment type may receive."""
    return services.get_policy_limits(role)


@mcp.tool()
def check_request_eligibility(
    employee_id: str, item: str, reason: str, quantity: int
) -> dict:
    """Return eligible, denied, or unknown for a request against policy data."""
    return services.check_request_eligibility(employee_id, item, reason, quantity)


@mcp.tool()
def flag_for_human_review(employee_id: str, request: dict, reason: str) -> dict:
    """Append a review-queue record and return its id."""
    return services.flag_for_human_review(employee_id, request, reason)


if __name__ == "__main__":
    mcp.run()
