"""检索层集成测试：BM25、向量检索、混合检索（SQLite + 假向量库）。"""

from __future__ import annotations

import unittest

from app.core.constants import BusinessLine, DocStatus
from app.db import repository
from app.db.session import get_session_factory, init_db, reset_engine
from app.retrieval.bm25_retriever import Bm25Index, Bm25Retriever
from app.retrieval.hybrid import HybridRetriever, dedupe_by_document
from app.retrieval.types import RetrievalFilters
from app.retrieval.vector_retriever import VectorRetriever
from tests.fakes import FakeEmbedder, FakeVectorStore
from tests.support import make_settings, reset_app_state, sample_knowledge_base, seed_vectors

QUERY = "商业银行流动性风险压力测试的频率要求"


class RetrievalTestCase(unittest.TestCase):
    """公共夹具：SQLite 知识库 + 假向量库。"""

    def setUp(self):
        reset_app_state()
        init_db(drop=True)
        # 该测试类聚焦「融合 / 回查 / 时效过滤」逻辑，因此把相关度下限置 0，
        # 避免哈希向量的相似度分布干扰断言；下限本身由 TestRelevanceFloor 专门验证。
        self.settings = make_settings(
            qwen_embedding_dim=64, retrieve_candidate_k=20, min_vector_score=0.0
        )
        self.db = get_session_factory()()
        self.data = sample_knowledge_base(self.db)
        self.db.commit()

        self.store = FakeVectorStore(dim=64)
        self.embedder = FakeEmbedder(dim=64)
        seed_vectors(self.store, self.embedder, self.data["all_chunks"])

        self.bm25 = Bm25Retriever(self.settings)
        self.vector_retriever = VectorRetriever(self.store, self.embedder, self.settings)
        self.hybrid = HybridRetriever(self.vector_retriever, self.bm25, self.settings)

    def tearDown(self):
        self.db.close()
        reset_engine()


class TestBm25Index(unittest.TestCase):
    """BM25 内存索引。"""

    def test_build_and_search(self):
        """索引可检索并按分数降序。"""
        index = Bm25Index().build(
            [
                (1, "商业银行应当开展流动性风险压力测试。"),
                (2, "保险公司偿付能力充足率不得低于法定标准。"),
                (3, "压力测试结果应当作为制定风险偏好的依据。"),
            ]
        )
        self.assertEqual(index.size, 3)
        results = index.search("流动性风险压力测试", top_k=3)
        self.assertTrue(results)
        self.assertEqual(results[0][0], 1)
        scores = [score for _, score in results]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_search_empty_index(self):
        """空索引返回空结果。"""
        self.assertEqual(Bm25Index().build([]).search("查询"), [])
        self.assertEqual(Bm25Index().build([(1, "")]).search(""), [])

    def test_search_no_match(self):
        """无匹配词时返回空（不返回零分噪声）。"""
        index = Bm25Index().build([(1, "保险公司偿付能力")])
        self.assertEqual(index.search("流动性压力测试"), [])

    def test_size(self):
        """size 反映条文数。"""
        self.assertEqual(Bm25Index().build([(1, "a"), (2, "b")]).size, 2)


