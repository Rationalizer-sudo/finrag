"""FastAPI 接口层集成测试。

使用真实的 FastAPI 应用、真实的服务层与数据层（SQLite），
仅把 Milvus 与通义千问替换为测试替身。
"""

from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from app.core.constants import BusinessLine, DocStatus
from app.db.session import get_session_factory, init_db, reset_engine
from app.main import create_app
from app.services.container import reset_container, set_container
from tests.support import (
    DEFAULT_ANSWER,
    make_settings,
    make_test_container,
    reset_app_state,
    sample_knowledge_base,
    seed_vectors,
)

DOCUMENT_TEXT = """国家金融监督管理总局关于规范商业银行流动性风险管理的通知
金规〔2022〕3号
国家金融监督管理总局
发布日期：2022年1月10日

第一条 为加强商业银行流动性风险管理，制定本通知。
第二条 【适用范围】本通知适用于在中华人民共和国境内依法设立的商业银行。
第三条 【压力测试】商业银行应当至少每季度开展一次流动性风险压力测试。
第四条 【报告义务】商业银行应当至少每季度向监管机构报送流动性风险指标。
第五条 本通知自2022年4月1日起施行。
"""


class ApiTestCase(unittest.TestCase):
    """公共夹具：应用 + SQLite + 替身容器 + 已播种语料。"""

    answer = DEFAULT_ANSWER

    def setUp(self):
        reset_app_state()
        init_db(drop=True)
        self.settings = make_settings(qwen_embedding_dim=48, retrieve_candidate_k=20)
        self.container, self.store, self.embedder, self.chat = make_test_container(
            self.settings, answer=self.answer, dim=48
        )
        set_container(self.container)
        self.app = create_app(self.settings)
        self.client = TestClient(self.app, raise_server_exceptions=False)

        self.db = get_session_factory()()
        data = sample_knowledge_base(self.db)
        self.db.commit()
        seed_vectors(self.store, self.embedder, data["all_chunks"])
        self.data = data

    def tearDown(self):
        self.client.close()
        self.db.close()
        reset_engine()
        reset_container()

    # ------------------------------------------------------------------ 工具
    def ok(self, response):
        """断言请求成功并返回 ``data``。"""
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["code"], 0, payload)
        return payload["data"]


class TestSystemEndpoints(ApiTestCase):
    """系统接口。"""

    def test_liveness(self):
        """存活探针不依赖外部组件，且为裸对象（便于容器健康检查）。"""
        response = self.client.get("/api/v1/health/live")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "alive")
        self.assertIn("uptime_seconds", data)
        self.assertNotIn("code", data)

    def test_health_reports_components(self):
        """健康检查列出各依赖状态。"""
        response = self.client.get("/api/v1/health")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        names = {item["name"] for item in payload["components"]}
        self.assertEqual(
            names,
            {"mysql", "milvus", "qwen-chat", "qwen-embedding", "tesseract-ocr"},
        )
        self.assertIn(payload["status"], ("ok", "degraded", "down"))
        self.assertIn("version", payload)

    def test_capabilities(self):
        """能力清单（业务接口，使用标准响应信封）。"""
        data = self.ok(self.client.get("/api/v1/capabilities"))
        self.assertIn("hybrid", data["retrieval_modes"])
        self.assertIn(DocStatus.EFFECTIVE.value, data["doc_statuses"])
        self.assertIn("weights", data["credibility_formula"])
        self.assertTrue(data["features"]["hybrid_retrieval"])
        self.assertTrue(data["features"]["citation_tracing"])
        self.assertIn("supported_extensions", data)
        self.assertIn("min_vector_score", data["defaults"])

    def test_api_index(self):
        """/api 返回接口索引（裸对象）。"""
        response = self.client.get("/api")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("endpoints", data)
        self.assertIn("ask", data["endpoints"])

    def test_openapi_schema(self):
        """OpenAPI 文档可生成。"""
        response = self.client.get("/openapi.json")
        self.assertEqual(response.status_code, 200)
        schema = response.json()
        self.assertIn("/api/v1/qa/ask", schema["paths"])
        self.assertIn("/api/v1/knowledge/stats", schema["paths"])

    def test_root_redirects(self):
        """根路径重定向到前端或文档。"""
        response = self.client.get("/", follow_redirects=False)
        self.assertIn(response.status_code, (302, 307))
        self.assertIn(response.headers["location"], ("/app/", "/docs"))

    def test_request_id_header(self):
        """响应带请求 ID 与耗时头，便于链路追踪与性能观测。"""
        response = self.client.get("/api/v1/health/live")
        self.assertIn("X-Request-ID", response.headers)
        self.assertIn("X-Process-Time-Ms", response.headers)


