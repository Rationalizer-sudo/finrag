"""FinRAG 测试包。

**务必先于任何 ``app.*`` 模块设置环境变量**：``app.core.config`` 在模块导入时
就会实例化全局配置，若此时环境仍是默认值，测试会连到真实 MySQL。
把这段逻辑放在包的 ``__init__`` 里，可保证 ``import tests.xxx`` 时最先执行。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"

# 测试环境：SQLite 内存库 + 无真实 API Key
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL_OVERRIDE", "sqlite://")
os.environ.setdefault("MYSQL_ECHO", "false")
os.environ.setdefault("DASHSCOPE_API_KEY", "")
os.environ.setdefault("UPLOAD_DIR", "./var/test-uploads")

for path in (str(PROJECT_ROOT), str(BACKEND_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

__all__ = ["PROJECT_ROOT", "BACKEND_DIR"]
