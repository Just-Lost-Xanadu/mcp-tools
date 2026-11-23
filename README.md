# mcp-tools · 企业 MCP 工具套件

> **测试口径（2026-11-08）**：`pytest -q` → kbase-agent **40** / ecommerce-dw **32** / mcp-tools **30**，合计 **102 passed**。

> 把两类企业常见能力封装成**标准 MCP Server**，任何 MCP 客户端（Claude Desktop / Cursor / MCP Inspector / 自研 Agent）即插即用。
>
> **与姊妹项目 `kbase-agent` 的关系**：两者是**一个系统的两层**，不是同一个项目做两遍。
> `kbase-agent` 是**编排层**（Agent 决策/检索/状态/评测），它把自己的工具用 FastMCP 写在进程内、经 stdio 子进程接入；
> 本套件是**协议层**——换成数据/文件域、只保留 stdio，验证"MCP 让工具与 Agent 解耦、可跨客户端复用"这件事本身。
> 定位：**编排 + 协议分层**；本套件的价值在"安全边界与可交付性"，不在工具数量。

## 工具域与安全边界

| Server | 工具 | 安全设计 |
|---|---|---|
| `sqlite-ro` 只读 SQLite | `list_tables_tool` / `describe_table_tool` / `query_sql_tool` | 三层保护、硬度不同：`mode=ro` 是文件层**硬保证**；`PRAGMA query_only` 只是纵深（可被带内关闭）；`enable_load_extension(False)` 禁扩展；文本闸只做粗筛。结果上限：≤200 行、单元格 ≤2000 字符、单次 ≤20000 字符。 |
| `files-safe` 受限文件 | `list_dir_tool` / `read_file_tool` / `glob_files_tool` | 白名单根 + realpath 校验，越界即拒；`read_file` 只读前 20 万字符，`list_dir`/`glob` 各 ≤200 条；`glob` 排除点目录并在遍历时剪枝。 |

## 接入方式（stdio）

本套件以 **stdio**（本地进程）为接入形态：任何本机 MCP 客户端拉起 `python -m mcp_tools.<server>` 即用。
单一传输、两类工具是有意收敛——把复杂度留给安全边界本身。

## 快速开始

```bash
cd mcp-tools                                        # 仓库根（clone 目录名 = 仓库名）
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
>
> **这两份 JSON 是生成物，不入库**（仓库里只有 `configs/*.json.example` 作为"产物形状"参考）。
> 也就是说：**你不需要填任何路径**——跑一次上面那条 `gen_configs.py` 就会生成本机正确的绝对路径；
> 换机器/换目录再跑一次即可。生成脚本会顺带检查"当前解释器能不能 import 到本包"，避免用裸 `python`
> 生成出一份坏配置。

## 目录结构

```
src/mcp_tools/
  sqlite_ro.py         # 只读 SQLite Server：list_tables_tool / describe_table_tool / query_sql_tool
  files_safe.py        # 受限文件 Server：list_dir_tool / read_file_tool / glob_files_tool
  security.py          # 共用安全守卫：SQL 只读文本闸 + 路径白名单 realpath 校验
tests/test_tools.py    # 30 项单测：SQL 只读/注入、路径白名单、输出上限与边界回归
configs/               # *.json 是生成物（不入库），*.json.example 只是产物形状参考；用 gen_configs.py 生成
demo_client/           # 自研 MCP 客户端示例（纯 JSON-RPC，无业务耦合）
scripts/               # make_demo_db.py（生成演示库）/ gen_configs.py（生成客户端配置）
requirements.lock      # 已验证可跑的依赖组合（实测 mcp 1.30.0，Python 3.12）
```

## 演示与验证

1. **MCP Inspector**：先 `python scripts/gen_configs.py`，再加载生成的 `configs/mcp_inspector.json`，浏览器里直接调两个 Server 的工具；
2. **Claude Desktop**：把 `configs/claude_desktop_config.json` 的 `mcpServers` 整段并入其配置并重启，直接问"查一下华东区一季度销售额"；
3. **自研 Agent**：`demo_client/mcp_call.py` 走标准协议调用——证明"工具与消费方解耦、可跨客户端复用"（脚本会检查 `isError`，工具报错不会被当成查询结果打印）。
4. 安全单测：`pytest tests`（**30 passed**：SQL 注入/多语句被拦、目录穿越与越界 glob 被拒、中文表名与大小写回归、输出上限与 `busy_timeout`、schema 限定写类 PRAGMA、glob 点目录剪枝与跨平台 POSIX 分隔符、截断提示口径、`sqlite` 前缀用户表不被隐藏、列表/描述工具的输出与行数上限等）。

## 许可证

[MIT](./LICENSE) © 2026 Just-Lost-Xanadu
