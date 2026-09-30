"""端到端链路测试：模拟真实使用流程，验证课题各项要求的闭环。

覆盖的核心场景：
1. 空知识库 -> 文本入库 -> 提问 -> 条文溯源可点回原文；
2. 时效性管理：文档改为已废止后，问答不再引用该条文；
3. 增量更新：同一文件重新入库不产生重复文档，版本号递增；
4. 收藏与历史闭环；
5. 文档删除后向量同步清理，问答不再召回。
"""

from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from app.core.constants import DocStatus
from app.db.session import get_session_factory, init_db, reset_engine
from app.main import create_app
from app.services.container import reset_container, set_container
from tests.support import (
    make_settings,
    make_test_container,
    reset_app_state,
)

# 一篇结构完整的制度文本：含文号、机关、章节、条文、小标题、施行日期
REGULATION = """国家金融监督管理总局关于规范商业银行互联网贷款业务的通知
金规〔2023〕12号
国家金融监督管理总局
发布日期：2023年2月1日

第一章 总则
第一条 为规范商业银行互联网贷款业务经营行为，防范金融风险，保护金融消费者合法权益，根据《中华人民共和国银行业监督管理法》等法律法规，制定本通知。
第二条 【适用范围】本通知适用于在中华人民共和国境内依法设立的商业银行开展互联网贷款业务的活动。
第三条 【定义】本通知所称互联网贷款，是指商业银行运用互联网和移动通信等信息通信技术，基于风险数据和风险模型进行交叉验证和风险管理，线上自动受理贷款申请及开展风险评估的贷款业务。

第二章 业务规则
第四条 【授信限额】商业银行应当根据借款人收入水平、负债情况等因素合理确定授信额度，单户用于消费的个人信用贷款授信额度应当不超过人民币20万元。
第五条 【贷款期限】商业银行互联网贷款期限应当与借款人还款能力相匹配，个人经营性互联网贷款期限原则上不超过5年。
第六条 【资金用途】商业银行应当采取有效措施监测贷款资金用途，贷款资金不得用于购房、股票、债券、期货、金融衍生品和资产管理产品投资。

第三章 风险管理
第七条 【风险模型】商业银行应当建立完善的风险模型体系，并至少每年对风险模型进行一次全面评估。
第八条 【信息披露】商业银行应当在借款合同和产品页面中以显著方式向借款人披露贷款利率、期限、还款方式等关键信息。

第四章 附则
第九条 本通知自2023年5月1日起施行。
"""