class TestBm25Retriever(RetrievalTestCase):
    """带缓存的 BM25 检索器。"""

    def test_retrieve_returns_chunk_ids(self):
        """能检索出相关内容。"""
        results = self.bm25.retrieve(self.db, "流动性风险压力测试", top_k=5)
        self.assertTrue(results)
        chunk_ids = [chunk_id for chunk_id, _ in results]
        self.assertTrue(all(isinstance(cid, int) for cid in chunk_ids))

    def test_index_is_cached(self):
        """相同过滤条件复用索引（避免每次重建）。"""
        first = self.bm25.get_index(self.db, None)
        second = self.bm25.get_index(self.db, None)
        self.assertIs(first, second)

    def test_index_rebuilt_after_corpus_change(self):
        """语料变化后指纹变化 -> 索引自动重建（增量更新可见性）。"""
        first = self.bm25.get_index(self.db, None)
        self.db.commit()
        from tests.support import seed_document

        seed_document(
            self.db,
            title="新增制度",
            doc_no="测试〔2024〕1号",
            clauses=[("第一条", "新增", "本条规定了全新的核算口径与报送要求。")],
        )
        self.db.commit()
        second = self.bm25.get_index(self.db, None)
        self.assertIsNot(first, second)
        self.assertGreater(second.size, first.size)

    def test_invalidate_clears_cache(self):
        """主动失效后重建索引。"""
        first = self.bm25.get_index(self.db, None)
        self.bm25.invalidate()
        second = self.bm25.get_index(self.db, None)
        self.assertIsNot(first, second)

    def test_retrieve_empty_query(self):
        """空查询返回空。"""
        self.assertEqual(self.bm25.retrieve(self.db, "", top_k=5), [])
        self.assertEqual(self.bm25.retrieve(self.db, "   ", top_k=5), [])

    def test_validity_filter_excludes_repealed(self):
        """默认过滤条件应排除已废止文档的条文（时效性管理）。"""
        chunk_ids = self.bm25.retrieve_ids(self.db, "同业业务过渡期", top_k=10, filters=RetrievalFilters())
        stale_ids = {chunk.id for chunk in self.data["stale_chunks"]}
        self.assertFalse(set(chunk_ids) & stale_ids)

    def test_filter_by_business_line(self):
        """按业务条线过滤。"""
        filters = RetrievalFilters(
            only_valid=True, business_lines=[BusinessLine.INSURANCE.value]
        )
        chunk_ids = self.bm25.retrieve_ids(self.db, "偿付能力充足率", top_k=10, filters=filters)
        insurance_ids = {chunk.id for chunk in self.data["insurance_chunks"]}
        self.assertTrue(set(chunk_ids).issubset(insurance_ids))


class TestVectorRetriever(RetrievalTestCase):
    """向量检索器。"""

    def test_build_expr_default(self):
        """默认表达式包含有效性过滤。"""
        expr = self.vector_retriever.build_expr(None)
        self.assertIn(DocStatus.EFFECTIVE.value, expr)

    def test_build_expr_with_filters(self):
        """过滤条件被翻译为表达式。"""
        expr = self.vector_retriever.build_expr(
            RetrievalFilters(
                business_lines=[BusinessLine.SECURITIES.value],
                doc_types=["规范性文件"],
            )
        )
        self.assertIn("business_line in", expr)
        self.assertIn("doc_type in", expr)

    def test_retrieve_returns_hits(self):
        """向量检索返回命中且按相似度降序。"""
        hits = self.vector_retriever.retrieve(QUERY, top_k=5)
        self.assertTrue(hits)
        scores = [hit.score for hit in hits]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_retrieve_empty_query(self):
        """空查询返回空（不调用 embedding）。"""
        self.assertEqual(self.vector_retriever.retrieve(""), [])
        self.assertEqual(self.vector_retriever.retrieve_ids("  "), [])

    def test_validity_filter_applied_to_store(self):
        """有效性与过期过滤下推到向量库。"""
        chunk_ids = self.vector_retriever.retrieve_ids(QUERY, top_k=20)
        stale_ids = {chunk.id for chunk in self.data["stale_chunks"]}
        self.assertFalse(set(chunk_ids) & stale_ids)


