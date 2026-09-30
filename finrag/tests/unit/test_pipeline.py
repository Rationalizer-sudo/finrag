"""知识库入库流水线单元测试（SQLite + 假向量库/假向量器）。"""

from __future__ import annotations

import unittest
from datetime import date

from app.core.constants import DocStatus, IngestStatus
from app.db import repository
from app.db.session import get_session_factory, init_db, reset_engine
from app.ingest.pipeline import IngestPipeline
from tests.fakes import FakeEmbedder, FakeVectorStore
from tests.support import make_settings, reset_app_state, temp_dir

DOCUMENT_TEXT = """中国人民银行关于规范商业银行流动性风险管理的通知
银发〔2021〕8号
中国人民银行
发布日期：2021年3月1日

第一条 为加强商业银行流动性风险管理，维护银行体系安全稳健运行，制定本通知。
第二条 本通知适用于在中华人民共和国境内依法设立的商业银行及其分支机构。
第三条 【压力测试】商业银行应当至少每季度开展一次流动性风险压力测试，压力测试结果应当作为制定风险偏好和限额的重要依据。
第四条 【报告义务】商业银行应当至少每季度向监管机构报送流动性风险指标及分析报告。
第五条 本通知自2021年6月1日起施行。
"""


class PipelineTestCase(unittest.TestCase):
    """公共夹具。"""

    def setUp(self):
        reset_app_state()
        init_db(drop=True)
        self.settings = make_settings(qwen_embedding_dim=32, chunk_size=400)
        self.db = get_session_factory()()
        self.store = FakeVectorStore(dim=32)
        self.embedder = FakeEmbedder(dim=32)
        self.pipeline = IngestPipeline(self.settings, self.store, self.embedder)

    def tearDown(self):
        self.db.close()
        reset_engine()


class TestIngestText(PipelineTestCase):
    """文本入库。"""

    def test_ingest_text_success(self):
        """完整链路：清洗 -> 抽元数据 -> 切分 -> 落库 -> 向量化。"""
        result = self.pipeline.ingest_text(self.db, DOCUMENT_TEXT, "流动性风险通知.txt")
        self.db.commit()

        self.assertEqual(result.status, IngestStatus.SUCCESS.value)
        self.assertIsNotNone(result.document_id)
        self.assertGreater(result.chunk_count, 0)
        self.assertEqual(result.vector_count, result.chunk_count)
        self.assertEqual(result.doc_no, "银发〔2021〕8号")
        self.assertEqual(result.issuer, "中国人民银行")
        self.assertEqual(result.doc_status, DocStatus.EFFECTIVE.value)
        self.assertGreater(result.cost_ms, 0)

        # MySQL 侧
        document = repository.get_document(self.db, result.document_id)
        self.assertEqual(document.chunk_count, result.chunk_count)
        self.assertGreater(document.effective_date.year, 2000)

        # 向量侧
        self.assertEqual(len(self.store.rows), result.chunk_count)

    def test_ingest_text_sets_milvus_pk(self):
        """条文应回写向量主键（协同查询的关键）。"""
        result = self.pipeline.ingest_text(self.db, DOCUMENT_TEXT, "a.txt")
        self.db.commit()
        chunks, _ = repository.list_chunks(self.db, document_id=result.document_id, limit=100)
        self.assertTrue(chunks)
        for chunk in chunks:
            self.assertEqual(chunk.milvus_pk, chunk.id)

    def test_ingest_text_attaches_tags(self):
        """业务条线等标签应写入标签表。"""
        result = self.pipeline.ingest_text(self.db, DOCUMENT_TEXT, "a.txt")
        self.db.commit()
        tags = repository.tag_names_of_document(self.db, result.document_id)
        self.assertTrue(tags)

    def test_ingest_text_creates_task_record(self):
        """入库任务应被审计记录。"""
        self.pipeline.ingest_text(self.db, DOCUMENT_TEXT, "a.txt")
        self.db.commit()
        tasks = repository.list_ingest_tasks(self.db)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].status, IngestStatus.SUCCESS.value)
        self.assertEqual(tasks[0].stage, "done")
        self.assertGreater(tasks[0].total_chunks, 0)

    def test_ingest_text_empty_fails(self):
        """空文本应失败并给出原因。"""
        result = self.pipeline.ingest_text(self.db, "   ", "empty.txt")
        self.db.commit()
        self.assertEqual(result.status, IngestStatus.FAILED.value)
        self.assertIn("空", result.message)

    def test_ingest_text_no_clause_content_fails(self):
        """无法切分出知识片段时失败。"""
        result = self.pipeline.ingest_text(self.db, "短", "tiny.txt")
        self.db.commit()
        self.assertEqual(result.status, IngestStatus.FAILED.value)

    def test_override_metadata(self):
        """人工指定的元数据覆盖自动抽取。"""
        result = self.pipeline.ingest_text(
            self.db, DOCUMENT_TEXT, "a.txt",
            doc_type="部门规章", business_line="证券", status=DocStatus.AMENDED.value,
        )
        self.db.commit()
        self.assertEqual(result.doc_type, "部门规章")
        self.assertEqual(result.business_line, "证券")
        self.assertEqual(result.doc_status, DocStatus.AMENDED.value)

    def test_source_url_persisted(self):
        """来源链接（公开数据溯源）写入文档。"""
        result = self.pipeline.ingest_text(
            self.db, DOCUMENT_TEXT, "a.txt", source_url="https://www.pbc.gov.cn/notice"
        )
        self.db.commit()
        document = repository.get_document(self.db, result.document_id)
        self.assertEqual(document.source_url, "https://www.pbc.gov.cn/notice")


