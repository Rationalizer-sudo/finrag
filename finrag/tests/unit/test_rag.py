"""RAG 层单元测试：Prompt、条文溯源、可信度评分、生成链。"""

from __future__ import annotations

import unittest
from datetime import date

from app.core.constants import BusinessLine, CredibilityLevel, DocStatus, DocType
from app.rag.answer_chain import RagAnswer, RagAnswerEngine, resolve_chat_model
from app.rag.citation import (
    build_citation_bundle,
    collect_source_links,
    extract_citation_indexes,
    has_uncited_claim,
    render_markdown_sources,
)
from app.rag.credibility import (
    WEIGHTS,
    compute_credibility,
    describe_formula,
    grade,
)
from app.rag import prompts
from app.retrieval.types import RetrievedClause
from tests.fakes import FakeChatClient, build_fake_chat_model
from tests.support import DEFAULT_ANSWER, make_settings


def make_clause(
    chunk_id: int = 1,
    content: str = "商业银行应当至少每季度向监管机构报送流动性风险指标。",
    *,
    clause_no: str = "第十二条",
    doc_title: str = "商业银行流动性风险管理办法",
    status: str = DocStatus.EFFECTIVE.value,
    score: float = 0.8,
    **kwargs,
) -> RetrievedClause:
    """构造条文证据。"""
    return RetrievedClause(
        chunk_id=chunk_id,
        document_id=kwargs.pop("document_id", 1),
        content=content,
        clause_no=clause_no,
        doc_title=doc_title,
        doc_no=kwargs.pop("doc_no", "银发〔2020〕15号"),
        issuer=kwargs.pop("issuer", "中国人民银行"),
        status=status,
        score=score,
        business_line=kwargs.pop("business_line", BusinessLine.BANKING.value),
        doc_type=kwargs.pop("doc_type", DocType.NORMATIVE.value),
        effective_date=kwargs.pop("effective_date", date(2020, 6, 1)),
        source_url=kwargs.pop("source_url", "https://www.pbc.gov.cn/example"),
        **kwargs,
    )


class TestPrompts(unittest.TestCase):
    """Prompt 组装。"""

    def test_format_evidence_contains_key_fields(self):
        """证据段落应包含制度名、条文号、状态与正文。"""
        text = prompts.format_evidence(1, make_clause())
        self.assertIn("[1]", text)
        self.assertIn("《商业银行流动性风险管理办法》", text)
        self.assertIn("第十二条", text)
        self.assertIn(DocStatus.EFFECTIVE.value, text)
        self.assertIn("商业银行应当至少每季度", text)

    def test_format_evidence_truncates_long_content(self):
        """过长条文应截断，控制上下文长度。"""
        long_clause = make_clause(content="内容" * 2000)
        text = prompts.format_evidence(1, long_clause)
        self.assertIn("条文过长已截断", text)
        self.assertLess(len(text), len(long_clause.content))

    def test_format_evidence_without_title(self):
        """缺少全部元数据时回退到条文编号。"""
        clause = make_clause(doc_title="", doc_no="", issuer="", clause_no="")
        text = prompts.format_evidence(3, clause)
        self.assertIn("条文#", text)

    def test_format_evidence_with_doc_no_only(self):
        """只有文号时直接展示文号，不输出空括号。"""
        clause = make_clause(doc_title="", issuer="", clause_no="")
        text = prompts.format_evidence(2, clause)
        self.assertIn("银发〔2020〕15号", text)
        self.assertNotIn("（）", text)

    def test_format_context_empty(self):
        """无证据时给出明确占位。"""
        self.assertIn("未检索到", prompts.format_context([]))

    def test_format_context_lists_all(self):
        """多条证据按序号排列。"""
        context = prompts.format_context([make_clause(1), make_clause(2)])
        self.assertIn("[1]", context)
        self.assertIn("[2]", context)

    def test_build_prompt_inputs(self):
        """模板输入变量正确。"""
        inputs = prompts.build_prompt_inputs("报送频率是多少", [make_clause()])
        self.assertEqual(inputs["question"], "报送频率是多少")
        self.assertIn("[1]", inputs["context"])

    def test_build_messages_roles(self):
        """返回 system + human 两条消息。"""
        messages = prompts.build_messages("问题", [make_clause()])
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0][0], "system")
        self.assertEqual(messages[1][0], "human")

    def test_system_prompt_has_anti_hallucination_rules(self):
        """系统提示必须包含防幻觉与强制溯源规则（金融场景底线）。"""
        text = prompts.SYSTEM_PROMPT
        self.assertIn("禁止", text)
        self.assertIn("现有知识库中未检索到相关规定", text)
        self.assertIn("[1]", text)

    def test_count_context_chars(self):
        """上下文长度统计。"""
        self.assertGreater(prompts.count_context_chars([make_clause()]), 0)
        self.assertEqual(prompts.count_context_chars([]), 0)


