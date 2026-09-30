"""初始化 Milvus 向量集合。

用法::

    python scripts/init_milvus.py              # 不存在则创建，存在则打印结构
    python scripts/init_milvus.py --recreate    # 删除并重建（会清空全部向量！）
    python scripts/init_milvus.py --check       # 只做连通性检查

集合结构（与 backend/app/db/milvus_store.py 保持一致）：

====================  =============  ==================================
字段                   类型            说明
====================  =============  ==================================
pk                    INT64          主键 = MySQL kb_chunk.id（协同查询）
document_id           INT64          所属文档，用于按文档删除
embedding             FLOAT_VECTOR   通义千问 text-embedding-v3 向量
chunk_index           INT64          文内顺序号
status                VARCHAR(16)    时效状态（现行有效/已废止…），过滤下推
business_line         VARCHAR(32)    业务条线，过滤下推
doc_type              VARCHAR(32)    文件类型，过滤下推
effective_ts          INT64          生效时间戳，区间过滤用
expiry_ts             INT64          失效时间戳，0=长期有效
clause_no             VARCHAR(64)    条文号，展示用
====================  =============  ==================================
"""

from __future__ import annotations

import argparse
import sys

from _common import banner, error, info, kv, success, warn


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="初始化 FinRAG 的 Milvus 集合")
    parser.add_argument("--recreate", action="store_true", help="删除并重建集合（清空向量）")
    parser.add_argument("--check", action="store_true", help="仅做连通性检查")
    return parser.parse_args()


def main() -> int:
    """主流程。"""
    args = parse_args()
    banner("FinRAG 向量库初始化")

    try:
        from app.core.config import get_settings
        from app.db.milvus_store import MilvusVectorStore
    except Exception as exc:
        error(f"导入向量库模块失败：{exc}")
        return 1

    settings = get_settings()
    store = MilvusVectorStore(settings)

    kv("Milvus 地址", store.uri)
    kv("集合名称", store.collection)
    kv("向量维度", store.dim)
    kv("索引类型", settings.milvus_index_type)
    kv("距离度量", settings.milvus_metric_type)
    kv("一致性级别", settings.milvus_consistency_level)

    try:
        health = store.health()
    except Exception as exc:
        error(f"连接 Milvus 失败：{exc}")
        info("请确认 Milvus 已启动：docker compose up -d etcd minio milvus")
        return 1

    if not health.get("connected"):
        error(f"Milvus 不可用：{health.get('error')}")
        info("排障建议：")
        info("  1. docker compose ps               查看容器状态")
        info("  2. docker compose logs milvus      查看 Milvus 日志")
        info("  3. 确认 .env 中 MILVUS_HOST / MILVUS_PORT 正确")
        return 1
    success("Milvus 连接正常")

    if args.check:
        kv("集合是否存在", health.get("collection_exists"))
        kv("向量数量", health.get("vector_count"))
        store.close()
        return 0

    try:
        description = store.ensure_collection(drop=args.recreate)
    except Exception as exc:
        error(f"创建集合失败：{exc}")
        store.close()
        return 1

    banner("集合信息")
    kv("集合名称", description.get("collection"))
    kv("是否存在", description.get("exists"), bool(description.get("exists")))
    kv("向量维度", description.get("dim"))
    kv("索引类型", description.get("index_type"))
    kv("距离度量", description.get("metric_type"))
    fields = description.get("fields") or []
    info(f"字段数量：{len(fields)}")
    for field in fields:
        params = field.get("params") or {}
        extra = f" dim={params.get('dim')}" if "dim" in params else ""
        max_len = f" max_length={params.get('max_length')}" if "max_length" in params else ""
        print(f"    - {field.get('name'):<16} {field.get('type')}{extra}{max_len}")

    if args.recreate:
        warn("集合已重建，历史向量全部清空，请重新入库或执行 --rebuild-vectors")

    store.close()
    success("Milvus 初始化完成。下一步：python scripts/ingest.py --path data/corpus")
    return 0


if __name__ == "__main__":
    sys.exit(main())