class TestIncrementalUpdate(PipelineTestCase):
    """增量更新（创新点）。"""

    def test_same_content_skipped_when_replace_disabled(self):
        """内容未变且不允许覆盖时应跳过，避免重复计算与写入。"""
        first = self.pipeline.ingest_text(self.db, DOCUMENT_TEXT, "a.txt", replace=False)
        self.db.commit()
        second = self.pipeline.ingest_text(self.db, DOCUMENT_TEXT, "a.txt", replace=False)
        self.db.commit()

        self.assertEqual(first.status, IngestStatus.SUCCESS.value)
        self.assertEqual(second.status, IngestStatus.SKIPPED.value)
        self.assertEqual(second.document_id, first.document_id)
        self.assertIn("未变化", second.message)

        _, total = repository.list_documents(self.db, limit=10)
        self.assertEqual(total, 1)

    def test_force_reingest_overrides_skip(self):
        """force=True 时即使内容未变也重新入库。"""
        first = self.pipeline.ingest_text(self.db, DOCUMENT_TEXT, "a.txt", replace=False)
        self.db.commit()
        forced = self.pipeline.ingest_text(
            self.db, DOCUMENT_TEXT, "a.txt", replace=False, force=True
        )
        self.db.commit()
        self.assertEqual(forced.status, IngestStatus.SUCCESS.value)
        self.assertEqual(forced.document_id, first.document_id)

    def test_replace_clears_old_chunks_and_vectors(self):
        """覆盖入库应清理旧条文与旧向量，不产生残留。"""
        first = self.pipeline.ingest_text(self.db, DOCUMENT_TEXT, "a.txt", replace=True)
        self.db.commit()
        first_chunk_ids = set(self.store.rows.keys())
        self.assertEqual(len(first_chunk_ids), first.chunk_count)

        modified = DOCUMENT_TEXT.replace("第四条 【报告义务】", "第四条 【报送要求】")
        second = self.pipeline.ingest_text(self.db, modified, "a.txt", replace=True)
        self.db.commit()

        # 同一文件名 -> 识别为同一制度的新版本，复用同一条文档记录
        self.assertEqual(second.document_id, first.document_id)
        self.assertEqual(second.status, IngestStatus.SUCCESS.value)

        # 关键不变式：向量库中的主键集合 == 当前 MySQL 中的条文集合
        # （SQLite 会复用 rowid，因此不能用 id 是否变化来判断是否清理干净）
        current, total = repository.list_chunks(
            self.db, document_id=second.document_id, limit=1000
        )
        self.assertEqual(set(self.store.rows.keys()), {chunk.id for chunk in current})
        self.assertEqual(len(self.store.rows), total)

        # 文档在库中只有一份，且版本号递增
        _, doc_total = repository.list_documents(self.db, limit=10)
        self.assertEqual(doc_total, 1)
        document = repository.get_document(self.db, second.document_id)
        self.assertEqual(document.version, 2)

    def test_old_vectors_removed_for_document(self):
        """覆盖入库会调用向量库的按文档删除。"""
        self.pipeline.ingest_text(self.db, DOCUMENT_TEXT, "a.txt", replace=True)
        self.db.commit()
        document_id = repository.list_documents(self.db, limit=1)[0][0].id
        self.pipeline.ingest_text(self.db, DOCUMENT_TEXT, "a.txt", replace=True)
        self.db.commit()
        self.assertIn(document_id, self.store.deleted_documents)


