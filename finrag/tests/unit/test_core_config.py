"""配置模块单元测试。"""

from __future__ import annotations

import unittest

from tests.support import make_settings


class TestSettings(unittest.TestCase):
    """``app.core.config.Settings`` 行为验证。"""

    def test_defaults_are_sane(self):
        """默认配置应满足课题指标。"""
        settings = make_settings()
        self.assertEqual(settings.retrieve_mode, "hybrid")
        self.assertLess(settings.chunk_overlap, settings.chunk_size)
        self.assertEqual(settings.response_time_budget_ms, 2000)
        self.assertAlmostEqual(settings.verify_answer_accuracy_target, 0.85)

    def test_database_url_mysql(self):
        """未覆盖时应拼接出 MySQL 连接串。"""
        settings = make_settings(
            database_url_override="",
            mysql_user="u",
            mysql_password="p",
            mysql_host="db",
            mysql_port=3307,
            mysql_db="finrag",
        )
        self.assertTrue(settings.database_url.startswith("mysql+pymysql://u:p@db:3307/finrag"))
        self.assertIn("charset=utf8mb4", settings.database_url)

    def test_database_url_override_wins(self):
        """覆盖串优先级高于各项 MySQL 配置。"""
        settings = make_settings(database_url_override="sqlite://")
        self.assertEqual(settings.database_url, "sqlite://")

    def test_cors_origin_list(self):
        """CORS 白名单应被正确切分。"""
        settings = make_settings(cors_origins="http://a.com, http://b.com ,")
        self.assertEqual(settings.cors_origin_list, ["http://a.com", "http://b.com"])

    def test_allowed_extensions(self):
        """扩展名集合应统一为小写带点。"""
        settings = make_settings(allowed_extensions=".TXT,.Pdf , .docx")
        self.assertEqual(settings.allowed_extension_set, {".txt", ".pdf", ".docx"})

    def test_max_upload_bytes(self):
        """上传上限换算为字节。"""
        settings = make_settings(max_upload_mb=2)
        self.assertEqual(settings.max_upload_bytes, 2 * 1024 * 1024)

    def test_overlap_must_be_less_than_size(self):
        """overlap >= chunk_size 应校验失败（否则切分会死循环）。"""
        with self.assertRaises(Exception):
            make_settings(chunk_size=100, chunk_overlap=100)

    def test_ensure_dashscope_key_raises_when_missing(self):
        """未配置 API Key 时应快速失败并给出可操作提示。"""
        settings = make_settings(dashscope_api_key="")
        with self.assertRaises(RuntimeError) as ctx:
            settings.ensure_dashscope_key()
        self.assertIn("DASHSCOPE_API_KEY", str(ctx.exception))

    def test_ensure_dashscope_key_passes(self):
        """已配置 API Key 时不应抛错。"""
        settings = make_settings(dashscope_api_key="sk-real-key-value")
        settings.ensure_dashscope_key()

    def test_placeholder_key_is_rejected(self):
        """模板占位 Key 应被视为未配置。"""
        settings = make_settings(dashscope_api_key="sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
        with self.assertRaises(RuntimeError):
            settings.ensure_dashscope_key()

    def test_upload_path_is_created(self):
        """上传目录应自动创建并返回绝对路径。"""
        settings = make_settings()
        path = settings.upload_path
        self.assertTrue(path.exists())
        self.assertTrue(path.is_absolute())

    def test_is_testing_flag(self):
        """测试环境标记。"""
        self.assertTrue(make_settings().is_testing)
        self.assertFalse(make_settings(app_env="dev").is_testing)


if __name__ == "__main__":
    unittest.main()
