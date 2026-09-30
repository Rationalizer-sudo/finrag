"""文本清洗与规范化。

金融制度文档（尤其是政府网站 PDF 与扫描件）常带页眉页脚、页码、
多余空白、繁体混排、全角半角混用等问题。清洗质量直接决定检索与
问答准确率，因此本模块的规则全部是**纯函数**，便于单测与回归。

对应课程思政要点「严谨处理金融制度数据的准确性」。
"""

from __future__ import annotations

import re
from collections import Counter
from typing import List, Sequence

# ---------------------------------------------------------------- 正则表
# 连续空白（含全角空格、制表符）
RE_MULTI_SPACE = re.compile(r"[ \t\u3000\u00a0]{2,}")
# 3 个以上换行压缩为 2 个
RE_MULTI_NEWLINE = re.compile(r"\n{3,}")
# 页码行：单独成行的「- 12 -」「第 12 页 / 共 30 页」「12」
RE_PAGE_NUMBER = re.compile(
    r"^\s*[-—–－]?\s*(?:第\s*)?\d{1,4}\s*(?:页)?\s*(?:[/|]\s*共\s*\d{1,4}\s*页)?\s*[-—–－]?\s*$"
)
# 控制字符（保留 \n 与 \t）
RE_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# 软连字符
RE_SOFT_HYPHEN = re.compile(r"[\u00ad\u200b-\u200f\ufeff]")
# 全角英文字母与数字（FF10-FF19 / FF21-FF3A / FF41-FF5A）-> 半角。
# 刻意不转换全角标点（，。！？；：（）），因为它们是中文正式文本的标准写法，
# 转换后会降低制度原文的可读性。
RE_FULLWIDTH_ALNUM = re.compile(r"[\uff10-\uff19\uff21-\uff3a\uff41-\uff5a]")
# 「自 X 年 X 月 X 日起施行」等日期表达里的空白
RE_DATE_SPACE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
# 章节序号粘连：「第十二条」被拆为「第 十二 条」
RE_CLAUSE_SPACE = re.compile(
    r"第\s*([零一二三四五六七八九十百千两\d]{1,8})\s*(条|章|节|款|项)"
)
# 文号：机关简称 + 括号年份 + 序号 + 号
RE_DOC_NO = re.compile(
    r"([\u4e00-\u9fa5A-Za-z]{1,12})\s*[（(【\[]\s*(\d{4})\s*[)）】\]]\s*第?\s*(\d{1,5})\s*号"
)

# 常见页眉页脚特征词（政府网站下载 PDF 的水印/版权行）
FOOTER_HINTS: Sequence[str] = (
    "http://",
    "https://",
    "www.",
    "版权所有",
    "下载于",
    "本文件为电子版",
    "第 1 页 共",
)


def _to_halfwidth(match: re.Match) -> str:
    """全角字母/数字转半角。"""
    return chr(ord(match.group(0)) - 0xFEE0)


def normalize_unicode(text: str) -> str:
    """把全角英文字母/数字统一为半角（如 ``２０２０`` -> ``2020``）。

    刻意**不触碰**中文标点，也不使用 ``unicodedata.normalize('NFKC')``：
    NFKC 会改写 CJK 兼容表意文字与中文标点，损害制度原文的保真度。
    """
    if not text:
        return ""
    return RE_FULLWIDTH_ALNUM.sub(_to_halfwidth, text)


def strip_control_chars(text: str) -> str:
    """移除控制字符与零宽字符。"""
    if not text:
        return ""
    return RE_SOFT_HYPHEN.sub("", RE_CONTROL.sub("", text))


def normalize_clause_spacing(text: str) -> str:
    """修正条文号被空格拆散的问题：``第 十二 条`` -> ``第十二条``。"""
    if not text:
        return ""
    return RE_CLAUSE_SPACE.sub(lambda m: f"第{m.group(1)}{m.group(2)}", text)


def normalize_dates(text: str) -> str:
    """规整中文日期中的多余空白：``2020 年 1 月 1 日`` -> ``2020年1月1日``。

    同时吃掉日期两侧的 ASCII 空格——中文正式文本不会用空格分隔日期与上下文，
    这些空格基本都是 PDF 抽取产生的噪声。
    """
    if not text:
        return ""
    pattern = re.compile(r"[ \t]*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日[ \t]*")
    return pattern.sub(lambda m: f"{m.group(1)}年{m.group(2)}月{m.group(3)}日", text)


