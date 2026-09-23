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


def test_list_tables_does_not_hide_user_tables_starting_with_sqlite(tmp_path):
    """以 sqlite 开头的**用户表**不能被当成内部表隐藏。

    回归背景：过滤条件写的是 `name NOT LIKE 'sqlite_%'`，而 LIKE 里 `_` 是**单字符通配符**，
    于是 `'sqlite_%'` 匹配的是"sqlite + 任意 1 字符 + 任意后缀"——实测库里 4 张表
    （normal / sqlitemp / sqlitex_hidden / t）只返回 2 张，而 query_sql 照样能查
    `sqlitemp`：模型拿到一份"缺表的可查表清单"，与工具实际能力自相矛盾。
    SQLite 只保留**字面** `sqlite_` 前缀（建表时会被拒绝），因此 GLOB 才是对的。
    """
    from mcp_tools import sqlite_ro

    db = tmp_path / "prefixed.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE sqlitemp(a);"
        "CREATE TABLE sqlitex_hidden(a);"
        "CREATE TABLE normal(a);"
        # sqlite_sequence 由 AUTOINCREMENT 自动创建，属于真正的内部表，必须仍被排除
        "CREATE TABLE with_autoinc(id INTEGER PRIMARY KEY AUTOINCREMENT, v TEXT);"
    )
    conn.commit()
    conn.close()

    out = sqlite_ro.list_tables(str(db))
    assert "sqlitemp" in out and "sqlitex_hidden" in out and "normal" in out
    assert "sqlite_sequence" not in out, "真正的内部表仍要排除"


def test_list_tables_respects_total_output_cap(tmp_path, monkeypatch):
    """表名很长时也要受 MAX_OUTPUT_CHARS 约束，且提示要说清"实际显示了几张"。

    回归背景：list_tables 此前只有条数上限（200 张），200 个各 200 字符的表名实测返回 40889
    字符——是 MAX_OUTPUT_CHARS 的两倍，而 README 把"单次输出 ≤20000 字符"列为本 server 的口径。
    """
    from mcp_tools import sqlite_ro

    monkeypatch.setattr(sqlite_ro, "MAX_OUTPUT_CHARS", 1000)
    db = tmp_path / "longnames.db"
    conn = sqlite3.connect(db)
    for i in range(200):
        conn.execute(f'CREATE TABLE "{"t" + str(i) + "x" * 200}" (a)')
    conn.commit()
    conn.close()

    out = sqlite_ro.list_tables(str(db))
    assert len(out) <= 1000, f"超出总输出上限：{len(out)}"
    assert "实际只显示了前" in out
    shown = len([ln for ln in out.splitlines() if ln.startswith("t")])
    assert shown < 200


def test_describe_table_respects_row_cap(tmp_path, monkeypatch):
    """列数很多时 describe_table 也要受 MAX_ROWS 约束（此前硬编码 truncated=False + fetchall）。"""
    from mcp_tools import sqlite_ro

    monkeypatch.setattr(sqlite_ro, "MAX_ROWS", 5)
    db = tmp_path / "wide_cols.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE wide (" + ", ".join(f"c{i}" for i in range(20)) + ")")
    conn.commit()
    conn.close()

    out = sqlite_ro.describe_table(str(db), "wide")
    assert "仅显示前 5 行" in out
    assert len(out.splitlines()) <= 7      # 表头 + 5 行列 + 1 行提示


def test_describe_table_says_when_object_is_not_a_table(tmp_path):
    """视图不在 `type='table'` 里，但 query_sql 能查它——措辞不能只说"表不存在"。"""
    from mcp_tools import sqlite_ro

    db = tmp_path / "view.db"
    conn = sqlite3.connect(db)
    conn.executescript("CREATE TABLE t(a); CREATE VIEW v AS SELECT 1 AS a;")
    conn.commit()
    conn.close()

    out = sqlite_ro.describe_table(str(db), "v")
    assert "表不存在" in out and "视图" in out
    assert "a" in sqlite_ro.query_sql(str(db), "SELECT * FROM v")