class TestQaEndpoints(ApiTestCase):
    """问答接口。"""

    def test_ask_success(self):
        """提问返回回答、引用、可信度与性能信息。"""
        data = self.ok(
            self.client.post(
                "/api/v1/qa/ask",
                json={"question": "流动性风险压力测试的频率要求是什么", "top_k": 5},
            )
        )
        self.assertTrue(data["answer"])
        self.assertTrue(data["has_answer"])
        self.assertTrue(data["citations"])
        self.assertIn("credibility", data)
        self.assertIn("score", data["credibility"])
        self.assertIn("trace", data)
        self.assertIsNotNone(data["history_id"])

    def test_ask_citation_shape(self):
        """引用对象字段完整，前端可直接渲染溯源卡片。"""
        data = self.ok(
            self.client.post("/api/v1/qa/ask", json={"question": "流动性风险压力测试"})
        )
        citation = data["citations"][0]
        for key in (
            "index", "chunk_id", "document_id", "doc_title", "doc_no", "clause_no",
            "content", "status", "is_valid", "source_link", "citation_label", "score",
        ):
            self.assertIn(key, citation)

    def test_ask_filters(self):
        """业务条线与文档范围过滤生效。"""
        data = self.ok(
            self.client.post(
                "/api/v1/qa/ask",
                json={
                    "question": "偿付能力充足率不得低于多少",
                    "business_lines": [BusinessLine.INSURANCE.value],
                },
            )
        )
        for citation in data["citations"]:
            self.assertEqual(citation["business_line"], BusinessLine.INSURANCE.value)

    def test_ask_no_evidence(self):
        """相关度不足时不编造答案：无关键词命中 + 抬高向量下限 -> 返回「未检索到」。

        问题刻意选用与语料完全无关的术语，确保 BM25 无命中；
        再调高 ``min_vector_score`` 使向量召回也被下限挡住，
        从而确定性地模拟「库里确实没有相关条文」。
        """
        original = self.settings.min_vector_score
        self.settings.min_vector_score = 0.999
        try:
            data = self.ok(
                self.client.post(
                    "/api/v1/qa/ask",
                    json={"question": "量子纠缠与超导材料的制备工艺"},
                )
            )
        finally:
            self.settings.min_vector_score = original

        self.assertFalse(data["has_answer"])
        self.assertEqual(data["citations"], [])
        self.assertEqual(data["evidence_count"], 0)
        self.assertIn("未检索到", data["answer"])
        self.assertEqual(data["credibility"]["score"], 0.0)

    def test_ask_keyword_hit_survives_relevance_floor(self):
        """关键词命中属于强证据，不应被向量相似度下限误杀。"""
        original = self.settings.min_vector_score
        self.settings.min_vector_score = 0.999
        try:
            data = self.ok(
                self.client.post(
                    "/api/v1/qa/ask",
                    json={"question": "压力测试", "retrieval_mode": "bm25"},
                )
            )
        finally:
            self.settings.min_vector_score = original

        self.assertTrue(data["citations"])

    def test_ask_validation_error(self):
        """非法请求体返回 422 与字段明细。"""
        response = self.client.post("/api/v1/qa/ask", json={"question": ""})
        self.assertEqual(response.status_code, 422)
        payload = response.json()
        self.assertEqual(payload["code"], 42200)
        self.assertIn("detail", payload)

    def test_ask_invalid_enum(self):
        """非法枚举值被拒绝。"""
        response = self.client.post(
            "/api/v1/qa/ask", json={"question": "问题", "statuses": ["非法状态"]}
        )
        self.assertEqual(response.status_code, 422)

    def test_ask_llm_failure_returns_502(self):
        """模型不可用时返回 502 与可操作提示。"""
        from app.core.errors import LLMServiceError

        class BrokenEngine:
            """模拟模型故障。"""

            @staticmethod
            def generate(*args, **kwargs):
                raise LLMServiceError("模拟模型不可用")

        original = self.container._rag_engine
        self.container._rag_engine = BrokenEngine()
        try:
            response = self.client.post(
                "/api/v1/qa/ask", json={"question": "流动性风险压力测试"}
            )
        finally:
            self.container._rag_engine = original

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["code"], 50030)

    def test_ask_stream(self):
        """流式接口按 SSE 协议返回 meta/delta/done。"""
        with self.client.stream(
            "POST",
            "/api/v1/qa/ask/stream",
            json={"question": "流动性风险压力测试", "top_k": 3},
        ) as response:
            self.assertEqual(response.status_code, 200)
            self.assertIn("text/event-stream", response.headers["content-type"])
            body = "".join(response.iter_text())

        self.assertIn("event: meta", body)
        self.assertIn("event: delta", body)
        self.assertIn("event: done", body)

    def test_history_crud(self):
        """历史列表、详情、删除、清空。"""
        created = self.ok(
            self.client.post(
                "/api/v1/qa/ask", json={"question": "流动性风险压力测试", "user_id": "u1"}
            )
        )
        history_id = created["history_id"]

        listing = self.ok(self.client.get("/api/v1/qa/history", params={"user_id": "u1"}))
        self.assertEqual(listing["meta"]["total"], 1)
        self.assertEqual(len(listing["items"]), 1)

        detail = self.ok(self.client.get(f"/api/v1/qa/history/{history_id}", params={"user_id": "u1"}))
        self.assertEqual(detail["id"], history_id)
        self.assertFalse(detail["favorited"])

        self.ok(self.client.delete(f"/api/v1/qa/history/{history_id}", params={"user_id": "u1"}))
        after = self.ok(self.client.get("/api/v1/qa/history", params={"user_id": "u1"}))
        self.assertEqual(after["meta"]["total"], 0)

    def test_clear_history(self):
        """清空历史返回删除条数。"""
        for question in ("问题一", "问题二"):
            self.client.post(
                "/api/v1/qa/ask", json={"question": question, "user_id": "u1"}
            )
        data = self.ok(self.client.delete("/api/v1/qa/history", params={"user_id": "u1"}))
        self.assertEqual(data["deleted_count"], 2)

    def test_history_wrong_user_404(self):
        """跨用户访问历史返回 404。"""
        created = self.ok(
            self.client.post("/api/v1/qa/ask", json={"question": "问题", "user_id": "u1"})
        )
        response = self.client.get(
            f"/api/v1/qa/history/{created['history_id']}", params={"user_id": "u2"}
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], 40400)


