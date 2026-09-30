"""Milvus 封装单元测试（不依赖真实 Milvus 服务）。

覆盖：过滤表达式构造、时间戳归一化、写入行构造、结果解析。
"""

from __future__ import annotations

import time
import unittest
from datetime import date, datetime

from app.core.constants import BusinessLine, DocStatus, DocType
from app.core.errors import VectorStoreError
from app.db.milvus_store import (
    ClauseVector,
    MilvusVectorStore,
    VectorHit,
    build_filter_expr,
    to_timestamp,
)
from tests.support import make_settings


class TestToTimestamp(unittest.TestCase):
    """时间戳归一化。"""

    def test_none_and_bool(self):
        """None 与布尔值都视为「不限」。"""
        self.assertEqual(to_timestamp(None), 0)
        self.assertEqual(to_timestamp(True), 0)
        self.assertEqual(to_timestamp(False), 0)

    def test_numeric(self):
        """数字直接取整。"""
        self.assertEqual(to_timestamp(1700000000), 1700000000)
        self.assertEqual(to_timestamp(1700000000.9), 1700000000)

    def test_date_and_datetime(self):
        """日期对象转为秒级时间戳。"""
        self.assertGreater(to_timestamp(date(2020, 6, 1)), 0)
        self.assertGreater(to_timestamp(datetime(2020, 6, 1, 12, 0, 0)), 0)

    def test_string_formats(self):
        """支持的字符串日期格式。"""
        self.assertGreater(to_timestamp("2020-06-01"), 0)
        self.assertGreater(to_timestamp("2020/06/01"), 0)
        self.assertGreater(to_timestamp("20200601"), 0)

    def test_invalid_strings(self):
        """非法字符串返回 0。"""
        self.assertEqual(to_timestamp(""), 0)
        self.assertEqual(to_timestamp("不是日期"), 0)


class TestClauseVector(unittest.TestCase):
    """写入行构造。"""

    def test_to_row_fields(self):
        """包含全部 Milvus 字段。"""
        row = ClauseVector(
            pk=7,
            document_id=3,
            embedding=[0.1, 0.2],
            chunk_index=2,
            status=DocStatus.EFFECTIVE.value,
            business_line=BusinessLine.BANKING.value,
            doc_type=DocType.NORMATIVE.value,
            effective_ts=123,
            expiry_ts=456,
            clause_no="第十二条",
        ).to_row()
        self.assertEqual(row["pk"], 7)
        self.assertEqual(row["document_id"], 3)
        self.assertEqual(row["embedding"], [0.1, 0.2])
        self.assertEqual(row["clause_no"], "第十二条")
        self.assertEqual(row["effective_ts"], 123)

    def test_to_row_truncates_long_strings(self):
        """超长标量字段截断，避免 Milvus 写入报错。"""
        row = ClauseVector(
            pk=1,
            document_id=1,
            embedding=[0.0],
            clause_no="第" * 200,
        ).to_row()
        self.assertLessEqual(len(row["clause_no"]), 64)

    def test_to_row_defaults(self):
        """未提供的标量使用默认值。"""
        row = ClauseVector(pk=1, document_id=1, embedding=[0.0]).to_row()
        self.assertEqual(row["status"], DocStatus.UNKNOWN.value)
        self.assertEqual(row["business_line"], BusinessLine.GENERAL.value)
        self.assertEqual(row["effective_ts"], 0)


class TestVectorHit(unittest.TestCase):
    """命中结果。"""

    def test_chunk_id_alias(self):
        """向量主键即 MySQL 条文 id。"""
        hit = VectorHit(pk=11, document_id=2, score=0.87)
        self.assertEqual(hit.chunk_id, 11)

    def test_to_dict(self):
        """序列化字段完整。"""
        hit = VectorHit(pk=11, document_id=2, score=0.876543, fields={"clause_no": "第五条"})
        data = hit.to_dict()
        self.assertEqual(data["chunk_id"], 11)
        self.assertEqual(data["clause_no"], "第五条")
        self.assertEqual(data["score"], 0.876543)