class TestVectorFailureHandling(PipelineTestCase):
    """向量写入失败的降级处理。"""

    def test_vector_failure_yields_partial(self):
        """向量写入失败时条文仍应留在 MySQL，任务标记 partial 可恢复。"""
        self.store.fail_on_upsert = True
        result = self.pipeline.ingest_text(self.db, DOCUMENT_TEXT, "a.txt")
        self.db.commit()

        self.assertEqual(result.status, IngestStatus.PARTIAL.value)
        self.assertGreater(result.chunk_count, 0)
        self.assertEqual(result.vector_count, 0)
        self.assertIn("rebuild-vectors", result.message)

        # MySQL 数据完整
        chunks, total = repository.list_chunks(self.db, limit=100)
        self.assertEqual(total, result.chunk_count)

    def test_rebuild_vectors_after_failure(self):
        """失败后可重建向量索引。"""
        self.store.fail_on_upsert = True
        result = self.pipeline.ingest_text(self.db, DOCUMENT_TEXT, "a.txt")
        self.db.commit()

        self.store.fail_on_upsert = False
        summary = self.pipeline.rebuild_vectors(self.db)
        self.assertEqual(summary["total"], result.chunk_count)
        self.assertEqual(summary["written"], result.chunk_count)
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(len(self.store.rows), result.chunk_count)

    def test_rebuild_on_empty_knowledge_base(self):
        """空知识库重建返回零值。"""
        summary = self.pipeline.rebuild_vectors(self.db)
        self.assertEqual(summary, {"total": 0, "written": 0, "failed": 0})


class TestIngestFile(PipelineTestCase):
    """文件入库。"""

    def test_ingest_txt_file(self):
        """从文件入库并生成哈希。"""
        directory = temp_dir()
        path = directory / "制度.txt"
        path.write_text(DOCUMENT_TEXT, encoding="utf-8")

        result = self.pipeline.ingest_file(self.db, path)
        self.db.commit()
        self.assertEqual(result.status, IngestStatus.SUCCESS.value)
        self.assertTrue(result.file_hash)
        self.assertEqual(result.parser_name, "text:utf-8")
        self.assertFalse(result.ocr_used)

    def test_ingest_unsupported_file(self):
        """不支持的文件类型应失败并说明原因。"""
        directory = temp_dir()
        path = directory / "a.exe"
        path.write_bytes(b"\x00\x01")
        result = self.pipeline.ingest_file(self.db, path)
        self.db.commit()
        self.assertEqual(result.status, IngestStatus.FAILED.value)
        self.assertIn("不支持", result.message)

    def test_ingest_missing_file(self):
        """文件不存在应失败而不是抛异常。"""
        result = self.pipeline.ingest_file(self.db, "definitely_missing.txt")
        self.db.commit()
        self.assertEqual(result.status, IngestStatus.FAILED.value)


