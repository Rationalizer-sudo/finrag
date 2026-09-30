"""通义千问接入模块单元测试（不发起真实网络请求）。"""

from __future__ import annotations

import unittest

from app.core.errors import LLMServiceError
from app.llm.embeddings import MAX_BATCH_SIZE, MAX_INPUT_CHARS, QwenEmbedder
from app.llm.qwen import ROLE_AI, ROLE_HUMAN, ROLE_SYSTEM, QwenChatClient
from tests.support import make_settings

CONFIGURED = dict(
    dashscope_api_key="sk-real-key-for-test",
    qwen_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    qwen_chat_model="qwen-plus",
    qwen_embedding_model="text-embedding-v3",
    qwen_embedding_dim=1024,
)


class TestQwenChatClient(unittest.TestCase):
    """对话模型客户端。"""

    def test_status_structure(self):
        """状态字段完整且不泄漏 API Key。"""
        client = QwenChatClient(make_settings(**CONFIGURED))
        status = client.status()
        self.assertEqual(status["provider"], "dashscope")
        self.assertEqual(status["model"], "qwen-plus")
        self.assertTrue(status["configured"])
        self.assertNotIn("sk-real-key-for-test", str(status))

    def test_not_configured(self):
        """未配置 API Key 时 is_configured 为 False。"""
        self.assertFalse(QwenChatClient(make_settings(dashscope_api_key="")).is_configured())
        self.assertFalse(
            QwenChatClient(
                make_settings(dashscope_api_key="sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
            ).is_configured()
        )

    def test_build_model_raises_without_key(self):
        """缺少 Key 时构造模型应抛出可读错误。"""
        client = QwenChatClient(make_settings(dashscope_api_key=""))
        with self.assertRaises(LLMServiceError) as ctx:
            _ = client.model
        self.assertIn("DASHSCOPE_API_KEY", str(ctx.exception))

    def test_build_messages_roles(self):
        """角色映射为 LangChain 消息类型。"""
        messages = QwenChatClient.build_messages(
            [(ROLE_SYSTEM, "系统"), (ROLE_HUMAN, "问题"), (ROLE_AI, "回答")]
        )
        self.assertEqual(len(messages), 3)
        self.assertEqual(type(messages[0]).__name__, "SystemMessage")
        self.assertEqual(type(messages[1]).__name__, "HumanMessage")
        self.assertEqual(type(messages[2]).__name__, "AIMessage")

    def test_build_messages_unknown_role_defaults_human(self):
        """未知角色降级为 human，避免上游拼装错误导致整链崩溃。"""
        messages = QwenChatClient.build_messages([("unknown_role", "内容")])
        self.assertEqual(type(messages[0]).__name__, "HumanMessage")

    def test_extract_text_from_string(self):
        """字符串内容直接返回。"""
        class Response:
            content = "回答内容"

        self.assertEqual(QwenChatClient._extract_text(Response()), "回答内容")

    def test_extract_text_from_blocks(self):
        """内容块列表应被拼接（兼容推理/多模态模型）。"""
        class Response:
            content = [{"type": "text", "text": "片段一"}, {"type": "text", "text": "片段二"}]

        self.assertEqual(QwenChatClient._extract_text(Response()), "片段一片段二")

    def test_extract_text_partial_none(self):
        """流式分片可能返回 None 内容。"""
        class Response:
            content = None

        self.assertEqual(QwenChatClient._extract_text(Response(), partial=True), "")


class TestQwenEmbedder(unittest.TestCase):
    """向量化客户端。"""

    def test_status_and_dimension(self):
        """状态与维度来自配置。"""
        embedder = QwenEmbedder(make_settings(**CONFIGURED))
        status = embedder.status()
        self.assertEqual(status["dimension"], 1024)
        self.assertEqual(status["model"], "text-embedding-v3")
        self.assertTrue(status["configured"])
        self.assertEqual(embedder.dimension, 1024)

    def test_embed_documents_empty(self):
        """空输入直接返回空列表，不触发连接。"""
        embedder = QwenEmbedder(make_settings(**CONFIGURED))
        self.assertEqual(embedder.embed_documents([]), [])

    def test_build_client_raises_without_key(self):
        """缺少 Key 时抛出可操作错误。"""
        embedder = QwenEmbedder(make_settings(dashscope_api_key=""))
        with self.assertRaises(LLMServiceError) as ctx:
            _ = embedder.client
        self.assertIn("DASHSCOPE_API_KEY", str(ctx.exception))

    def test_unsupported_dimension_rejected(self):
        """不支持的维度应被明确拒绝（避免与 Milvus 集合维度不一致）。"""
        embedder = QwenEmbedder(
            make_settings(**{**CONFIGURED, "qwen_embedding_dim": 999})
        )
        with self.assertRaises(LLMServiceError) as ctx:
            _ = embedder.client
        self.assertIn("999", str(ctx.exception))

    def test_embed_query_empty_raises(self):
        """空查询应被拒绝（不浪费一次调用）。"""
        embedder = QwenEmbedder(make_settings(**CONFIGURED))
        with self.assertRaises(LLMServiceError):
            embedder.embed_query("   ")

    def test_truncate_long_text(self):
        """超长文本被截断到上限。"""
        embedder = QwenEmbedder(make_settings(**CONFIGURED))
        truncated = embedder._truncate("中" * (MAX_INPUT_CHARS + 500))
        self.assertEqual(len(truncated), MAX_INPUT_CHARS)

    def test_validate_dimension_mismatch(self):
        """返回维度不符时抛出明确错误。"""
        embedder = QwenEmbedder(make_settings(**CONFIGURED))
        with self.assertRaises(LLMServiceError) as ctx:
            embedder._validate([[0.1, 0.2]], 1)
        self.assertIn("维度", str(ctx.exception))

    def test_validate_count_mismatch(self):
        """返回条数不符时报错。"""
        embedder = QwenEmbedder(make_settings(**CONFIGURED))
        with self.assertRaises(LLMServiceError) as ctx:
            embedder._validate([[0.0] * 1024], 2)
        self.assertIn("条数", str(ctx.exception))

    def test_batch_size_constant(self):
        """text-embedding-v3 单批上限为 10。"""
        self.assertEqual(MAX_BATCH_SIZE, 10)


if __name__ == "__main__":
    unittest.main()
