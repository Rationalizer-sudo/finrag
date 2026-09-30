"""服务层单元测试：KnowledgeBaseService 与 QaService。"""

from __future__ import annotations

import unittest
from datetime import date

from app.core.constants import BusinessLine, DocStatus, DocType
from app.core.errors import BadRequestError, NotFoundError
from app.db import repository
from app.db.session import get_session_factory, init_db, reset_engine
from app.schemas.knowledge import DocumentUpdateRequest, IngestTextRequest
from app.schemas.qa import AskRequest, FavoriteCreateRequest
from app.services.container import set_container
from app.services.kb_service import KnowledgeBaseService, parse_date
from app.services.qa_service import QaService, filters_from_request
from tests.fakes import FakeChatClient, FakeEmbedder, FakeVectorStore
from tests.support import (
    DEFAULT_ANSWER,
    make_settings,
    make_test_container,
    reset_app_state,
    sample_knowledge_base,
    seed_vectors,
)

DOCUMENT_TEXT = """中国银行保险监督管理委员会关于规范商业银行流动性风险管理的通知
金规〔2022〕3号
国家金融监督管理总局
发布日期：2022年1月10日

第一条 为加强商业银行流动性风险管理，制定本通知。
第二条 【适用范围】本通知适用于在中华人民共和国境内依法设立的商业银行。
第三条 【压力测试】商业银行应当至少每季度开展一次流动性风险压力测试。
第四条 【报告义务】商业银行应当至少每季度向监管机构报送流动性风险指标。
第五条 本通知自2022年4月1日起施行。
"""


class ServiceTestCase(unittest.TestCase):
    """公共夹具：SQLite + 假向量库 + 注入了替身的容器。"""

    answer = DEFAULT_ANSWER

    def setUp(self):
        reset_app_state()
        init_db(drop=True)
        self.settings = make_settings(
            qwen_embedding_dim=48, retrieve_candidate_k=20, min_vector_score=0.0
        )
        self.db = get_session_factory()()
        self.container, self.store, self.embedder, self.chat = make_test_container(
            self.settings, answer=self.answer, dim=48
        )
        set_container(self.container)
        self.kb = KnowledgeBaseService(self.container, self.settings)
        self.qa = QaService(self.container, self.settings)

    def tearDown(self):
        self.db.close()
        reset_engine()

    def seed(self):
        """播种样例知识库并同步向量。"""
        data = sample_knowledge_base(self.db)
        self.db.commit()
        seed_vectors(self.store, self.embedder, data["all_chunks"])
        return data