class TestCitation(unittest.TestCase):
    """条文溯源。"""

    def test_extract_indexes(self):
        """解析 [n] / ［n］ / 【n】 三种形态并去重保序。"""
        answer = "结论一[1]，结论二[2]，补充[1]，全角［3］，方头【4】。"
        self.assertEqual(extract_citation_indexes(answer), [1, 2, 3, 4])

    def test_extract_indexes_respects_max(self):
        """超出证据数量的编号被忽略（防止模型编造引用）。"""
        self.assertEqual(extract_citation_indexes("依据[1][9]", max_index=2), [1])

    def test_extract_indexes_empty(self):
        """空回答返回空列表。"""
        self.assertEqual(extract_citation_indexes(""), [])
        self.assertEqual(extract_citation_indexes("没有引用"), [])

    def test_build_bundle_explicit(self):
        """显式引用时只返回被引用的条文。"""
        evidences = [make_clause(1), make_clause(2), make_clause(3)]
        bundle = build_citation_bundle("结论[2]", evidences)
        self.assertTrue(bundle.has_explicit_citation)
        self.assertEqual(bundle.cited_indexes, [2])
        self.assertEqual(bundle.count, 1)
        self.assertEqual(bundle.citations[0]["chunk_id"], 2)
        self.assertEqual(bundle.uncited_indexes, [1, 3])

    def test_build_bundle_fallback_to_all(self):
        """模型未标注引用时兜底返回全部条文并告警。"""
        evidences = [make_clause(1), make_clause(2)]
        bundle = build_citation_bundle("没有标注引用的回答", evidences)
        self.assertFalse(bundle.has_explicit_citation)
        self.assertEqual(bundle.count, 2)
        self.assertTrue(bundle.warnings)

    def test_build_bundle_fallback_disabled(self):
        """关闭兜底时不返回条文。"""
        bundle = build_citation_bundle("无引用", [make_clause(1)], fallback_to_all=False)
        self.assertEqual(bundle.count, 0)
        self.assertTrue(bundle.warnings)

    def test_build_bundle_no_evidence(self):
        """无证据时返回空集合。"""
        bundle = build_citation_bundle("回答[1]", [])
        self.assertEqual(bundle.count, 0)

    def test_has_uncited_claim(self):
        """长句无引用应被识别为「未溯源陈述」。"""
        self.assertTrue(
            has_uncited_claim(
                "商业银行应当按照监管要求建立完善的流动性风险管理体系，"
                "并至少每季度定期评估该体系的有效性。"
            )
        )
        self.assertFalse(
            has_uncited_claim("商业银行应当建立流动性风险管理体系并定期评估其有效性。[1]")
        )

    def test_has_uncited_claim_ignores_boilerplate(self):
        """操作提示句不计入未溯源。"""
        self.assertFalse(
            has_uncited_claim("以上回答依据公开制度文本生成，具体业务办理请以正式发文及监管口径为准。")
        )

    def test_citation_contains_traceability_fields(self):
        """引用对象必须带条文溯源链接与状态（差异化要求）。"""
        bundle = build_citation_bundle("结论[1]", [make_clause()])
        citation = bundle.citations[0]
        for key in ("chunk_id", "clause_no", "doc_title", "status", "source_link",
                    "citation_label", "content"):
            self.assertIn(key, citation)
        self.assertEqual(citation["source_link"], "https://www.pbc.gov.cn/example")

    def test_source_link_falls_back_to_internal(self):
        """无官方链接时回退到系统内条文详情页。"""
        clause = make_clause(source_url="")
        self.assertIn("/api/v1/knowledge/chunks/", clause.source_link)

    def test_collect_source_links(self):
        """提取引用卡片数据。"""
        bundle = build_citation_bundle("结论[1]", [make_clause()])
        links = collect_source_links(bundle.citations)
        self.assertEqual(len(links), 1)
        self.assertIn("source_link", links[0])

    def test_render_markdown_sources(self):
        """Markdown 参考条文渲染。"""
        bundle = build_citation_bundle("结论[1]", [make_clause()])
        markdown = render_markdown_sources(bundle.citations)
        self.assertIn("**参考条文**", markdown)
        self.assertIn("第十二条", markdown)

    def test_render_markdown_empty(self):
        """无引用时返回空串。"""
        self.assertEqual(render_markdown_sources([]), "")


