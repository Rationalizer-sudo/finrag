"""uvicorn 启动入口。

用法::

    # 开发（自动重载）
    python backend/main.py

    # 生产
    uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4

注意：工作目录需为 ``backend/``，或把 ``backend`` 加入 ``PYTHONPATH``。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 允许 `python backend/main.py` 直接运行（无需手动设置 PYTHONPATH）
BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def main() -> None:
    """启动 uvicorn 服务。"""
    import uvicorn

    from app.core.config import get_settings

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.app_env == "dev",
        log_level="debug" if settings.app_debug else "info",
        access_log=True,
    )


if __name__ == "__main__":
    main()
