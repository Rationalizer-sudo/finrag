"""图片 / 扫描件 OCR 解析模块。

对应课题差异化要求「多模态输入支持：新增图片类金融制度文档（扫描件）的
OCR 解析与问答功能」。

实现要点：
* 采用 ``Tesseract`` 引擎（``pytesseract`` 封装）+ ``pdf2image`` 做 PDF 渲染；
* 依赖**惰性导入**，未安装时不影响系统其它能力，只在真正调用 OCR 时报错；
* 提供 :meth:`OcrEngine.status` 供 ``/health`` 与前端展示「OCR 是否就绪」。
"""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core.config import Settings, get_settings
from app.core.errors import OCRError
from app.core.logging_conf import get_logger

logger = get_logger(__name__)

# 金融制度扫描件常见 OCR 混淆字符修正表：
# 光学识别容易把「〇/零」「一/—」「O/0」等混用，而条文号必须精确。
OCR_CHAR_FIXES: Sequence[Tuple[str, str]] = (
    ("第—条", "第一条"),
    ("第–条", "第一条"),
    ("第━条", "第一条"),
    ("第〇条", "第零条"),
    ("笫", "第"),
    ("奈", "条"),
    ("镕", "融"),
    ("银衍", "银行"),
    ("咀", "阻"),
    ("罚欹", "罚款"),
)

# 中文 OCR 常见「数字混排」纠正：仅用于条文号上下文，避免误伤正文数字
OCR_DIGIT_MAP = str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "S": "5", "B": "8"})


class OcrEngine:
    """Tesseract OCR 引擎封装。"""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings: Settings = settings or get_settings()
        self._configured = False

    # --------------------------------------------------------- 可用性探测
    @staticmethod
    def _module_available(name: str) -> bool:
        """不导入模块地判断其是否已安装。"""
        try:
            return importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):  # pragma: no cover
            return False

    @property
    def binary(self) -> str:
        """tesseract 可执行文件路径（优先显式配置，其次 PATH）。"""
        if self.settings.tesseract_cmd:
            return self.settings.tesseract_cmd
        return shutil.which("tesseract") or ""

    def status(self) -> Dict[str, Any]:
        """返回 OCR 能力状态，供接口层与前端提示使用。"""
        has_pytesseract = self._module_available("pytesseract")
        has_pdf2image = self._module_available("pdf2image")
        has_pillow = self._module_available("PIL")
        binary = self.binary
        enabled = bool(self.settings.ocr_enabled)

        if not enabled:
            reason = "配置中已关闭 OCR（OCR_ENABLED=false）"
        elif not has_pytesseract:
            reason = "未安装 pytesseract，请执行 pip install pytesseract"
        elif not binary:
            reason = (
                "未找到 tesseract 可执行文件，请安装 Tesseract-OCR 并配置 "
                "TESSERACT_CMD，或将安装目录加入 PATH"
            )
        else:
            reason = ""

        available = bool(has_pytesseract and binary and has_pillow and enabled)
        return {
            "enabled": enabled,
            "available": available,
            "reason": reason,
            "binary": binary,
            "lang": self.settings.tesseract_lang,
            "dpi": self.settings.ocr_dpi,
            "pytesseract": has_pytesseract,
            "pdf2image": has_pdf2image,
            "pillow": has_pillow,
        }

    @property
    def available(self) -> bool:
        """OCR 是否可用。"""
        return bool(self.status()["available"])

    def _ensure_configured(self) -> Any:
        """配置并返回 pytesseract 模块。"""
        status = self.status()
        if not status["available"]:
            raise OCRError(f"OCR 当前不可用：{status['reason']}")
        import pytesseract

        if not self._configured:
            if self.settings.tesseract_cmd:
                pytesseract.pytesseract.tesseract_cmd = self.settings.tesseract_cmd
            self._configured = True
        return pytesseract

    # ------------------------------------------------------------- 识别
    def fix_text(self, text: str) -> str:
        """修正 OCR 常见错字（公开、幂等、可单测）。"""
        if not text:
            return ""
        result = text
        for wrong, right in OCR_CHAR_FIXES:
            result = result.replace(wrong, right)
        return result

    def image_to_text(self, image: Any) -> str:
        """对 PIL Image 做 OCR。"""
        pytesseract = self._ensure_configured()
        try:
            text = pytesseract.image_to_string(image, lang=self.settings.tesseract_lang)
        except Exception as exc:
            raise OCRError(f"图片 OCR 识别失败：{exc}") from exc
        return self.fix_text(text or "")

    def image_path_to_text(self, path: str | Path) -> str:
        """对图片文件做 OCR。"""
        self._ensure_configured()
        from PIL import Image

        image_path = Path(path)
        if not image_path.is_file():
            raise OCRError(f"图片文件不存在：{image_path}")
        try:
            with Image.open(image_path) as image:
                return self.image_to_text(image)
        except OCRError:
            raise
        except Exception as exc:
            raise OCRError(f"打开图片失败（{image_path.name}）：{exc}") from exc

    def pdf_to_pages(self, path: str | Path) -> List[Tuple[int, str]]:
        """把扫描版 PDF 逐页渲染成图片后 OCR。

        :returns: ``[(页码, 文本), ...]``，页码从 1 开始。
        """
        self._ensure_configured()
        if not self._module_available("pdf2image"):
            raise OCRError("未安装 pdf2image，无法对 PDF 做 OCR（pip install pdf2image）")

        from pdf2image import convert_from_path

        pdf_path = Path(path)
        if not pdf_path.is_file():
            raise OCRError(f"PDF 文件不存在：{pdf_path}")
        try:
            images = convert_from_path(str(pdf_path), dpi=self.settings.ocr_dpi)
        except Exception as exc:
            raise OCRError(
                f"渲染 PDF 为图片失败（{pdf_path.name}）：{exc}；"
                "请确认已安装 poppler 并将其加入 PATH"
            ) from exc

        pages: List[Tuple[int, str]] = []
        for index, image in enumerate(images, start=1):
            try:
                pages.append((index, self.image_to_text(image)))
            finally:
                close = getattr(image, "close", None)
                if callable(close):
                    close()
        logger.info("PDF OCR 完成：%s 共 %d 页", pdf_path.name, len(pages))
        return pages


def ocr_status(settings: Optional[Settings] = None) -> Dict[str, Any]:
    """便捷函数：返回 OCR 能力状态。"""
    return OcrEngine(settings).status()


__all__ = ["OcrEngine", "ocr_status", "OCR_CHAR_FIXES"]