class TestCredibility(unittest.TestCase):
    """可信度评分。"""

    def test_grade_thresholds(self):
        """等级阈值。"""
        self.assertEqual(grade(95), CredibilityLevel.HIGH.value)
        self.assertEqual(grade(80), CredibilityLevel.HIGH.value)
        self.assertEqual(grade(70), CredibilityLevel.MEDIUM.value)
        self.assertEqual(grade(50), CredibilityLevel.LOW.value)
        self.assertEqual(grade(10), CredibilityLevel.UNRELIABLE.value)

    def test_weights_sum_to_100(self):
        """各维度权重之和为 100（满分口径清晰）。"""
        self.assertAlmostEqual(sum(WEIGHTS.values()), 100.0)

    def test_high_quality_answer_scores_high(self):
        """证据充分、引用规范、时效有效时应为高分。"""
        evidences = [
            make_clause(1, "商业银行应当至少每季度向监管机构报送流动性风险指标。", score=0.95),
            make_clause(2, "商业银行应当至少每季度开展一次流动性风险压力测试。", score=0.9),
        ]
        answer = (
            "商业银行应当至少每季度向监管机构报送流动性风险指标。[1]"
            "同时应当至少每季度开展一次流动性风险压力测试。[2]"
        )
        bundle = build_citation_bundle(answer, evidences)
        result = compute_credibility("流动性风险指标的报送频率", answer, evidences, bundle)
        self.assertGreaterEqual(result.score, 60)
        self.assertIn(result.label, (CredibilityLevel.HIGH.value, CredibilityLevel.MEDIUM.value))

    def test_no_answer_scores_zero(self):
        """未基于知识库作答时可信度为 0 且等级为不可信。"""
        result = compute_credibility("问题", "未检索到", [], None, has_answer=False)
        self.assertEqual(result.score, 0.0)
        self.assertEqual(result.label, CredibilityLevel.UNRELIABLE.value)
        self.assertIn("未基于知识库条文作答", result.reasons[0])

    def test_empty_evidences_lowers_score(self):
        """无证据时分数为 0。"""
        result = compute_credibility("问题", "回答", [], None)
        self.assertLess(result.score, 40)
        self.assertTrue(result.reasons)

    def test_stale_evidence_penalized(self):
        """证据全为已废止条文时，有效性维度为 0 并给出提示。"""
        stale = [make_clause(status=DocStatus.REPEALED.value)]
        answer = "依据该条[1]"
        bundle = build_citation_bundle(answer, stale)
        result = compute_credibility("问题", answer, stale, bundle)
        self.assertEqual(result.components["evidence_validity"], 0.0)
        self.assertTrue(any("非现行有效" in reason for reason in result.reasons))

    def test_missing_citation_penalized(self):
        """未标注引用时引用充分度下降。"""
        evidences = [make_clause(1), make_clause(2)]
        bundle = build_citation_bundle("没有任何引用的回答", evidences)
        result = compute_credibility("流动性风险", "没有任何引用的回答", evidences, bundle)
        self.assertLess(result.components["citation_coverage"], 1.0)

    def test_components_within_range(self):
        """各维度得分都应在 [0,1]。"""
        evidences = [make_clause()]
        answer = "商业银行应当每季度报送。[1]"
        bundle = build_citation_bundle(answer, evidences)
        result = compute_credibility("报送频率", answer, evidences, bundle)
        self.assertEqual(set(result.components), set(WEIGHTS))
        for value in result.components.values():
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 1.0)

    def test_result_to_dict(self):
        """评分结果可序列化。"""
        result = compute_credibility("q", "a", [make_clause()], None)
        data = result.to_dict()
        self.assertIn("score", data)
        self.assertIn("label", data)
        self.assertIn("components", data)
        self.assertIn("reasons", data)

    def test_describe_formula(self):
        """评分口径可对外解释（答辩演示用）。"""
        formula = describe_formula()
        self.assertIn("weights", formula)
        self.assertIn("thresholds", formula)
        self.assertEqual(len(formula["thresholds"]), 4)


