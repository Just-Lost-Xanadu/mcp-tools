"""工具域共用的安全守卫：只读 SQL / 路径白名单。

原则：宁可拒绝、不可放行。所有校验失败都抛 ValueError，由工具层转成错误文本
返回给模型（工具错误也要"看得见"，便于模型自纠或如实告知）。
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

    - union 只按整词拦（\\bunion\\b），避免误伤含 union 子串的列名/字符串
      （如 communication、reunion）；真正的写库由"只读 URI + query_only"双保险兜底。
    - describe/pragma 属于查询前辅助（describe_table 内部用 PRAGMA table_info），
      写入类 PRAGMA 由 _SQL_PRAGMA_WRITE 单独拦截，最终写库由 DB 层只读兜底。
    """
    if not sql or not isinstance(sql, str):
        raise ValueError("SQL 不能为空")
    if not _SQL_HEAD.match(sql):
        raise ValueError("仅允许 SELECT/WITH/EXPLAIN 只读查询")
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
    """把相对/绝对路径解析后，确保落在白名单根目录内（防目录穿越）。"""
    root = root.resolve()
    target = Path(path)
    if not target.is_absolute():
        target = root / target
    resolved = target.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"路径超出白名单目录：{path}")
    return resolved