class TestBuildFilterExpr(unittest.TestCase):
    """Milvus 过滤表达式构造（时效性管理的核心）。"""

    def test_empty_returns_expiry_guard(self):
        """默认应带上「未过期」保护。"""
        expr = build_filter_expr()
        self.assertIn("expiry_ts", expr)
        self.assertNotIn("status in", expr)

    def test_only_valid(self):
        """只保留现行有效/尚未生效。"""
        expr = build_filter_expr(only_valid=True)
        for status in DocStatus.valid_values():
            self.assertIn(f'"{status}"', expr)
        self.assertIn("effective_ts", expr)

    def test_effective_only(self):
        """仅现行有效。"""
        expr = build_filter_expr(effective_only=True)
        self.assertIn(f'"{DocStatus.EFFECTIVE.value}"', expr)
        self.assertNotIn(f'"{DocStatus.PENDING.value}"', expr)

    def test_explicit_statuses(self):
        """显式状态过滤。"""
        expr = build_filter_expr(statuses=[DocStatus.REPEALED.value], only_valid=False)
        self.assertIn(f'"{DocStatus.REPEALED.value}"', expr)

    def test_business_line_and_doc_type(self):
        """条线与类型过滤。"""
        expr = build_filter_expr(
            business_lines=[BusinessLine.SECURITIES.value],
            doc_types=[DocType.NORMATIVE.value],
            only_valid=False,
        )
        self.assertIn("business_line in", expr)
        self.assertIn("doc_type in", expr)

    def test_document_ids(self):
        """按文档范围过滤。"""
        expr = build_filter_expr(document_ids=[3, 5], only_valid=False)
        self.assertIn("document_id in [3, 5]", expr)

    def test_as_of_fixed_moment(self):
        """as_of 使时效判断锚定到指定时刻（可复现）。"""
        expr = build_filter_expr(as_of=1700000000)
        self.assertIn("1700000000", expr)

    def test_exclude_expired_disabled(self):
        """关闭过期过滤时不应出现 expiry 条件。"""
        expr = build_filter_expr(only_valid=False, exclude_expired=False)
        self.assertEqual(expr, "")

    def test_escaping(self):
        """字符串字面量转义，避免表达式注入。"""
        expr = build_filter_expr(statuses=['现"行'], only_valid=False)
        self.assertIn('\\"', expr)

    def test_conditions_joined_by_and(self):
        """多条件用 and 连接。"""
        expr = build_filter_expr(
            statuses=[DocStatus.EFFECTIVE.value],
            business_lines=[BusinessLine.BANKING.value],
        )
        self.assertEqual(expr.count(" and "), 2)


class TestMilvusStoreWithoutServer(unittest.TestCase):
    """无 Milvus 服务时的构造与参数校验（惰性连接）。"""

    def setUp(self):
        self.settings = make_settings(
            milvus_host="127.0.0.1", milvus_port=19530,
            qwen_embedding_dim=8, milvus_collection="unit_test_collection",
        )
        self.store = MilvusVectorStore(self.settings)

    def tearDown(self):
        self.store.close()

    def test_uri_and_token(self):
        """连接串构造。"""
        self.assertEqual(self.store.uri, "http://127.0.0.1:19530")
        self.assertEqual(self.store.token, "")

    def test_token_with_credentials(self):
        """配置了用户时生成 token。"""
        store = MilvusVectorStore(
            make_settings(milvus_user="root", milvus_password="secret")
        )
        self.assertEqual(store.token, "root:secret")

    def test_search_empty_vector(self):
        """空向量直接返回空结果，不触发连接。"""
        self.assertEqual(self.store.search([]), [])

    def test_search_dimension_mismatch(self):
        """维度不匹配时明确报错（而不是静默错误）。"""
        self.store.dim = 8
        with self.assertRaises(VectorStoreError) as ctx:
            self.store.search([0.1, 0.2, 0.3])
        self.assertIn("维度", str(ctx.exception))

    def test_upsert_empty(self):
        """空列表不写入。"""
        self.assertEqual(self.store.upsert_clauses([]), 0)

    def test_delete_empty_pks(self):
        """空主键列表不删除。"""
        self.assertEqual(self.store.delete_by_pks([]), 0)

    def test_health_reports_error_without_server(self):
        """服务不可达时健康检查返回 connected=False 且带错误信息。"""
        store = MilvusVectorStore(
            make_settings(milvus_port=1, milvus_host="127.0.0.1")
        )
        health = store.health()
        self.assertFalse(health["connected"])
        self.assertIn("error", health)
        self.assertEqual(health["collection"], store.collection)


