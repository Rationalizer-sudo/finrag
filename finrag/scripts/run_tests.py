"""统一的测试入口与覆盖率报告。

两种运行模式，自动选择：

1. **pytest 模式**（推荐，装了 pytest 时自动使用）
   ``pytest --cov=app --cov-report=term-missing --cov-fail-under=70``
   覆盖率由 pytest-cov 精确统计（基于字节码，含分支）。

2. **标准库模式**（没装 pytest 时自动降级）
   自实现 ``sys.settrace`` 逐行追踪 ``backend/app``，用 ``dis`` 精确识别可执行行。
   零额外依赖，保证在没有网络的机器上也能验证「覆盖率 ≥ 70%」这条课题要求。

   为什么不直接用标准库 ``trace`` 模块：``trace._Ignore`` 会按**文件名主干**
   （``os.path.splitext(os.path.basename(path))[0]``）缓存忽略判定，因此只要
   site-packages 里存在同名的 ``config.py`` / ``types.py``，我们自己的
   ``app/core/config.py``、``app/retrieval/types.py`` 就会被整体误判为
   "ignored" 而统计为 0%——实测会把总体覆盖率低估十多个百分点。
   自实现追踪器按**绝对路径前缀**判定，结果准确且更快（只追踪本项目代码）。

用法::

    python scripts/run_tests.py                 # 跑全部测试 + 覆盖率
    python scripts/run_tests.py --no-cov        # 只跑测试
    python scripts/run_tests.py -k credibility  # 只跑匹配的用例
    python scripts/run_tests.py --threshold 80  # 自定义覆盖率门槛
    python scripts/run_tests.py --report        # 输出未覆盖行明细
"""

from __future__ import annotations

import argparse
import dis
import os
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Set, Tuple

from _common import BACKEND_DIR, PROJECT_ROOT, banner, error, info, kv, success, warn

APP_DIR = (BACKEND_DIR / "app").resolve()
DEFAULT_THRESHOLD = 70.0

# 不参与覆盖率统计的文件（包声明文件本身不含逻辑）
COVERAGE_EXCLUDES = {
    "__init__.py",
}


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="FinRAG 测试与覆盖率")
    parser.add_argument("-k", "--keyword", default=None, help="只运行名称匹配的用例")
    parser.add_argument("--pattern", default="test_*.py", help="测试文件匹配模式")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                        help=f"覆盖率门槛（%%），默认 {DEFAULT_THRESHOLD}")
    parser.add_argument("--no-cov", action="store_true", help="跳过覆盖率统计")
    parser.add_argument("--report", action="store_true", help="打印未覆盖行明细")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")
    return parser.parse_args()


def pytest_available() -> bool:
    """是否安装了 pytest。"""
    import importlib.util

    try:
        return importlib.util.find_spec("pytest") is not None
    except (ImportError, ValueError):  # pragma: no cover
        return False


def pytest_cov_available() -> bool:
    """是否安装了 pytest-cov。"""
    import importlib.util

    try:
        return importlib.util.find_spec("pytest_cov") is not None
    except (ImportError, ValueError):  # pragma: no cover
        return False


# ---------------------------------------------------------------------------
#  标准库覆盖率实现
# ---------------------------------------------------------------------------
def executable_lines(path: Path) -> Set[int]:
    """用 ``dis`` 精确找出文件中的可执行行号。

    比「非空非注释行」的文本启发式更准确：能正确排除纯 docstring、
    续行、以及只出现在装饰器里的行。
    """
    try:
        source = path.read_text(encoding="utf-8")
        code = compile(source, str(path), "exec")
    except (OSError, SyntaxError, UnicodeDecodeError):  # pragma: no cover
        return set()

    lines: Set[int] = set()
    stack = [code]
    while stack:
        current = stack.pop()
        for _, lineno in dis.findlinestarts(current):
            if lineno:
                lines.add(lineno)
        for const in current.co_consts:
            if hasattr(const, "co_code"):
                stack.append(const)
    return lines


def iter_target_files() -> List[Path]:
    """列出需要统计覆盖率的源码文件。"""
    return [
        path
        for path in sorted(APP_DIR.rglob("*.py"))
        if path.name not in COVERAGE_EXCLUDES
    ]