def test_both_row_and_output_gates_fire_reports_actually_rendered_rows(tmp_path, monkeypatch):
    """行数闸与总输出闸**同时**生效时，必须报实际渲染行数。

    回归背景：`truncated` 只表示"fetchmany 取到了第 201 行"，不等于"渲染了 200 行"。
    实测 300 行 × 143 字符的查询只渲染 134 行，提示却写"仅显示前 200 行"——
    正是现有用例（库里只有 50 行，行数闸不触发）绕过的那条路径。
    """
    from mcp_tools import sqlite_ro

    db = tmp_path / "both.db"
    conn = sqlite3.connect(db)
    conn.executescript("CREATE TABLE t(id INTEGER PRIMARY KEY, v TEXT);")
    conn.executemany(
        "INSERT INTO t(id, v) VALUES (?, ?)", [(i, "y" * 143) for i in range(1, 301)]
    )
    conn.commit()
    conn.close()

    out = sqlite_ro.query_sql(str(db), "SELECT * FROM t")
    shown = len(out.splitlines()) - 2      # 去掉表头与结尾提示
    assert shown < sqlite_ro.MAX_ROWS
    assert f"其中只有前 {shown} 行能显示" in out
    assert "仅显示前 200 行" not in out, "只渲染了 %d 行，就不能说显示了前 200 行" % shown
    assert len(out) <= sqlite_ro.MAX_OUTPUT_CHARS, f"总长 {len(out)} 超上限"


def test_header_clip_is_not_reported_as_cell_clip(tmp_path, monkeypatch):
    """列名被截断 ≠ 单元格被截断：两者必须分开说，否则模型以为数据被砍了。"""
    from mcp_tools import sqlite_ro

    monkeypatch.setattr(sqlite_ro, "MAX_CELL_CHARS", 50)
    db = tmp_path / "hdr.db"
    conn = sqlite3.connect(db)
    # 40 个各 80 字符的列名：拼起来 2000+ 字符，但每个数据格只有 1 个字符
    conn.execute("CREATE TABLE h (" + ", ".join(f'"{ "c" * 80 }{i}"' for i in range(40)) + ")")
    conn.execute("INSERT INTO h VALUES (" + ", ".join(["1"] * 40) + ")")
    conn.commit()
    conn.close()

    out = sqlite_ro.query_sql(str(db), "SELECT * FROM h")
    assert "列名过长" in out
    assert "有单元格超过" not in out, "没有任何单元格被截断"


def test_single_too_wide_row_does_not_say_zero_rows(tmp_path, monkeypatch):
    """单行过宽、一行都渲染不出来时，不能说"只有前 0 行能显示"。"""
    from mcp_tools import sqlite_ro

    monkeypatch.setattr(sqlite_ro, "MAX_CELL_CHARS", 200)
    monkeypatch.setattr(sqlite_ro, "MAX_OUTPUT_CHARS", 1500)
    db = tmp_path / "onerow.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE w (" + ", ".join(f"w{i}" for i in range(20)) + ")")
    conn.execute(
        "INSERT INTO w VALUES (" + ", ".join(["replace(hex(zeroblob(2000)),'0','x')"] * 20) + ")"
    )
    conn.commit()
    conn.close()

    out = sqlite_ro.query_sql(str(db), "SELECT * FROM w")
    assert "前 0 行" not in out
    assert "整行都放不下" in out


def test_empty_result_set_is_marked_as_zero_rows(tmp_path):
    """空结果集此前只回一行表头（`id | v`），与"一行数据、两列恰好叫 id/v"同形。"""
    from mcp_tools import sqlite_ro

    db = tmp_path / "empty.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE e(id INTEGER, v TEXT)")
    conn.commit()
    conn.close()

    out = sqlite_ro.query_sql(str(db), "SELECT * FROM e")
    assert out.splitlines()[0] == "id | v"
    assert "（0 行）" in out


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
    assert "单元格过长" in out          # 单元格这道闸的提示
    assert "有单元格超过" in out        # 汇总提示里也要说实话（是单元格那道闸生效）
    assert len(out) < 1000  # 关键断言：不是把两行各 5000 字节原样拼出来


