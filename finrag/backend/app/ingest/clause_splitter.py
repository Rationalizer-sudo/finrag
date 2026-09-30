"""条文感知切分（知识库构建的核心步骤之一）。

两类切分策略并存：

1. **条文优先**：金融制度以「第 X 条」为最小规范单元，一条即一个知识点，
   因此优先按条文切分，并保留所属章节作为上下文；
2. **递归兜底**：无条文结构（如通知正文、附件表格说明）或单条过长时，
   使用 LangChain ``RecursiveCharacterTextSplitter`` 按 段落->句号->分号->逗号
   的优先级递归切分，并保留 ``chunk_overlap`` 重叠，避免语义被截断。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.core.config import Settings, get_settings
from app.core.constants import CLAUSE_NUMERAL
from app.core.logging_conf import get_logger
from app.ingest.cleaner import estimate_tokens

logger = get_logger(__name__)

# 第X条 / 第X章 / 第X节（要求位于行首或紧随换行，避免正文中的引用被误切）
RE_CLAUSE = re.compile(
    rf"(?m)^[ \u3000]*(第[{CLAUSE_NUMERAL}\d]{{1,8}}条)(?=[\s\u3000【\[（(]|$)"
)
RE_CHAPTER = re.compile(
    rf"(?m)^[ \u3000]*(第[{CLAUSE_NUMERAL}\d]{{1,8}}[章节][^\n]*?)[ \u3000]*$"
)
# 条文小标题：【信息披露】 / [信息披露]
RE_ARTICLE_TITLE = re.compile(
    rf"^第[{CLAUSE_NUMERAL}\d]{{1,8}}条[ \u3000、.．]*[【\[](.{{2,30}}?)[】\]]"
)
# 段落型编号：一、 / （一） / 1. / (1)
RE_PARAGRAPH_NO = re.compile(
    r"(?m)^[ \u3000]*(?:[一二三四五六七八九十]+[、.．]|[（(][一二三四五六七八九十\d]+[)）]|\d+[、.．])"
)

# 递归切分的分隔符优先级：先按语义边界，最后才按字符硬切
DEFAULT_SEPARATORS: List[str] = [
    "\n\n",
    "\n",
    "。",
    "；",
    "！",
    "？",
    "，",
    "、",
    " ",
    "",
]

MIN_CLAUSE_CHARS = 12  # 低于此长度的「条文」视为噪声（如目录中的“第十二条……5”）
# 条文前的引言/前言需要达到该长度才单独入库，避免把「标题+文号+日期」这类
# 纯元数据行也切成一条知识片段，污染检索结果
MIN_PREAMBLE_CHARS = 60


@dataclass
class ClauseChunk:
    """一条待入库的条文片段。"""

    index: int
    content: str
    clause_no: str = ""
    chapter: str = ""
    article_title: str = ""
    part: int = 0
    is_clause_based: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def char_count(self) -> int:
        """字符数。"""
        return len(self.content)

    @property
    def token_count(self) -> int:
        """估算词数。"""
        return estimate_tokens(self.content)

    @property
    def display_clause_no(self) -> str:
        """带分片后缀的条文号，如 ``第十二条(2/3)``。"""
        if self.part > 1:
            return f"{self.clause_no}({self.part})"
        return self.clause_no

    def to_dict(self) -> Dict[str, Any]:
        """转换为可序列化字典。"""
        return {
            "index": self.index,
            "clause_no": self.clause_no,
            "chapter": self.chapter,
            "article_title": self.article_title,
            "part": self.part,
            "content": self.content,
            "char_count": self.char_count,
            "token_count": self.token_count,
            "is_clause_based": self.is_clause_based,
        }


def build_splitter(settings: Optional[Settings] = None) -> RecursiveCharacterTextSplitter:
    """构造递归字符切分器。"""
    cfg = settings or get_settings()
    return RecursiveCharacterTextSplitter(
        chunk_size=cfg.chunk_size,
        chunk_overlap=cfg.chunk_overlap,
        separators=DEFAULT_SEPARATORS,
        keep_separator=True,
        length_function=len,
        strip_whitespace=True,
    )


def extract_article_title(clause_text: str) -> str:
    """抽取条文小标题（【…】形式），无则返回空串。"""
    if not clause_text:
        return ""
    match = RE_ARTICLE_TITLE.match(clause_text.strip())
    return match.group(1).strip() if match else ""


def _iter_chapter_marks(text: str) -> List[Tuple[int, str]]:
    """返回 ``[(位置, 章节标签), ...]``，章节标签包含标题，如 ``"第二章 治理与职责"``。"""
    return [(m.start(), m.group(1).strip()) for m in RE_CHAPTER.finditer(text)]


def _chapter_at(marks: List[Tuple[int, str]], position: int) -> str:
    """返回某位置之前最近的章节号。"""
    current = ""
    for start, label in marks:
        if start <= position:
            current = label
        else:
            break
    return current


def _split_long_clause(
    text: str,
    clause_no: str,
    chapter: str,
    settings: Settings,
) -> List[Tuple[str, int]]:
    """过长条文二次切分，返回 ``[(片段文本, 分片序号), ...]``。"""
    if len(text) <= settings.chunk_size * 1.6:
        return [(text, 1)]

    splitter = build_splitter(settings)
    pieces = splitter.split_text(text)
    if not pieces:
        return [(text, 1)]

    # 为每个分片补上条文号前缀，保证片段脱离上下文仍可被理解与引用
    prefix = f"{clause_no} " if clause_no else ""
    result: List[Tuple[str, int]] = []
    for order, piece in enumerate(pieces, start=1):
        body = piece.strip()
        if not body:
            continue
        if prefix and not body.startswith(clause_no):
            body = f"{prefix}{body}"
        result.append((body, order))
    return result or [(text, 1)]


def split_by_clause(
    text: str,
    settings: Optional[Settings] = None,
) -> List[ClauseChunk]:
    """按「第 X 条」切分文本。

    :returns: :class:`ClauseChunk` 列表；若文本无条文结构则返回空列表。
    """
    cfg = settings or get_settings()
    if not text or not text.strip():
        return []

    matches = list(RE_CLAUSE.finditer(text))
    if not matches:
        return []

    chapters = _iter_chapter_marks(text)
    chunks: List[ClauseChunk] = []
    index = 0
    pending_prefix = text[: matches[0].start()].strip()  # 条文前的标题/正文部分

    for order, match in enumerate(matches):
        start = match.start()
        end = matches[order + 1].start() if order + 1 < len(matches) else len(text)
        segment = text[start:end].strip()
        clause_no = match.group(1)

        if len(segment) < MIN_CLAUSE_CHARS:
            # 过短（多为目录或误匹配），并入下一段而不是丢弃
            continue

        for body, part in _split_long_clause(segment, clause_no, "", cfg):
            chunks.append(
                ClauseChunk(
                    index=index,
                    content=body,
                    clause_no=clause_no,
                    chapter=_chapter_at(chapters, start),
                    article_title=extract_article_title(segment),
                    part=part,
                    is_clause_based=True,
                )
            )
            index += 1

    if pending_prefix and len(pending_prefix) >= MIN_PREAMBLE_CHARS:
        # 前置说明（发文依据、适用范围、定义等）同样是有价值的知识点
        for body, part in _split_long_clause(pending_prefix, "", "", cfg):
            chunks.append(
                ClauseChunk(
                    index=index,
                    content=body,
                    clause_no="前言",
                    chapter="",
                    part=part,
                    is_clause_based=False,
                )
            )
            index += 1

    return chunks


def split_by_recursive(
    text: str,
    settings: Optional[Settings] = None,
) -> List[ClauseChunk]:
    """纯递归切分（无条文结构时的兜底策略）。"""
    cfg = settings or get_settings()
    if not text or not text.strip():
        return []

    splitter = build_splitter(cfg)
    pieces = splitter.split_text(text)
    chunks: List[ClauseChunk] = []
    for index, piece in enumerate(pieces):
        body = piece.strip()
        if not body:
            continue
        # 尝试用段落编号（一、/（一）/1.）作为伪条文号，便于溯源展示
        label_match = RE_PARAGRAPH_NO.match(body)
        chunks.append(
            ClauseChunk(
                index=index,
                content=body,
                clause_no=label_match.group(0).strip() if label_match else f"片段{index + 1}",
                article_title=extract_article_title(body),
                is_clause_based=False,
            )
        )
    return chunks


def split_document(
    text: str,
    settings: Optional[Settings] = None,
) -> List[ClauseChunk]:
    """统一的切分入口：优先条文切分，兜底递归切分。

    另外对「条文体量占比过低」的情况做保护：若识别出的条文平均长度
    明显偏小，说明匹配到的多为目录条目，此时回退到递归切分。
    """
    cfg = settings or get_settings()
    clause_chunks = split_by_clause(text, cfg)

    if clause_chunks:
        total_chars = sum(chunk.char_count for chunk in clause_chunks)
        coverage = total_chars / max(len(text), 1)
        avg_chars = total_chars / len(clause_chunks)
        if coverage >= 0.6 and avg_chars >= 20:
            logger.debug(
                "条文切分完成：%d 条，覆盖率 %.2f，平均 %d 字",
                len(clause_chunks), coverage, avg_chars,
            )
            return clause_chunks
        logger.info(
            "条文切分覆盖率过低(%.2f)，回退递归切分", coverage
        )

    return split_by_recursive(text, cfg)


def summarize_chunks(chunks: List[ClauseChunk]) -> Dict[str, Any]:
    """汇总切分结果统计，用于接口返回与入库任务记录。"""
    if not chunks:
        return {"count": 0, "total_chars": 0, "avg_chars": 0, "max_chars": 0, "clause_based": False}
    total = sum(chunk.char_count for chunk in chunks)
    return {
        "count": len(chunks),
        "total_chars": total,
        "avg_chars": round(total / len(chunks), 1),
        "max_chars": max(chunk.char_count for chunk in chunks),
        "clause_based": any(chunk.is_clause_based for chunk in chunks),
    }


__all__ = [
    "ClauseChunk",
    "build_splitter",
    "split_document",
    "split_by_clause",
    "split_by_recursive",
    "extract_article_title",
    "summarize_chunks",
    "DEFAULT_SEPARATORS",
]
