"""MCP Server：受限文件系统（白名单根目录内 list_dir / read_file / glob）。

安全范式：一切路径先过 `resolve_within_root`（security.py）——realpath 归一化后必须落在
白名单根目录内，防目录穿越。所有读取限制在 root 之下进行：
  - list_dir：列某目录条目（root 的相对目录）
  - read_file：读某文件文本（UTF-8，超大文件截断）
  - glob_files：按 glob 在 root 内递归查找（v1: 无前缀点目录，rglob 不跟符号链）

【OOP 说明】files_safe 的差异点是"root 白名单目录"是一次性配置——这让它是三件里最接近
"对象状态"的模块。现状用 create_server(root) 在闭包捕获 root（函数式注入）；若你希望换成
显式类，语义等价地可写：
    class FileServer:
        def __init__(self, root: Path):
            self.root = root.resolve()          # 启动时即解析固定住，防止中途被人替换 cwd
            if not self.root.is_dir(): raise ...
        def list(self, rel="."): return list_dir(self.root, rel)
        def read(self, rel): return read_file(self.root, rel)
        def glob(self, pat): return glob_files(self.root, pat)
        def as_mcp(self): return FastMCP("files-safe") decorated with self methods
主要动机是保留"root 解析一次"的状态与启动校验；闭包已做到同样效果，但类写法更显式、
更方便以后叠加每-server 专属配置。

运行：python -m mcp_tools.files_safe
环境变量：MCP_FILES_ROOT（默认当前目录）
"""

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .security import resolve_within_root

MAX_READ_CHARS = 200_000  # 单文件最大读取字符；超过即截断（不是先整读再截断的"体积预算"，v1 简化）


def list_dir(root: Path, relative_dir: str = ".") -> str:
    """列出 root 下某相对目录的内容（条目名，目录带尾 /）。"""
    target = resolve_within_root(relative_dir, root)
    if not target.is_dir():
        return f"不是目录：{relative_dir}"
    items = sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir())
    return "\n".join(items) or "（空目录）"


def read_file(root: Path, relative_path: str) -> str:
    """读取 root 白名单内某文件的 UTF-8 文本，超过 MAX_READ_CHARS 字符即截断标记。

    先 resolve_within_root（越界直接 ValueError），再 is_file 校验，然后整读+按字符截断；
    读取前不做字节级体积预算（v1 简化），超大二进制文件不在工具设计目标内。
    """
    target = resolve_within_root(relative_path, root)
    if not target.is_file():
        return f"不是文件：{relative_path}"
    text = target.read_text(encoding="utf-8", errors="replace")
    if len(text) > MAX_READ_CHARS:
        text = text[:MAX_READ_CHARS] + f"\n...（内容过长，已截断前 {MAX_READ_CHARS} 字符）"
    return text


MAX_GLOB_RESULTS = 200  # glob 结果上限；超过仅展示前 200 个并提示


def glob_files(root: Path, pattern: str) -> str:
    """在 root 内按 glob 递归查找文件，返回相对 root 的路径列表（按字典序）。

    用 pathlib.rglob（不默认跟随目录 symlink），命中过多时只返回前 MAX_GLOB_RESULTS 条提示
    缩小 pattern——防止一整棵大目录被模型一条 glob 全部拉回。
    """
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
    """工厂：构造一个绑定 root 只读目录的 files-safe MCP Server。

    通过闭包把 root_path 捕获进来，三个工具函数内部 token 持有该 root——函数式依赖注入。
    root 来源于 env(MCP_FILES_ROOT) 或显式传入；此处 resolve+is_dir 校验放在"创建即失败"，
    避免起了一个指向不存在目录的 Server 到调用时才报错。
    """
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
