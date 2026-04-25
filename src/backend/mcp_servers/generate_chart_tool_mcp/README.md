# generate_chart_tool MCP Server

Standalone **stdio MCP server** exposing Jingxin-Agent tool:

- Tool: `generate_chart_tool(data: str, query: str) -> str`

## Run

```bash
python3 -m pip install mcp

python3 -m mcp_servers.generate_chart_tool_mcp.server

# Or (recommended)
/home/aaron/miniconda3/bin/conda run -n jingxin-agent python -m mcp_servers.generate_chart_tool_mcp.server
```

## Local self-test

```bash
python3 -m mcp_servers.generate_chart_tool_mcp._selftest

# Or (recommended)
/home/aaron/miniconda3/bin/conda run -n jingxin-agent python -m mcp_servers.generate_chart_tool_mcp._selftest
```

## Notes

- StdIO transport: underlying tool prints are captured and forwarded to stderr.
- Underlying chart generation may require external LLM credentials at runtime.
