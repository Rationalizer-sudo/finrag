"""pytest 全局配置。

用例以 ``unittest.TestCase`` 编写，因此既可以用 ``pytest`` 运行（并能被
``pytest-cov`` 统计覆盖率），也可以用标准库 ``python -m unittest`` 直接运行——
在没有 pytest 的开发机上依然可以验证代码。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL_OVERRIDE", "sqlite://")
os.environ.setdefault("MYSQL_ECHO", "false")
os.environ.setdefault("DASHSCOPE_API_KEY", "")
os.environ.setdefault("UPLOAD_DIR", "./var/test-uploads")

for path in (str(PROJECT_ROOT), str(BACKEND_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)


def pytest_sessionfinish(session, exitstatus):  # pragma: no cover - pytest 钩子
    """测试会话结束时清理临时目录。"""
    try:
        from tests.support import cleanup_temp_dirs

        cleanup_temp_dirs()
    except Exception:
        pass
