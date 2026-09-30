"""MySQL 数据访问层（Repository）单元测试。"""

from __future__ import annotations

import unittest
from datetime import date

from app.core.constants import BusinessLine, DocStatus, DocType
from app.db import repository
from app.db.models import QaFavorite, QaHistory
from app.db.session import get_session_factory, init_db, reset_engine
from app.retrieval.types import RetrievalFilters
from tests.support import reset_app_state, sample_knowledge_base, seed_document


class RepositoryTestCase(unittest.TestCase):
    """公共夹具：干净的 SQLite 库 + 样例知识库。"""

    def setUp(self):
        reset_app_state()
        init_db(drop=True)
        self.db = get_session_factory()()
        self.data = sample_knowledge_base(self.db)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        reset_engine()


class TestDocumentRepository(RepositoryTestCase):
    """文档 CRUD 与查询。"""

    def test_get_document(self):
        """按主键取文档。"""
        document = repository.get_document(self.db, self.data["document"].id)
        self.assertIsNotNone(document)
        self.assertEqual(document.title, self.data["document"].title)

    def test_get_missing_document(self):
        """不存在的文档返回 None。"""
        self.assertIsNone(repository.get_document(self.db, 999999))

    def test_get_document_by_hash(self):
        """按文件哈希判重（增量入库依据）。"""
        document = self.data["document"]
        found = repository.get_document_by_hash(self.db, document.file_hash)
        self.assertEqual(found.id, document.id)
        self.assertIsNone(repository.get_document_by_hash(self.db, ""))

    def test_list_documents_pagination(self):
        """分页返回列表与总数。"""
        items, total = repository.list_documents(self.db, limit=2, offset=0)
        self.assertEqual(len(items), 2)
        self.assertEqual(total, 3)
        items2, _ = repository.list_documents(self.db, limit=2, offset=2)
        self.assertEqual(len(items2), 1)

    def test_list_documents_keyword(self):
        """关键词命中标题/文号/机关。"""
        items, total = repository.list_documents(self.db, keyword="流动性")
        self.assertEqual(total, 1)
        self.assertIn("流动性", items[0].title)

    def test_list_documents_filters(self):
        """状态/条线/类型过滤。"""
        items, total = repository.list_documents(
            self.db, status=DocStatus.REPEALED.value
        )
        self.assertEqual(total, 1)
        self.assertEqual(items[0].status, DocStatus.REPEALED.value)

        items, total = repository.list_documents(
            self.db, business_line=BusinessLine.INSURANCE.value
        )
        self.assertEqual(total, 1)

        items, total = repository.list_documents(self.db, doc_type=DocType.NORMATIVE.value)
        self.assertGreaterEqual(total, 1)

    def test_list_documents_ordered_by_id_desc(self):
        """默认按 id 倒序（最新在前）。"""
        items, _ = repository.list_documents(self.db, limit=10)
        ids = [item.id for item in items]
        self.assertEqual(ids, sorted(ids, reverse=True))

    def test_update_document(self):
        """更新字段。"""
        document = self.data["document"]
        repository.update_document(self.db, document, title="新标题", remark="人工校核")
        self.db.commit()
        self.assertEqual(repository.get_document(self.db, document.id).title, "新标题")

    def test_update_document_ignores_unknown_field(self):
        """未知字段被忽略而不是报错。"""
        document = self.data["document"]
        repository.update_document(self.db, document, not_a_field=1)
        self.assertFalse(hasattr(document, "not_a_field"))

    def test_delete_document_cascades(self):
        """删除文档应级联删除条文。"""
        document = self.data["document"]
        document_id = document.id
        chunk_ids = [chunk.id for chunk in self.data["chunks"]]

        self.assertTrue(repository.delete_document(self.db, document_id))
        self.db.commit()

        self.assertIsNone(repository.get_document(self.db, document_id))
        remaining = repository.get_chunks_by_ids(self.db, chunk_ids)
        self.assertEqual(remaining, {})

    def test_delete_missing_document(self):
        """删除不存在的文档返回 False。"""
        self.assertFalse(repository.delete_document(self.db, 999999))

    def test_document_status_counts(self):
        """状态分布统计。"""
        counts = repository.document_status_counts(self.db)
        self.assertEqual(counts.get(DocStatus.EFFECTIVE.value), 2)
        self.assertEqual(counts.get(DocStatus.REPEALED.value), 1)

    def test_document_business_counts(self):
        """条线分布统计。"""
        counts = repository.document_business_counts(self.db)
        self.assertEqual(counts.get(BusinessLine.BANKING.value), 2)