class TestAnswerChain(unittest.TestCase):
    """LangChain 生成链。"""

    def setUp(self):
        self.settings = make_settings()

    def test_resolve_chat_model_from_client(self):
        """可从客户端对象解析出 LangChain 模型。"""
        client = FakeChatClient()
        model = resolve_chat_model(client)
        self.assertTrue(hasattr(model, "invoke"))

    def test_resolve_chat_model_direct(self):
        """可直接传 LangChain 模型。"""
        model = build_fake_chat_model("x")
        self.assertIs(resolve_chat_model(model), model)

    def test_resolve_chat_model_invalid(self):
        """非法对象应抛出 TypeError。"""
        with self.assertRaises(TypeError):
            resolve_chat_model(object())
        with self.assertRaises(ValueError):
            resolve_chat_model(None)

    def test_generate_with_evidence(self):
        """有证据时应调用模型、生成回答、给出引用与可信度。"""
        engine = RagAnswerEngine(build_fake_chat_model(DEFAULT_ANSWER), self.settings)
        result = engine.generate("流动性风险指标的报送频率是多少", [make_clause()])
        self.assertIsInstance(result, RagAnswer)
        self.assertTrue(result.has_answer)
        self.assertTrue(result.llm_called)
        self.assertTrue(result.answer)
        self.assertTrue(result.citations)
        self.assertGreaterEqual(result.evidence_count, 1)
        self.assertGreater(result.context_chars, 0)

    def test_generate_without_evidence_short_circuits(self):
        """无证据时不调用模型，直接返回确定性提示。"""
        engine = RagAnswerEngine(build_fake_chat_model("不该被调用"), self.settings)
        result = engine.generate("无关问题", [])
        self.assertFalse(result.has_answer)
        self.assertFalse(result.llm_called)
        self.assertEqual(result.answer, prompts.NO_EVIDENCE_ANSWER)
        self.assertEqual(result.credibility.score, 0.0)

    def test_generate_with_only_stale_evidence_short_circuits(self):
        """证据全为过时条文时拒绝作答（时效性要求）。"""
        engine = RagAnswerEngine(build_fake_chat_model("不该被调用"), self.settings)
        stale = [make_clause(status=DocStatus.REPEALED.value)]
        result = engine.generate("问题", stale)
        self.assertFalse(result.has_answer)
        self.assertFalse(result.llm_called)
        self.assertEqual(result.answer, prompts.STALE_EVIDENCE_ANSWER)

    def test_valid_evidences_filters(self):
        """有效性筛选。"""
        evidences = [
            make_clause(1),
            make_clause(2, status=DocStatus.REPEALED.value),
            make_clause(3, status=DocStatus.PENDING.value),
        ]
        usable = RagAnswerEngine.valid_evidences(evidences)
        self.assertEqual([item.chunk_id for item in usable], [1, 3])

    def test_chain_is_langchain_runnable(self):
        """生成链由 LangChain 组件装配而成。"""
        engine = RagAnswerEngine(build_fake_chat_model("x"), self.settings)
        chain = engine.chain
        self.assertTrue(hasattr(chain, "invoke"))
        self.assertTrue(hasattr(chain, "stream"))

    def test_invoke_returns_text(self):
        """invoke 返回模型原始文本。"""
        engine = RagAnswerEngine(build_fake_chat_model("回答内容[1]"), self.settings)
        text = engine.invoke("问题", [make_clause()])
        self.assertIn("回答内容", text)

    def test_stream_yields_chunks(self):
        """流式生成产出增量文本。"""
        engine = RagAnswerEngine(build_fake_chat_model("流式回答[1]"), self.settings)
        chunks = list(engine.stream("问题", [make_clause()]))
        self.assertTrue(chunks)
        self.assertIn("流式回答", "".join(chunks))

    def test_finalize_builds_citations(self):
        """finalize 后处理生成引用与可信度。"""
        engine = RagAnswerEngine(build_fake_chat_model("x"), self.settings)
        result = engine.finalize("问题", "依据该条[1]", [make_clause()])
        self.assertTrue(result.has_answer)
        self.assertEqual(result.cited_indexes, [1])
        self.assertGreater(result.credibility.score, 0.0)

    def test_result_to_dict(self):
        """结果可序列化。"""
        engine = RagAnswerEngine(build_fake_chat_model(DEFAULT_ANSWER), self.settings)
        result = engine.generate("问题", [make_clause()])
        data = result.to_dict()
        for key in ("question", "answer", "citations", "credibility", "elapsed_ms",
                    "evidence_count", "llm_called", "warnings"):
            self.assertIn(key, data)

    def test_elapsed_ms_recorded(self):
        """耗时被记录（性能指标）。"""
        engine = RagAnswerEngine(build_fake_chat_model(DEFAULT_ANSWER), self.settings)
        result = engine.generate("问题", [make_clause()])
        self.assertGreaterEqual(result.elapsed_ms, 0)


