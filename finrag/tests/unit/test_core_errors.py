"""异常体系与可选依赖探测单元测试。"""

from __future__ import annotations

import unittest

from app.core.errors import (
    BadRequestError,
    DatabaseError,
    ErrorCode,
    FinRAGError,
    LLMServiceError,
    NotFoundError,
    OCRError,
    ParserError,
    UnsupportedFileError,
    VectorStoreError,
)
from app.core.features import (
    OPTIONAL_DEPENDENCIES,
    dependency_report,
    feature_flags,
    has_module,
    multipart_available,
)


class TestErrors(unittest.TestCase):
    """业务异常应携带稳定的错误码与 HTTP 状态。"""

    def test_base_error_defaults(self):
        """基类默认 500 / 内部错误码。"""
        error = FinRAGError()
        self.assertEqual(error.code, ErrorCode.INTERNAL_ERROR)
        self.assertEqual(error.http_status, 500)
        self.assertEqual(error.message, "服务内部错误")

    def test_error_to_dict(self):
        """to_dict 输出接口统一错误体。"""
        error = NotFoundError("文档不存在", detail={"id": 7})
        payload = error.to_dict()
        self.assertEqual(payload["code"], ErrorCode.NOT_FOUND)
        self.assertEqual(payload["message"], "文档不存在")
        self.assertEqual(payload["detail"], {"id": 7})

    def test_subclass_codes_and_status(self):
        """各子类应映射到正确的错误码与 HTTP 状态。"""
        cases = [
            (BadRequestError, ErrorCode.BAD_REQUEST, 400),
            (NotFoundError, ErrorCode.NOT_FOUND, 404),
            (UnsupportedFileError, ErrorCode.UNSUPPORTED_FILE, 415),
            (ParserError, ErrorCode.PARSER_ERROR, 500),
            (OCRError, ErrorCode.OCR_ERROR, 500),
            (VectorStoreError, ErrorCode.VECTOR_DB_ERROR, 500),
            (LLMServiceError, ErrorCode.LLM_ERROR, 502),
            (DatabaseError, ErrorCode.DB_ERROR, 500),
        ]
        for cls, code, status in cases:
            with self.subTest(cls=cls.__name__):
                error = cls()
                self.assertEqual(error.code, code)
                self.assertEqual(error.http_status, status)
                self.assertTrue(error.message)

    def test_custom_code_override(self):
        """允许显式覆盖错误码。"""
        error = FinRAGError("自定义", code=12345)
        self.assertEqual(error.code, 12345)
        self.assertEqual(str(error), "自定义")


class TestFeatures(unittest.TestCase):
    """可选依赖探测。"""

    def test_has_module_known(self):
        """标准库模块必定可导入。"""
        self.assertTrue(has_module("json"))
        self.assertTrue(has_module("pathlib"))

    def test_has_module_unknown(self):
        """不存在的模块返回 False 而不是抛异常。"""
        self.assertFalse(has_module("definitely_not_a_real_module_xyz"))

    def test_multipart_flag_is_bool(self):
        """multipart 探测返回布尔值。"""
        self.assertIsInstance(multipart_available(), bool)

    def test_dependency_report_structure(self):
        """依赖报告结构完整。"""
        report = dependency_report()
        self.assertIn("dependencies", report)
        self.assertIn("missing", report)
        self.assertIn("hint", report)
        self.assertIsInstance(report["all_installed"], bool)
        for name in OPTIONAL_DEPENDENCIES:
            self.assertIn(name, report["dependencies"])
            self.assertIn("installed", report["dependencies"][name])

    def test_feature_flags_always_on(self):
        """与依赖无关的核心能力应恒为 True。"""
        flags = feature_flags()
        for key in (
            "text_ingest",
            "hybrid_retrieval",
            "citation_tracing",
            "credibility_scoring",
            "incremental_ingest",
            "validity_filtering",
            "history",
            "favorites",
            "streaming",
        ):
            with self.subTest(key=key):
                self.assertTrue(flags[key])


if __name__ == "__main__":
    unittest.main()
