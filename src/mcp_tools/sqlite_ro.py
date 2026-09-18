"""MCP Server：只读 SQLite 查询。

对外以 FastMCP 暴露三个工具：list_tables_tool / describe_table_tool / query_sql_tool。
SQLite 只读由三层共同保证，但三层的"硬度"并不相同（这个区分要能讲准，别把三层说成等价）：
  1) **只读 URI `?mode=ro`（唯一的硬保证）**：由 SQLite 在文件层拒绝写，
     `WITH x AS (SELECT 1) DELETE FROM t` 这类绕过文本闸的语句到这里会被
     "attempt to write a readonly database" 拦下（已实测）。它无法被 SQL 语句解除。
  2) `PRAGMA query_only=ON` + 显式 `enable_load_extension(False)`（纵深，可被带内削弱）：
     query_only 是连接级开关，**能被一条通过文本闸的 `PRAGMA query_only=OFF` 关掉**（已实测），
     所以它只算"对抗意外写"的第二道，而不是"对抗对抗性输入"的保证；
     `SELECT load_extension(...)` 能过文本闸，靠 `enable_load_extension(False)` 拒绝（实测报 not authorized）。
  3) 应用层再叠加 validate_readonly_sql：放行以 SELECT/WITH/EXPLAIN/PRAGMA 开头的单条语句，
     拦多语句/注释/UNION。它只是**粗筛**——见 security.validate_readonly_sql 的已知绕过清单。
  另有：结果行数上限（MAX_ROWS=200，用 fetchmany 而非 fetchall，超大结果集不会一次性进内存）；
  语句执行级超时**未实现**，不对外宣称。

【OOP 说明】本模块主体是函数（_connect/_rows_to_text/各查询函数），FastMCP 的 @mcp.tool
装饰把它们"变成工具"。真正需要一个"类"的是当你想对同一份代码同时服务多个不同 db 连接
或做依赖注入做单测时可抽成：
    class ReadonlySqliteServer:
        def __init__(self, db_path): self.db_path = db_path        # 持有路径 => 状态
        def list_tables(self) -> str: ...
        def describe_table(self, table) -> str: ...
        def as_mcp(self) -> FastMCP:  # 用 self 方法装饰工具并返回 FastMCP
本项目 demo 只需要固定一个 db，所以用轻量函数 + create_server(db_path) 闭包注入即可。

运行（stdio）：python -m mcp_tools.sqlite_ro
环境变量：MCP_SQLITE_DB（默认当前目录 demo.db，可用 scripts/make_demo_db.py 生成）
"""

import os
import sqlite3
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .security import validate_readonly_sql

MAX_ROWS = 200
MAX_TABLES = 200   # list_tables 结果上限，与 query_sql 的行数上限保持一致的收敛口径


def _connect(db_path: Path) -> sqlite3.Connection:
    """以只读模式打开 SQLite 连接（DB 层强制只读的落点）。

    每次调用都新开连接、用完即 close（工具调用本身低频，无池化成本）；
    关键点：uri=f"{……}?mode=ro" 让 SQLite 以只读 URI 打开（file: 探针拒绝写），
    `PRAGMA query_only=ON` 再补一道保险——两道都在 DB 层，独立于 SQL 文本校验，
    因此"文本层漏放行也无法真正写库"。
    """
    if not db_path.is_file():
        raise FileNotFoundError(f"数据库不存在：{db_path}（先运行 scripts/make_demo_db.py）")
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    # busy_timeout 放在 query_only 之前：只读连接同样会撞上 SQLITE_BUSY
    # （别的进程正在写、或有未提交事务），不设超时就直接抛 "database is locked"。
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA query_only = ON")
    # 第三道：显式关闭扩展加载。`SELECT load_extension('...')` 能过 SQL 文本闸，
    # 而它一旦可用就能加载任意 .dll/.so 绕过一切 SQL 层限制。
    # Python 的 sqlite3 默认就是关闭的（实测该语句会报 "not authorized"），
    # 这里显式声明一次，是为了让这条防线"有意为之"，而不是依赖解释器默认值——
    # 换解释器/换包装库/未来版本改默认时不会静默失效。
    conn.enable_load_extension(False)
    return conn


