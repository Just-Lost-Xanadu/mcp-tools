import sqlite3
import tempfile
from pathlib import Path

import pytest

from mcp_tools.files_safe import glob_files, list_dir, read_file
from mcp_tools.security import (
    host_allowed,
    resolve_within_root,
    validate_fetch_url,
    validate_readonly_sql,
)
from mcp_tools.sqlite_ro import list_tables, query_sql, describe_table


# ---------- 只读 SQL 守卫 ----------
def test_sql_accept_select():
    assert validate_readonly_sql("SELECT name FROM regions LIMIT 3").startswith("SELECT")


def test_sql_reject_write_and_multi_statement():
    for bad in ("INSERT INTO regions VALUES (9,'x')", "DROP TABLE regions", "UPDATE regions SET name='x'",
                "SELECT 1; DROP TABLE regions", "select * from orders -- 注释后跟删除", "SELECT 1 /* 注释 */"):
        with pytest.raises(ValueError):
            validate_readonly_sql(bad)


# ---------- 路径白名单 ----------
def test_path_inside_root_ok(tmp_path):
    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
    resolved = resolve_within_root("a.txt", tmp_path)
    assert resolved == (tmp_path / "a.txt").resolve()


def test_path_traversal_rejected(tmp_path):
    with pytest.raises(ValueError):
        resolve_within_root("../outside.txt", tmp_path)


def test_absolute_outside_rejected(tmp_path):
    with pytest.raises(ValueError):
        resolve_within_root(str(Path(tempfile.gettempdir())), tmp_path)


# ---------- HTTP 域名白名单 ----------
def test_http_allow_domains():
    allow = frozenset({"example.com"})
    assert host_allowed("example.com", allow)
    assert host_allowed("api.example.com", allow)
    assert not host_allowed("evil.com", allow)


def test_http_reject_private_ip():
    with pytest.raises(ValueError):
        validate_fetch_url("http://127.0.0.1/", frozenset({"127.0.0.1"}))
    with pytest.raises(ValueError):
        validate_fetch_url("http://192.168.1.1/", frozenset({"192.168.1.1"}))


# ---------- SQLite server 工具（直连函数，不走网络） ----------
@pytest.fixture()
def demo_db(tmp_path):
    db = tmp_path / "demo.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE regions(id INTEGER PRIMARY KEY, name TEXT);"
        "INSERT INTO regions VALUES (1,'华东'),(2,'华南');"
    )
    conn.commit()
    conn.close()
    return str(db)


def test_sqlite_query_ok(demo_db):
    out = query_sql(demo_db, "SELECT * FROM regions ORDER BY id")
    assert "华东" in out and "华南" in out


def test_sqlite_list_and_describe(demo_db):
    assert "regions" in list_tables(demo_db)
    out = describe_table(demo_db, "regions")
    assert "name" in out


def test_sqlite_query_reject_write(demo_db):
    with pytest.raises(ValueError):
        query_sql(demo_db, "DROP TABLE regions")


# ---------- files server 工具 ----------
def test_files_read_and_list(tmp_path):
    (tmp_path / "note.md").write_text("hello notes", encoding="utf-8")
    assert "note.md" in list_dir(tmp_path)
    assert "hello" in read_file(tmp_path, "note.md")
    assert "note.md" in glob_files(tmp_path, "*.md")