class TestChunkRepository(RepositoryTestCase):
    """条文查询。"""

    def test_list_chunks_by_document(self):
        """按文档分页列出条文。"""
        items, total = repository.list_chunks(
            self.db, document_id=self.data["document"].id, limit=5, offset=0
        )
        self.assertEqual(total, len(self.data["chunks"]))
        self.assertEqual(len(items), 5)

    def test_list_chunks_keyword(self):
        """关键词命中正文或条文号。"""
        items, total = repository.list_chunks(self.db, keyword="压力测试")
        self.assertGreaterEqual(total, 1)
        self.assertTrue(all("压力测试" in item.content for item in items))

    def test_list_chunks_with_validity_filter(self):
        """有效性过滤排除已废止条文。"""
        items, total = repository.list_chunks(self.db, filters=RetrievalFilters())
        stale_ids = {chunk.id for chunk in self.data["stale_chunks"]}
        self.assertFalse({item.id for item in items} & stale_ids)

    def test_list_chunks_without_filter_includes_all(self):
        """不过滤时包含全部条文。"""
        _, total = repository.list_chunks(self.db, filters=None, limit=100)
        self.assertEqual(total, len(self.data["all_chunks"]))

    def test_count_chunks(self):
        """计数。"""
        self.assertEqual(
            repository.count_chunks(self.db, None), len(self.data["all_chunks"])
        )

    def test_get_chunk_with_relations(self):
        """单条查询应带出标签与文档。"""
        chunk = self.data["chunks"][0]
        loaded = repository.get_chunk(self.db, chunk.id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.document.id, chunk.document_id)
        self.assertIsInstance(loaded.tag_names, list)

    def test_get_chunk_missing(self):
        """不存在的条文返回 None。"""
        self.assertIsNone(repository.get_chunk(self.db, 999999))

    def test_delete_chunks_by_document(self):
        """按文档删除条文并返回条数。"""
        document_id = self.data["document"].id
        removed = repository.delete_chunks_by_document(self.db, document_id)
        self.db.commit()
        self.assertEqual(removed, len(self.data["chunks"]))
        self.assertEqual(repository.count_chunks(self.db, RetrievalFilters(document_ids=[document_id])), 0)

    def test_delete_chunks_by_missing_document(self):
        """不存在的文档返回 0。"""
        self.assertEqual(repository.delete_chunks_by_document(self.db, 999999), 0)

    def test_iter_chunk_texts_limit(self):
        """可限制语料条数（用于构建 BM25 索引）。"""
        corpus = repository.iter_chunk_texts(self.db, None, limit=3)
        self.assertEqual(len(corpus), 3)

    def test_chunk_tag_names(self):
        """条文标签查询。"""
        self.assertIsInstance(repository.chunk_tag_names(self.db, self.data["chunks"][0].id), list)


class TestTagRepository(RepositoryTestCase):
    """标签管理。"""

    def test_get_or_create_tag(self):
        """不存在则创建，存在则复用。"""
        tag = repository.get_or_create_tag(self.db, "流动性风险")
        self.assertIsNotNone(tag)
        again = repository.get_or_create_tag(self.db, "流动性风险")
        self.assertEqual(tag.id, again.id)

    def test_get_or_create_tag_empty(self):
        """空名称返回 None。"""
        self.assertIsNone(repository.get_or_create_tag(self.db, ""))
        self.assertIsNone(repository.get_or_create_tag(self.db, "   "))

    def test_get_or_create_tag_truncates(self):
        """超长标签名被截断。"""
        tag = repository.get_or_create_tag(self.db, "标" * 200)
        self.assertLessEqual(len(tag.name), 64)

    def test_attach_tags_to_chunk(self):
        """条文打标签且幂等。"""
        chunk = self.data["chunks"][0]
        repository.attach_tags_to_chunk(self.db, chunk, ["流动性", "压力测试"])
        self.db.commit()
        names = repository.chunk_tag_names(self.db, chunk.id)
        self.assertIn("流动性", names)

        repository.attach_tags_to_chunk(self.db, chunk, ["流动性", "压力测试"])
        self.db.commit()
        self.assertEqual(len(repository.chunk_tag_names(self.db, chunk.id)), len(names))

    def test_attach_tags_to_document(self):
        """文档打标签。"""
        document = self.data["document"]
        repository.attach_tags_to_document(self.db, document, ["银行", "监管"])
        self.db.commit()
        names = repository.tag_names_of_document(self.db, document.id)
        self.assertIn("银行", names)

    def test_list_tags(self):
        """列出标签并按引用数排序。"""
        repository.attach_tags_to_document(self.db, self.data["document"], ["甲", "乙"])
        self.db.commit()
        tags = repository.list_tags(self.db)
        self.assertGreaterEqual(len(tags), 2)

    def test_list_tags_by_category(self):
        """按分类过滤。"""
        repository.get_or_create_tag(self.db, "某标签", category="风险类型")
        self.db.commit()
        tags = repository.list_tags(self.db, category="风险类型")
        self.assertTrue(all(tag.category == "风险类型" for tag in tags))