def _rows_to_text(columns: list[str], rows: list[tuple], truncated: bool) -> str:
    lines = [" | ".join(str(c) for c in columns)]
    for row in rows:
        lines.append(" | ".join("" if v is None else str(v) for v in row))
    if truncated:
        lines.append(f"...（仅显示前 {MAX_ROWS} 行，请用更精确的 SQL 缩小范围）")
    return "\n".join(lines)


def list_tables(db_path: str) -> str:
    """返回业务表名（按字母序，每行一个），超过 MAX_TABLES 即截断。

    排除 sqlite 内部表（NOT LIKE 'sqlite_%'）：对模型暴露的就是"干净的可查表清单"。
    截断上限是为了和 query_sql/glob 的口径一致——这段文本会整段进模型上下文，
    一个上千张表的库足以把上下文挤爆（此前只有 query_sql 和 glob 有上限）。
    """
    conn = _connect(Path(db_path))
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name LIMIT ?",
            (MAX_TABLES + 1,),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return "（空库）"
    truncated = len(rows) > MAX_TABLES
    names = [r[0] for r in rows[:MAX_TABLES]]
    text = "\n".join(names)
    if truncated:
        text += f"\n...（表过多，仅显示前 {MAX_TABLES} 张）"
    return text


def describe_table(db_path: str, table: str) -> str:
    """查看某表结构：列名/类型/是否可空/主键(用 PRAGMA table_info)。

    - 先用**绑定参数**在 sqlite_master 里查真实表名，查不到直接返回"表不存在"；
    - PRAGMA 的表名需要拼进 SQL，所以拼进去的是"从 sqlite_master 取回的真实表名"，
      并按 SQLite 标识符规则把双引号双写转义 —— 注入面在绑定查询那一步就被切断；
    - 刻意不用 ASCII 正则限制表名：那会导致 list_tables 能列出的中文表名
      （如 `订单`）在 describe_table 里被误报"表不存在"。
    """
    conn = _connect(Path(db_path))
    try:
        row = conn.execute(
            # COLLATE NOCASE：SQLite 自身对表名大小写不敏感（PRAGMA/SELECT 都认 REGIONS），
            # 这里若用默认 BINARY 比较，会出现"表能查、却 describe 不了"的错误答案
            "SELECT name FROM sqlite_master WHERE type='table' AND name=? COLLATE NOCASE",
            (table,),
        ).fetchone()
        if row is None:
            return f"表不存在：{table}"
        quoted = str(row[0]).replace('"', '""')
        rows = conn.execute(f'PRAGMA table_info("{quoted}")').fetchall()
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
    """执行一条只读 SQL 并返回表格文本，最多 MAX_ROWS(200) 行。

    流程：先 validate_readonly_sql（文本闸）→ 再 _connect（只读 URI/query_only DB 闸）→
    执行 → fetchmany(MAX_ROWS+1) 探测是否超量（多取一行来判断 truncated）。

    安全要点：真正执行的 SQL 是模型给的文本，因此两道闸缺一不可；即便文本校验写出 bug，
    DB 层也只读，写不进去（纵深防御）。结果用 fetchmany 而非 fetchall，超大结果集不会一次
    拉爆内存，只取前 200 行返回给模型即可。
    """
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
    resolved = Path(
        db_path or os.getenv("MCP_SQLITE_DB", str(Path.cwd() / "demo.db"))
    ).resolve()
    # 与 files_safe.create_server 一致：启动即校验，避免"看起来连上了、每次调用都报错"。
    # 尤其 demo.db 被 .gitignore 忽略，新克隆下来必须先生成，否则配置一加载就是坏的。
    if not resolved.is_file():
        raise FileNotFoundError(
            f"数据库不存在：{resolved}（请先运行 python scripts/make_demo_db.py）"
        )
    db_path = str(resolved)
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
        """执行一条只读查询（以 SELECT/WITH/EXPLAIN/PRAGMA 开头的单条语句），返回表格文本，最多 200 行。"""
        return query_sql(db_path, sql)

    return mcp


if __name__ == "__main__":
    create_server().run()
