# mcp-tools · 企业 MCP 工具套件

> 把三类企业常见能力封装成**标准 MCP Server**，任何 MCP 客户端（Claude Desktop / Cursor / MCP Inspector / 自研 Agent）即插即用。
> 与个人一号项目（知识库问答 Agent）的区别：一号项目是"用 MCP 给我的 Agent 接自家工具"；本套件是"**按 MCP 标准把工具做成可对外交付的 Server**"，消费方换成第三方客户端，工具域刻意不同（数据/文件/网络），不碰一号项目的知识库素材。

## 工具域与安全边界

| Server | 工具 | 安全设计 |
|---|---|---|
| `sqlite-ro` 只读 SQLite | `list_tables` / `describe_table` / `query_sql` | SQLite **只读 URI(mode=ro)** + 只允许 `SELECT/WITH/EXPLAIN`、拦多语句/注释/注入；结果 ≤200 行 |
| `files-safe` 受限文件 | `list_dir` / `read_file` / `glob` | 白名单根目录：realpath 越界即拒（防目录穿越）；读文件有大小上限 |
| `http-fetch` 受限网络 | `fetch_url` | 域名白名单 + 拒绝内网/保留 IP（防 SSRF）；超时 10s + 响应截断 |

## 两种传输（一个套件、本地与远程两用）

- **stdio**（本地主力）：`python -m mcp_tools.sqlite_ro` 等，任何本机 MCP 客户端拉起即用，最简单。
- **Streamable HTTP**（远程，现行规范）：`uvicorn mcp_tools.gateway:app`，三个 Server 分别挂在
  `/mcp/sqlite`、`/mcp/files`、`/mcp/http`，统一 **`Authorization: Bearer <token>`** 鉴权（token 必填，见下）。
  > SSE 是旧远程方案，已被 Streamable HTTP 取代，此处不再实现（仅作历史了解）。

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

### 远程网关

```bash
set MCP_TOOLS_TOKEN=dev-token-change-me            # 必填（>=8 位），未配置拒绝启动
uvicorn mcp_tools.gateway:app --host 0.0.0.0 --port 8001
# MCP 客户端以 Streamable HTTP 连 http://<host>:8001/mcp/<name>，带 Bearer 头
```

## 演示与验证

1. **MCP Inspector**：加载 `configs/mcp_inspector.json`，浏览器里直接调三个 Server 的工具；
2. **Claude Desktop**：复制 `configs/claude_desktop_config.json` 相应片段到其配置并重启，直接问"查一下华东区一季度销售额"；
3. **自研 Agent**：`demo_client/mcp_call.py` 走标准协议调用——证明"工具与消费方解耦、可跨客户端复用"。
4. 安全单测：`pytest tests`（SQL 注入/多语句被拦、目录穿越被拒、内网 IP 被拒等）。

## 简历口径（求职项目段雏形）

> 独立开发"企业 MCP 工具套件"：用 FastMCP 封装只读 SQLite / 受限文件 / 受限 HTTP 三类 Server，
> stdio 本地 + Streamable HTTP 远程两种传输，落地三类安全边界（SQL 只读拦截、路径白名单、域名白名单防 SSRF）与
> Bearer 鉴权；提供 MCP Inspector / Claude Desktop / 自研 MCP 客户端三种消费方接入示例，验证"MCP 让工具与 Agent 解耦、可跨客户端复用"。

## 已知取舍 / 下一步

- 鉴权为 Bearer 单 token 演示版；正式可演进 OAuth 2.0 与每 Server 独立 token（README 注明即可）。
- HTTP 域名用字面量 IP/白名单校验；对"域名→内网 IP"的 DNS 重绑防护只在 README 说明，未实现。
- 下一步方向：动态工具注册 / 多 Server 路由网关（衔接 A2A 生态叙事），暂不实现。

## 常见坑

- 换环境后先 `python scripts/make_demo_db.py`，否则 `sqlite-ro` 工具返回"数据库不存在"。
- Anaconda 下如遇 onnxruntime 之类无关（本套件无 torch/embedding），纯标准依赖。
- `cwd` 必须指向仓库根（模块名 `mcp_tools.*`、相对 `demo.db`）。
