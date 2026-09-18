# mcp-tools · 企业 MCP 工具套件

> 把两类企业常见能力封装成**标准 MCP Server**，任何 MCP 客户端（Claude Desktop / Cursor / MCP Inspector / 自研 Agent）即插即用。
>
> **与姊妹项目 `kbase-agent` 的关系**：两者是**一个系统的两层**，不是同一个项目做两遍。
> `kbase-agent` 是**编排层**（Agent 决策/检索/状态/评测），它把自己的工具用 FastMCP 写在进程内、经 stdio 子进程接入；
> 本套件是**协议层**——刻意换成数据/文件域、只保留 stdio，验证"MCP 让工具与 Agent 解耦、可跨客户端复用"这件事本身。
> 面试口径：**编排 + 协议分层**；本套件的价值在"安全边界与可交付性"，不在工具数量。

## 工具域与安全边界

| Server | 工具 | 安全设计 |
|---|---|---|
| `sqlite-ro` 只读 SQLite | `list_tables_tool` / `describe_table_tool` / `query_sql_tool` | 真正的只读保证是 **只读 URI(mode=ro) + PRAGMA query_only**（两层都在 DB 层）；文本闸只做粗筛（放 SELECT/WITH/EXPLAIN/PRAGMA 开头、拦多语句/注释/UNION，已知可被 `WITH … DELETE` 绕过）；结果 ≤200 行 |
| `files-safe` 受限文件 | `list_dir_tool` / `read_file_tool` / `glob_files_tool` | 白名单根目录：三个入口都过 realpath 校验（`glob` 另拒绝对路径与 `..` pattern），越界即拒（防目录穿越）；读文件有输出截断上限（20 万字符） |

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

## 简历口径（求职项目段雏形）

> 独立开发"企业 MCP 工具套件"：用 FastMCP 封装只读 SQLite / 受限文件两类 Server（本机 stdio 接入），
> 落地安全护栏（SQL 只读拦截、路径白名单防穿越），配套 15 项单测（覆盖只读/穿越拦截与中文表名、大小写等边界回归）；
> 提供 MCP Inspector / Claude Desktop / 自研 MCP 客户端三种消费方接入示例，验证"MCP 让工具与 Agent 解耦、可跨客户端复用"。

> **与一号项目并列时别被读成"重复项目"**：简历里建议把两者写成"编排层 / 协议层"的分层关系
> （见本文件顶部），或只留一段并注明"其中的 MCP 工具层已抽成独立可交付 Server"。**不要**让两段的
> 技术栈（MCP + FastAPI + SQLite）看起来是同一种项目写了两遍。

## 已知取舍 / 下一步

- 只保留"只读 SQLite + 受限文件"两类最稳的工具、只做 stdio：被问"为什么只两类"可答"聚焦能讲透的工程子集"；更多工具类目属于后续扩展方向，不写进"已完成"。
- **SQL 安全是三层，讲的时候别把文本闸说成保证**：文本闸只做粗筛（`WITH … DELETE`、`PRAGMA query_only=OFF`、`load_extension` 都能过），真正兜住写操作的是 `mode=ro` 只读 URI + `PRAGMA query_only=ON`；`load_extension` 属于**已知未封堵项**（生产化方向：连接层禁用扩展加载）。
- 语句执行级超时未实现（只有结果行数上限 200），不对外宣称。
- 下一步方向：动态工具注册 / 集成到一号项目作为其 MCP 工具源，暂不实现。

## 常见坑

- 换环境后先 `python scripts/make_demo_db.py`，否则 `sqlite-ro` 启动即报"数据库不存在"（`create_server` 会主动校验库文件，避免"看起来连上了、每次调用都报错"）。
- 换目录/换机器后先 `python scripts/gen_configs.py`，否则 `configs/*.json` 里还是上一台机器的绝对路径。
- 纯标准 + mcp 依赖，无 torch/embedding。
- `cwd` 必须指向仓库根（模块名 `mcp_tools.*`、相对 `demo.db`）。