class TestKnowledgeBaseService(ServiceTestCase):
    """知识库服务。"""

    def test_stats_on_empty_base(self):
        """空知识库统计与达标情况。"""
        stats = self.kb.stats(self.db)
        self.assertEqual(stats["document_count"], 0)
        self.assertEqual(stats["chunk_count"], 0)
        self.assertFalse(stats["meets_target"]["knowledge_points_ok"])
        self.assertGreater(stats["meets_target"]["knowledge_points_target"], 0)
        self.assertIn("ocr_status", stats)
        self.assertIn("llm_status", stats)

    def test_stats_after_ingest(self):
        """入库后统计更新并反映向量库状态。"""
        self.kb.ingest_text(
            self.db, IngestTextRequest(text=DOCUMENT_TEXT, file_name="a.txt")
        )
        self.db.commit()
        stats = self.kb.stats(self.db)
        self.assertEqual(stats["document_count"], 1)
        self.assertGreater(stats["chunk_count"], 0)
        self.assertTrue(stats["vector_ready"])
        self.assertEqual(stats["vector_count"], stats["chunk_count"])

    def test_ingest_text(self):
        """文本入库返回可序列化结果。"""
        result = self.kb.ingest_text(
            self.db, IngestTextRequest(text=DOCUMENT_TEXT, file_name="a.txt")
        )
        self.db.commit()
        self.assertTrue(result.ok)
        self.assertGreater(result.chunk_count, 0)

    def test_list_and_get_documents(self):
        """文档列表与详情。"""
        result = self.kb.ingest_text(
            self.db, IngestTextRequest(text=DOCUMENT_TEXT, file_name="a.txt")
        )
        self.db.commit()

        items, total = self.kb.list_documents(self.db, limit=10)
        self.assertEqual(total, 1)
        self.assertTrue(items[0].tags)

        detail = self.kb.get_document(self.db, result.document_id)
        self.assertEqual(detail.id, result.document_id)

    def test_get_missing_document_raises(self):
        """不存在的文档抛 404。"""
        with self.assertRaises(NotFoundError):
            self.kb.get_document(self.db, 999999)

    def test_list_documents_filters(self):
        """关键词与状态过滤。"""
        self.kb.ingest_text(self.db, IngestTextRequest(text=DOCUMENT_TEXT, file_name="a.txt"))
        self.db.commit()
        items, total = self.kb.list_documents(self.db, keyword="流动性")
        self.assertEqual(total, 1)
        _, none_total = self.kb.list_documents(self.db, keyword="不存在的关键词")
        self.assertEqual(none_total, 0)

    def test_update_document_metadata(self):
        """人工修订元数据。"""
        result = self.kb.ingest_text(
            self.db, IngestTextRequest(text=DOCUMENT_TEXT, file_name="a.txt")
        )
        self.db.commit()

        updated = self.kb.update_document(
            self.db,
            result.document_id,
            DocumentUpdateRequest(
                title="人工修订标题",
                status=DocStatus.AMENDED.value,
                effective_date="2022-04-01",
                remark="人工校核通过",
            ),
        )
        self.assertEqual(updated.title, "人工修订标题")
        self.assertEqual(updated.status, DocStatus.AMENDED.value)
        self.assertEqual(updated.effective_date, "2022-04-01")

    def test_update_document_syncs_chunk_validity(self):
        """修改时效字段必须同步到条文（否则检索过滤会用到旧值）。"""
        result = self.kb.ingest_text(
            self.db, IngestTextRequest(text=DOCUMENT_TEXT, file_name="a.txt")
        )
        self.db.commit()

        self.kb.update_document(
            self.db,
            result.document_id,
            DocumentUpdateRequest(status=DocStatus.REPEALED.value, expiry_date="2023-01-01"),
        )

        chunks, total = repository.list_chunks(
            self.db, document_id=result.document_id, limit=100
        )
        self.assertGreater(total, 0)
        for chunk in chunks:
            self.assertEqual(chunk.status, DocStatus.REPEALED.value)
            self.assertEqual(chunk.expiry_date, date(2023, 1, 1))
            self.assertGreater(chunk.expiry_ts, 0)

    def test_update_document_invalid_status(self):
        """无效状态被拒绝。"""
        result = self.kb.ingest_text(
            self.db, IngestTextRequest(text=DOCUMENT_TEXT, file_name="a.txt")
        )
        self.db.commit()
        with self.assertRaises(BadRequestError):
            self.kb.update_document(
                self.db, result.document_id, DocumentUpdateRequest(status="无效状态")
            )

    def test_update_document_invalid_date(self):
        """非法日期格式被拒绝。"""
        result = self.kb.ingest_text(
            self.db, IngestTextRequest(text=DOCUMENT_TEXT, file_name="a.txt")
        )
        self.db.commit()
        with self.assertRaises(BadRequestError):
            self.kb.update_document(
                self.db, result.document_id, DocumentUpdateRequest(effective_date="去年")
            )

    def test_update_missing_document(self):
        """更新不存在的文档抛 404。"""
        with self.assertRaises(NotFoundError):
            self.kb.update_document(self.db, 999999, DocumentUpdateRequest(title="x"))

    def test_delete_document(self):
        """删除文档并清理向量。"""
        result = self.kb.ingest_text(
            self.db, IngestTextRequest(text=DOCUMENT_TEXT, file_name="a.txt")
        )
        self.db.commit()
        removal = self.kb.delete_document(self.db, result.document_id)
        self.assertTrue(removal["deleted"])
        self.assertEqual(len(self.store.rows), 0)

    def test_delete_missing_document_raises(self):
        """删除不存在的文档抛 404。"""
        with self.assertRaises(NotFoundError):
            self.kb.delete_document(self.db, 999999)

    def test_list_chunks_and_get_chunk(self):
        """条文列表与详情（溯源落地页）。"""
        result = self.kb.ingest_text(
            self.db, IngestTextRequest(text=DOCUMENT_TEXT, file_name="a.txt")
        )
        self.db.commit()

        items, total = self.kb.list_chunks(self.db, document_id=result.document_id, limit=10)
        self.assertGreater(total, 0)
        detail = self.kb.get_chunk(self.db, items[0].id)
        self.assertTrue(detail.content)
        self.assertTrue(detail.doc_title)

    def test_get_missing_chunk_raises(self):
        """不存在的条文抛 404。"""
        with self.assertRaises(NotFoundError):
            self.kb.get_chunk(self.db, 999999)

    def test_list_chunks_only_valid_filter(self):
        """只有效过滤生效。"""
        self.seed()
        _, total = self.kb.list_chunks(self.db, only_valid=True, limit=100)
        self.assertGreater(total, 0)

    def test_list_tags_and_tasks(self):
        """标签与任务列表。"""
        self.kb.ingest_text(self.db, IngestTextRequest(text=DOCUMENT_TEXT, file_name="a.txt"))
        self.db.commit()
        self.assertTrue(self.kb.list_tags(self.db))
        self.assertTrue(self.kb.list_tasks(self.db))

    def test_rebuild_vectors(self):
        """重建向量索引。"""
        result = self.kb.ingest_text(
            self.db, IngestTextRequest(text=DOCUMENT_TEXT, file_name="a.txt")
        )
        self.db.commit()
        self.store.rows.clear()
        summary = self.kb.rebuild_vectors(self.db)
        self.assertEqual(summary["written"], result.chunk_count)
        self.assertEqual(len(self.store.rows), result.chunk_count)