class LineCoverage:
    """基于 ``sys.settrace`` 的逐行覆盖率收集器。

    两个必须同时处理的细节：

    1. **按绝对路径前缀判定归属**，只追踪 ``backend/app`` 下的文件，
       标准库与 site-packages 完全不进入统计（既准确又快）；
    2. **必须同时设置 ``threading.settrace``**：FastAPI 的 ``TestClient``
       （Starlette + anyio）会把 ASGI 应用跑在**独立的工作线程**里，
       只设 ``sys.settrace`` 会导致所有接口层代码被统计成 0%。
    """

    def __init__(self, root: Path) -> None:
        self.root = os.path.normcase(str(root))
        self.lines: Dict[str, Set[int]] = defaultdict(set)
        self._previous_sys = None
        self._previous_thread = None

    def _interesting(self, filename: str) -> bool:
        """判断该文件是否属于被追踪目录。"""
        if not filename or not filename.endswith(".py"):
            return False
        try:
            resolved = os.path.normcase(os.path.abspath(filename))
        except (OSError, ValueError):  # pragma: no cover
            return False
        return resolved.startswith(self.root)

    def _global_trace(self, frame, event, arg):
        """只对 'call' 事件做归属判断，命中才安装局部追踪。"""
        if event != "call":
            return None
        if self._interesting(frame.f_code.co_filename):
            return self._local_trace
        return None

    def _local_trace(self, frame, event, arg):  # pragma: no cover - 热路径
        if event == "line":
            self.lines[os.path.abspath(frame.f_code.co_filename)].add(frame.f_lineno)
        return self._local_trace

    def start(self) -> None:
        """开始追踪（主线程 + 之后新建的线程）。"""
        self._previous_sys = sys.gettrace()
        self._previous_thread = threading.gettrace()
        threading.settrace(self._global_trace)
        sys.settrace(self._global_trace)

    def stop(self) -> None:
        """结束追踪并恢复原有 trace 函数。"""
        sys.settrace(self._previous_sys)
        threading.settrace(self._previous_thread)
        self._previous_sys = None
        self._previous_thread = None


def compute_coverage(covered: Dict[str, Set[int]]) -> Tuple[Dict[Path, Tuple[int, int]], float, Dict[Path, Set[int]]]:
    """计算各文件覆盖率与总体覆盖率。

    :returns: ``(每文件 (已覆盖, 可执行), 总体百分比, 每文件未覆盖行)``
    """
    per_file: Dict[Path, Tuple[int, int]] = {}
    missing_map: Dict[Path, Set[int]] = {}
    covered_total = 0
    executable_total = 0

    for path in iter_target_files():
        lines = executable_lines(path)
        if not lines:
            continue
        hit = lines & covered.get(str(path.resolve()), set())
        per_file[path] = (len(hit), len(lines))
        missing_map[path] = sorted(lines - hit)  # type: ignore[assignment]
        covered_total += len(hit)
        executable_total += len(lines)

    overall = (covered_total / executable_total * 100) if executable_total else 0.0
    return per_file, overall, missing_map


def print_coverage(
    per_file: Dict[Path, Tuple[int, int]],
    overall: float,
    missing_map: Dict[Path, Set[int]],
    threshold: float,
    report: bool,
) -> None:
    """打印覆盖率表格。"""
    banner("覆盖率报告（backend/app）")
    rows = sorted(per_file.items(), key=lambda item: (item[1][0] / max(item[1][1], 1)))
    print(f"  {'覆盖率':>8}  {'已覆盖':>7}  {'可执行':>7}  文件")
    print(f"  {'-' * 8}  {'-' * 7}  {'-' * 7}  {'-' * 42}")
    for path, (hit, total) in rows:
        percent = hit / total * 100 if total else 0.0
        marker = " " if percent >= threshold else "!"
        rel = path.relative_to(BACKEND_DIR)
        print(f"  {percent:7.1f}%{marker} {hit:7d}  {total:7d}  {rel}")
        if report and percent < 100.0:
            missing = missing_map.get(path, set())
            if missing:
                preview = ", ".join(str(line) for line in list(missing)[:40])
                suffix = " ..." if len(missing) > 40 else ""
                print(f"            未覆盖行: {preview}{suffix}")
    print()
    print(f"  总体覆盖率：{overall:.2f}%")

    below = [(p, h, t) for p, (h, t) in rows if t and h / t * 100 < threshold]
    if below:
        info(f"低于 {threshold:.0f}% 的文件（可作为后续补测重点）：")
        for path, hit, total in below:
            print(f"    - {path.relative_to(BACKEND_DIR)}: {hit / total * 100:.1f}%  ({hit}/{total})")


