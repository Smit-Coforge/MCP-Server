from mcp.server import MCPServer

mcp = MCPServer("it-request-minimal")


@mcp.tool()
def ping() -> dict:
    """Return ok so a client can confirm this server registered a tool."""
    return {"ok": True}


if __name__ == "__main__":
    mcp.run()