class EndToEndTest(unittest.TestCase):
    """端到端流程验证。"""

    def setUp(self):
        reset_app_state()
        init_db(drop=True)
        self.settings = make_settings(
            qwen_embedding_dim=64, retrieve_candidate_k=20, min_vector_score=0.0
        )
        self.container, self.store, self.embedder, self.chat = make_test_container(
            self.settings, dim=64
        )
        set_container(self.container)
        self.client = TestClient(create_app(self.settings), raise_server_exceptions=False)
        self.db = get_session_factory()()

    def tearDown(self):
        self.client.close()
        self.db.close()
        reset_engine()
        reset_container()

    # ------------------------------------------------------------------ 工具
    def ok(self, response) -> dict:
        """断言成功并返回 data。"""
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["code"], 0, payload)
        return payload["data"]

    def ingest(self, text: str = REGULATION, file_name: str = "互联网贷款通知.txt") -> dict:
        """通过接口入库制度文本。"""
        return self.ok(
            self.client.post(
                "/api/v1/knowledge/documents/text",
                json={
                    "text": text,
                    "file_name": file_name,
                    "source_url": "https://www.nfra.gov.cn/example",
                },
            )
        )

    def ask(self, question: str, **kwargs) -> dict:
        """通过接口提问。"""
        payload = {"question": question, "top_k": 5}
        payload.update(kwargs)
        return self.ok(self.client.post("/api/v1/qa/ask", json=payload))

    # ------------------------------------------------------------------ 场景
    def test_full_workflow(self):
        """空库 -> 入库 -> 问答 -> 溯源 -> 收藏 -> 删除，全链路闭环。"""
        # ---- 0. 初始状态：空知识库 ----
        stats = self.ok(self.client.get("/api/v1/knowledge/stats"))
        self.assertEqual(stats["document_count"], 0)
        self.assertFalse(stats["meets_target"]["knowledge_points_ok"])

        first = self.ask("互联网贷款授信额度上限是多少")
        self.assertFalse(first["has_answer"])
        self.assertEqual(first["citations"], [])

        # ---- 1. 入库 ----
        result = self.ingest()
        self.assertEqual(result["status"], "success")
        self.assertGreater(result["chunk_count"], 5)
        self.assertEqual(result["doc_no"], "金规〔2023〕12号")
        self.assertEqual(result["doc_status"], DocStatus.EFFECTIVE.value)
        # 向量与条文数量一致
        self.assertEqual(result["vector_count"], result["chunk_count"])
        self.assertEqual(len(self.store.rows), result["chunk_count"])

        stats = self.ok(self.client.get("/api/v1/knowledge/stats"))
        self.assertEqual(stats["document_count"], 1)
        self.assertEqual(stats["vector_count"], result["chunk_count"])
        self.assertTrue(stats["vector_ready"])

        # ---- 2. 问答 + 条文溯源 ----
        answer = self.ask("互联网贷款单户消费信用贷款授信额度上限是多少")
        self.assertTrue(answer["has_answer"])
        self.assertTrue(answer["citations"])

        citation = answer["citations"][0]
        self.assertTrue(citation["clause_no"])
        self.assertEqual(citation["status"], DocStatus.EFFECTIVE.value)
        self.assertTrue(citation["is_valid"])

        # 溯源链接可点回原文
        source = self.ok(self.client.get(f"/api/v1/knowledge/chunks/{citation['chunk_id']}"))
        self.assertEqual(source["id"], citation["chunk_id"])
        self.assertEqual(source["clause_no"], citation["clause_no"])
        self.assertEqual(source["source_url"], "https://www.nfra.gov.cn/example")
        self.assertIn("20万元", source["content"])

        # 可信度与性能指标
        self.assertIn(answer["credibility"]["label"], ("高", "中", "低", "不可信"))
        self.assertIn("total_ms", answer["trace"])
        self.assertTrue(answer["trace"]["vector_used"])
        self.assertTrue(answer["trace"]["bm25_used"])

        # ---- 3. 历史与收藏 ----
        history_id = answer["history_id"]
        listing = self.ok(self.client.get("/api/v1/qa/history", params={"user_id": "anonymous"}))
        # 两次提问都会被记录（包含知识库为空时的那次），便于回溯「哪些问题没人答上」
        self.assertEqual(listing["meta"]["total"], 2)
        answered = next(item for item in listing["items"] if item["id"] == history_id)
        self.assertTrue(answered["has_answer"])
        self.assertGreater(answered["credibility_score"], 0.0)

        favorite = self.ok(
            self.client.post(
                "/api/v1/favorites",
                json={"history_id": history_id, "user_id": "anonymous", "note": "重点额度要求"},
            )
        )
        self.assertIn("20万元", favorite["citations"][0]["content"])

        listing = self.ok(self.client.get("/api/v1/qa/history", params={"user_id": "anonymous"}))
        marked = next(item for item in listing["items"] if item["id"] == history_id)
        self.assertTrue(marked["favorited"])

        favorites = self.ok(self.client.get("/api/v1/favorites", params={"user_id": "anonymous"}))
        self.assertEqual(favorites["meta"]["total"], 1)

        # ---- 4. 时效性管理：标记为已废止后不再引用 ----
        document_id = result["document_id"]
        self.ok(
            self.client.patch(
                f"/api/v1/knowledge/documents/{document_id}",
                json={"status": DocStatus.REPEALED.value, "expiry_date": "2024-01-01"},
            )
        )
        after_repeal = self.ask("互联网贷款单户消费信用贷款授信额度上限是多少")
        self.assertFalse(after_repeal["has_answer"])
        self.assertEqual(after_repeal["citations"], [])

        # 打开有效性过滤后可以查到历史条文（供沿革研究）
        legacy = self.ask(
            "互联网贷款单户消费信用贷款授信额度上限是多少",
            only_valid=False,
            exclude_expired=False,
        )
        self.assertTrue(legacy["citations"])
        self.assertEqual(legacy["citations"][0]["status"], DocStatus.REPEALED.value)
        self.assertFalse(legacy["citations"][0]["is_valid"])

        # ---- 5. 删除文档后向量同步清理 ----
        self.ok(self.client.delete(f"/api/v1/knowledge/documents/{document_id}"))
        self.assertEqual(len(self.store.rows), 0)

        final = self.ask("互联网贷款授信额度上限是多少")
        self.assertFalse(final["has_answer"])

        stats = self.ok(self.client.get("/api/v1/knowledge/stats"))
        self.assertEqual(stats["document_count"], 0)
        self.assertEqual(stats["chunk_count"], 0)

    def test_incremental_update_workflow(self):
        """增量更新：同一文件重新入库识别为新版本，不产生重复文档。"""
        first = self.ingest()
        self.assertEqual(first["document_id"] is not None, True)

        documents = self.ok(self.client.get("/api/v1/knowledge/documents"))
        self.assertEqual(documents["meta"]["total"], 1)

        # 修订第五条后重新入库（文件名不变）
        revised = REGULATION.replace("原则上不超过5年", "原则上不超过3年")
        second = self.ingest(text=revised)

        self.assertEqual(second["document_id"], first["document_id"])
        self.assertEqual(second["status"], "success")

        documents = self.ok(self.client.get("/api/v1/knowledge/documents"))
        self.assertEqual(documents["meta"]["total"], 1)
        self.assertEqual(documents["items"][0]["version"], 2)

        # 向量库主键集合与 MySQL 条文集合一致（无残留）
        chunks = self.ok(
            self.client.get(
                "/api/v1/knowledge/chunks",
                params={"document_id": first["document_id"], "limit": 200},
            )
        )
        self.assertEqual(
            set(self.store.rows.keys()), {item["id"] for item in chunks["items"]}
        )

        # 新版本内容可被检索到
        answer = self.ask("互联网贷款期限原则上不超过几年")
        self.assertTrue(answer["has_answer"])
        joined = " ".join(item["content"] for item in answer["citations"])
        self.assertIn("3年", joined)

    def test_identical_content_is_skipped(self):
        """内容完全一致且不覆盖时应跳过（增量判重）。"""
        self.ingest()
        skipped = self.ok(
            self.client.post(
                "/api/v1/knowledge/documents/text",
                json={"text": REGULATION, "file_name": "互联网贷款通知.txt", "replace": False},
            )
        )
        self.assertEqual(skipped["status"], "skipped")
        self.assertIn("未变化", skipped["message"])

        documents = self.ok(self.client.get("/api/v1/knowledge/documents"))
        self.assertEqual(documents["meta"]["total"], 1)

    def test_rebuild_vectors_recovers_index(self):
        """向量索引丢失后可一键重建（故障恢复能力）。"""
        result = self.ingest()
        self.store.rows.clear()

        rebuilt = self.ok(self.client.post("/api/v1/knowledge/vectors/rebuild"))
        self.assertEqual(rebuilt["written"], result["chunk_count"])
        self.assertEqual(rebuilt["failed"], 0)

        answer = self.ask("互联网贷款资金不得用于哪些用途")
        self.assertTrue(answer["citations"])

    def test_retrieval_mode_comparison(self):
        """三种检索模式在同一问题上都能给出可溯源回答。"""
        self.ingest()
        for mode in ("vector", "bm25", "hybrid"):
            with self.subTest(mode=mode):
                answer = self.ask("互联网贷款资金用途限制", retrieval_mode=mode)
                self.assertEqual(answer["retrieval_mode"], mode)
                self.assertTrue(answer["citations"])
                for citation in answer["citations"]:
                    self.assertTrue(citation["chunk_no"] if False else citation["chunk_id"])

    def test_business_line_filter_isolates_domains(self):
        """业务条线过滤能把问题限定在对应领域。"""
        self.ingest()
        self.ingest(
            text=(
                "某保险公司偿付能力管理规定\n保监发〔2019〕5号\n中国保险监督管理委员会\n"
                "发布日期：2019年3月1日\n"
                "第一条 为加强保险公司偿付能力监管，制定本规定。\n"
                "第二条 保险公司核心偿付能力充足率不得低于百分之五十。\n"
                "第三条 本规定自2019年6月1日起施行。\n"
            ),
            file_name="偿付能力规定.txt",
        )

        insurance = self.ask("偿付能力充足率要求", business_lines=["保险"])
        for citation in insurance["citations"]:
            self.assertEqual(citation["business_line"], "保险")

        banking = self.ask("授信额度上限", business_lines=["银行"])
        for citation in banking["citations"]:
            self.assertEqual(citation["business_line"], "银行")


if __name__ == "__main__":
    unittest.main()