# ---------------------------------------------------------------------------
#  运行模式
# ---------------------------------------------------------------------------
def run_pytest(args: argparse.Namespace) -> int:
    """使用 pytest（+ 可选 pytest-cov）运行。"""
    import pytest

    cmd: List[str] = ["tests", "-p", "no:cacheprovider"]
    if args.verbose:
        cmd.append("-v")
    if args.keyword:
        cmd += ["-k", args.keyword]

    if not args.no_cov and pytest_cov_available():
        cmd += [
            "--cov=app",
            "--cov-report=term-missing",
            f"--cov-fail-under={args.threshold}",
        ]
    elif not args.no_cov:
        warn("未安装 pytest-cov，本次不统计覆盖率（pip install pytest-cov）")

    info("执行：" + "python -m pytest " + " ".join(cmd))
    return int(pytest.main(cmd))


def run_stdlib_mode(args: argparse.Namespace) -> int:
    """使用标准库 ``unittest`` + 自实现 ``sys.settrace`` 追踪器运行。

    注意：**测试发现（``loader.discover``）必须放在追踪开始之后**。
    发现过程会导入测试模块，进而导入 ``app.*``；若在追踪前导入，
    所有模块级代码（函数定义、常量、ORM 模型声明等）都会被漏统计。
    """
    import unittest

    def build_suite():
        """构造测试套件（在追踪上下文中调用）。"""
        loader = unittest.TestLoader()
        if args.keyword:
            loader.testNamePatterns = [f"*{args.keyword}*"]
        return loader.discover(str(PROJECT_ROOT / "tests"), top_level_dir=str(PROJECT_ROOT))

    runner = unittest.TextTestRunner(verbosity=2 if args.verbose else 1)

    if args.no_cov:
        return 0 if runner.run(build_suite()).wasSuccessful() else 1

    warn("未安装 pytest，使用自实现的逐行追踪器统计覆盖率")
    tracker = LineCoverage(APP_DIR)
    started = time.perf_counter()
    tracker.start()
    try:
        suite = build_suite()
        passed = runner.run(suite).wasSuccessful()
    finally:
        tracker.stop()
    elapsed = time.perf_counter() - started

    per_file, overall, missing_map = compute_coverage(tracker.lines)
    print_coverage(per_file, overall, missing_map, args.threshold, args.report)

    kv("用例耗时", f"{elapsed:.1f} 秒")
    kv("测试结果", "全部通过" if passed else "存在失败", passed)
    kv("总体覆盖率", f"{overall:.2f}%", overall >= args.threshold)
    kv("覆盖率门槛", f"{args.threshold:.0f}%")

    if not passed:
        error("测试未全部通过。")
        return 1
    if overall < args.threshold:
        error(f"覆盖率 {overall:.2f}% 低于门槛 {args.threshold:.0f}%（课题要求 ≥ 70%）")
        return 2
    success(f"测试全部通过，覆盖率 {overall:.2f}% 达标。")
    info("提示：pip install pytest pytest-cov 后可获得含分支统计的精确报告")
    return 0


def main() -> int:
    """入口。"""
    args = parse_args()
    banner("FinRAG 测试与覆盖率")
    kv("项目根目录", PROJECT_ROOT)
    kv("覆盖率门槛", f"{args.threshold:.0f}%")
    kv("运行模式", "pytest" if pytest_available() else "标准库 unittest+trace")

    if pytest_available():
        code = run_pytest(args)
        if not args.no_cov and not pytest_cov_available():
            info("提示：pip install pytest-cov 后可获得带分支统计的精确覆盖率报告")
        return code
    return run_stdlib_mode(args)


if __name__ == "__main__":
    sys.exit(main())
