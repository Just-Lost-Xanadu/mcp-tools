"""极简 MCP stdio 客户端示例：走标准协议连接 sqlite-ro，列出并调用工具。

目的：证明"工具与消费方解耦"——这里没有任何 sqlite 逻辑，只发 JSON-RPC。

用法（需先 python scripts/make_demo_db.py）：python demo_client/mcp_call.py
"""

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 包在 src/ 下（src-layout），未安装时要把 src 放进来才 import 得到 mcp_tools
sys.path.insert(0, str(ROOT / "src"))

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_tools.sqlite_ro"],
        cwd=str(ROOT),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print("可用工具:", names)
            result = await session.call_tool(
                "query_sql_tool",
                {"sql": "SELECT r.name AS region, SUM(o.amount) AS sales FROM orders o "
                        "JOIN regions r ON r.id = o.region_id GROUP BY r.name ORDER BY sales DESC"},
            )
            if result.isError:  # 不看这个标志的话，工具报错也会被当成查询结果打印出来
                raise SystemExit(f"工具调用失败：{[getattr(c, 'text', c) for c in result.content]}")
            for item in result.content:
                print("查询结果:\n", item.text)


if __name__ == "__main__":
    asyncio.run(main())
