"""脚本公共工具：路径引导、控制台输出、SQL 拆分。

所有 ``scripts/*.py`` 都通过本模块把 ``backend/`` 加入 ``sys.path``，
因此可以在项目根目录直接执行，无需手动设置 ``PYTHONPATH``。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterable, List

# 项目根目录与后端目录
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
BACKEND_DIR: Path = PROJECT_ROOT / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


# ---------------------------------------------------------------------- 输出
class C:
    """终端颜色。"""

    RESET = "\033[0m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[36m"
    BOLD = "\033[1m"


def banner(title: str) -> None:
    """打印分节标题。"""
    print(f"\n{C.BOLD}{C.BLUE}{'=' * 72}{C.RESET}")
    print(f"{C.BOLD}{C.BLUE}  {title}{C.RESET}")
    print(f"{C.BOLD}{C.BLUE}{'=' * 72}{C.RESET}")


def info(message: str) -> None:
    """普通信息。"""
    print(f"{C.BLUE}[信息]{C.RESET} {message}")


def success(message: str) -> None:
    """成功信息。"""
    print(f"{C.GREEN}[成功]{C.RESET} {message}")


def warn(message: str) -> None:
    """警告信息。"""
    print(f"{C.YELLOW}[警告]{C.RESET} {message}")


def error(message: str) -> None:
    """错误信息。"""
    print(f"{C.RED}[错误]{C.RESET} {message}")


def kv(label: str, value: object, ok: bool | None = None) -> None:
    """打印 ``键: 值``，可选状态标记。"""
    mark = ""
    if ok is True:
        mark = f" {C.GREEN}[正常]{C.RESET}"
    elif ok is False:
        mark = f" {C.RED}[异常]{C.RESET}"
    print(f"  · {label}: {value}{mark}")


# ------------------------------------------------------------------ SQL 工具
RE_LINE_COMMENT = re.compile(r"--[^\n]*")
RE_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


def strip_sql_comments(sql: str) -> str:
    """移除 SQL 注释，避免注释中的分号干扰语句拆分。"""
    without_block = RE_BLOCK_COMMENT.sub("", sql)
    return RE_LINE_COMMENT.sub("", without_block)


def split_sql_statements(sql: str) -> List[str]:
    """把 SQL 文件拆分为单条语句（按分号切分，忽略注释与空语句）。"""
    cleaned = strip_sql_comments(sql)
    statements: List[str] = []
    for chunk in cleaned.split(";"):
        statement = chunk.strip()
        if statement:
            statements.append(statement)
    return statements


def read_sql_file(path: Path) -> List[str]:
    """读取并拆分 SQL 文件。"""
    return split_sql_statements(path.read_text(encoding="utf-8"))


def find_supported_files(directory: Path, extensions: Iterable[str]) -> List[Path]:
    """递归查找指定扩展名的文件（排序稳定）。"""
    allowed = {ext.lower() for ext in extensions}
    return sorted(
        path
        for path in directory.rglob("*")
        if path.is_file() and path.suffix.lower() in allowed
    )


__all__ = [
    "PROJECT_ROOT",
    "BACKEND_DIR",
    "C",
    "banner",
    "info",
    "success",
    "warn",
    "error",
    "kv",
    "split_sql_statements",
    "strip_sql_comments",
    "read_sql_file",
    "find_supported_files",
]