class TestIngestTaskRepository(RepositoryTestCase):
    """入库任务审计。"""

    def test_create_and_update_task(self):
        """创建与更新任务。"""
        task = repository.create_ingest_task(
            self.db, task_no="T1", file_name="a.txt", status="running", stage="parsing"
        )
        self.db.commit()
        repository.update_ingest_task(
            self.db, task, status="success", stage="done", processed_chunks=5
        )
        self.db.commit()
        tasks = repository.list_ingest_tasks(self.db)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].status, "success")
        self.assertEqual(tasks[0].processed_chunks, 5)

    def test_update_task_ignores_unknown(self):
        """未知字段被忽略。"""
        task = repository.create_ingest_task(self.db, task_no="T2")
        repository.update_ingest_task(self.db, task, unknown=1)
        self.assertFalse(hasattr(task, "unknown"))

    def test_list_tasks_ordered(self):
        """任务按 id 倒序。"""
        for index in range(3):
            repository.create_ingest_task(self.db, task_no=f"T{index}")
        self.db.commit()
        tasks = repository.list_ingest_tasks(self.db, limit=2)
        self.assertEqual(len(tasks), 2)
        self.assertGreater(tasks[0].id, tasks[1].id)


class TestHistoryRepository(RepositoryTestCase):
    """问答历史。"""

    def _create(self, user_id: str = "u1", question: str = "问题一") -> QaHistory:
        return repository.create_history(
            self.db,
            session_id="s1",
            user_id=user_id,
            question=question,
            answer="回答",
            retrieval_mode="hybrid",
            top_k=5,
            evidence_count=2,
            latency_ms=800,
            credibility_score=85.5,
            credibility_label="高",
            citations=[{"index": 1, "chunk_id": 1}],
            has_answer=True,
        )

    def test_create_and_get(self):
        """写入并按主键读取。"""
        record = self._create()
        self.db.commit()
        loaded = repository.get_history(self.db, record.id)
        self.assertEqual(loaded.question, "问题一")
        self.assertEqual(loaded.credibility_label, "高")
        self.assertEqual(loaded.citations, [{"index": 1, "chunk_id": 1}])

    def test_get_missing(self):
        """不存在返回 None。"""
        self.assertIsNone(repository.get_history(self.db, 999999))

    def test_list_history_pagination_and_isolation(self):
        """分页且按用户隔离。"""
        self._create(user_id="u1", question="A")
        self._create(user_id="u1", question="B")
        self._create(user_id="u2", question="C")
        self.db.commit()

        items, total = repository.list_history(self.db, "u1", limit=10, offset=0)
        self.assertEqual(total, 2)
        self.assertTrue(all(item.user_id == "u1" for item in items))

    def test_list_history_keyword(self):
        """关键词命中问题或回答。"""
        self._create(question="流动性风险")
        self._create(question="偿付能力")
        self.db.commit()
        items, total = repository.list_history(self.db, "u1", keyword="流动性")
        self.assertEqual(total, 1)

    def test_list_history_ordered_desc(self):
        """按时间倒序。"""
        self._create(question="第一")
        self._create(question="第二")
        self.db.commit()
        items, _ = repository.list_history(self.db, "u1", limit=10)
        self.assertEqual(items[0].question, "第二")

    def test_delete_history_with_ownership(self):
        """删除时校验归属，防止越权。"""
        record = self._create(user_id="u1")
        self.db.commit()
        self.assertFalse(repository.delete_history(self.db, record.id, "u2"))
        self.assertTrue(repository.delete_history(self.db, record.id, "u1"))

    def test_delete_missing_history(self):
        """删除不存在返回 False。"""
        self.assertFalse(repository.delete_history(self.db, 999999))

    def test_clear_history(self):
        """清空指定用户历史。"""
        self._create(user_id="u1", question="A")
        self._create(user_id="u1", question="B")
        self._create(user_id="u2", question="C")
        self.db.commit()
        deleted = repository.clear_history(self.db, "u1")
        self.db.commit()
        self.assertEqual(deleted, 2)
        _, total = repository.list_history(self.db, "u2", limit=10)
        self.assertEqual(total, 1)


