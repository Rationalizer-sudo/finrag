"""语料解析质量抽查：清洗 -> 元数据抽取 -> 条文切分 的效果统计。

用于在真正入库前，先离线确认解析链路对当前语料的效果，
避免把错误的元数据写进知识库（对应课程思政：严谨处理金融制度数据的准确性）。

用法::

    python scripts/analyze_corpus.py --path data/demo --samples 3
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import List

from _common import (
    PROJECT_ROOT,
    banner,
    find_supported_files,
    info,
    kv,
    success,
    warn,
)


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="语料解析质量抽查")
    parser.add_argument("--path", default="data/demo", help="语料目录")
    parser.add_argument("--samples", type=int, default=3, help="打印前 N 篇的详细解析结果")
    return parser.parse_args()


def main() -> int:
    """主流程。"""
    args = parse_args()

    from app.core.constants import DocStatus
    from app.ingest.clause_splitter import split_document
    from app.ingest.cleaner import clean_text, dedupe_paragraphs
    from app.ingest.loaders import SUPPORTED_EXTENSIONS, load_document
    from app.ingest.metadata import extract_metadata

    target = Path(args.path)
    if not target.is_absolute():
        target = PROJECT_ROOT / target
    if not target.exists():
        warn(f"路径不存在：{target}")
        return 1

    files = (
        [target] if target.is_file() else find_supported_files(target, SUPPORTED_EXTENSIONS)
    )
    banner("语料解析质量抽查")
    kv("目录", target)
    kv("文件数", len(files))
    if not files:
        warn("未找到可解析文件。")
        return 1

    stats = Counter()
    chunk_counts: List[int] = []
    detail_rows: List[str] = []

    for path in files:
        try:
            parsed = load_document(path)
            cleaned = dedupe_paragraphs(clean_text(parsed.text))
            meta = extract_metadata(cleaned, file_name=parsed.file_name)
            chunks = split_document(cleaned)
        except Exception as exc:
            stats["解析失败"] += 1
            warn(f"{path.name}: {exc}")
            continue

        stats["文档数"] += 1
        chunk_counts.append(len(chunks))
        stats["条文总数"] += len(chunks)
        stats[f"状态:{meta.status}"] += 1
        stats[f"条线:{meta.business_line}"] += 1
        stats[f"类型:{meta.doc_type}"] += 1
        if meta.doc_no:
            stats["识别出文号"] += 1
        if meta.issuer:
            stats["识别出机关"] += 1
        if meta.effective_date:
            stats["识别出施行日期"] += 1
        if any(c.is_clause_based for c in chunks):
            stats["条文切分"] += 1
        else:
            stats["递归切分（兜底）"] += 1

        if len(detail_rows) < args.samples:
            avg = sum(c.char_count for c in chunks) / max(len(chunks), 1)
            detail_rows.append(
                f"  文件：{path.name}\n"
                f"    标题      : {meta.title}\n"
                f"    文号      : {meta.doc_no or '(未识别)'}\n"
                f"    发文机关  : {meta.issuer or '(未识别)'}\n"
                f"    文件类型  : {meta.doc_type}    业务条线: {meta.business_line}\n"
                f"    发布日期  : {meta.publish_date}    施行日期: {meta.effective_date}\n"
                f"    时效状态  : {meta.status}\n"
                f"    标签      : {', '.join(meta.tags)}\n"
                f"    切分结果  : {len(chunks)} 条，平均 {avg:.0f} 字\n"
                f"    首条预览  : {chunks[0].display_clause_no} "
                f"{chunks[0].content[:60] if chunks else ''}..."
            )

    banner("解析结果统计")
    total_docs = stats.get("文档数", 0)
    kv("成功解析文档", total_docs, total_docs > 0)
    kv("解析失败文档", stats.get("解析失败", 0), stats.get("解析失败", 0) == 0)
    kv("条文（知识点）总数", stats.get("条文总数", 0))
    if chunk_counts:
        kv("单文档条文数", f"最少 {min(chunk_counts)} / 最多 {max(chunk_counts)}"
           f" / 平均 {sum(chunk_counts) / len(chunk_counts):.1f}")
    kv("识别出文号的文档", stats.get("识别出文号", 0), stats.get("识别出文号", 0) > 0)
    kv("识别出发文机关的文档", stats.get("识别出机关", 0), stats.get("识别出机关", 0) > 0)
    kv("识别出施行日期的文档", stats.get("识别出施行日期", 0))
    kv("条文切分成功", stats.get("条文切分", 0), stats.get("条文切分", 0) > 0)
    kv("回退递归切分", stats.get("递归切分（兜底）", 0))
    unknown = stats.get(f"状态:{DocStatus.UNKNOWN.value}", 0)
    kv("时效状态待核", unknown, unknown == 0)

    info("按业务条线分布：")
    for key in sorted(k for k in stats if k.startswith("条线:")):
        print(f"    - {key[3:]}: {stats[key]}")

    banner("抽样明细")
    for row in detail_rows:
        print(row)
        print()

    total = stats.get("条文总数", 0)
    if total >= 1000:
        success(f"知识点总量 {total} 条，已达到课题「1000+ 知识点」要求。")
    else:
        warn(
            f"知识点总量 {total} 条，未达 1000 条。"
            "可增加语料后重新入库（python scripts/ingest.py --path data/corpus）。"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
