"""MCP Server：受限 HTTP 抓取（域名白名单 + 防内网/SSRF）。

- validate_fetch_url：仅 http/https、域名白名单、字面量内网/保留 IP 拒绝；
- httpx 超时 + 响应体积上限，防慢/大响应拖垮调用。

运行：python -m mcp_tools.http_fetch
环境变量：MCP_HTTP_ALLOW（逗号分隔域名白名单，如 "example.com,github.com"）
"""

import os

import httpx
from mcp.server.fastmcp import FastMCP

from .security import validate_fetch_url

MAX_RESPONSE_CHARS = 20_000
TIMEOUT_SECONDS = 10.0
DEFAULT_ALLOW = frozenset({"example.com"})


def _allow_from_env() -> frozenset[str]:
    raw = os.getenv("MCP_HTTP_ALLOW", "")
    domains = {d.strip().lower() for d in raw.split(",") if d.strip()}
    return frozenset(domains) if domains else DEFAULT_ALLOW


async def fetch_url(url: str, allow_domains: frozenset[str]) -> str:
    validate_fetch_url(url, allow_domains)
    async with httpx.AsyncClient(
        timeout=TIMEOUT_SECONDS, follow_redirects=True, headers={"User-Agent": "mcp-tools/0.1"}
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
    text = resp.text
    if len(text) > MAX_RESPONSE_CHARS:
        text = text[:MAX_RESPONSE_CHARS] + f"\n...（响应过大，截断前 {MAX_RESPONSE_CHARS} 字符）"
    return text


def create_server(allow_domains: frozenset[str] | None = None) -> FastMCP:
    domains = allow_domains if allow_domains is not None else _allow_from_env()
    mcp = FastMCP("http-fetch")

    @mcp.tool()
    async def fetch_url_tool(url: str) -> str:
        """抓取一个在域名白名单内的网页正文（防内网/SSRF，超时 10s）。"""
        return await fetch_url(url, domains)

    return mcp


if __name__ == "__main__":
    create_server().run()
