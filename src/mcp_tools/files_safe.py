"""MCP Server：受限文件系统（白名单根目录内 list/read/glob）。

- 所有访问先过 resolve_within_root：解析 realpath 后必须落在白名单根目录内；
- 读取有大小上限，glob 限制在根目录内递归搜索，防止拖垮/越权。

运行：python -m mcp_tools.files_safe
环境变量：MCP_FILES_ROOT（默认当前目录）
"""

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .security import resolve_within_root

MAX_READ_CHARS = 200_000


def list_dir(root: Path, relative_dir: str = ".") -> str:
    target = resolve_within_root(relative_dir, root)
    if not target.is_dir():
        return f"不是目录：{relative_dir}"
    items = sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir())
    return "\n".join(items) or "（空目录）"


def read_file(root: Path, relative_path: str) -> str:
    target = resolve_within_root(relative_path, root)
    if not target.is_file():
        return f"不是文件：{relative_path}"
    text = target.read_text(encoding="utf-8", errors="replace")
    if len(text) > MAX_READ_CHARS:
        text = text[:MAX_READ_CHARS] + f"\n...（内容过长，已截断前 {MAX_READ_CHARS} 字符）"
    return text


MAX_GLOB_RESULTS = 200


def glob_files(root: Path, pattern: str) -> str:
    if not pattern:
        return "（pattern 不能为空）"
    hits = [str(p.relative_to(root)) for p in root.rglob(pattern) if p.is_file()]
    hits.sort()
    if not hits:
        return "（无匹配文件）"
    if len(hits) > MAX_GLOB_RESULTS:
        hits = hits[:MAX_GLOB_RESULTS]
        return "\n".join(hits) + (
            f"\n...（命中过多，仅显示前 {MAX_GLOB_RESULTS} 个，请用更精确的 pattern）"
        )
    return "\n".join(hits)


def create_server(root: str | None = None) -> FastMCP:
    root_path = Path(root or os.getenv("MCP_FILES_ROOT", ".")).resolve()
    if not root_path.is_dir():
        raise FileNotFoundError(f"白名单根目录不存在：{root_path}")
    mcp = FastMCP("files-safe")

    @mcp.tool()
    def list_dir_tool(relative_dir: str = ".") -> str:
        """列出白名单根目录下某目录的内容（不可越出根目录）。"""
        return list_dir(root_path, relative_dir)

    @mcp.tool()
    def read_file_tool(relative_path: str) -> str:
        """读取白名单根目录内某文件的文本（UTF-8，截断超大文件）。"""
        return read_file(root_path, relative_path)

    @mcp.tool()
    def glob_files_tool(pattern: str) -> str:
        """按 glob 在根目录内递归查找文件（如 '**/*.md'）。"""
        return glob_files(root_path, pattern)

    return mcp


if __name__ == "__main__":
    create_server().run()
