# query_database MCP Server

This is a standalone **stdio MCP server** that exposes the existing Jingxin-Agent tool:

- Tool: `query_database(question: str, empNo: str = "80049875") -> str`

It is isolated under `mcp_servers/` so it does **not** affect FastAPI app startup.

## Run

From the repo root:

```bash
# Install MCP SDK (if needed)
python3 -m pip install mcp

# Run server (stdio)
python3 -m mcp_servers.query_database_mcp.server

# Or (recommended) run with project conda env
/home/aaron/miniconda3/bin/conda run -n jingxin-agent python -m mcp_servers.query_database_mcp.server
```

## Local self-test (no network, no pytest)

```bash
python3 -m mcp_servers.query_database_mcp._selftest

# Or (recommended)
/home/aaron/miniconda3/bin/conda run -n jingxin-agent python -m mcp_servers.query_database_mcp._selftest
```

## Notes

- This server uses stdio transport. **Anything printed to stdout will break the protocol**.
  The implementation captures stdout/stderr from the underlying tool call and forwards it to stderr.
- The underlying `search.query_database` calls an HTTP backend and may require `DATABASE_URL` / credentials.
