"""远程网关：把 stdio 型 MCP Server 以 Streamable HTTP 暴露到网络上，并加 Bearer 鉴权。

- 每个 Server 挂一个路径：/mcp/sqlite、/mcp/files、/mcp/http
- 所有 /mcp/* 请求先校验 Authorization: Bearer <token>（token 从环境变量 MCP_TOOLS_TOKEN 读取）
- token 未配置或为空直接拒绝启动（宁可不开，不可裸奔）

启动：uvicorn mcp_tools.gateway:app --host 0.0.0.0 --port 8001
示例：MCP_TOOLS_TOKEN=dev-token uvicorn mcp_tools.gateway:app
"""

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .files_safe import create_server as create_files_server
from .http_fetch import create_server as create_http_server
from .sqlite_ro import create_server as create_sqlite_server


def _token() -> str:
    token = os.getenv("MCP_TOOLS_TOKEN", "")
    if not token or len(token) < 8:
        raise RuntimeError("远程网关必须配置 MCP_TOOLS_TOKEN（>=8 位），否则拒绝启动")
    return token


def build_app() -> FastAPI:
    token = _token()
    app = FastAPI(title="mcp-tools gateway", version="0.1.0")

    servers = {
        "sqlite": create_sqlite_server(os.getenv("MCP_SQLITE_DB", str(Path.cwd() / "demo.db"))),
        "files": create_files_server(os.getenv("MCP_FILES_ROOT", ".")),
        "http": create_http_server(),
    }
    for name, server in servers.items():
        try:
            sub_app = server.streamable_http_app()
        except AttributeError as exc:  # pragma: no cover
            raise RuntimeError("当前 mcp 版本不支持 streamable_http_app，请升级 mcp>=1.9") from exc
        app.mount(f"/mcp/{name}", sub_app)

    @app.middleware("http")
    async def require_bearer(request: Request, call_next):  # noqa: ANN001
        if request.url.path.startswith("/mcp"):
            auth = request.headers.get("authorization", "")
            if auth != f"Bearer {token}":
                return JSONResponse(status_code=401, content={"detail": "unauthorized"})
        return await call_next(request)

    @app.get("/")
    async def index() -> dict:
        return {
            "name": "mcp-tools gateway",
            "endpoints": {
                name: f"/mcp/{name}",
                "health": "/health",
            },
            "usage": "MCP 客户端用 Streamable HTTP 连接，需带 Authorization: Bearer <token>",
        }

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    return app


app = build_app()
