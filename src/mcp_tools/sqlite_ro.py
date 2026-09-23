"""MCP Server：只读 SQLite 查询。

对外以 FastMCP 暴露三个工具：list_tables_tool / describe_table_tool / query_sql_tool。
SQLite 只读由三层共同保证，但三层的"硬度"并不相同（三层不可当成等价，也不可混为一类）：
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
MAX_CELL_CHARS = 2000     # 单个单元格上限：行数上限约束不了"单行一个超大值"
MAX_OUTPUT_CHARS = 20000  # 单次工具输出总上限（与 read_file 的 MAX_READ_CHARS 同属收敛口径）
# 结尾那行"...（提示）"是在渲染循环 break 之后无条件追加的，因此必须在预算里先给它留位置，
# 否则"单次输出 ≤ MAX_OUTPUT_CHARS"这条声明会失效（实测溢出到 20054 字符）。
_NOTE_RESERVE_CHARS = 200


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
    # busy_timeout 显式写死：Python 的 sqlite3.connect 默认 timeout=5.0 **已经等于** 5000ms，
    # 所以这行不是为了"不设就会抛 database is locked"（不设也是 5000），而是把这个值钉在
    # 本模块里——将来若有人改 connect 的 timeout（或换包装库），这里不会跟着变。
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA query_only = ON")
    # 第三道：显式关闭扩展加载。`SELECT load_extension('...')` 能过 SQL 文本闸，
    # 而它一旦可用就能加载任意 .dll/.so 绕过一切 SQL 层限制。
    # Python 的 sqlite3 默认就是关闭的（实测该语句会报 "not authorized"），
    # 这里显式声明一次，是为了让这条防线"有意为之"，而不是依赖解释器默认值——
    # 换解释器/换包装库/未来版本改默认时不会静默失效。
    conn.enable_load_extension(False)
    return conn


def _clip_cell(value) -> tuple[str, bool]:
    """把单元格渲染成文本并做长度收敛，返回 (文本, 是否被截断)。"""
    if value is None:
        return "", False
    text = value if isinstance(value, str) else str(value)
    if len(text) > MAX_CELL_CHARS:
        return text[:MAX_CELL_CHARS] + f"…（单元格过长，已截断前 {MAX_CELL_CHARS} 字符）", True
    return text, False


def _rows_to_text(columns: list[str], rows: list[tuple], truncated: bool) -> str:
    """渲染表格文本，并对"体量"做收敛：行数上限之外，再加单元格上限与总输出上限。

    为什么行数上限不够：`fetchmany(MAX_ROWS+1)` 只约束行数，单行里的一个超大值
    （例如 `SELECT zeroblob(5000000)` 或一条超长 TEXT 列）仍会展开成上千万字符的
    Python 字符串——既能把 server 进程的内存打爆，也会把模型上下文挤爆。
    文件侧早有 MAX_READ_CHARS 收敛，这里把 SQL 侧的对称口径补上。

    截断提示必须**按实际触发的那道闸**来说：上面三道闸（行数 / 单元格 / 总输出）
    是彼此独立的，早期实现一律把"单元格上限 + 总输出上限"两句都印出来，于是
    "行数很多但每列都很短"的场景会看到一句根本没发生的"单元格过长"，
    而真正生效的"只显示了前 N 行"反而因为行数闸没触发而不说——模型据此以为看到了全量。
    实测：300 行 × 约 145 字符的查询实际只渲染了 136 行，输出里却写着"仅显示前 200 行"。

    补充（两道闸**同时**生效时的口径，实测 134 行却称"前 200 行"就是这个分支）：
    `truncated` 只表示"fetchmany 取到了第 201 行"，它**不等于**"实际渲染了 200 行"。
    所以两闸同开时不能再声称"仅显示前 MAX_ROWS 行"，必须报实际渲染行数——否则
    本函数要修的那个毛病会从"从句式"换个分支原样长回来。
    """
    header, header_clipped = _clip_cell(" | ".join(str(c) for c in columns))
    lines = [header]
    used = len(header)
    cell_clipped = False
    output_clipped = False
    budget = MAX_OUTPUT_CHARS - min(_NOTE_RESERVE_CHARS, MAX_OUTPUT_CHARS // 4)
    for row in rows:
        rendered = []
        for value in row:
            cell, was_clipped = _clip_cell(value)
            rendered.append(cell)
            cell_clipped = cell_clipped or was_clipped
        line = " | ".join(rendered)
        if used + len(line) + 1 > budget:
            output_clipped = True
            break
        lines.append(line)
        used += len(line) + 1

    shown = len(lines) - 1   # 实际渲染出来的数据行数（不含表头）
    notes: list[str] = []
    if header_clipped:
        # 表头被截断不是"单元格被截断"：混为一谈会让模型以为数据被砍了，而列名被砍反而没人说
        notes.append(f"列名过长，已按 {MAX_CELL_CHARS} 字符截断")
    if cell_clipped:
        notes.append(f"有单元格超过 {MAX_CELL_CHARS} 字符，已截断")
    if output_clipped:
        notes.append(f"输出超过 {MAX_OUTPUT_CHARS} 字符上限，已提前停止渲染")
    if shown == 0 and rows:
        # 单行过宽：一行都放不下。此时说"只有前 0 行能显示"是无意义措辞
        notes.append("单行过宽，整行都放不下，请减少列数或缩短列宽")
    elif truncated and output_clipped:
        # 两闸同开：实际渲染行数由输出闸决定，不能再声称"显示了前 MAX_ROWS 行"
        notes.append(
            f"已取回 {len(rows)} 行（行数上限 {MAX_ROWS}），其中只有前 {shown} 行能显示，"
            "请用更精确的 SQL 缩小范围"
        )
    elif truncated:
        # 行数闸触发 = 数据库里还有更多行没取回来，且这一批都已渲染出来
        notes.append(f"仅显示前 {MAX_ROWS} 行，请用更精确的 SQL 缩小范围")
    elif output_clipped:
        # 行数闸没触发，但输出闸先掐掉了尾巴：必须说清"少了几行、为什么"
        notes.append(
            f"已取回 {len(rows)} 行，其中只有前 {shown} 行能显示（受总输出上限所限），"
            "请用更精确的 SQL 缩小范围"
        )
    if not rows and not notes:
        # 空结果集此前只回一行表头（如 `id | v`）——与"一行数据、其两列恰好是 id/v"同形，
        # 模型读不出"0 行"，可能据此以为查到了东西。显式标出来（与 list_dir 的"（空目录）"同口径）。
        lines.append("（0 行）")
    if notes:
        lines.append("...（" + "；".join(notes) + "）")
    return "\n".join(lines)


def list_tables(db_path: str) -> str:
    """返回业务表名（按字母序，每行一个），超过 MAX_TABLES 或 MAX_OUTPUT_CHARS 即截断。

    排除 sqlite 内部表用 `name NOT GLOB 'sqlite_*'` 而**不是** `NOT LIKE 'sqlite_%'`：
    LIKE 里 `_` 是单字符通配符，`'sqlite_%'` 实际匹配"sqlite + 任意 1 字符 + 任意后缀"，
    于是所有形如 `sqlitemp` / `sqlitex_hidden` 的**合法用户表**都被当内部表静默隐藏
    （实测：库里 4 张表，list_tables 只返回 2 张，而 query_sql 照样能查那张"不存在"的表）。
    SQLite 保留的只是**字面** `sqlite_` 前缀（建表时会被拒绝），GLOB 的 `*` 通配不涉及 `_` 语义。
    截断上限是为了和 query_sql/glob 的口径一致——这段文本会整段进模型上下文，
    一个上千张表的库足以把上下文挤爆（此前只有 query_sql 和 glob 有上限）；
    条数之外还要有字符上限：200 个超长表名实测能到 40889 字符，是 MAX_OUTPUT_CHARS 的两倍。
    """
    conn = _connect(Path(db_path))
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT GLOB 'sqlite_*' "
            "ORDER BY name LIMIT ?",
            (MAX_TABLES + 1,),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return "（空库）"
    truncated = len(rows) > MAX_TABLES
    names = [r[0] for r in rows[:MAX_TABLES]]
    budget = MAX_OUTPUT_CHARS - min(_NOTE_RESERVE_CHARS, MAX_OUTPUT_CHARS // 4)
    used = 0
    lines: list[str] = []
    for name in names:
        if used + len(name) + 1 > budget:
            break
        lines.append(name)
        used += len(name) + 1
    text = "\n".join(lines)
    notes: list[str] = []
    if len(lines) < len(names):
        notes.append(
            f"表名过长，受 {MAX_OUTPUT_CHARS} 字符上限所限，实际只显示了前 {len(lines)} 张"
        )
    if truncated:
        notes.append(f"表过多，仅显示前 {MAX_TABLES} 张")
    if notes:
        text += "\n...（" + "；".join(notes) + "）"
    return text


def describe_table(db_path: str, table: str) -> str:
    """查看某表结构：列名/类型/是否可空/主键(用 PRAGMA table_info)。

    - 先用**绑定参数**在 sqlite_master 里查真实表名，查不到直接返回"表不存在"；
    - PRAGMA 的表名需要拼进 SQL，所以拼进去的是"从 sqlite_master 取回的真实表名"，
      并按 SQLite 标识符规则把双引号双写转义 —— 注入面在绑定查询那一步就被切断；
    - 刻意不用 ASCII 正则限制表名：那会导致 list_tables 能列出的中文表名
      （如 `订单`）在 describe_table 里被误报"表不存在"。
    - 列数同样受 MAX_ROWS 约束（此前硬编码 truncated=False + fetchall，300 列的表会整表返回
      且没有任何行数提示——同一个 _rows_to_text 在两个工具上口径不一致）。
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
            # 视图/虚拟表不在 type='table' 里，但 query_sql 能查它；措辞要如实，
            # 不能让模型刚查成功一张"不存在的表"。
            return f"表不存在（或不是普通表，如视图/虚拟表）：{table}"
        quoted = str(row[0]).replace('"', '""')
        rows = conn.execute(f'PRAGMA table_info("{quoted}")').fetchmany(MAX_ROWS + 1)
    finally:
        conn.close()
    if not rows:
        return f"表不存在（或不是普通表，如视图/虚拟表）：{table}"
    truncated = len(rows) > MAX_ROWS
    return _rows_to_text(
        ["cid", "name", "type", "notnull", "default", "pk"],
        [(r[0], r[1], r[2], r[3], r[4], r[5]) for r in rows[:MAX_ROWS]],
        truncated=truncated,
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
