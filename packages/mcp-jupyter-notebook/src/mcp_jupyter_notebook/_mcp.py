"""MCP SDK compatibility imports for supported major versions."""

try:
    from mcp.server.mcpserver import Context
    from mcp.server.mcpserver import MCPServer as FastMCP

    MCP_V2 = True
except ImportError:  # MCP SDK 1.x
    from mcp.server.fastmcp import Context, FastMCP

    MCP_V2 = False


async def run_http_server(mcp: FastMCP, transport: str, *, host: str, port: int) -> None:
    """Apply HTTP bind settings using the supported SDK's public interface."""
    run = mcp.run_sse_async if transport == "sse" else mcp.run_streamable_http_async
    if MCP_V2:
        await run(host=host, port=port)
    else:
        mcp.settings.host = host
        mcp.settings.port = port
        await run()


__all__ = ["Context", "FastMCP"]
