"""环境预检脚本：一次性看清本机是否具备运行 FinRAG 的全部条件。

用法::

    python scripts/check_env.py
    python scripts/check_env.py --strict     # 有缺失项时以非零码退出（适合 CI）

检查项：
1. Python 版本与关键第三方包
2. MySQL 连通性（以及数据表是否已创建）
3. Milvus 连通性与集合状态
4. 通义千问 API Key 配置
5. Tesseract OCR 与系统依赖
6. 数据目录与配置项合法性
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from typing import Dict, List, Tuple

from _common import C, banner, error, info, kv, success, warn

REQUIRED_PACKAGES: List[Tuple[str, str, bool]] = [
    # (导入名, 用途, 是否硬性要求)
    ("fastapi", "Web 框架", True),
    ("uvicorn", "ASGI 服务器", True),
    ("pydantic", "数据校验", True),
    ("pydantic_settings", "配置管理", True),
    ("sqlalchemy", "ORM", True),
    ("pymysql", "MySQL 驱动", True),
    ("pymilvus", "Milvus 向量库客户端", True),
    ("langchain_core", "LangChain 核心", True),
    ("langchain_openai", "通义千问兼容接入", True),
    ("langchain_text_splitters", "文本切分", True),
    ("openai", "OpenAI 兼容 SDK", True),
    ("pypdf", "PDF 解析", True),
    ("jieba", "中文分词", True),
    ("numpy", "数值计算", True),
    ("rank_bm25", "BM25 关键词检索", False),
    ("docx", "Word 文档解析", False),
    ("PIL", "图片处理（OCR 前置）", False),
    ("pytesseract", "OCR 引擎封装", False),
    ("pdf2image", "扫描版 PDF 转图片", False),
    ("multipart", "文件上传（multipart）", False),
    ("bcrypt", "口令哈希", False),
]

MIN_PYTHON = (3, 10)


def check_python() -> bool:
    """检查 Python 版本。"""
    banner("1. Python 运行时")
    version = sys.version_info
    current = f"{version.major}.{version.minor}.{version.micro}"
    ok = (version.major, version.minor) >= MIN_PYTHON
    kv("Python 版本", current, ok)
    kv("最低要求", ".".join(map(str, MIN_PYTHON)), ok)
    kv("解释器路径", sys.executable)
    return ok


def check_packages() -> Tuple[bool, List[str]]:
    """检查第三方依赖。"""
    banner("2. 第三方依赖")
    missing_required: List[str] = []
    missing_optional: List[str] = []

    for module, purpose, required in REQUIRED_PACKAGES:
        try:
            installed = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            installed = False
        kv(f"{module}", f"{'已安装' if installed else '未安装'} — {purpose}", installed)
        if not installed:
            (missing_required if required else missing_optional).append(module)

    if missing_required:
        error(f"缺少必需依赖（{len(missing_required)} 个）：{', '.join(missing_required)}")
        info("修复：pip install -r requirements.txt")
    if missing_optional:
        warn(f"缺少可选依赖（{len(missing_optional)} 个）：{', '.join(missing_optional)}")
        info("可选依赖缺失只会禁用对应功能（如上传、OCR），不影响主链路。")
    if not missing_required and not missing_optional:
        success("全部依赖已安装。")
    return not missing_required, missing_optional


def check_mysql() -> bool:
    """检查 MySQL 连接与建表情况。"""
    banner("3. MySQL 结构化存储")
    try:
        from sqlalchemy import inspect

        from app.core.config import get_settings
        from app.db.session import check_connection, get_engine
    except Exception as exc:
        error(f"导入数据库模块失败：{exc}")
        return False

    settings = get_settings()
    kv("连接串", settings.database_url.split("@")[-1])
    result = check_connection()
    kv("连通状态", result.get("connected"), bool(result.get("connected")))
    if result.get("connected"):
        kv("方言", result.get("dialect"))
        kv("版本", result.get("version") or "(未知)")
        try:
            tables = sorted(inspect(get_engine()).get_table_names())
        except Exception as exc:
            warn(f"读取表清单失败：{exc}")
            tables = []
        kv("数据表数量", len(tables), len(tables) > 0)
        if tables:
            info("已存在的表：" + ", ".join(tables))
        else:
            warn("尚未创建数据表。")
            info("修复：python scripts/init_mysql.py")
    else:
        error(f"连接失败：{result.get('error')}")
        info("请确认 MySQL 服务已启动，且 .env 中 MYSQL_* 配置正确。")
    return bool(result.get("connected"))


def check_milvus() -> bool:
    """检查 Milvus 连通性与集合。"""
    banner("4. Milvus 向量数据库")
    try:
        from app.core.config import get_settings
        from app.db.milvus_store import MilvusVectorStore
    except Exception as exc:
        error(f"导入向量库模块失败：{exc}")
        return False

    settings = get_settings()
    store = MilvusVectorStore(settings)
    kv("地址", store.uri)
    kv("集合", store.collection)
    kv("向量维度", store.dim)
    kv("索引/度量", f"{settings.milvus_index_type} / {settings.milvus_metric_type}")

    health = store.health()
    connected = bool(health.get("connected"))
    kv("连通状态", connected, connected)
    if connected:
        kv("集合是否存在", health.get("collection_exists"), bool(health.get("collection_exists")))
        kv("向量数量", health.get("vector_count"))
        if not health.get("collection_exists"):
            warn("集合尚未创建。")
            info("修复：python scripts/init_milvus.py")
    else:
        error(f"连接失败：{health.get('error')}")
        info("请确认 Milvus 服务已启动（docker compose up -d milvus），或修改 .env 中 MILVUS_*。")
    store.close()
    return connected


def check_llm() -> bool:
    """检查通义千问配置。"""
    banner("5. 通义千问（DashScope）")
    try:
        from app.core.config import get_settings
        from app.llm.embeddings import QwenEmbedder
        from app.llm.qwen import QwenChatClient
    except Exception as exc:
        error(f"导入大模型模块失败：{exc}")
        return False

    settings = get_settings()
    chat = QwenChatClient(settings)
    embedder = QwenEmbedder(settings)

    configured = chat.is_configured()
    kv("DASHSCOPE_API_KEY", "已配置" if configured else "未配置", configured)
    kv("对话模型", settings.qwen_chat_model)
    kv("向量模型", f"{settings.qwen_embedding_model} (dim={settings.qwen_embedding_dim})")
    kv("接口地址", settings.qwen_base_url)
    if not configured:
        error("未配置 API Key，问答接口将无法生成回答。")
        info("请在 .env 中填写 DASHSCOPE_API_KEY（阿里云百炼控制台申请）。")
    else:
        success("大模型配置就绪。")
    return configured


def check_ocr() -> bool:
    """检查 OCR 能力。"""
    banner("6. OCR（图片 / 扫描件）")
    try:
        from app.core.config import get_settings
        from app.ingest.ocr import OcrEngine
    except Exception as exc:
        error(f"导入 OCR 模块失败：{exc}")
        return False

    settings = get_settings()
    status = OcrEngine(settings).status()
    kv("功能开关", status.get("enabled"))
    kv("是否可用", status.get("available"), bool(status.get("available")))
    kv("tesseract 路径", status.get("binary") or "(未找到)")
    kv("识别语言", status.get("lang"))
    if not status.get("available"):
        warn(f"OCR 不可用：{status.get('reason')}")
        info("Windows 安装示例：winget install UB-Mannheim.TesseractOCR")
        info("Ubuntu 安装示例：apt-get install -y tesseract-ocr tesseract-ocr-chi-sim poppler-utils")
    return bool(status.get("available"))


def check_paths() -> bool:
    """检查数据目录与关键配置项。"""
    banner("7. 目录与配置")
    try:
        from app.core.config import PROJECT_ROOT, get_settings
    except Exception as exc:
        error(f"导入配置模块失败：{exc}")
        return False

    settings = get_settings()
    ok = True
    for name, path in (
        ("项目根目录", PROJECT_ROOT),
        ("上传目录", settings.upload_path),
        ("语料目录", PROJECT_ROOT / "data" / "corpus"),
        ("日志目录", PROJECT_ROOT / "var" / "logs"),
        ("SQL 脚本", PROJECT_ROOT / "sql" / "01_schema.sql"),
    ):
        exists = path.exists()
        if name in ("日志目录",):
            path.mkdir(parents=True, exist_ok=True)
            exists = True
        kv(name, path, exists)
        ok = ok and exists

    kv("应用环境", settings.app_env)
    kv("切分参数", f"chunk_size={settings.chunk_size} overlap={settings.chunk_overlap}",
       settings.chunk_overlap < settings.chunk_size)
    kv("检索参数", f"top_k={settings.retrieve_top_k} mode={settings.retrieve_mode}")
    kv("响应预算", f"{settings.response_time_budget_ms} ms")
    return ok


def main() -> int:
    """执行全部预检项并汇总。"""
    parser = argparse.ArgumentParser(description="FinRAG 环境预检")
    parser.add_argument("--strict", action="store_true", help="存在必需项缺失时返回非零码")
    parser.add_argument("--skip-network", action="store_true", help="跳过 MySQL/Milvus 连通性检查")
    args = parser.parse_args()

    print(f"{C.BOLD}FinRAG 环境预检{C.RESET}")
    print("检查本机是否具备运行「金融制度智能问答系统」的全部条件\n")

    results: Dict[str, bool] = {}
    results["Python"] = check_python()
    packages_ok, missing_optional = check_packages()

    if args.skip_network:
        warn("已跳过 MySQL / Milvus 连通性检查")
    else:
        results["MySQL"] = check_mysql()
        results["Milvus"] = check_milvus()

    results["通义千问"] = check_llm()
    results["OCR"] = check_ocr()
    results["目录与配置"] = check_paths()

    banner("预检结论")
    for name, passed in results.items():
        kv(name, "通过" if passed else "未通过", passed)

    hard_failures = [name for name, passed in results.items() if not passed]
    optional_only = hard_failures in ([], ["OCR"]) and packages_ok

    if not hard_failures:
        success("环境完全就绪，可以启动服务：python backend/main.py")
        code = 0
    elif optional_only:
        warn("必需项全部就绪；OCR 为可选能力，缺失只影响图片/扫描件入库。")
        code = 0 if not args.strict else 1
    else:
        error("存在未通过项：" + ", ".join(hard_failures))
        if missing_optional:
            info("可选依赖缺失（不影响主链路）：" + ", ".join(missing_optional))
        code = 1

    if args.strict and (hard_failures or not packages_ok):
        code = 1
    return code


if __name__ == "__main__":
    sys.exit(main())
