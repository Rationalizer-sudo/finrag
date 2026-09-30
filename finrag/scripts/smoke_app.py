"""本地冒烟检查：不依赖 pytest，验证应用可创建、路由完整、核心逻辑可运行。

用途：在没有 pytest / 没有 Milvus / 没有 API Key 的开发机上，
快速确认代码结构、导入链与纯逻辑部分是否正常。
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND))

ok = 0
fail = 0


def check(name, fn):
    """执行一项检查并打印结果。"""
    global ok, fail
    try:
        result = fn()
        ok += 1
        print(f"[OK]   {name}" + (f" -> {result}" if result is not None else ""))
    except Exception as exc:
        fail += 1
        print(f"[FAIL] {name}: {type(exc).__name__}: {exc}")
        traceback.print_exc()


def list_routes():
    """通过 OpenAPI 列出全部接口路径。"""
    from app.main import create_app

    schema = create_app().openapi()
    paths = sorted(schema.get("paths", {}).keys())
    for path in paths:
        print("      ", path)
    return f"{len(paths)} 个接口"


if __name__ == "__main__":
    check("导入 app.main", lambda: __import__("app.main"))
    check("创建应用", lambda: f"title={__import__('app.main', fromlist=['create_app']).create_app().title}")
    check("接口清单", list_routes)
    print(f"\n合计: OK={ok} FAIL={fail}")
    sys.exit(1 if fail else 0)
