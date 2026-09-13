"""工具域共用的安全守卫：只读 SQL / 路径白名单。

两个 server（sqlite_ro / files_safe）内部都复用这里的校验函数，保证"安全逻辑只写一份、
任何人/任何场景都走同一闸口"。

原则：宁可拒绝、不可放行。本模块的校验失败一律抛 ValueError；工具层对"越界/非法输入"
返回错误文本、对 DB/IO 异常则向上抛出（v1 未做统一错误包裹，DB/IO 异常会直接冒泡成 MCP 错误）。

【设计说明：为何用模块函数而不是 class？】
本项目刻意保持小：两处安全校验彼此无共享状态、无生命周期，模块顶层函数是"最简组合"。
如果未来出现"多个 server 各自实例化不同白名单 / 不同只读范围"的需求（要带配置的状态），
就应重构为身份类，例如：
    class ReadonlyGuard:
        def __init__(self, allow_keywords: set[str]): ...
        def validate(self, sql: str) -> str: ...
调用方按需 `ReadonlyGuard(allow_keywords={...})` 并持有实例——这在语义上等价但把
"每个 server 自己的约束"变成了对象状态，多个实例互不干扰。
本项目现在没有这种"多实例不同配置"的场景，所以保留模块函数以减熵。
"""

import re
from pathlib import Path

_SQL_HEAD = re.compile(r"^\s*(select|with|explain|describe|pragma)\b", re.IGNORECASE)
_SQL_UNION = re.compile(r"\bunion\b")
_SQL_PRAGMA_WRITE = re.compile(
    r"\bpragma\s+(journal_mode|synchronous|locking_mode|wal_checkpoint|cache_size)\b",
    re.IGNORECASE,
)
_SQL_BLOCK = (";", "--", "/*", "*/", "\\")


def validate_readonly_sql(sql: str) -> str:
    """只允许单条只读查询：以 select/with/explain/pragma 开头，
    且不含多语句/注释/UNION/写类 PRAGMA 特征。

    被调用方：sqlite_ro.query_sql 在真正执行前先过这里（第一道闸）。

    返回：去掉首尾空白的原 SQL（放行时原样返回，不做任何改写）。
    抛出：ValueError —— 命中任意一条禁止特征即抛，绝不静默放行。

    - union 只按整词拦（\\bunion\\b），避免误伤含 union 子串的列名/字符串
      （如 communication、reunion）；真正的写库由"只读 URI + query_only"双保险兜底。
    - describe/pragma 属于查询前辅助（describe_table 内部用 PRAGMA table_info），
      写入类 PRAGMA 由 _SQL_PRAGMA_WRITE 单独拦截，最终写库由 DB 层只读兜底。
    - 这是"粗筛"而不是语法解析：`WITH x AS (SELECT 1) DELETE FROM t` 能通过本闸
      （SQLite 允许数据修改型 CTE），`PRAGMA query_only=OFF`、`SELECT load_extension(...)`
      也能通过。真正兜住写操作的是 sqlite_ro 的 mode=ro 只读连接 + query_only。
    """
    if not sql or not isinstance(sql, str):
        raise ValueError("SQL 不能为空")
    if not _SQL_HEAD.match(sql):
        raise ValueError("仅允许以 SELECT/WITH/EXPLAIN/PRAGMA 开头的单条只读查询")
    lowered = sql.lower()
    for token in _SQL_BLOCK:
        if token in lowered:
            raise ValueError(f"检测到不允许的内容：{token}")
    if _SQL_UNION.search(lowered):
        raise ValueError("检测到不允许的内容：union")
    if _SQL_PRAGMA_WRITE.search(lowered):
        raise ValueError("检测到不允许的内容：写类 PRAGMA")
    return sql.strip()


def resolve_within_root(path: str, root: Path) -> Path:
    """把传入的相对/绝对路径，realpath 归一化后确保落在白名单根目录内（防目录穿越）。

    被调用方：files_safe 的 list_dir / read_file（入口第一步）与 glob_files（每个命中），
    处理：绝对路径直接用；相对路径视为"相对 root"解析；随后 .resolve() 跟掉 .. 与符号链接，
    只剩真路径再判归属。

    返回：解析后的绝对 Path（已确认在 root 内）。
    抛出：ValueError —— 解析后落在 root 之外（含恰为 root 的祖先），即穿越/越权，直接拒。
    """
    root = root.resolve()
    target = Path(path)
    if not target.is_absolute():
        target = root / target
    resolved = target.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"路径超出白名单目录：{path}")
    return resolved
