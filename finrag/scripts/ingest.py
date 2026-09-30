"""知识库批量入库 CLI。

用法::

    # 入库 data/corpus 下的全部制度文件
    python scripts/ingest.py --path data/corpus

    # 入库单个文件，并记录公开来源链接
    python scripts/ingest.py --path data/corpus/xxx.txt --source-url https://www.pbc.gov.cn/...

    # 内容未变也强制重新入库（例如调整了切分参数）
    python scripts/ingest.py --path data/corpus --force

    # 只重建向量（Milvus 集合丢失、或上次向量写入失败时使用）
    python scripts/ingest.py --rebuild-vectors

    # 查看知识库统计
    python scripts/ingest.py --stats

    # 删除某个文档
    python scripts/ingest.py --delete-document 12

流程与接口层 ``POST /knowledge/documents/upload`` 完全一致：
解析 -> 清洗 -> 元数据抽取 -> 条文切分 -> MySQL 落库 -> 向量化写入 Milvus。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from _common import (
    C,
    PROJECT_ROOT,
    banner,
    error,
    find_supported_files,
    info,
    kv,
    success,
    warn,
)

DEFAULT_CORPUS = PROJECT_ROOT / "data" / "corpus"


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="FinRAG 知识库批量入库工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--path", type=str, default=None, help="文件或目录路径")
    parser.add_argument("--source-url", type=str, default="", help="公开来源链接")
    parser.add_argument("--no-replace", action="store_true", help="已存在同哈希文档时跳过（默认覆盖）")
    parser.add_argument("--force", action="store_true", help="内容未变也强制重新入库")
    parser.add_argument("--rebuild-vectors", action="store_true", help="按 MySQL 条文重建全部向量")
    parser.add_argument("--delete-document", type=int, default=None, help="删除指定文档 ID")
    parser.add_argument("--stats", action="store_true", help="打印知识库统计")
    parser.add_argument("--limit", type=int, default=0, help="最多处理多少个文件（0=全部）")
    parser.add_argument("--dry-run", action="store_true", help="只列出将要入库的文件")
    return parser.parse_args()


def print_stats(db) -> None:
    """打印知识库统计。"""
    from app.db import repository
    from app.ingest.ocr import OcrEngine
    from app.core.config import get_settings

    stats = repository.knowledge_base_stats(db)
    banner("知识库统计")
    kv("制度文档数", stats["document_count"])
    kv("知识点（条文）数", stats["chunk_count"])
    kv("标签数", stats["tag_count"])
    kv("OCR 解析文档数", stats["ocr_document_count"])
    kv("最近更新时间", stats["latest_updated_at"] or "(无)")

    info("按状态分布：")
    for status, count in sorted(stats["status_counts"].items()):
        print(f"    - {status}: {count}")
    info("按业务条线分布：")
    for line, count in sorted(stats["business_line_counts"].items()):
        print(f"    - {line or '(未标注)'}: {count}")

    target = 1000
    ok = stats["chunk_count"] >= target
    kv(f"达标情况（目标 {target} 条知识点）", f"{stats['chunk_count']} 条", ok)

    ocr = OcrEngine(get_settings()).status()
    kv("OCR 可用", ocr["available"], bool(ocr["available"]))
    if not ocr["available"]:
        print(f"      原因：{ocr['reason']}")


def main() -> int:
    """主流程。"""
    args = parse_args()

    try:
        from app.core.config import get_settings
        from app.db import repository
        from app.db.session import check_connection, init_db, session_scope
        from app.services.container import get_container, reset_container
    except Exception as exc:
        error(f"导入后端模块失败：{exc}")
        return 1

    settings = get_settings()
    database = check_connection()
    if not database.get("connected"):
        error(f"MySQL 连接失败：{database.get('error')}")
        info("请先执行 python scripts/init_mysql.py")
        return 1

    # 容错：表不存在时自动创建（等价于执行 schema.sql 的简化版）
    try:
        from sqlalchemy import inspect

        from app.db.session import get_engine

        if not inspect(get_engine()).get_table_names():
            warn("未检测到数据表，正在自动创建 ...")
            init_db()
    except Exception as exc:  # pragma: no cover
        warn(f"数据表检查跳过：{exc}")

    # ------------------------------------------------------------ 统计
    if args.stats:
        with session_scope() as db:
            print_stats(db)
        return 0

    container = get_container()
    pipeline = container.ingest_pipeline

    # ------------------------------------------------------ 删除文档
    if args.delete_document is not None:
        banner(f"删除文档 {args.delete_document}")
        with session_scope() as db:
            result = pipeline.remove_document(db, args.delete_document)
        if not result.get("deleted"):
            error(f"文档 {args.delete_document} 不存在")
            reset_container()
            return 1
        success(
            f"已删除《{result.get('title')}》及其 {result.get('chunk_count')} 条条文"
        )
        if result.get("vector_error"):
            warn(f"向量删除异常：{result['vector_error']}")
        reset_container()
        return 0

    # ------------------------------------------------------ 重建向量
    if args.rebuild_vectors:
        banner("重建向量索引")
        started = time.perf_counter()
        with session_scope() as db:
            result = pipeline.rebuild_vectors(db)
        cost = time.perf_counter() - started
        kv("条文总数", result["total"])
        kv("写入向量数", result["written"], result["written"] > 0)
        kv("失败数", result["failed"], result["failed"] == 0)
        kv("耗时", f"{cost:.1f} 秒")
        reset_container()
        if result["failed"]:
            error("部分向量重建失败，请检查 Milvus 与 API Key 配置。")
            return 1
        success("向量索引重建完成。")
        return 0

    # ---------------------------------------------------------- 入库
    path_arg = args.path or str(DEFAULT_CORPUS)
    target = Path(path_arg)
    if not target.is_absolute():
        target = (PROJECT_ROOT / target).resolve()

    if target.is_file():
        files = [target]
    elif target.is_dir():
        from app.ingest.loaders import SUPPORTED_EXTENSIONS

        files = find_supported_files(target, SUPPORTED_EXTENSIONS)
    else:
        error(f"路径不存在：{target}")
        info("提示：可先从监管官网下载公开制度文件放入 data/corpus/")
        return 1

    if args.limit > 0:
        files = files[: args.limit]

    banner("待入库文件")
    kv("路径", target)
    kv("文件数量", len(files))
    for item in files[:20]:
        size_kb = item.stat().st_size / 1024
        print(f"    - {item.name} ({size_kb:.1f} KB)")
    if len(files) > 20:
        print(f"    ... 其余 {len(files) - 20} 个文件省略")

    if not files:
        warn("没有找到可入库的文件（支持 txt/md/pdf/docx/png/jpg 等）。")
        info("请将公开制度文件放入 data/corpus/ 后重试。")
        reset_container()
        return 0

    if args.dry_run:
        success("Dry-run 结束，未做任何修改。")
        reset_container()
        return 0

    # ------------------------------------------------------- 逐文件处理
    banner("开始入库")
    summary = {"success": 0, "partial": 0, "skipped": 0, "failed": 0}
    started = time.perf_counter()
    total_chunks = 0
    total_vectors = 0

    for index, file_path in enumerate(files, start=1):
        print(
            f"\n{C.BOLD}[{index}/{len(files)}] {file_path.name}{C.RESET}"
        )
        try:
            with session_scope() as db:
                result = pipeline.ingest_file(
                    db,
                    file_path,
                    source_url=args.source_url,
                    replace=not args.no_replace,
                    force=args.force,
                )
        except Exception as exc:
            summary["failed"] += 1
            error(f"入库异常：{exc}")
            continue

        summary[result.status] = summary.get(result.status, 0) + 1
        total_chunks += result.chunk_count
        total_vectors += result.vector_count

        kv("状态", result.status)
        kv("标题", result.title or "(未识别)")
        kv("文号", result.doc_no or "(未识别)")
        kv("业务条线", result.business_line)
        kv("时效状态", result.doc_status)
        kv("条文数", result.chunk_count)
        kv("向量数", result.vector_count)
        kv("耗时", f"{result.cost_ms} ms")
        if result.ocr_used:
            info("该文件使用了 OCR 解析")
        if result.message:
            print(f"      说明：{result.message}")
        for warning in result.warnings:
            warn(warning)

        if result.status in ("success", "partial"):
            print(f"      {C.GREEN}✓{C.RESET} 已入库")
        elif result.status == "skipped":
            print(f"      {C.YELLOW}—{C.RESET} 已跳过")
        else:
            print(f"      {C.RED}✗{C.RESET} 失败")

    cost = time.perf_counter() - started
    banner("入库总结")
    kv("处理文件数", len(files))
    kv("成功", summary.get("success", 0))
    kv("部分成功（条文已入库，向量失败）", summary.get("partial", 0))
    kv("跳过（内容未变）", summary.get("skipped", 0))
    kv("失败", summary.get("failed", 0))
    kv("新增条文总数", total_chunks)
    kv("新增向量总数", total_vectors)
    kv("总耗时", f"{cost:.1f} 秒")

    with session_scope() as db:
        print_stats(db)

    reset_container()

    if summary.get("failed", 0) > 0:
        error("存在失败文件，请查看上方日志。")
        return 1
    if summary.get("partial", 0) > 0:
        warn("存在向量写入失败，请执行：python scripts/ingest.py --rebuild-vectors")
        return 2
    success("全部入库完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
