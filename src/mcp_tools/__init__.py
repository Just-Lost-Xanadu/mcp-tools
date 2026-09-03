"""企业 MCP 工具套件：只读 SQLite / 受限文件系统 / 受限 HTTP 抓取。

把三类企业常见能力封装成标准 MCP Server，任何 MCP 客户端（Claude Desktop /
MCP Inspector / 自研 Agent）都能即插即用。安全边界按工具域分别设计：
只读拦截(SQL)、路径白名单(文件)、域名白名单(HTTP)。
"""

__version__ = "0.1.0"