class TestFavoriteRepository(RepositoryTestCase):
    """收藏。"""

    def _history(self, user_id: str = "u1") -> QaHistory:
        return repository.create_history(
            self.db, user_id=user_id, question="问题", answer="回答",
            citations=[], credibility_score=90.0,
        )

    def _favorite(self, history: QaHistory, user_id: str = "u1") -> QaFavorite:
        return repository.create_favorite(
            self.db,
            user_id=user_id,
            history_id=history.id,
            question=history.question,
            answer=history.answer,
            citations=[],
            credibility_score=90.0,
        )

    def test_create_and_get(self):
        """创建并按主键读取。"""
        history = self._history()
        self.db.commit()
        favorite = self._favorite(history)
        self.db.commit()
        loaded = repository.get_favorite(self.db, favorite.id)
        self.assertEqual(loaded.question, "问题")

    def test_find_by_history(self):
        """按 (用户, 历史) 查找用于幂等判断。"""
        history = self._history()
        self.db.commit()
        self._favorite(history)
        self.db.commit()
        found = repository.find_favorite_by_history(self.db, "u1", history.id)
        self.assertIsNotNone(found)
        self.assertIsNone(repository.find_favorite_by_history(self.db, "u2", history.id))

    def test_list_favorites(self):
        """分页且按用户隔离。"""
        history = self._history()
        self.db.commit()
        self._favorite(history, "u1")
        self.db.commit()
        items, total = repository.list_favorites(self.db, "u1", limit=10)
        self.assertEqual(total, 1)
        _, other_total = repository.list_favorites(self.db, "u2", limit=10)
        self.assertEqual(other_total, 0)

    def test_list_favorites_keyword(self):
        """关键词匹配问题或备注。"""
        history = self._history()
        self.db.commit()
        favorite = self._favorite(history)
        favorite.note = "重点条款"
        self.db.commit()
        _, total = repository.list_favorites(self.db, "u1", keyword="重点")
        self.assertEqual(total, 1)

    def test_delete_favorite_with_ownership(self):
        """删除校验归属。"""
        history = self._history()
        self.db.commit()
        favorite = self._favorite(history)
        self.db.commit()
        self.assertFalse(repository.delete_favorite(self.db, favorite.id, "u2"))
        self.assertTrue(repository.delete_favorite(self.db, favorite.id, "u1"))

    def test_delete_missing_favorite(self):
        """删除不存在返回 False。"""
        self.assertFalse(repository.delete_favorite(self.db, 999999))


class TestKnowledgeBaseStats(RepositoryTestCase):
    """知识库总览统计。"""

    def test_stats(self):
        """统计字段正确。"""
        stats = repository.knowledge_base_stats(self.db)
        self.assertEqual(stats["document_count"], 3)
        self.assertEqual(stats["chunk_count"], len(self.data["all_chunks"]))
        self.assertIn("status_counts", stats)
        self.assertIn("business_line_counts", stats)
        self.assertIsInstance(stats["tag_count"], int)

    def test_stats_empty_database(self):
        """空库统计不报错。"""
        init_db(drop=True)
        stats = repository.knowledge_base_stats(self.db)
        self.assertEqual(stats["document_count"], 0)
        self.assertEqual(stats["chunk_count"], 0)


class TestSeedHelpers(RepositoryTestCase):
    """测试夹具自身的正确性（避免「测试的测试」出错）。"""

    def test_seed_document_sets_validity_timestamps(self):
        """播种时应写入生效/失效时间戳，供 Milvus 区间过滤。"""
        document, chunks = seed_document(
            self.db,
            title="测试制度",
            effective_date=date(2021, 1, 1),
            expiry_date=date(2022, 1, 1),
            clauses=[("第一条", "标题", "内容")],
        )
        self.db.commit()
        chunk = chunks[0]
        self.assertGreater(chunk.effective_ts, 0)
        self.assertGreater(chunk.expiry_ts, 0)
        self.assertEqual(chunk.status, DocStatus.EFFECTIVE.value)
        self.assertEqual(document.chunk_count, 1)


if __name__ == "__main__":
    unittest.main()