class TestKnowledgeEndpoints(ApiTestCase):
    """知识库接口。"""

    def test_stats(self):
        """总览统计。"""
        data = self.ok(self.client.get("/api/v1/knowledge/stats"))
        self.assertGreater(data["document_count"], 0)
        self.assertGreater(data["chunk_count"], 0)
        self.assertIn("meets_target", data)

    def test_list_documents(self):
        """文档列表分页。"""
        data = self.ok(self.client.get("/api/v1/knowledge/documents", params={"limit": 2}))
        self.assertLessEqual(len(data["items"]), 2)
        self.assertIn("meta", data)
        self.assertIn("tags", data["items"][0])

    def test_document_detail_and_404(self):
        """文档详情与不存在时的 404。"""
        document_id = self.data["document"].id
        data = self.ok(self.client.get(f"/api/v1/knowledge/documents/{document_id}"))
        self.assertEqual(data["id"], document_id)

        response = self.client.get("/api/v1/knowledge/documents/999999")
        self.assertEqual(response.status_code, 404)

    def test_document_filters(self):
        """状态与条线过滤。"""
        data = self.ok(
            self.client.get(
                "/api/v1/knowledge/documents",
                params={"status": DocStatus.REPEALED.value},
            )
        )
        self.assertEqual(data["meta"]["total"], 1)

    def test_patch_document(self):
        """人工修订元数据。"""
        document_id = self.data["document"].id
        data = self.ok(
            self.client.patch(
                f"/api/v1/knowledge/documents/{document_id}",
                json={"remark": "人工校核", "status": DocStatus.AMENDED.value},
            )
        )
        self.assertEqual(data["remark"], "人工校核")
        self.assertEqual(data["status"], DocStatus.AMENDED.value)

    def test_patch_document_invalid(self):
        """非法状态返回 400。"""
        document_id = self.data["document"].id
        response = self.client.patch(
            f"/api/v1/knowledge/documents/{document_id}", json={"status": "非法"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], 40000)

    def test_ingest_text(self):
        """文本入库接口。"""
        data = self.ok(
            self.client.post(
                "/api/v1/knowledge/documents/text",
                json={"text": DOCUMENT_TEXT, "file_name": "接口入库.txt"},
            )
        )
        self.assertEqual(data["status"], "success")
        self.assertGreater(data["chunk_count"], 0)
        self.assertTrue(data["doc_no"])

    def test_ingest_text_validation(self):
        """过短文本被参数校验拒绝。"""
        response = self.client.post(
            "/api/v1/knowledge/documents/text", json={"text": "短"}
        )
        self.assertEqual(response.status_code, 422)

    def test_delete_document(self):
        """删除文档。"""
        document_id = self.data["insurance_document"].id
        data = self.ok(self.client.delete(f"/api/v1/knowledge/documents/{document_id}"))
        self.assertTrue(data["deleted"])
        response = self.client.get(f"/api/v1/knowledge/documents/{document_id}")
        self.assertEqual(response.status_code, 404)

    def test_chunks_and_detail(self):
        """条文列表与详情（溯源落地页）。"""
        listing = self.ok(self.client.get("/api/v1/knowledge/chunks", params={"limit": 5}))
        self.assertTrue(listing["items"])
        chunk_id = listing["items"][0]["id"]
        detail = self.ok(self.client.get(f"/api/v1/knowledge/chunks/{chunk_id}"))
        self.assertEqual(detail["id"], chunk_id)
        self.assertTrue(detail["content"])

    def test_chunk_404(self):
        """不存在的条文返回 404。"""
        response = self.client.get("/api/v1/knowledge/chunks/999999")
        self.assertEqual(response.status_code, 404)

    def test_tags_and_tasks(self):
        """标签与入库任务列表。"""
        self.client.post(
            "/api/v1/knowledge/documents/text",
            json={"text": DOCUMENT_TEXT, "file_name": "t.txt"},
        )
        tags = self.ok(self.client.get("/api/v1/knowledge/tags"))
        self.assertIsInstance(tags, list)
        tasks = self.ok(self.client.get("/api/v1/knowledge/tasks"))
        self.assertTrue(tasks)

    def test_ocr_status(self):
        """OCR 状态接口。"""
        data = self.ok(self.client.get("/api/v1/knowledge/ocr/status"))
        self.assertIn("available", data)
        self.assertIn("reason", data)

    def test_upload_limits(self):
        """上传限制说明。"""
        data = self.ok(self.client.get("/api/v1/knowledge/documents/upload/limits"))
        self.assertGreater(data["max_upload_mb"], 0)
        self.assertIn(".pdf", data["allowed_extensions"])

    def test_upload_endpoint_responds(self):
        """上传接口始终可用。

        已安装 python-multipart 时走真实上传（此处缺文件应 422）；
        未安装时由降级路由返回 code=50010 的提示，而不是 404。
        """
        response = self.client.post("/api/v1/knowledge/documents/upload")
        self.assertIn(response.status_code, (200, 422))
        if response.status_code == 200:
            self.assertNotEqual(response.json()["code"], 0)
            self.assertIn("python-multipart", response.json()["message"])

    def test_rebuild_vectors(self):
        """重建向量索引。"""
        self.store.rows.clear()
        data = self.ok(self.client.post("/api/v1/knowledge/vectors/rebuild"))
        self.assertGreater(data["written"], 0)


