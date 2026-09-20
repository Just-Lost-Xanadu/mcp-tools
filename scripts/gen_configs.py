"""生成 configs/*.json —— 把"本机绝对路径"从手写文件里挪到脚本里，换机器一条命令即可。

为什么需要它：MCP 客户端配置里的 command / cwd / env 必须是**绝对路径**，且 command 必须
指向装了本包的 .venv 解释器（裸 python 往往 import 不到 mcp_tools）。这些路径因人而异、
因目录而异，手写在仓库里等于"换台机器就跑不起来"，而没人愿意去改别人的 JSON。

本脚本从**自身位置**推导仓库根，因此 clone 到任何目录、只要用它自己的解释器运行就正确：

    python scripts/gen_configs.py

产物（覆盖写入）：
    configs/mcp_inspector.json        —— MCP Inspector / 通用 stdio 客户端
    configs/claude_desktop_config.json —— Claude Desktop（把 mcpServers 整段并入其配置后重启）

注意：JSON 里全部用正斜杠（Python 在 Windows 上同样接受 `/`），避免反斜杠转义问题。
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = ROOT / "configs"
DB = ROOT / "demo.db"


def _servers(python: str) -> dict:
    common = {"command": python, "cwd": str(ROOT).replace("\\", "/")}
    return {
        "sqlite-ro": {
            **common,
            "args": ["-m", "mcp_tools.sqlite_ro"],
            "env": {"MCP_SQLITE_DB": str(DB).replace("\\", "/")},
        },
        "files-safe": {
            **common,
            "args": ["-m", "mcp_tools.files_safe"],
            "env": {"MCP_FILES_ROOT": str(ROOT).replace("\\", "/")},
        },
    }


def main() -> int:
    python = sys.executable
    servers = _servers(python)

    # 自检：配置里的 command 指向的解释器必须能 import 到本包，否则生成的配置
    # "看起来能加载、一调用就 ModuleNotFoundError"。裸 python（没装本包）是最常见的踩法。
    # 这里选择**拒绝写入**而不是"警告后照写"：否则一次手滑就会把本机原本可用的配置覆盖成坏的。
    import importlib.util

    if importlib.util.find_spec("mcp_tools") is None:
        print(
            f"[!] 当前解释器 import 不到 mcp_tools：{python}\n"
            "    请改用装好本包的 .venv 解释器运行本脚本，例如：\n"
            r"      .venv\Scripts\python.exe scripts\gen_configs.py" + "\n"
            "    （已跳过写文件，避免把你本机可用的配置覆盖成坏的）"
        )
        return 1

    inspector = {
        "_说明": (
            "MCP Inspector 的 server 配置（也可粘进 Claude Desktop）。"
            "本文件由 scripts/gen_configs.py 生成：cwd 必须指向仓库根，"
            "command 必须是装了本包的 .venv 解释器。换目录/换机器后重跑一次生成脚本即可。"
        ),
        "mcpServers": servers,
    }
    desktop = {
        "_说明": (
            "把 mcpServers 整段并入 Claude Desktop 的 claude_desktop_config.json 后重启。"
            "本文件由 scripts/gen_configs.py 生成；若 demo.db 不存在，先跑 "
            "python scripts/make_demo_db.py（sqlite-ro 启动即校验库文件，缺库会直接起不来）。"
        ),
        "mcpServers": servers,
    }

    CONFIGS.mkdir(parents=True, exist_ok=True)
    for name, payload in (
        ("mcp_inspector.json", inspector),
        ("claude_desktop_config.json", desktop),
    ):
        out = CONFIGS / name
        out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"已写入 {out}")

    if not DB.is_file():
        print(f"[!] 演示库不存在：{DB}\n    先运行 python scripts/make_demo_db.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
