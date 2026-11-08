"""MCP Server：受限文件系统（白名单根目录内 list_dir / read_file / glob）。

安全范式：一切路径先过 `resolve_within_root`（security.py）——realpath 归一化后必须落在
白名单根目录内，防目录穿越。所有读取限制在 root 之下进行：
  - list_dir：列某目录条目（root 的相对目录）
  - read_file：读某文件文本（UTF-8，超大文件截断）
  - glob_files：按 glob 在 root 内递归查找（**不返回点开头路径**，如 .git/ .venv/；
    rglob 不跟目录符号链）。注意这是"结果过滤"而不是权限——真正的边界只有白名单根本身。

【OOP 说明】files_safe 的差异点是"root 白名单目录"是一次性配置——这让它是三件里最接近
"对象状态"的模块。现状用 create_server(root) 在闭包捕获 root（函数式注入）；若你希望换成
显式类，语义等价地可写：
    class FileServer:
        def __init__(self, root: Path):
            self.root = root.resolve()          # 启动时即解析固定住，防止中途被人替换 cwd
            if not self.root.is_dir(): raise ...
        def list(self, rel="."): return list_dir(self.root, rel)
        def read(self, rel): return read_file(self.root, rel)
        def glob(self, pat): return glob_files(self.root, pat)
        def as_mcp(self): return FastMCP("files-safe") decorated with self methods
主要动机是保留"root 解析一次"的状态与启动校验；闭包已做到同样效果，但类写法更显式、
更方便以后叠加每-server 专属配置。

运行：python -m mcp_tools.files_safe
环境变量：MCP_FILES_ROOT（默认当前目录）
"""

import heapq
import os
import stat
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .security import resolve_within_root

MAX_READ_CHARS = 200_000  # 单次读取上限（字符）；按流读取，不会先把整个文件读进内存
MAX_LIST_ENTRIES = 200    # list_dir 结果上限；与 glob/query 的行数上限保持一致的收敛口径
# 输出字符预算：只限条数挡不住"200 个超长文件名/路径"把上下文挤爆。
# 与 sqlite_ro / list_tables 的 20000 字符口径保持一致。
MAX_OUTPUT_CHARS = 20_000


def list_dir(root: Path, relative_dir: str = ".") -> str:
    """列出 root 下某相对目录的内容（条目名，目录带尾 /）。

    超过 MAX_LIST_ENTRIES 即截断：`list_dir` 的输出会整段进模型上下文，
    一个上万条目的目录足以把上下文挤爆（glob 早有 200 上限，这里此前是个缺口）。
    """
    target = resolve_within_root(relative_dir, root)
    if not target.is_dir():
        return f"不是目录：{relative_dir}"
    # 用 nsmallest 流式取字典序前 N+1 项：sorted(iterdir()) 会先把超大目录的**全部**
    # 条目建成列表，等于在"限制输出"之前先把内存占满。多取 1 项只用于判断是否截断。
    items = heapq.nsmallest(
        MAX_LIST_ENTRIES + 1,
        (p.name + ("/" if p.is_dir() else "") for p in target.iterdir()),
    )
    if not items:
        return "（空目录）"
    truncated = len(items) > MAX_LIST_ENTRIES
    items = items[:MAX_LIST_ENTRIES]
    budget = MAX_OUTPUT_CHARS - 200   # 给结尾提示行留位置
    lines: list[str] = []
    used = 0
    for item in items:
        if used + len(item) + 1 > budget:
            break
        lines.append(item)
        used += len(item) + 1
    text = "\n".join(lines)
    notes: list[str] = []
    if len(lines) < len(items):
        notes.append(
            f"受 {MAX_OUTPUT_CHARS} 字符上限所限，实际只显示了前 {len(lines)} 个"
        )
    # 字符上限已经把列表截短时，上面那条已经报了实际条数；
    # 不能再补一句"仅显示前 MAX_LIST_ENTRIES 个"——实测两道闸同时生效时它会虚报条数。
    elif truncated:
        notes.append(f"条目过多，仅显示前 {MAX_LIST_ENTRIES} 个，请指明更具体的子目录")
    if notes:
        text += "\n...（" + "；".join(notes) + "）"
    return text