class TestQaService(ServiceTestCase):
    """问答服务。"""

    def test_ask_returns_answer_with_citations(self):
        """完整问答：回答 + 引用 + 可信度 + 耗时。"""
        self.seed()
        response = self.qa.ask(
            self.db, AskRequest(question="流动性风险压力测试的频率要求是什么", top_k=5)
        )
        self.assertTrue(response.answer)
        self.assertTrue(response.has_answer)
        self.assertTrue(response.citations)
        self.assertGreater(response.evidence_count, 0)
        self.assertGreaterEqual(response.credibility.score, 0.0)
        self.assertLessEqual(response.credibility.score, 100.0)
        self.assertIsNotNone(response.history_id)
        self.assertIn(response.trace.total_ms, range(0, 600000))

    def test_ask_citation_has_traceability(self):
        """引用必须带条文溯源信息。"""
        self.seed()
        response = self.qa.ask(self.db, AskRequest(question="流动性风险压力测试", top_k=3))
        citation = response.citations[0]
        self.assertTrue(citation.clause_no)
        self.assertTrue(citation.source_link)
        self.assertTrue(citation.citation_label)
        self.assertIn(citation.status, DocStatus.values())

    def test_ask_without_evidence_returns_no_answer(self):
        """知识库为空时不编造回答。"""
        response = self.qa.ask(self.db, AskRequest(question="完全无关的问题"))
        self.assertFalse(response.has_answer)
        self.assertEqual(response.citations, [])
        self.assertEqual(response.evidence_count, 0)

    def test_ask_saves_history(self):
        """历史记录被持久化。"""
        self.seed()
        response = self.qa.ask(
            self.db, AskRequest(question="流动性风险压力测试", user_id="u1")
        )
        record = repository.get_history(self.db, response.history_id)
        self.assertEqual(record.user_id, "u1")
        self.assertEqual(record.question, "流动性风险压力测试")
        self.assertGreater(record.credibility_score, 0.0)

    def test_ask_can_skip_history(self):
        """save_history=False 时不写库。"""
        self.seed()
        response = self.qa.ask(
            self.db, AskRequest(question="流动性风险压力测试", save_history=False)
        )
        self.assertIsNone(response.history_id)
        _, total = repository.list_history(self.db, "anonymous", limit=10)
        self.assertEqual(total, 0)

    def test_ask_respects_business_line_filter(self):
        """业务条线过滤透传到检索链。"""
        self.seed()
        response = self.qa.ask(
            self.db,
            AskRequest(
                question="偿付能力充足率不得低于多少",
                business_lines=[BusinessLine.INSURANCE.value],
                top_k=5,
            ),
        )
        for citation in response.citations:
            self.assertEqual(citation.business_line, BusinessLine.INSURANCE.value)

    def test_ask_retrieval_modes(self):
        """三种检索模式都可工作。"""
        self.seed()
        for mode in ("vector", "bm25", "hybrid"):
            with self.subTest(mode=mode):
                response = self.qa.ask(
                    self.db, AskRequest(question="流动性风险压力测试", retrieval_mode=mode)
                )
                self.assertEqual(response.retrieval_mode, mode)
                self.assertIsInstance(response.answer, str)

    def test_ask_include_all_citations(self):
        """include_all_citations 时补齐未引用条文。"""
        self.seed()
        response = self.qa.ask(
            self.db,
            AskRequest(
                question="流动性风险压力测试", top_k=5, include_all_citations=True
            ),
        )
        self.assertGreaterEqual(len(response.citations), 1)

    def test_within_budget_flag(self):
        """性能预算标记存在且为布尔。"""
        self.seed()
        response = self.qa.ask(self.db, AskRequest(question="流动性风险"))
        self.assertIsInstance(response.within_budget, bool)

    def test_credibility_components_present(self):
        """可信度包含五维明细。"""
        self.seed()
        response = self.qa.ask(self.db, AskRequest(question="流动性风险压力测试"))
        self.assertEqual(
            set(response.credibility.components),
            {"retrieval_strength", "term_alignment", "citation_coverage",
             "evidence_validity", "answer_grounding"},
        )

    def test_ask_with_invalid_llm_raises(self):
        """模型调用失败应向上抛出，由接口层转为 502。"""
        from app.core.errors import LLMServiceError

        self.seed()

        class BrokenEngine:
            """模拟生成失败。"""

            @staticmethod
            def generate(*args, **kwargs):
                raise LLMServiceError("模拟模型不可用")

        original = self.container._rag_engine
        self.container._rag_engine = BrokenEngine()
        try:
            with self.assertRaises(LLMServiceError):
                self.qa.ask(self.db, AskRequest(question="流动性风险压力测试"))
        finally:
            self.container._rag_engine = original


