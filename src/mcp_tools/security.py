"""三类工具域共用的安全守卫：只读 SQL / 路径白名单 / 域名白名单。

原则：宁可拒绝、不可放行。所有校验失败都抛 ValueError，由工具层转成错误文本
返回给模型（工具错误也要"看得见"，便于模型自纠或如实告知）。
"""

import ipaddress
import re
from pathlib import Path
from urllib.parse import urlparse

_SQL_HEAD = re.compile(r"^\s*(select|with|explain|describe|pragma)\b", re.IGNORECASE)
_SQL_BLOCK = (";", "--", "/*", "*/", "\\", "union", "pragma write")


def validate_readonly_sql(sql: str) -> str:
    """只允许单条只读查询：以 select/with/explain 开头，且不含注入/多语句特征。"""
    if not sql or not isinstance(sql, str):
        raise ValueError("SQL 不能为空")
    if not _SQL_HEAD.match(sql):
        raise ValueError("仅允许 SELECT/WITH/EXPLAIN 只读查询")
    lowered = sql.lower()
    for token in _SQL_BLOCK:
        if token in lowered and token not in ("select",):
            raise ValueError(f"检测到不允许的内容：{token}")
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


def _is_private_ip(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved


def host_allowed(host: str, allow_domains: frozenset[str]) -> bool:
    """域名白名单：精确匹配或子域名归属白名单内。"""
    if not allow_domains:
        raise ValueError("未配置允许访问的域名，拒绝请求")
    if host in allow_domains:
        return True
    return any(host.endswith("." + domain) for domain in allow_domains)


def validate_fetch_url(url: str, allow_domains: frozenset[str]) -> str:
    """HTTP 抓取守卫：仅 http/https、域名白名单、禁止内网/保留地址。"""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("仅允许 http/https 链接")
    host = parsed.hostname
    if not host:
        raise ValueError("URL 缺少主机名")
    if _is_private_ip(host):
        raise ValueError("禁止访问内网/保留地址")
    if not host_allowed(host, allow_domains):
        raise ValueError(f"域名不在白名单内：{host}")
    return url
