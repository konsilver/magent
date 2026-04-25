# ai_chain_information_mcp

A grouped stdio MCP server that provides **three related tools** under one MCP server so that
**pluggability is at the MCP level** (enable/disable the whole server in `configs/mcp_config.py`).

Tools:
- `get_chain_information(chain_id: str)`
- `get_industry_news(keyword?: str, news_type?: str, chain?: str, region?: str)`
- `get_latest_ai_news()`

Implementation delegates to the existing `*_mcp/impl.py` modules to avoid duplication.

## Run
```bash
python -m mcp_servers.ai_chain_information_mcp.server
```

## Selftest
```bash
python -m mcp_servers.ai_chain_information_mcp._selftest
```