class TestParseHits(unittest.TestCase):
    """pymilvus 原始返回解析。"""

    def test_parse_dict_shape(self):
        """pymilvus 2.4 的 dict 形态。"""
        raw = [
            [
                {"id": 5, "distance": 0.91, "entity": {"document_id": 2, "clause_no": "第五条"}},
                {"id": 6, "distance": 0.80, "entity": {"document_id": 2}},
            ]
        ]
        hits = MilvusVectorStore._parse_hits(raw, "COSINE")
        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[0].chunk_id, 5)
        self.assertAlmostEqual(hits[0].score, 0.91)
        self.assertEqual(hits[0].document_id, 2)

    def test_parse_empty(self):
        """空返回与空列表安全。"""
        self.assertEqual(MilvusVectorStore._parse_hits([]), [])
        self.assertEqual(MilvusVectorStore._parse_hits([[]]), [])
        self.assertEqual(MilvusVectorStore._parse_hits(None), [])

    def test_parse_l2_normalized(self):
        """L2 距离归一化为「越大越相似」。"""
        raw = [[{"id": 1, "distance": 1.0, "entity": {}}]]
        hits = MilvusVectorStore._parse_hits(raw, "L2")
        self.assertAlmostEqual(hits[0].score, 0.5)

    def test_parse_skips_missing_id(self):
        """缺少主键的条目被跳过。"""
        raw = [[{"distance": 0.5, "entity": {}}, {"id": 9, "distance": 0.4, "entity": {}}]]
        hits = MilvusVectorStore._parse_hits(raw, "COSINE")
        self.assertEqual([hit.chunk_id for hit in hits], [9])


class TestFilterExprTimeSemantics(unittest.TestCase):
    """时效性语义验证：过期条文必须被表达式排除。"""

    def test_expired_document_filtered_out(self):
        """已过期（expiry_ts 早于当下）应被排除。"""
        from tests.fakes import FakeVectorStore

        store = FakeVectorStore(dim=4)
        now = int(time.time())
        store.upsert_clauses(
            [
                ClauseVector(pk=1, document_id=1, embedding=[1.0, 0, 0, 0],
                             status=DocStatus.EFFECTIVE.value, expiry_ts=now - 86400),
                ClauseVector(pk=2, document_id=1, embedding=[1.0, 0, 0, 0],
                             status=DocStatus.EFFECTIVE.value, expiry_ts=0),
            ]
        )
        expr = build_filter_expr(only_valid=True)
        hits = store.search([1.0, 0, 0, 0], top_k=5, expr=expr)
        self.assertEqual([hit.chunk_id for hit in hits], [2])

    def test_repealed_status_filtered_out(self):
        """已废止状态应被 only_valid 排除。"""
        from tests.fakes import FakeVectorStore

        store = FakeVectorStore(dim=4)
        store.upsert_clauses(
            [
                ClauseVector(pk=1, document_id=1, embedding=[1.0, 0, 0, 0],
                             status=DocStatus.REPEALED.value),
                ClauseVector(pk=2, document_id=1, embedding=[1.0, 0, 0, 0],
                             status=DocStatus.EFFECTIVE.value),
            ]
        )
        hits = store.search([1.0, 0, 0, 0], top_k=5, expr=build_filter_expr(only_valid=True))
        self.assertEqual([hit.chunk_id for hit in hits], [2])

    def test_business_line_filter(self):
        """业务条线过滤有效。"""
        from tests.fakes import FakeVectorStore

        store = FakeVectorStore(dim=4)
        store.upsert_clauses(
            [
                ClauseVector(pk=1, document_id=1, embedding=[1.0, 0, 0, 0],
                             business_line=BusinessLine.BANKING.value),
                ClauseVector(pk=2, document_id=1, embedding=[1.0, 0, 0, 0],
                             business_line=BusinessLine.INSURANCE.value),
            ]
        )
        hits = store.search(
            [1.0, 0, 0, 0], top_k=5,
            expr=build_filter_expr(business_lines=[BusinessLine.INSURANCE.value],
                                   only_valid=False),
        )
        self.assertEqual([hit.chunk_id for hit in hits], [2])


if __name__ == "__main__":
    unittest.main()