def test_truncation_notice_names_the_gate_that_actually_fired(tmp_path, monkeypatch):
    """输出上限先掐掉尾巴时，绝不能声称"仅显示前 200 行"。

    回归背景：`truncated` 只表示"fetchmany 取到了第 201 行"，与"实际渲染了几行"是两件事。
    早先实现一律把"仅显示前 200 行"和"单元格上限"两句都印出来，于是
    "行数很多但每列都很短"的查询（实测 300 行 × 约 145 字符只渲染了 136 行）
    会得到一句根本没发生的"单元格过长"，而真正生效的"少显示了 N 行"反而没提——
    模型据此以为拿到了全量数据。
    """
    from mcp_tools import sqlite_ro

    monkeypatch.setattr(sqlite_ro, "MAX_OUTPUT_CHARS", 400)
    db = tmp_path / "wide.db"
    conn = sqlite3.connect(db)
    conn.executescript("CREATE TABLE t(id INTEGER PRIMARY KEY, v TEXT);")
    conn.executemany("INSERT INTO t(id, v) VALUES (?, ?)", [(i, "x" * 50) for i in range(1, 51)])
    conn.commit()
    conn.close()

    out = sqlite_ro.query_sql(str(db), "SELECT * FROM t")
    assert "仅显示前 200 行" not in out, "行数闸根本没触发，不许这么说"
    assert "单元格" not in out, "没有单元格被截断"
    assert "已取回 50 行" in out, "必须说清取回了几行、实际显示了几行"


def test_row_cap_notice_only_when_row_cap_fired(tmp_path, monkeypatch):
    """行数闸真的触发时才说"仅显示前 N 行"，且不得顺带报没发生的单元格截断。"""
    from mcp_tools import sqlite_ro

    monkeypatch.setattr(sqlite_ro, "MAX_ROWS", 3)
    db = tmp_path / "many.db"
    conn = sqlite3.connect(db)
    conn.executescript("CREATE TABLE t(id INTEGER PRIMARY KEY, v TEXT);")
    conn.executemany("INSERT INTO t(id, v) VALUES (?, ?)", [(i, "y") for i in range(1, 7)])
    conn.commit()
    conn.close()

    out = sqlite_ro.query_sql(str(db), "SELECT * FROM t")
    assert "仅显示前 3 行" in out
    assert "单元格" not in out


def test_glob_skips_dot_directories_and_dot_files(tmp_path):
    """点开头的目录/文件不进结果：默认白名单根是仓库根，否则返回的全是 .venv 噪声。

    实测（修复前）：`glob_files(仓库根, '**/*.py')` 的前 200 条**全部**是
    `.venv\\Lib\\site-packages\\...`，项目自己的代码一条都没有。
    """
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x", encoding="utf-8")
    (tmp_path / ".venv" / "Lib").mkdir(parents=True)
    (tmp_path / ".venv" / "Lib" / "b.py").write_text("y", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "c.py").write_text("z", encoding="utf-8")
    (tmp_path / ".hidden.py").write_text("h", encoding="utf-8")

    out = glob_files(tmp_path, "*.py")
    assert "a.py" in out
    assert "b.py" not in out and "c.py" not in out
    assert ".hidden.py" not in out


def test_pragma_write_with_schema_prefix_is_rejected():
    """schema 限定写法的写类 PRAGMA 也必须被拦（此前只拦得住不带前缀的）。"""
    for bad in (
        "PRAGMA main.journal_mode=WAL",
        'PRAGMA "journal_mode"=WAL',
        "PRAGMA main.wal_checkpoint(TRUNCATE)",
        "PRAGMA synchronous=OFF",
    ):
        with pytest.raises(ValueError):
            validate_readonly_sql(bad)
    # 正常只读 PRAGMA 仍要放行（别把闸门收得连 describe 都用不了）
    assert validate_readonly_sql("PRAGMA table_info(regions)")
    assert validate_readonly_sql("PRAGMA main.table_info(regions)")


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