class TestRemoveDocument(PipelineTestCase):
    """文档删除。"""

    def test_remove_document(self):
        """删除文档应同时清理 MySQL 与向量库。"""
        result = self.pipeline.ingest_text(self.db, DOCUMENT_TEXT, "a.txt")
        self.db.commit()
        document_id = result.document_id

        removal = self.pipeline.remove_document(self.db, document_id)
        self.db.commit()

        self.assertTrue(removal["deleted"])
        self.assertEqual(removal["chunk_count"], result.chunk_count)
        self.assertIsNone(repository.get_document(self.db, document_id))
        self.assertEqual(len(self.store.rows), 0)
        self.assertIn(document_id, self.store.deleted_documents)

    def test_remove_missing_document(self):
        """删除不存在的文档返回 deleted=False。"""
        removal = self.pipeline.remove_document(self.db, 999999)
        self.assertFalse(removal["deleted"])


class TestPipelineReuse(PipelineTestCase):
    """多次入库与检索可见性。"""

    def test_multiple_documents_accumulate(self):
        """多次入库应累积知识库规模。"""
        for index in range(3):
            text = DOCUMENT_TEXT.replace("第一条", f"第一条").replace(
                "流动性风险", f"流动性风险{index}"
            )
            self.pipeline.ingest_text(self.db, text, f"doc{index}.txt")
            self.db.commit()

        _, total = repository.list_documents(self.db, limit=10)
        self.assertEqual(total, 3)
        self.assertEqual(
            repository.count_chunks(self.db, None), len(self.store.rows)
        )

    def test_bm25_cache_invalidated_after_ingest(self):
        """入库后 BM25 索引缓存应失效，保证新条文立即可检索。"""
        from app.retrieval.bm25_retriever import get_bm25_retriever

        retriever = get_bm25_retriever(refresh=True)
        before = retriever.get_index(self.db, None)
        self.pipeline.ingest_text(self.db, DOCUMENT_TEXT, "a.txt")
        self.db.commit()
        after = retriever.get_index(self.db, None)
        self.assertIsNot(before, after)
        self.assertGreater(after.size, before.size)


class TestMetadataInPipeline(PipelineTestCase):
    """入库后的时效性字段（过滤依赖）。"""

    def test_validity_timestamps_written(self):
        """生效/失效时间戳必须写入条文与向量库。"""
        result = self.pipeline.ingest_text(self.db, DOCUMENT_TEXT, "a.txt")
        self.db.commit()

        chunks, _ = repository.list_chunks(self.db, document_id=result.document_id, limit=100)
        for chunk in chunks:
            self.assertGreater(chunk.effective_ts, 0)
            self.assertEqual(chunk.status, DocStatus.EFFECTIVE.value)

        for row in self.store.rows.values():
            self.assertGreater(row.effective_ts, 0)
            self.assertEqual(row.status, DocStatus.EFFECTIVE.value)

    def test_repealed_document_marked(self):
        """含废止用语的文件应标记为已废止，且不进入默认检索。"""
        text = (
            "某规定\n某发〔2015〕1号\n某机关\n"
            "第一条 为规范相关业务，制定本规定。\n"
            "第二条 本规定自2018年1月1日起废止。\n"
        )
        result = self.pipeline.ingest_text(self.db, text, "old.txt")
        self.db.commit()
        self.assertEqual(result.doc_status, DocStatus.REPEALED.value)

        document = repository.get_document(self.db, result.document_id)
        self.assertEqual(document.expiry_date, date(2018, 1, 1))
        # 默认过滤下不可检索
        from app.retrieval.types import RetrievalFilters

        _, total = repository.list_chunks(self.db, filters=RetrievalFilters(), limit=10)
        self.assertEqual(total, 0)


if __name__ == "__main__":
    unittest.main()
