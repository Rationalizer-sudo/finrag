"""统一日志配置（仅依赖标准库 logging）。

设计要点：
* 控制台 + 按天滚动文件双通道输出，格式统一；
* 幂等：重复调用 ``setup_logging`` 不会重复添加 handler；
* 自动压低第三方库（uvicorn / pymilvus / urllib3）的噪声日志。
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from app.core.config import PROJECT_ROOT, Settings

LOG_FORMAT = (
    "%(asctime)s.%(msecs)03d | %(levelname)-8s | "
    "%(name)s:%(funcName)s:%(lineno)d - %(message)s"
)
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_NOISY_LOGGERS = ("uvicorn.access", "pymilvus", "urllib3", "httpx", "openai", "jieba")

_configured = False


class _ColorFormatter(logging.Formatter):
    """控制台彩色输出；非 TTY 环境自动降级为纯文本。"""

    _COLORS = {
        logging.DEBUG: "\033[36m",
        logging.INFO: "\033[32m",
        logging.WARNING: "\033[33m",
        logging.ERROR: "\033[31m",
        logging.CRITICAL: "\033[1;31m",
    }
    _RESET = "\033[0m"

    def __init__(self, fmt: str, datefmt: str, use_color: bool) -> None:
        super().__init__(fmt=fmt, datefmt=datefmt)
        self._use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        if not self._use_color:
            return text
        color = self._COLORS.get(record.levelno, "")
        return f"{color}{text}{self._RESET}" if color else text


def setup_logging(settings: Settings) -> logging.Logger:
    """初始化根 logger。幂等，返回根 logger。

    :param settings: 应用配置，用于决定日志级别与调试开关。
    """
    global _configured
    root = logging.getLogger()
    if _configured:
        return root

    level = logging.DEBUG if settings.app_debug else logging.INFO
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    # ---- 控制台 ----
    console = logging.StreamHandler(stream=sys.stderr)
    console.setLevel(level)
    console.setFormatter(
        _ColorFormatter(LOG_FORMAT, DATE_FORMAT, use_color=sys.stderr.isatty())
    )
    root.addHandler(console)

    # ---- 文件（按天滚动，保留 14 天） ----
    log_dir: Path = PROJECT_ROOT / "var" / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.TimedRotatingFileHandler(
            filename=str(log_dir / "finrag.log"),
            when="midnight",
            backupCount=14,
            encoding="utf-8",
            delay=True,
        )
        file_handler.suffix = "%Y-%m-%d"
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
        root.addHandler(file_handler)
    except OSError:  # pragma: no cover - 文件系统只读等极端情况
        root.warning("日志文件目录不可写，仅启用控制台日志：%s", log_dir)

    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    _configured = True
    root.debug("日志系统初始化完成 env=%s level=%s", settings.app_env, level)
    return root


def get_logger(name: str) -> logging.Logger:
    """获取模块级 logger。

    用法::

        logger = get_logger(__name__)
        logger.info("知识库入库完成，共 %d 条", n)
    """
    return logging.getLogger(name)


def reset_logging() -> None:
    """重置日志配置标记（仅供测试使用）。"""
    global _configured
    _configured = False


__all__ = ["setup_logging", "get_logger", "reset_logging", "LOG_FORMAT"]