class TestRetrievedClauseModel(unittest.TestCase):
    """证据对象的派生属性。"""

    def test_is_valid(self):
        """有效性判断。"""
        self.assertTrue(make_clause(status=DocStatus.EFFECTIVE.value).is_valid)
        self.assertTrue(make_clause(status=DocStatus.PENDING.value).is_valid)
        self.assertFalse(make_clause(status=DocStatus.REPEALED.value).is_valid)
        self.assertFalse(make_clause(status=DocStatus.AMENDED.value).is_valid)

    def test_citation_label(self):
        """引用标签。"""
        self.assertEqual(make_clause().citation_label, "《商业银行流动性风险管理办法》第十二条")

    def test_citation_label_fallback(self):
        """缺少元数据时回退条文 ID。"""
        clause = make_clause(doc_title="", clause_no="")
        self.assertEqual(clause.citation_label, f"条文#{clause.chunk_id}")

    def test_to_langchain_document(self):
        """转换为 LangChain Document。"""
        document = make_clause().to_langchain_document()
        self.assertIn("第十二条", document.page_content)
        self.assertIn("《", document.page_content)
        self.assertEqual(document.metadata["chunk_id"], 1)
        self.assertEqual(document.metadata["status"], DocStatus.EFFECTIVE.value)

    def test_to_citation_index(self):
        """引用序号可自定义。"""
        citation = make_clause().to_citation(5)
        self.assertEqual(citation["index"], 5)


if __name__ == "__main__":
    unittest.main()