class TestHistoryService(ServiceTestCase):
    """历史记录服务。"""

    def _ask(self, question: str = "流动性风险", user_id: str = "u1"):
        self.seed()
        return self.qa.ask(self.db, AskRequest(question=question, user_id=user_id))

    def test_list_history_marks_favorited(self):
        """列表标记已收藏项。"""
        response = self._ask()
        self.qa.add_favorite(
            self.db, FavoriteCreateRequest(history_id=response.history_id, user_id="u1")
        )
        items, total = self.qa.list_history(self.db, "u1", limit=10)
        self.assertEqual(total, 1)
        self.assertTrue(items[0].favorited)

    def test_get_history(self):
        """按主键取详情。"""
        response = self._ask()
        detail = self.qa.get_history(self.db, response.history_id, "u1")
        self.assertEqual(detail.id, response.history_id)

    def test_get_history_wrong_user(self):
        """跨用户访问被拒绝。"""
        response = self._ask()
        with self.assertRaises(NotFoundError):
            self.qa.get_history(self.db, response.history_id, "u2")

    def test_delete_history(self):
        """删除单条。"""
        response = self._ask()
        self.assertTrue(self.qa.delete_history(self.db, response.history_id, "u1"))
        with self.assertRaises(NotFoundError):
            self.qa.delete_history(self.db, response.history_id, "u1")

    def test_delete_history_wrong_user(self):
        """越权删除被拒绝。"""
        response = self._ask()
        with self.assertRaises(NotFoundError):
            self.qa.delete_history(self.db, response.history_id, "u2")

    def test_clear_history(self):
        """清空历史。"""
        self._ask("问题一")
        self._ask("问题二")
        deleted = self.qa.clear_history(self.db, "u1")
        self.assertEqual(deleted, 2)


class TestFavoriteService(ServiceTestCase):
    """收藏服务。"""

    def setUp(self):
        super().setUp()
        self.seed()
        self.history_id = self.qa.ask(
            self.db, AskRequest(question="流动性风险压力测试", user_id="u1")
        ).history_id

    def test_add_favorite_from_history(self):
        """从历史创建收藏并带出引用。"""
        item = self.qa.add_favorite(
            self.db, FavoriteCreateRequest(history_id=self.history_id, user_id="u1")
        )
        self.assertEqual(item.history_id, self.history_id)
        self.assertTrue(item.question)
        self.assertTrue(item.answer)
        self.assertGreater(item.credibility_score, 0.0)

    def test_add_favorite_is_idempotent(self):
        """重复收藏同一历史幂等。"""
        first = self.qa.add_favorite(
            self.db, FavoriteCreateRequest(history_id=self.history_id, user_id="u1")
        )
        second = self.qa.add_favorite(
            self.db, FavoriteCreateRequest(history_id=self.history_id, user_id="u1")
        )
        self.assertEqual(first.id, second.id)

    def test_add_favorite_missing_history(self):
        """历史不存在抛 404。"""
        with self.assertRaises(NotFoundError):
            self.qa.add_favorite(
                self.db, FavoriteCreateRequest(history_id=999999, user_id="u1")
            )

    def test_add_favorite_requires_content(self):
        """既无历史又无问题内容时拒绝。"""
        with self.assertRaises(BadRequestError):
            self.qa.add_favorite(self.db, FavoriteCreateRequest(user_id="u1"))

    def test_update_note(self):
        """更新备注。"""
        item = self.qa.add_favorite(
            self.db, FavoriteCreateRequest(history_id=self.history_id, user_id="u1")
        )
        updated = self.qa.update_favorite_note(self.db, item.id, "重点条文", "u1")
        self.assertEqual(updated.note, "重点条文")

    def test_update_note_wrong_user(self):
        """越权修改被拒绝。"""
        item = self.qa.add_favorite(
            self.db, FavoriteCreateRequest(history_id=self.history_id, user_id="u1")
        )
        with self.assertRaises(NotFoundError):
            self.qa.update_favorite_note(self.db, item.id, "x", "u2")

    def test_list_favorites(self):
        """分页列出收藏。"""
        self.qa.add_favorite(
            self.db, FavoriteCreateRequest(history_id=self.history_id, user_id="u1")
        )
        items, total = self.qa.list_favorites(self.db, "u1", limit=10)
        self.assertEqual(total, 1)
        self.assertEqual(len(items), 1)

    def test_delete_favorite(self):
        """删除收藏。"""
        item = self.qa.add_favorite(
            self.db, FavoriteCreateRequest(history_id=self.history_id, user_id="u1")
        )
        self.assertTrue(self.qa.delete_favorite(self.db, item.id, "u1"))
        with self.assertRaises(NotFoundError):
            self.qa.delete_favorite(self.db, item.id, "u1")