class TestFavoriteEndpoints(ApiTestCase):
    """收藏接口。"""

    def _ask(self, user_id: str = "u1") -> int:
        data = self.ok(
            self.client.post(
                "/api/v1/qa/ask", json={"question": "流动性风险压力测试", "user_id": user_id}
            )
        )
        return data["history_id"]

    def test_create_and_list(self):
        """从历史创建收藏并列出。"""
        history_id = self._ask()
        created = self.ok(
            self.client.post(
                "/api/v1/favorites",
                json={"history_id": history_id, "user_id": "u1", "note": "重点"},
            )
        )
        self.assertEqual(created["history_id"], history_id)
        self.assertEqual(created["note"], "重点")

        listing = self.ok(self.client.get("/api/v1/favorites", params={"user_id": "u1"}))
        self.assertEqual(listing["meta"]["total"], 1)

    def test_update_note(self):
        """更新备注。"""
        history_id = self._ask()
        created = self.ok(
            self.client.post(
                "/api/v1/favorites", json={"history_id": history_id, "user_id": "u1"}
            )
        )
        updated = self.ok(
            self.client.patch(
                f"/api/v1/favorites/{created['id']}",
                params={"user_id": "u1"},
                json={"note": "复核后确认"},
            )
        )
        self.assertEqual(updated["note"], "复核后确认")

    def test_delete_favorite(self):
        """删除收藏。"""
        history_id = self._ask()
        created = self.ok(
            self.client.post(
                "/api/v1/favorites", json={"history_id": history_id, "user_id": "u1"}
            )
        )
        self.ok(
            self.client.delete(
                f"/api/v1/favorites/{created['id']}", params={"user_id": "u1"}
            )
        )
        listing = self.ok(self.client.get("/api/v1/favorites", params={"user_id": "u1"}))
        self.assertEqual(listing["meta"]["total"], 0)

    def test_favorite_missing_history(self):
        """历史不存在返回 404。"""
        response = self.client.post(
            "/api/v1/favorites", json={"history_id": 999999, "user_id": "u1"}
        )
        self.assertEqual(response.status_code, 404)

    def test_history_marks_favorited(self):
        """历史列表标记已收藏。"""
        history_id = self._ask()
        self.client.post(
            "/api/v1/favorites", json={"history_id": history_id, "user_id": "u1"}
        )
        listing = self.ok(self.client.get("/api/v1/qa/history", params={"user_id": "u1"}))
        self.assertTrue(listing["items"][0]["favorited"])


class TestErrorHandling(ApiTestCase):
    """统一错误处理。"""

    def test_unknown_route_404(self):
        """未知路由返回统一错误体。"""
        response = self.client.get("/api/v1/definitely/not/here")
        self.assertEqual(response.status_code, 404)
        payload = response.json()
        self.assertIn("code", payload)
        self.assertIn("message", payload)

    def test_error_body_has_request_id(self):
        """错误响应带 request_id，便于日志定位。"""
        response = self.client.get("/api/v1/knowledge/documents/999999")
        self.assertIn("request_id", response.json())
        self.assertTrue(response.json()["request_id"])


if __name__ == "__main__":
    unittest.main()