class TestHybridRetriever(RetrievalTestCase):
    """混合检索器（向量 + BM25 + RRF）。"""

    def test_hybrid_returns_clauses_with_metadata(self):
        """返回结果应携带完整溯源元数据。"""
        results = self.hybrid.retrieve(self.db, QUERY, top_k=5)
        self.assertTrue(results)
        top = results[0]
        self.assertTrue(top.content)
        self.assertTrue(top.doc_title)
        self.assertTrue(top.clause_no)
        self.assertGreater(top.score, 0.0)
        self.assertEqual(top.rank, 1)

    def test_ranks_are_sequential(self):
        """rank 从 1 开始连续编号。"""
        results = self.hybrid.retrieve(self.db, QUERY, top_k=5)
        self.assertEqual([item.rank for item in results], list(range(1, len(results) + 1)))

    def test_scores_descending(self):
        """最终得分降序。"""
        results = self.hybrid.retrieve(self.db, QUERY, top_k=5)
        scores = [item.score for item in results]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_vector_only_mode(self):
        """纯向量模式：BM25 分数应为 0。"""
        results = self.hybrid.retrieve(self.db, QUERY, top_k=5, mode="vector")
        self.assertTrue(results)
        self.assertTrue(all(item.bm25_score == 0.0 for item in results))

    def test_bm25_only_mode(self):
        """纯关键词模式：向量分数应为 0。"""
        results = self.hybrid.retrieve(self.db, QUERY, top_k=5, mode="bm25")
        self.assertTrue(results)
        self.assertTrue(all(item.vector_score == 0.0 for item in results))

    def test_invalid_mode_falls_back_to_hybrid(self):
        """非法模式回退为混合检索。"""
        results = self.hybrid.retrieve(self.db, QUERY, top_k=5, mode="不存在")
        self.assertTrue(results)

    def test_excludes_repealed_documents(self):
        """默认配置下已废止条文不得出现在结果中。"""
        results = self.hybrid.retrieve(self.db, "同业业务 过渡期 废止", top_k=10)
        stale_ids = {chunk.id for chunk in self.data["stale_chunks"]}
        self.assertFalse({item.chunk_id for item in results} & stale_ids)

    def test_repealed_returned_when_filter_disabled(self):
        """关闭有效性过滤后，已废止条文可被召回（用于历史沿革查询）。"""
        filters = RetrievalFilters(only_valid=False, exclude_expired=False)
        results = self.hybrid.retrieve(self.db, "同业业务过渡期", top_k=10, filters=filters)
        stale_ids = {chunk.id for chunk in self.data["stale_chunks"]}
        self.assertTrue({item.chunk_id for item in results} & stale_ids)

    def test_business_line_filter(self):
        """业务条线过滤在两条链路上一致生效。"""
        filters = RetrievalFilters(business_lines=[BusinessLine.INSURANCE.value])
        results = self.hybrid.retrieve(self.db, "偿付能力充足率", top_k=10, filters=filters)
        self.assertTrue(results)
        self.assertTrue(all(item.business_line == BusinessLine.INSURANCE.value for item in results))

    def test_document_id_filter(self):
        """限定文档范围。"""
        target = self.data["document"].id
        filters = RetrievalFilters(document_ids=[target])
        results = self.hybrid.retrieve(self.db, QUERY, top_k=10, filters=filters)
        self.assertTrue(results)
        self.assertTrue(all(item.document_id == target for item in results))

    def test_empty_query_returns_empty(self):
        """空问题返回空列表。"""
        self.assertEqual(self.hybrid.retrieve(self.db, ""), [])
        self.assertEqual(self.hybrid.retrieve(self.db, "   "), [])

    def test_no_match_returns_empty(self):
        """完全无关的问题在关键词链路无命中时仍可返回语义结果或空。"""
        results = self.hybrid.retrieve(self.db, "量子纠缠与超导材料", top_k=3)
        self.assertIsInstance(results, list)

    def test_retrieve_documents_langchain(self):
        """转换为 LangChain Document 时应带上文号与条文号。"""
        documents = self.hybrid.retrieve_documents(self.db, QUERY, top_k=3)
        self.assertTrue(documents)
        first = documents[0]
        self.assertIn("《", first.page_content)
        self.assertIn("chunk_id", first.metadata)
        self.assertIn("citation_label", first.metadata)

    def test_missing_vector_rows_are_skipped(self):
        """向量库中有、MySQL 中已删除的条文应被跳过而不是报错。"""
        self.store.delete_by_pks([self.data["chunks"][0].id])
        results = self.hybrid.retrieve(self.db, QUERY, top_k=10)
        self.assertFalse(any(item.chunk_id == self.data["chunks"][0].id for item in results))