def read_file(root: Path, relative_path: str) -> str:
    """读取 root 白名单内某文件的 UTF-8 文本，超过 MAX_READ_CHARS 字符即截断标记。

    先 resolve_within_root（越界直接 ValueError），再 is_file 校验。
    **只从流里读 MAX_READ_CHARS+1 个字符**，而不是"整读再截断"——后者虽然返回值有上限，
    但对一个几 GB 的文件仍会先把它整个读进内存，等于没有资源保护（而本项目的定位正是安全边界）。
    多读 1 个字符只为判断"是否被截断"。
    """
    target = resolve_within_root(relative_path, root)
    if not target.is_file():
        return f"不是文件：{relative_path}"
    with target.open("r", encoding="utf-8", errors="replace") as fh:
        text = fh.read(MAX_READ_CHARS + 1)
    if len(text) > MAX_READ_CHARS:
        text = text[:MAX_READ_CHARS] + f"\n...（内容过长，已截断前 {MAX_READ_CHARS} 字符）"
    return text


MAX_GLOB_RESULTS = 200  # glob 结果上限；超过仅展示前 200 个并提示


def _glob_matches(relative: str, pattern: str) -> bool:
    """单个相对路径（已归一化为正斜杠）是否匹配 glob pattern。

    为什么不能直接只用 `fnmatch(relative, pattern)`：标准 glob 语义里 `**/` 表示
    "**零个或多个**目录"，而 fnmatch 把整条相对路径当作普通字符串匹配，`**/...`
    转成正则后要求路径里**必须有一个斜杠**，于是顶层文件 `b.py` 匹配不上。
    实测：仓库根上 `glob_files(root,'**/*.py')` 会漏掉顶层所有 py
    （`**/*` 漏掉 LICENSE/README.md/pyproject.toml 等 5 个文件）。
    所以这里对带 `**/` 前缀的 pattern 额外用"去掉该前缀后的剩余部分"再匹配一次，
    把"零个目录"这一支补回来；其余情况仍交给 fnmatchcase（不自己实现通配语义）。

    注意 fnmatch 的 `*` **会**匹配 `/`：因此 `*.py` 在本工具里等价于"递归匹配所有
    py"，而不是标准 shell glob 的"仅当前目录"。本工具始终在 root 内递归遍历，
    pattern 只做结果过滤；要"只看一层"请用 `list_dir`，或在消费方对返回路径过滤。
    """
    import fnmatch

    # `**/` 可以匹配"零个或多个目录"。只剥离**开头**的 `**/` 不够：
    # `src/**/*.py` 里的 `**/` 同样应该能匹配零层，即 `src/a.py` 也该命中。
    # 这里对**每一处** `**/` 独立枚举"保留 / 去掉"，生成 2^n 个变体，
    # 而不是每次只去掉第一处——后者会漏掉 `a/**/b/**/c` → `a/**/b/c`
    # 这种"保留前面的 `**/`、只去掉后面的"组合（实测漏配）。
    # 变体数上限防住恶意 pattern 的指数膨胀（超过 1024 种就只用原 pattern）。
    positions = []
    start = 0
    while True:
        idx = pattern.find("**/", start)
        if idx < 0:
            break
        positions.append(idx)
        start = idx + 3
    if len(positions) > 10:  # 2^10 已到上限，更多变体没有实际价值
        variants = {pattern}
    else:
        variants = set()
        for mask in range(1 << len(positions)):
            item = pattern
            offset = 0
            for j, pos in enumerate(positions):
                if mask >> j & 1:
                    cut = pos - offset
                    item = item[:cut] + item[cut + 3 :]
                    offset += 3
            variants.add(item)
    return any(fnmatch.fnmatchcase(relative, item) for item in variants)


def _is_link_or_junction(path: Path) -> bool:
    """目录项是否符号链接 / Windows junction（reparse point）。

    `os.walk(... followlinks=False)` 只挡符号链接，**挡不住 junction**
    （Windows junction 的 is_symlink() 是 False）。若不在这里剪掉，root 内一个指向
    C:\\ 或祖先目录的 junction 会让遍历走进整棵外部树，而且可能形成环。
    per-hit 的 resolve_within_root 只负责"丢弃结果"，遍历成本已经付出。
    """
    try:
        if os.path.islink(path):
            return True
        isjunction = getattr(os.path, "isjunction", None)
        if callable(isjunction) and isjunction(path):
            return True
        st = os.lstat(path)
        attrs = getattr(st, "st_file_attributes", 0)
        return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    except OSError:
        return True


