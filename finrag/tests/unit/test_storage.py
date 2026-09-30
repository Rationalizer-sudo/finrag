"""上传文件落盘与校验单元测试。"""

from __future__ import annotations

import io
import unittest

from app.core.errors import FileTooLargeError, UnsupportedFileError
from app.services.storage import (
    disk_usage,
    remove_file,
    sanitize_filename,
    save_upload,
    validate_extension,
    validate_size,
)
from tests.support import make_settings, temp_dir


class TestSanitizeFilename(unittest.TestCase):
    """文件名净化。"""

    def test_strips_path(self):
        """路径分隔符与目录被剥离（防路径穿越）。"""
        self.assertEqual(sanitize_filename("../../etc/passwd.txt"), "passwd.txt")
        self.assertEqual(sanitize_filename(r"C:\Windows\system32\a.txt"), "a.txt")

    def test_keeps_chinese(self):
        """中文文件名保留。"""
        self.assertIn("制度", sanitize_filename("金融制度管理办法.txt"))

    def test_replaces_special_chars(self):
        """特殊字符被替换为下划线。"""
        result = sanitize_filename("a b*c?d.txt")
        self.assertNotIn("*", result)
        self.assertNotIn("?", result)

    def test_long_name_truncated(self):
        """超长文件名按长度截断且保留扩展名。"""
        name = "长" * 200 + ".pdf"
        result = sanitize_filename(name, max_length=40)
        self.assertLessEqual(len(result), 40)
        self.assertTrue(result.endswith(".pdf"))

    def test_empty_name(self):
        """空名回退默认值。"""
        self.assertEqual(sanitize_filename(""), "upload")
        self.assertEqual(sanitize_filename("..."), "upload")


class TestValidation(unittest.TestCase):
    """扩展名与大小校验。"""

    def setUp(self):
        self.settings = make_settings(
            allowed_extensions=".txt,.pdf,.docx,.png",
            max_upload_mb=1,
        )

    def test_validate_extension_ok(self):
        """白名单内通过并返回小写扩展名。"""
        self.assertEqual(validate_extension("A.PDF", self.settings), ".pdf")

    def test_validate_extension_rejected(self):
        """白名单外抛出 415 类错误。"""
        with self.assertRaises(UnsupportedFileError):
            validate_extension("a.exe", self.settings)

    def test_validate_extension_missing(self):
        """无扩展名被拒绝。"""
        with self.assertRaises(UnsupportedFileError):
            validate_extension("noext", self.settings)

    def test_validate_size_ok(self):
        """未超限通过。"""
        validate_size(100, "a.txt", self.settings)

    def test_validate_size_rejected(self):
        """超限抛出错误。"""
        with self.assertRaises(FileTooLargeError):
            validate_size(2 * 1024 * 1024, "a.txt", self.settings)


class TestSaveUpload(unittest.TestCase):
    """上传落盘。"""

    def setUp(self):
        self.directory = temp_dir()
        self.settings = make_settings(
            allowed_extensions=".txt,.pdf", max_upload_mb=1, upload_dir=str(self.directory)
        )

    def test_save_upload_ok(self):
        """正常落盘并返回元信息。"""
        stream = io.BytesIO("第十二条 内容".encode("utf-8"))
        path, info = save_upload(stream, "制度.txt", settings=self.settings)
        self.assertTrue(path.exists())
        self.assertEqual(path.read_text(encoding="utf-8"), "第十二条 内容")
        self.assertEqual(info["original_name"], "制度.txt")
        self.assertEqual(info["extension"], ".txt")
        self.assertGreater(info["size_bytes"], 0)

    def test_save_upload_unique_names(self):
        """两次上传不互相覆盖。"""
        first, _ = save_upload(io.BytesIO(b"a"), "same.txt", settings=self.settings)
        second, _ = save_upload(io.BytesIO(b"b"), "same.txt", settings=self.settings)
        self.assertNotEqual(first, second)
        self.assertTrue(first.exists() and second.exists())

    def test_save_upload_rejects_extension(self):
        """扩展名不在白名单时不落盘。"""
        with self.assertRaises(UnsupportedFileError):
            save_upload(io.BytesIO(b"x"), "a.exe", settings=self.settings)

    def test_save_upload_rejects_oversize_known(self):
        """已知大小超限时立即拒绝。"""
        with self.assertRaises(FileTooLargeError):
            save_upload(io.BytesIO(b"x"), "a.txt", size=5 * 1024 * 1024, settings=self.settings)

    def test_save_upload_aborts_on_stream_overflow(self):
        """流式写入过程中超限应中止并清理半成品文件。"""
        payload = b"x" * (2 * 1024 * 1024)
        with self.assertRaises(FileTooLargeError):
            save_upload(io.BytesIO(payload), "big.txt", settings=self.settings)
        leftovers = [p for p in self.directory.iterdir() if p.is_file()]
        self.assertEqual(leftovers, [])


class TestRemoveAndDisk(unittest.TestCase):
    """清理与磁盘信息。"""

    def test_remove_file(self):
        """删除文件。"""
        directory = temp_dir()
        path = directory / "a.txt"
        path.write_text("x", encoding="utf-8")
        self.assertTrue(remove_file(path))
        self.assertFalse(path.exists())

    def test_remove_missing_file_is_safe(self):
        """删除不存在的文件返回 True（幂等）。"""
        self.assertTrue(remove_file(temp_dir() / "missing.txt"))

    def test_disk_usage(self):
        """磁盘信息包含总量字段。"""
        usage = disk_usage(temp_dir())
        self.assertIn("total_mb", usage)
        self.assertIn("free_mb", usage)


if __name__ == "__main__":
    unittest.main()
