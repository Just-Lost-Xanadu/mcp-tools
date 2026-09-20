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


def test_describe_table_name_is_case_insensitive(demo_db):
    """SQLite 对表名大小写不敏感（SELECT/PRAGMA 都认 REGIONS），describe 也必须一致。"""
    assert "name" in describe_table(demo_db, "REGIONS")


# ---------- 资源上限回归：输出都要有收敛口径 ----------
def test_list_dir_truncates_many_entries(tmp_path, monkeypatch):
    """目录条目过多必须截断——这段文本会整段进模型上下文。"""
    from mcp_tools import files_safe

    monkeypatch.setattr(files_safe, "MAX_LIST_ENTRIES", 3)
    for i in range(6):
        (tmp_path / f"f{i}.txt").write_text("x", encoding="utf-8")

    out = files_safe.list_dir(tmp_path)
    assert "条目过多" in out
    assert len([ln for ln in out.splitlines() if ln.endswith(".txt")]) == 3


def test_read_file_truncates_without_slurping_whole_file(tmp_path, monkeypatch):
    """只从流里读上限+1 个字符，而不是"整读再截断"（后者对大文件没有资源保护）。"""
    from mcp_tools import files_safe

    monkeypatch.setattr(files_safe, "MAX_READ_CHARS", 10)
    (tmp_path / "big.txt").write_text("A" * 5000, encoding="utf-8")

    out = files_safe.read_file(tmp_path, "big.txt")
    assert out.startswith("A" * 10)
    assert "已截断" in out
    # 关键断言：返回值只比上限多一点（含提示语），不是把 5000 字符全读出来再切
    assert len(out) < 100


def test_list_tables_truncates(tmp_path, monkeypatch):
    from mcp_tools import sqlite_ro

    monkeypatch.setattr(sqlite_ro, "MAX_TABLES", 3)
    db = tmp_path / "many.db"
    conn = sqlite3.connect(db)
    for i in range(6):
        conn.execute(f"CREATE TABLE t{i}(a)")
    conn.commit()
    conn.close()

    out = sqlite_ro.list_tables(str(db))
    assert "表过多" in out
    assert len([ln for ln in out.splitlines() if ln.startswith("t")]) == 3


def test_query_sql_clips_huge_cell_and_output(tmp_path, monkeypatch):
    """单行超大值必须被收敛：行数上限管不住"一行里一个巨大值"。

    回归背景：此前只有 MAX_ROWS，`SELECT zeroblob(5000000)` 会展开成两千万字符的返回文本
    （既可能打爆 server 内存，也会挤爆模型上下文），与文件侧 MAX_READ_CHARS 的口径不对称。
    """
    from mcp_tools import sqlite_ro

    monkeypatch.setattr(sqlite_ro, "MAX_CELL_CHARS", 50)
    monkeypatch.setattr(sqlite_ro, "MAX_OUTPUT_CHARS", 200)

    db = tmp_path / "big.db"
    conn = sqlite3.connect(db)
    conn.executescript("CREATE TABLE blob_t(id INTEGER PRIMARY KEY, v BLOB);")
    conn.execute("INSERT INTO blob_t(id, v) VALUES (1, zeroblob(5000)), (2, zeroblob(5000))")
    conn.commit()
    conn.close()

    out = sqlite_ro.query_sql(str(db), "SELECT v FROM blob_t")
    assert "单元格过长" in out
    assert "结果过大已截断" in out
    assert len(out) < 1000  # 关键断言：不是把两行各 5000 字节原样拼出来


def test_glob_rooted_pattern_rejected(tmp_path):
    """Windows rooted pattern（有根无盘符）也必须被闸门拒绝，而不是由 pathlib 抛 NotImplementedError。"""
    for pattern in ("/Windows/*.ini", "\\Windows\\*.ini", "C:Windows/*.ini"):
        assert "不允许" in glob_files(tmp_path, pattern)


def test_connect_sets_busy_timeout(demo_db):
    """只读连接也要设 busy_timeout，否则撞上别的写事务会直接 'database is locked'。"""
    from mcp_tools import sqlite_ro

    conn = sqlite_ro._connect(Path(demo_db))
    try:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    finally:
        conn.close()