def _iter_files_pruning_dot_dirs(root: Path, pattern: str):
    """按 pattern 在 root 内遍历文件，**遇到点开头的目录就整棵剪掉**。

    为什么不用 `root.rglob(pattern)` 再过滤：rglob 会照走 `.venv/`、`.git/` 这些目录，
    而它们的返回结果本来就会被丢弃——实测在仓库根上跑 `**/*.py` 约 0.7s，其中大部分
    时间花在遍历 `.venv\\Lib\\site-packages`（几万个文件）。剪枝只是把"注定被丢掉的
    工作"提前不做，返回结果与 rglob+过滤完全一致。

    `os.walk(topdown=True)` 允许就地改 `dirnames` 来剪枝；它**不跟随目录符号链接**
    （followlinks 默认 False），与 rglob 的语义一致。注意 Windows junction 的
    `is_symlink()` 是 False，walk 会当普通目录照走——所以每个命中仍要过
    `resolve_within_root`（见 glob_files docstring），剪枝不能替代那道校验。

    匹配用 `fnmatchcase` 而不是 `fnmatch`：后者在 Windows 上会把两侧 normcase 成小写，
    于是 `'**/*.PY'` 在 Windows 能匹配、在 Linux 不能——同一份调用跨平台结果不同。
    rglob 本身是大小写敏感的（随文件系统），用 fnmatchcase 保持这个语义。
    """
    import os

    # pattern 里的反斜杠统一成正斜杠，并去掉 `.` 与重复分隔符：
    # 输入侧本来就接受 `./a/*.py`、`a//*.py` 这类写法，若原样交给匹配器会漏配
    # （实测 `./*.py` 返回"无匹配文件"）。`..` 已在 glob_files 入口被拒绝。
    from pathlib import PurePosixPath

    norm_pattern = PurePosixPath(pattern.replace("\\", "/")).as_posix()

    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        here = Path(dirpath)
        # 剪枝：点开头目录整体跳过（含 .git/.venv/.pytest_cache）；
        # 链接/junction 也整体跳过，避免遍历到白名单外面（见 _is_link_or_junction）。
        dirnames[:] = [
            d
            for d in dirnames
            if not d.startswith(".") and not _is_link_or_junction(here / d)
        ]
        for name in filenames:
            if name.startswith("."):
                continue
            candidate = here / name
            relative = candidate.relative_to(root).as_posix()
            if _glob_matches(relative, norm_pattern):
                yield candidate


