import sqlite3
import tempfile
from pathlib import Path

import pytest

from mcp_tools.files_safe import glob_files, list_dir, read_file
from mcp_tools.security import resolve_within_root, validate_readonly_sql
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


# ---------- glob 加固回归：pattern 不允许越界 ----------
def test_glob_traversal_rejected(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "inside.txt").write_text("ok", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("secret", encoding="utf-8")

    assert "inside.txt" in glob_files(root, "*.txt")
    out = glob_files(root, "../*.txt")
    assert "不允许" in out
    assert "outside" not in out


def test_glob_absolute_pattern_rejected(tmp_path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    assert "不允许" in glob_files(tmp_path, str(tmp_path / "*.txt"))


# ---------- 加固回归：union 词边界 / 表名注入 ----------
def test_sql_union_word_boundary_not_substring():
    # 含 union 子串的字符串不应被误拦
    assert validate_readonly_sql(
        "SELECT name FROM regions WHERE name='communication'"
    )
    # 真正的 UNION 关键字仍拦
    with pytest.raises(ValueError):
        validate_readonly_sql("SELECT id FROM regions UNION SELECT id FROM products")


def test_describe_table_rejects_non_identifier(demo_db):
    out = describe_table(demo_db, 'regions" ; DROP TABLE regions; --')
    assert "表不存在" in out


def test_describe_table_supports_non_ascii_name(tmp_path):
    """中文表名：list_tables 能列出，describe_table 也必须能描述（此前被 ASCII 正则误拒）。"""
    db = tmp_path / "cn.db"
    conn = sqlite3.connect(db)
    conn.executescript('CREATE TABLE "订单"(id INTEGER PRIMARY KEY, 金额 REAL);')
    conn.commit()
    conn.close()

    assert "订单" in list_tables(str(db))
    out = describe_table(str(db), "订单")
    assert "表不存在" not in out
    assert "金额" in out
