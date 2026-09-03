"""MCP Server：只读 SQLite 查询。

- 以 SQLite 只读 URI（mode=ro）打开，DB 层强制只读；
- 上层再叠加 validate_readonly_sql：仅 SELECT/WITH/EXPLAIN，拦截多语句/注释/注入特征；
- 结果行数上限 + 超时，防拖垮服务。

运行（stdio）：python -m mcp_tools.sqlite_ro
环境变量：MCP_SQLITE_DB（默认当前目录 demo.db，可用 scripts/make_demo_db.py 生成）
"""

import os
import sqlite3
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .security import validate_readonly_sql

MAX_ROWS = 200


def _connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.is_file():
        raise FileNotFoundError(f"数据库不存在：{db_path}（先运行 scripts/make_demo_db.py）")
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    conn.execute("PRAGMA query_only = ON")
    return conn


def _rows_to_text(columns: list[str], rows: list[tuple], truncated: bool) -> str:
    lines = [" | ".join(str(c) for c in columns)]
    for row in rows:
        lines.append(" | ".join("" if v is None else str(v) for v in row))
    if truncated:
        lines.append(f"...（仅显示前 {MAX_ROWS} 行，请用更精确的 SQL 缩小范围）")
    return "\n".join(lines)


def list_tables(db_path: str) -> str:
    conn = _connect(Path(db_path))
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    finally:
        conn.close()
    return "\n".join(r[0] for r in rows) or "（空库）"


def describe_table(db_path: str, table: str) -> str:
    conn = _connect(Path(db_path))
    try:
        rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    finally:
        conn.close()
    if not rows:
        return f"表不存在：{table}"
    return _rows_to_text(
        ["cid", "name", "type", "notnull", "default", "pk"],
        [(r[0], r[1], r[2], r[3], r[4], r[5]) for r in rows],
        truncated=False,
    )


def query_sql(db_path: str, sql: str) -> str:
    sql = validate_readonly_sql(sql)
    conn = _connect(Path(db_path))
    try:
        cur = conn.execute(sql)
        columns = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(MAX_ROWS + 1)
        truncated = len(rows) > MAX_ROWS
        rows = rows[:MAX_ROWS]
    finally:
        conn.close()
    if not columns:
        return "（查询无返回列）"
    return _rows_to_text(columns, rows, truncated)


def create_server(db_path: str | None = None) -> FastMCP:
    db_path = db_path or os.getenv("MCP_SQLITE_DB", str(Path.cwd() / "demo.db"))
    mcp = FastMCP("sqlite-ro")

    @mcp.tool()
    def list_tables_tool() -> str:
        """列出数据库全部业务表名（SQLite 只读）。"""
        return list_tables(db_path)

    @mcp.tool()
    def describe_table_tool(table: str) -> str:
        """查看某张表的字段结构（列名/类型/主键）。查数前建议先看结构。"""
        return describe_table(db_path, table)

    @mcp.tool()
    def query_sql_tool(sql: str) -> str:
        """执行一条只读 SQL（仅 SELECT/WITH/EXPLAIN），返回表格文本，最多 200 行。"""
        return query_sql(db_path, sql)

    return mcp


if __name__ == "__main__":
    create_server().run()