def glob_files(root: Path, pattern: str) -> str:
    """在 root 内按 glob 递归查找文件，返回相对 root 的路径列表（按字典序）。

    用 pathlib.rglob（不跟随目录 symlink，命中过多时只返回前 MAX_GLOB_RESULTS 条提示
    缩小 pattern——防止一整棵大目录被模型一条 glob 全部拉回）。

    **注意 Windows junction**：junction 的 `is_symlink()` 是 False（实测），所以 rglob 会
    把它当普通目录**照走**——"不跟随 symlink"这句话对 junction 不成立。安全结论不受影响：
    junction 下的文件在下面第 2 道（每个命中都过 resolve_within_root）会被丢弃，
    实测 `glob '**/*.txt'` 的结果里没有 junction 指向的外部文件。
    写在这里是为了让后续维护者不要误以为可以省掉 per-hit 校验。

    安全（与 list_dir / read_file 同一闸口，两道）：
      1. pattern 本身不允许绝对路径或 `..`——pathlib 对 `..` 只做词法拼接、不 resolve，
         否则 `../*.txt` 会枚举到白名单之外；
      2. 每个命中都过 resolve_within_root，realpath 落在 root 外（含指向外部的符号链接）即丢弃。

    点开头的路径（`.git/`、`.venv/`、`.pytest_cache/` 及其下文件）**不返回**。
    为什么必须排除：本工具默认的白名单根就是仓库根（见 gen_configs.py），而 rglob 会照走
    这些目录——实测 `glob_files(repo, '**/*.py')` 返回的前 200 条**全部**是
    `.venv\\Lib\\site-packages\\...`，项目自己的代码一条都没有：模型问"项目里有哪些 py"
    会得到一份纯依赖库清单，等于把 .git 与虚拟环境暴露给模型。
    这些子树在遍历时就被剪掉（见 `_iter_files_pruning_dot_dirs`），不是"先全走完再过滤"：
    实测在仓库根上跑 `**/*.py` 时约 0.7s 里大部分花在 `.venv`——剪枝把这份开销也去掉了。
    （要连遍历都省掉，仍应把白名单根设成专用数据目录，而不是仓库根。）

    返回的相对路径统一用 **正斜杠**（`as_posix()`）。这是刻意归一化，不是随平台走：
    Windows 下 `str(Path)` 会给 `src\\mcp_tools\\x.py`，而 Linux 会给 `src/mcp_tools/x.py`
    ——同一份代码、同一个 pattern，跨平台输出不一致；而消费方（模型/别的客户端脚本）
    通常按 POSIX 拼路径。实测把返回的 `src\\mcp_tools\\files_safe.py` 原样喂给下一个工具
    在 Windows 上能work，但换个 OS/换个客户端就对不上。输入侧本来就同时接受两种分隔符
    （`list_dir('./a')`、`glob('a/*.txt')`、`glob('a\\*.txt')` 实测都能用），所以只需归一化输出。
    """
    if not pattern:
        return "（pattern 不能为空）"
    raw = Path(pattern)
    # Windows 上 `is_absolute()` 只认"带盘符"的路径：`/Windows/*.ini`、`\Windows\*.ini`
    # 这类 **rooted**（有根无盘符）pattern 它会返回 False，于是漏过闸门、由 pathlib
    # 在 rglob 时抛 NotImplementedError。所以这里连 drive/root 一起判，让拒绝发生在闸门内。
    if raw.is_absolute() or raw.drive or raw.root or ".." in raw.parts:
        return "（pattern 不允许绝对路径或 .. ）"
    root = root.resolve()

    def _matched():
        for path in _iter_files_pruning_dot_dirs(root, pattern):
            relative = path.relative_to(root)
            if any(part.startswith(".") for part in relative.parts):
                continue  # 点目录/点文件：见 docstring（剪枝已挡掉大部分，这里兜住 pattern 本身带点的情形）
            try:
                resolve_within_root(str(path), root)
            except ValueError:
                continue
            yield relative.as_posix()

    # 用 nsmallest 只保留字典序前 MAX+1 条，而不是把命中**全部**收进列表再排序：
    # 白名单根指向几十万文件的目录时，旧写法会在"截断输出"之前先占满内存/时间。
    # 多取 1 条只用于判断是否还有更多；结果仍只取决于字典序，与遍历顺序无关。
    hits = heapq.nsmallest(MAX_GLOB_RESULTS + 1, _matched())
    if not hits:
        return "（无匹配文件）"
    truncated = len(hits) > MAX_GLOB_RESULTS
    hits = hits[:MAX_GLOB_RESULTS]
    budget = MAX_OUTPUT_CHARS - 200
    lines: list[str] = []
    used = 0
    for hit in hits:
        if used + len(hit) + 1 > budget:
            break
        lines.append(hit)
        used += len(hit) + 1
    text = "\n".join(lines)
    notes: list[str] = []
    if len(lines) < len(hits):
        notes.append(
            f"受 {MAX_OUTPUT_CHARS} 字符上限所限，实际只显示了前 {len(lines)} 个"
        )
    # 同上：字符闸已生效时不再虚报"仅显示前 MAX_GLOB_RESULTS 个"。
    elif truncated:
        notes.append(f"命中过多，仅显示前 {MAX_GLOB_RESULTS} 个，请用更精确的 pattern")
    if notes:
        text += "\n...（" + "；".join(notes) + "）"
    return text


def create_server(root: str | None = None) -> FastMCP:
    """工厂：构造一个绑定 root 只读目录的 files-safe MCP Server。

    通过闭包把 root_path 捕获进来，三个工具函数内部 token 持有该 root——函数式依赖注入。
    root 来源于 env(MCP_FILES_ROOT) 或显式传入；此处 resolve+is_dir 校验放在"创建即失败"，
    避免起了一个指向不存在目录的 Server 到调用时才报错。
    """
    root_path = Path(root or os.getenv("MCP_FILES_ROOT", ".")).resolve()
    if not root_path.is_dir():
        # 措辞要覆盖"存在但不是目录"：只说"不存在"会把排障引向"路径写错/盘没挂"，
        # 而真实原因可能是指向了一个文件。
        raise FileNotFoundError(f"白名单根目录不可用（不存在或不是目录）：{root_path}")
    mcp = FastMCP("files-safe")

    @mcp.tool()
    def list_dir_tool(relative_dir: str = ".") -> str:
        """列出白名单根目录下某目录的内容（不可越出根目录）。"""
        return list_dir(root_path, relative_dir)

    @mcp.tool()
    def read_file_tool(relative_path: str) -> str:
        """读取白名单根目录内某文件的文本（UTF-8，截断超大文件）。"""
        return read_file(root_path, relative_path)

    @mcp.tool()
    def glob_files_tool(pattern: str) -> str:
        """按 glob 在根目录内递归查找文件（如 '**/*.md'）。"""
        return glob_files(root_path, pattern)

    return mcp


if __name__ == "__main__":
    create_server().run()