def normalize_doc_no(text: str) -> str:
    """统一文号括号为中文六角括号：``银发(2020)第1号`` -> ``银发〔2020〕1号``。"""
    if not text:
        return ""
    return RE_DOC_NO.sub(
        lambda m: f"{m.group(1)}〔{m.group(2)}〕{m.group(3)}号", text
    )


def collapse_whitespace(text: str) -> str:
    """压缩多余空白与空行，并去掉每行首尾空白。

    制度文本的缩进不承载语义（PDF 抽取的缩进更是噪声），
    因此逐行 strip 可显著改善条文正文的规整度。
    """
    if not text:
        return ""
    stripped_lines = [line.strip() for line in text.splitlines()]
    result = "\n".join(stripped_lines)
    result = RE_MULTI_SPACE.sub(" ", result)
    result = RE_MULTI_NEWLINE.sub("\n\n", result)
    return result.strip()


def is_page_number_line(line: str) -> bool:
    """判断一行是否为页码。"""
    stripped = line.strip()
    if not stripped or len(stripped) > 24:
        return False
    return bool(RE_PAGE_NUMBER.match(stripped))


def is_footer_line(line: str) -> bool:
    """判断一行是否为页脚/水印行。"""
    stripped = line.strip()
    if not stripped:
        return False
    lowered = stripped.lower()
    return any(hint.lower() in lowered for hint in FOOTER_HINTS)


def remove_repeated_lines(pages: Sequence[str], min_repeat: int = 3) -> List[str]:
    """删除在多页中重复出现的页眉/页脚行。

    政府网站下载的制度文件经常每页都带相同的机构名与页码，
    若不清除会被切分进条文，污染检索结果。

    :param pages: 每页文本。
    :param min_repeat: 出现次数达到该值即视为页眉页脚。
    """
    if not pages:
        return []
    if len(pages) < min_repeat:
        return [collapse_whitespace(page) for page in pages]

    counter: Counter = Counter()
    for page in pages:
        for line in {ln.strip() for ln in page.splitlines() if ln.strip()}:
            if len(line) <= 120:
                counter[line] += 1

    repeated = {line for line, count in counter.items() if count >= min_repeat}
    cleaned: List[str] = []
    for page in pages:
        kept = [
            line
            for line in page.splitlines()
            if line.strip() not in repeated
            and not is_page_number_line(line)
            and not is_footer_line(line)
        ]
        cleaned.append(collapse_whitespace("\n".join(kept)))
    return cleaned


def clean_text(text: str, *, drop_numeric_lines: bool = False) -> str:
    """一站式文本清洗。

    流程：NFKC 规范化 -> 去控制字符 -> 去页码/页脚行 ->
    修正条文号与日期空格 -> 统一文号括号 -> 压缩空白。

    :param text: 原始文本。
    :param drop_numeric_lines: 是否删除「纯数字行」（扫描件页码常见）。
    """
    if not text:
        return ""
    result = strip_control_chars(normalize_unicode(text))

    lines: List[str] = []
    for line in result.splitlines():
        if is_page_number_line(line) or is_footer_line(line):
            continue
        if drop_numeric_lines and line.strip().isdigit():
            continue
        lines.append(line)
    result = "\n".join(lines)

    result = normalize_doc_no(result)
    result = normalize_clause_spacing(result)
    result = normalize_dates(result)
    return collapse_whitespace(result)


def dedupe_paragraphs(text: str) -> str:
    """去除完全重复的段落（PDF 重复渲染常见），保持原有顺序。"""
    if not text:
        return ""
    seen: set[str] = set()
    kept: List[str] = []
    for block in re.split(r"\n\s*\n", text):
        key = re.sub(r"\s+", "", block)
        if not key or key in seen:
            continue
        seen.add(key)
        kept.append(block.strip())
    return "\n\n".join(kept)


def estimate_tokens(text: str) -> int:
    """粗略估算词数：中文按字、英文按词。

    用于统计与切分预估，不引入分词依赖，保证纯函数可测。
    """
    if not text:
        return 0
    cn = len(re.findall(r"[\u4e00-\u9fa5]", text))
    en = len(re.findall(r"[A-Za-z]+", text))
    digits = len(re.findall(r"\d+", text))
    return cn + en + digits


__all__ = [
    "clean_text",
    "collapse_whitespace",
    "dedupe_paragraphs",
    "estimate_tokens",
    "is_footer_line",
    "is_page_number_line",
    "normalize_clause_spacing",
    "normalize_dates",
    "normalize_doc_no",
    "normalize_unicode",
    "remove_repeated_lines",
    "strip_control_chars",
]
