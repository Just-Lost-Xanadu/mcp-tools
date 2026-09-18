# mcp-tools · 企业 MCP 工具套件

> 把两类企业常见能力封装成**标准 MCP Server**，任何 MCP 客户端（Claude Desktop / Cursor / MCP Inspector / 自研 Agent）即插即用。
>
> **与姊妹项目 `kbase-agent` 的关系**：两者是**一个系统的两层**，不是同一个项目做两遍。
> `kbase-agent` 是**编排层**（Agent 决策/检索/状态/评测），它把自己的工具用 FastMCP 写在进程内、经 stdio 子进程接入；
> 本套件是**协议层**——刻意换成数据/文件域、只保留 stdio，验证"MCP 让工具与 Agent 解耦、可跨客户端复用"这件事本身。
> 定位：**编排 + 协议分层**；本套件的价值在"安全边界与可交付性"，不在工具数量。

## 工具域与安全边界

| Server | 工具 | 安全设计 |
|---|---|---|
| `sqlite-ro` 只读 SQLite | `list_tables_tool` / `describe_table_tool` / `query_sql_tool` | 三层"硬度不同"，别当成等价三层：**只有只读 URI(`mode=ro`) 是不可被 SQL 解除的硬保证**；`PRAGMA query_only=ON` 只是纵深——**它能被一条通过文本闸的 `PRAGMA query_only=OFF` 带内关掉**（已实测）；`enable_load_extension(False)` 显式关闭扩展加载，堵住能过文本闸的 `SELECT load_extension(...)`（实测报 `not authorized`）。文本闸只做粗筛（放行 SELECT/WITH/EXPLAIN/PRAGMA 开头、拦多语句/注释/UNION，已知可被 `WITH … DELETE` 绕过）；结果 ≤200 行、`list_tables` ≤200 张，且查询用 `fetchmany` 取，超大结果集不会一次性进内存；连接设 `busy_timeout=5000`，避免撞上别的写事务时直接报 `database is locked` |
| `files-safe` 受限文件 | `list_dir_tool` / `read_file_tool` / `glob_files_tool` | 白名单根目录：三个入口都过 realpath 校验（`glob` 另拒绝对路径与 `..` pattern），越界即拒（防目录穿越）；**三种输出都有收敛上限**——`read_file` 只从流里读前 20 万字符（不是整读再截断）、`list_dir` 与 `glob` 各上限 200 条 |

## 接入方式（stdio）

本套件以 **stdio**（本地进程）为接入形态：任何本机 MCP 客户端拉起 `python -m mcp_tools.<server>` 即用。
单一传输、两类工具是刻意的收敛——把复杂度留给安全边界本身。

## 快速开始

```bash
cd opencode_2                                       # 仓库根（项目/包名是 mcp-tools）
python -m venv .venv && .venv\Scripts\activate     # Windows
pip install -r requirements.lock                   # 可选但推荐：复现已验证的依赖组合（Python 3.12）
pip install -e ".[dev]"
python scripts/make_demo_db.py                     # 生成演示库 demo.db
python scripts/gen_configs.py                      # 按当前目录生成 configs/*.json（换机器必跑）

# ① stdio 本机使用：在 MCP Inspector / Claude Desktop 里加载 configs/ 下的配置
# ② 命令行自验（标准 MCP 客户端，无任何业务耦合）：
python demo_client/mcp_call.py
```

> `configs/*.json` 里的 `command` / `cwd` / `env` 必须是**绝对路径**，且 `command` 要指向装了本包的
> `.venv` 解释器（裸 `python` 通常 import 不到 `mcp_tools`）。这些路径因人而异，所以**不要手改 JSON**：
> 用 `python scripts/gen_configs.py` 从脚本自身位置推导生成，clone 到任何目录都对。

## 目录结构

```
src/mcp_tools/
  sqlite_ro.py         # 只读 SQLite Server：list_tables_tool / describe_table_tool / query_sql_tool
  files_safe.py        # 受限文件 Server：list_dir_tool / read_file_tool / glob_files_tool
  security.py          # 共用安全守卫：SQL 只读文本闸 + 路径白名单 realpath 校验
tests/test_tools.py    # 15 项单测：10 项安全护栏 + 5 项工具功能（含中文表名/大小写/union 词边界回归）
configs/               # MCP 客户端配置（由 scripts/gen_configs.py 生成，勿手改）
demo_client/           # 自研 MCP 客户端示例（纯 JSON-RPC，无业务耦合）
scripts/               # make_demo_db.py（生成演示库）/ gen_configs.py（生成客户端配置）
requirements.lock      # 已验证可跑的依赖组合（实测 mcp 1.30.0，Python 3.12）
```

## 演示与验证

1. **MCP Inspector**：先 `python scripts/gen_configs.py`，再加载生成的 `configs/mcp_inspector.json`，浏览器里直接调两个 Server 的工具；
2. **Claude Desktop**：把 `configs/claude_desktop_config.json` 的 `mcpServers` 整段并入其配置并重启，直接问"查一下华东区一季度销售额"；
3. **自研 Agent**：`demo_client/mcp_call.py` 走标准协议调用——证明"工具与消费方解耦、可跨客户端复用"（脚本会检查 `isError`，工具报错不会被当成查询结果打印）。
4. 安全单测：`pytest tests`（**15 passed**：SQL 注入/多语句被拦、目录穿越被拒、中文表名与大小写回归等）。

## 已知取舍 / 下一步

- 只保留"只读 SQLite + 受限文件"两类最稳的工具、只做 stdio：刻意收敛到能讲透的工程子集；更多工具类目属于后续扩展方向，不写进"已完成"。
- **SQL 安全是三层，但三层"硬度"不同——别把文本闸，也别把 `query_only`，说成最终保证**：
  - 文本闸（粗筛）：`WITH … DELETE`、`PRAGMA query_only=OFF`、`SELECT load_extension(...)` 都能过闸，它的作用是挡掉手滑与明显注入，不是安全边界；
  - `PRAGMA query_only=ON`（纵深）：**能被一条通过文本闸的 `PRAGMA query_only=OFF` 带内关掉**（实测），所以只算"对抗意外写"；
  - **只读 URI `mode=ro`（硬保证）**：SQLite 在文件层拒绝写，实测 `WITH x AS (SELECT 1) DELETE FROM t` 到这里报 `attempt to write a readonly database`。这是唯一无法被 SQL 语句解除的一层；
  - 扩展加载已显式关闭（`conn.enable_load_extension(False)`）：实测 `SELECT load_extension('evil')` 报 `not authorized`。Python `sqlite3` 默认即关闭，这里显式声明是为了不依赖解释器默认值。
- 语句执行级超时未实现（只有结果行数上限 200），不对外宣称。
- 只读是"文件级"的：`mode=ro` 保护的是**被打开的那个库文件**；不做 SQL 语义级白名单（例如某张表本不该给模型看），也不做按调用方的行级/列级过滤。
- 下一步方向：动态工具注册 / 集成到一号项目作为其 MCP 工具源，暂不实现。

## 常见坑

- 换环境后先 `python scripts/make_demo_db.py`，否则 `sqlite-ro` 启动即报"数据库不存在"（`create_server` 会主动校验库文件，避免"看起来连上了、每次调用都报错"）。
- 换目录/换机器后先 `python scripts/gen_configs.py`，否则 `configs/*.json` 里还是上一台机器的绝对路径。
- 纯标准 + mcp 依赖，无 torch/embedding。
- `cwd` 必须指向仓库根（模块名 `mcp_tools.*`、相对 `demo.db`）。