class TestDedupe(unittest.TestCase):
    """文档级去重（提升证据多样性）。"""

    def _clauses(self):
        from app.retrieval.types import RetrievedClause

        return [
            RetrievedClause(chunk_id=1, document_id=1, content="a", score=0.9),
            RetrievedClause(chunk_id=2, document_id=1, content="b", score=0.8),
            RetrievedClause(chunk_id=3, document_id=1, content="c", score=0.7),
            RetrievedClause(chunk_id=4, document_id=2, content="d", score=0.6),
        ]

    def test_limit_per_document(self):
        """同一文档最多保留 N 条。"""
        kept = dedupe_by_document(self._clauses(), max_per_document=2)
        self.assertEqual([item.chunk_id for item in kept], [1, 2, 4])

    def test_disabled(self):
        """max_per_document <= 0 时不去重。"""
        self.assertEqual(len(dedupe_by_document(self._clauses(), 0)), 4)


class TestRelevanceFloor(RetrievalTestCase):
    """相关度下限：宁可返回「未检索到」，也不拿不相关条文硬凑答案。"""

    def _set_floor(self, value: float) -> None:
        self.settings.min_vector_score = value

    def test_high_floor_drops_vector_only_candidates(self):
        """下限极高时纯向量召回应被全部丢弃。"""
        self._set_floor(0.999)
        try:
            results = self.hybrid.retrieve(self.db, QUERY, top_k=5, mode="vector")
        finally:
            self._set_floor(0.0)
        self.assertEqual(results, [])

    def test_keyword_hits_survive_any_floor(self):
        """关键词命中视为强证据，不受向量下限影响。"""
        self._set_floor(0.999)
        try:
            results = self.hybrid.retrieve(
                self.db, "流动性风险压力测试", top_k=5, mode="hybrid"
            )
        finally:
            self._set_floor(0.0)
        self.assertTrue(results)

    def test_zero_floor_keeps_everything(self):
        """下限为 0 时不做丢弃。"""
        self._set_floor(0.0)
        results = self.hybrid.retrieve(self.db, QUERY, top_k=5, mode="vector")
        self.assertTrue(results)

    def test_floor_from_settings_is_used(self):
        """下限读取自配置（可通过环境变量调整）。"""
        self.assertEqual(self.hybrid.settings.min_vector_score, self.settings.min_vector_score)


class TestRepositoryFilters(unittest.TestCase):
    """repository 过滤条件与检索层口径一致性。"""

    def setUp(self):
        reset_app_state()
        init_db(drop=True)
        self.db = get_session_factory()()
        self.data = sample_knowledge_base(self.db)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        reset_engine()

    def test_iter_chunk_texts_respects_filters(self):
        """BM25 语料读取使用同一套过滤条件。"""
        corpus = repository.iter_chunk_texts(self.db, None)
        self.assertEqual(len(corpus), len(self.data["all_chunks"]))

        valid_only = repository.iter_chunk_texts(self.db, RetrievalFilters())
        stale_ids = {chunk.id for chunk in self.data["stale_chunks"]}
        self.assertFalse({chunk_id for chunk_id, _ in valid_only} & stale_ids)

    def test_count_chunks(self):
        """条文计数。"""
        self.assertEqual(repository.count_chunks(self.db, None), len(self.data["all_chunks"]))

    def test_get_chunks_by_ids(self):
        """批量回查按 id 映射。"""
        ids = [chunk.id for chunk in self.data["chunks"][:3]]
        mapping = repository.get_chunks_by_ids(self.db, ids)
        self.assertEqual(set(mapping), set(ids))

    def test_get_chunks_by_ids_empty(self):
        """空 id 列表返回空字典。"""
        self.assertEqual(repository.get_chunks_by_ids(self.db, []), {})


if __name__ == "__main__":
    unittest.main()
