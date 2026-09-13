# mcp-tools · 企业 MCP 工具套件

> 把两类企业常见能力封装成**标准 MCP Server**，任何 MCP 客户端（Claude Desktop / Cursor / MCP Inspector / 自研 Agent）即插即用。
> 与个人一号项目（知识库问答 Agent）的区别：一号项目是"用 MCP 给我的 Agent 接自家工具"；本套件是"**按 MCP 标准把工具做成可对外交付的 Server**"，消费方换成第三方客户端，工具域刻意不同（数据/文件），不碰一号项目的知识库素材。

## 工具域与安全边界

| Server | 工具 | 安全设计 |
|---|---|---|
| `sqlite-ro` 只读 SQLite | `list_tables` / `describe_table` / `query_sql` | SQLite **只读 URI(mode=ro)** + 只允许 `SELECT/WITH/EXPLAIN`、拦多语句/注释/注入；结果 ≤200 行 |
| `files-safe` 受限文件 | `list_dir` / `read_file` / `glob` | 白名单根目录：三个入口都过 realpath 校验（`glob` 另拒绝对路径与 `..` pattern），越界即拒（防目录穿越）；读文件有输出截断上限 |

## 接入方式（stdio）

本套件当前以 **stdio**（本地进程）为接入形态：任何本机 MCP 客户端拉起 `python -m mcp_tools.<server>` 即用。
> 远程 Streamable HTTP 形态在早期版本验证过：把 FastMCP 子应用挂到 FastAPI 上时，Starlette 不会执行挂载子应用的 lifespan，`StreamableHTTPSessionManager` 起不来，该路径未打通。评估后认为 **stdio + 本地工具封装才是本项目的交付边界**，故收敛为两类工具、单一传输，把复杂度留给安全边界本身。

## 快速开始

```bash
cd mcp-tools
python -m venv .venv && .venv\Scripts\activate     # Windows
pip install -e ".[dev]"
python scripts/make_demo_db.py                     # 生成演示库 demo.db

# ① stdio 本机使用：在 MCP Inspector / Claude Desktop 里加载
#    configs/mcp_inspector.json（cwd 指向仓库根）
# ② 命令行自验（标准 MCP 客户端，无任何业务耦合）：
python demo_client/mcp_call.py
```

## 演示与验证

1. **MCP Inspector**：加载 `configs/mcp_inspector.json`，浏览器里直接调两个 Server 的工具；
2. **Claude Desktop**：复制 `configs/claude_desktop_config.json` 相应片段到其配置并重启，直接问"查一下华东区一季度销售额"；
3. **自研 Agent**：`demo_client/mcp_call.py` 走标准协议调用——证明"工具与消费方解耦、可跨客户端复用"。
4. 安全单测：`pytest tests`（SQL 注入/多语句被拦、目录穿越被拒等）。

## 简历口径（求职项目段雏形）

> 独立开发"企业 MCP 工具套件"：用 FastMCP 封装只读 SQLite / 受限文件两类 Server（本机 stdio 接入），
> 落地安全护栏（SQL 只读拦截、路径白名单防穿越），配套 11 项单测（8 项安全护栏 + 3 项工具功能）；
> 提供 MCP Inspector / Claude Desktop / 自研 MCP 客户端三种消费方接入示例，验证"MCP 让工具与 Agent 解耦、可跨客户端复用"。

## 已知取舍 / 下一步

- 早期版本尝试过三类（含 HTML 抓取/SSRF 防护）与远程 Streamable HTTP 网关，因保持项目小而聚焦已移除；当前只保留最稳的两类、只做 stdio——被问"为什么只两类"可答"聚焦能讲透的工程子集"。
- 鉴权若需远程访问，可演进为独立鉴权层 + 反代；当前 demo 不需。
- 下一步方向：动态工具注册 / 集成到一号项目作为其 MCP 工具源，暂不实现。

## 常见坑

- 换环境后先 `python scripts/make_demo_db.py`，否则 `sqlite-ro` 工具返回"数据库不存在"。
- 纯标准 + mcp 依赖，无 torch/embedding。
- `cwd` 必须指向仓库根（模块名 `mcp_tools.*`、相对 `demo.db`）。