class TestHelpers(unittest.TestCase):
    """服务层辅助函数。"""

    def test_parse_date_formats(self):
        """多种日期格式解析。"""
        self.assertEqual(parse_date("2020-06-01"), date(2020, 6, 1))
        self.assertEqual(parse_date("2020/06/01"), date(2020, 6, 1))
        self.assertEqual(parse_date("2020年6月1日"), date(2020, 6, 1))
        self.assertEqual(parse_date("20200601"), date(2020, 6, 1))

    def test_parse_date_empty_and_invalid(self):
        """空值返回 None，非法格式抛错。"""
        self.assertIsNone(parse_date(""))
        self.assertIsNone(parse_date(None))
        with self.assertRaises(BadRequestError):
            parse_date("不是日期")

    def test_filters_from_request(self):
        """请求参数转检索过滤条件。"""
        payload = AskRequest(
            question="q",
            only_valid=False,
            exclude_expired=False,
            business_lines=[BusinessLine.BANKING.value],
            doc_types=[DocType.NORMATIVE.value],
            document_ids=[1, 2],
            as_of=date(2021, 1, 1),
        )
        filters = filters_from_request(payload)
        self.assertFalse(filters.only_valid)
        self.assertFalse(filters.exclude_expired)
        self.assertEqual(filters.business_lines, [BusinessLine.BANKING.value])
        self.assertEqual(filters.document_ids, [1, 2])
        self.assertGreater(filters.as_of, 0)

    def test_ask_request_validation(self):
        """请求模型校验非法枚举与空问题。"""
        with self.assertRaises(Exception):
            AskRequest(question="   ")
        with self.assertRaises(Exception):
            AskRequest(question="q", statuses=["非法状态"])
        with self.assertRaises(Exception):
            AskRequest(question="q", business_lines=["非法条线"])
        with self.assertRaises(Exception):
            AskRequest(question="q", doc_types=["非法类型"])
        with self.assertRaises(Exception):
            AskRequest(question="q", top_k=999)

    def test_ask_request_strips_question(self):
        """问题首尾空白被清理。"""
        self.assertEqual(AskRequest(question="  问题  ").question, "问题")


class TestContainerFactory(unittest.TestCase):
    """容器装配。"""

    def test_make_test_container_wires_fakes(self):
        """替身被正确注入。"""
        container, store, embedder, chat = make_test_container(make_settings())
        self.assertIsInstance(store, FakeVectorStore)
        self.assertIsInstance(embedder, FakeEmbedder)
        self.assertIsInstance(chat, FakeChatClient)
        self.assertIs(container.vector_store, store)
        self.assertIs(container.embedder, embedder)

    def test_container_lazy_properties(self):
        """检索器与生成引擎惰性构建且被复用。"""
        container, _, _, _ = make_test_container(make_settings())
        self.assertIs(container.vector_retriever, container.vector_retriever)
        self.assertIs(container.hybrid_retriever, container.hybrid_retriever)
        self.assertIs(container.rag_engine, container.rag_engine)
        self.assertIs(container.ingest_pipeline, container.ingest_pipeline)

    def test_container_status(self):
        """状态汇总包含各组件。"""
        container, _, _, _ = make_test_container(make_settings())
        status = container.status()
        for key in ("llm", "embedding", "vector_store"):
            self.assertIn(key, status)

    def test_rag_engine_uses_langchain_model(self):
        """RAG 引擎从客户端解析出 LangChain 模型。"""
        container, _, _, _ = make_test_container(make_settings())
        engine = container.rag_engine
        self.assertTrue(hasattr(engine.chain, "invoke"))


if __name__ == "__main__":
    unittest.main()
