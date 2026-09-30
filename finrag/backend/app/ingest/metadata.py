"""制度元数据抽取。

从制度原文中自动抽取**文号、发文机关、发布日期、施行日期、失效状态**
等结构化字段，写回 MySQL ``kb_document``，并冗余到 ``kb_chunk`` 与
Milvus 标量字段，支撑按时效性过滤检索。

全部为纯函数 + 正则/词表规则，不依赖大模型，结果可复现、可单测。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core.constants import (
    BUSINESS_LINE_KEYWORDS,
    DOC_TYPE_KEYWORDS,
    BusinessLine,
    DocStatus,
    DocType,
)
from app.core.logging_conf import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------- 词表
KNOWN_ISSUERS: Sequence[str] = (
    "全国人民代表大会常务委员会",
    "最高人民法院",
    "最高人民检察院",
    "国务院办公厅",
    "国务院",
    "中国人民银行",
    "国家金融监督管理总局",
    "中国银行保险监督管理委员会",
    "中国证券监督管理委员会",
    "中国保险监督管理委员会",
    "国家外汇管理局",
    "财政部",
    "国家发展和改革委员会",
    "商务部",
    "工业和信息化部",
    "公安部",
    "国家市场监督管理总局",
    "中国证券业协会",
    "中国银行业协会",
    "中国保险行业协会",
    "中国期货业协会",
    "中国证券投资基金业协会",
    "上海证券交易所",
    "深圳证券交易所",
    "北京证券交易所",
)

# 文号前缀 -> 发文机关
DOC_NO_PREFIX_ISSUER: Dict[str, str] = {
    "国发": "国务院",
    "国办发": "国务院办公厅",
    "银发": "中国人民银行",
    "银办发": "中国人民银行办公厅",
    "银保监发": "中国银行保险监督管理委员会",
    "金规": "国家金融监督管理总局",
    "金办发": "国家金融监督管理总局办公厅",
    "证监发": "中国证券监督管理委员会",
    "证监会公告": "中国证券监督管理委员会",
    "保监发": "中国保险监督管理委员会",
    "汇发": "国家外汇管理局",
    "财金": "财政部",
    "发改财金": "国家发展和改革委员会",
    "法释": "最高人民法院",
}

# 业务条线关键词（用于打分）
ISSUER_HINT_MAX_SCAN = 3000  # 只在文首若干字符内找发文机关，避免正文提及误判

RE_CN_DATE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
RE_CN_DATE_LOOSE = re.compile(r"(\d{4})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})")
RE_DOC_NO = re.compile(
    r"([\u4e00-\u9fa5]{1,10})\s*〔\s*(\d{4})\s*〕\s*第?\s*(\d{1,5})\s*号"
)
# 宽松形态：尚未经过清洗的半角/方括号/全角括号文号，如 银发(2020)第15号
RE_DOC_NO_LOOSE = re.compile(
    r"([\u4e00-\u9fa5]{1,10})\s*[（(\[【]\s*(\d{4})\s*[)）\]】]\s*第?\s*(\d{1,5})\s*号"
)
RE_DOC_NO_ALT = re.compile(
    r"([\u4e00-\u9fa5]{1,10})\s*(?:公告|令)\s*〔?\s*(\d{4})\s*〕?\s*第?\s*(\d{1,5})\s*号?"
)

# 发布日期上下文
RE_PUBLISH = re.compile(
    r"(?:发布|印发|公布|签发|通过|批准)[^。\n]{0,20}?(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)"
)
# 施行日期上下文
RE_EFFECTIVE = re.compile(
    r"(?:自|从)\s*(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)\s*(?:起)?\s*"
    r"(?:施行|实施|执行|生效|发生法律效力)"
)
RE_EFFECTIVE_IMMEDIATE = re.compile(r"自(?:公布|发布|印发)之日起?\s*(?:施行|实施|执行|生效)")
# 失效日期上下文
RE_EXPIRY = re.compile(
    r"(?:自|从)\s*(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)\s*(?:起)?\s*"
    r"(?:废止|失效|停止执行|停止施行)"
)
RE_REPEAL_WORDS = re.compile(r"(予以废止|现予废止|决定废止|同时废止|宣布失效|停止执行)")

# 标题：以「办法/规定/通知/指引/意见/细则/条例/决定/公告/规则/准则/规范」结尾的一行
RE_TITLE_LIKE = re.compile(
    r"^[\u4e00-\u9fa5A-Za-z0-9（）()《》、，,：:\-—\s]{6,80}"
    r"(?:办法|规定|通知|指引|意见|细则|条例|决定|公告|规则|准则|规范|法律|解释)"
    r"（?(?:试行|暂行)?）?[\u4e00-\u9fa5A-Za-z0-9）)]{0,10}$"
)
RE_TITLE_STRIP = re.compile(r"^[《〈【\s]+|[》〉】\s]+$")
# 整行被括号包裹的标记行（如「【演示数据集】」「[附件]」），不是标题
RE_MARKER_LINE = re.compile(r"^[【\[〖（(][^】\]〗）)]{0,40}[】\]〗）)]$")


@dataclass
class DocumentMeta:
    """抽取出的制度元数据。"""

    title: str = ""
    doc_no: str = ""
    issuer: str = ""
    doc_type: str = DocType.NORMATIVE.value
    business_line: str = BusinessLine.GENERAL.value
    status: str = DocStatus.UNKNOWN.value
    publish_date: Optional[date] = None
    effective_date: Optional[date] = None
    expiry_date: Optional[date] = None
    tags: List[str] = field(default_factory=list)
    source_url: str = ""
    confidence: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """转换为可序列化字典（日期转 ISO 字符串）。"""
        data = asdict(self)
        for key in ("publish_date", "effective_date", "expiry_date"):
            value = data.get(key)
            data[key] = value.isoformat() if isinstance(value, date) else None
        return data


# ------------------------------------------------------------------ 基础解析
def parse_cn_date(text: str) -> Optional[date]:
    """从中文字符串中解析出第一个日期。"""
    if not text:
        return None
    for pattern in (RE_CN_DATE, RE_CN_DATE_LOOSE):
        match = pattern.search(text)
        if not match:
            continue
        try:
            year, month, day = (int(g) for g in match.groups())
            return date(year, month, day)
        except ValueError:
            continue
    return None


def extract_doc_no(text: str) -> str:
    """抽取文号，如 ``银发〔2020〕1号``。

    依次尝试标准六角括号、宽松括号（(2020)/[2020]/【2020】）、
    「公告/令 + 年份 + 序号」三种形态，因此对未清洗的原文同样有效。
    """
    if not text:
        return ""
    head = text[:ISSUER_HINT_MAX_SCAN]
    for pattern in (RE_DOC_NO, RE_DOC_NO_LOOSE, RE_DOC_NO_ALT):
        match = pattern.search(head)
        if match:
            return f"{match.group(1)}〔{match.group(2)}〕{match.group(3)}号"
    return ""


def extract_title(text: str, file_name: str = "") -> str:
    """抽取制度名称。

    优先级：显式「标题：」标签 > 《》书名号内容 > 形似标题的行 > 文件名。
    """
    if text:
        head = text[:ISSUER_HINT_MAX_SCAN]
        labelled = re.search(r"(?:标\s*题|文件名称)\s*[:：]\s*(.+)", head)
        if labelled:
            candidate = RE_TITLE_STRIP.sub("", labelled.group(1).strip())
            if 4 <= len(candidate) <= 100:
                return candidate

        for line in head.splitlines()[:20]:
            stripped = line.strip()
            if len(stripped) < 4 or len(stripped) > 100:
                continue
            # 跳过纯标记行与分隔线，它们不是制度名称
            if RE_MARKER_LINE.match(stripped) or set(stripped) <= set("-—_=*·. "):
                continue
            book = re.match(r"^《(.{2,80})》$", stripped)
            if book:
                return book.group(1).strip()
            if RE_TITLE_LIKE.match(stripped) and "第" not in stripped[:2]:
                return clean_title_line(stripped)

    return strip_file_name(file_name)


def clean_title_line(line: str) -> str:
    """清理标题行两端的书名号/括号等装饰符号。"""
    return RE_TITLE_STRIP.sub("", line.strip())


def strip_file_name(file_name: str) -> str:
    """从文件名推断标题：去掉扩展名、序号前缀与机器可读前缀。

    例：``demo_001_银行_商业银行流动性风险管理.txt``
        -> ``银行 商业银行流动性风险管理``
    """
    if not file_name:
        return ""
    name = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", file_name)
    # 去掉形如 ``demo_001_`` / ``doc-12_`` 的机器前缀
    name = re.sub(r"^[A-Za-z]{2,12}[\s_\-]+\d{1,6}[\s_\-]+", "", name)
    name = re.sub(r"^[\s\d._\-—]+", "", name)
    name = re.sub(r"[\s_\-—]+$", "", name)
    return name.replace("_", " ").strip()


def extract_issuer(text: str, doc_no: str = "") -> str:
    """抽取发文机关：先按文号前缀映射，再按词表在文首匹配。"""
    if doc_no:
        for prefix, issuer in sorted(
            DOC_NO_PREFIX_ISSUER.items(), key=lambda kv: -len(kv[0])
        ):
            if doc_no.startswith(prefix):
                return issuer

    if not text:
        return ""
    head = text[:ISSUER_HINT_MAX_SCAN]
    best: Tuple[int, str] = (len(head) + 1, "")
    for issuer in KNOWN_ISSUERS:
        position = head.find(issuer)
        if position >= 0 and position < best[0]:
            best = (position, issuer)
    return best[1]


def extract_publish_date(text: str) -> Optional[date]:
    """抽取发布日期。"""
    if not text:
        return None
    match = RE_PUBLISH.search(text[: ISSUER_HINT_MAX_SCAN * 2])
    if match:
        parsed = parse_cn_date(match.group(1))
        if parsed:
            return parsed
    # 回退：文首第一个日期通常即发布日期
    return parse_cn_date(text[:1000])


def extract_effective_date(text: str) -> Optional[date]:
    """抽取施行日期。"""
    if not text:
        return None
    match = RE_EFFECTIVE.search(text)
    if match:
        return parse_cn_date(match.group(1))
    if RE_EFFECTIVE_IMMEDIATE.search(text):
        # 「自公布之日起施行」——由调用方回填发布日期
        return None
    return None


def extract_expiry_date(text: str) -> Optional[date]:
    """抽取失效日期（若明示废止时间）。"""
    if not text:
        return None
    match = RE_EXPIRY.search(text)
    if match:
        return parse_cn_date(match.group(1))
    return None


def infer_status(
    text: str,
    effective_date: Optional[date] = None,
    expiry_date: Optional[date] = None,
    publish_date: Optional[date] = None,
    today: Optional[date] = None,
) -> str:
    """推断时效性状态。

    优先级：明示失效日期 > 明示废止用语 > 施行日期未到（尚未生效）
    > 有施行日期（现行有效）> 状态待核。
    """
    moment = today or date.today()

    if expiry_date and expiry_date <= moment:
        return DocStatus.REPEALED.value

    head = (text or "")[: ISSUER_HINT_MAX_SCAN * 2]
    if RE_REPEAL_WORDS.search(head):
        return DocStatus.REPEALED.value
    if re.search(r"(?:修订|修正|修改)后(?:重新)?(?:发布|公布)", head):
        return DocStatus.AMENDED.value

    anchor = effective_date or publish_date
    if anchor:
        if anchor > moment:
            return DocStatus.PENDING.value
        return DocStatus.EFFECTIVE.value

    if RE_EFFECTIVE_IMMEDIATE.search(text or ""):
        return DocStatus.EFFECTIVE.value
    return DocStatus.UNKNOWN.value


def infer_business_line(title: str, text: str = "") -> str:
    """按关键词命中次数推断业务条线。"""
    haystack = f"{title}\n{(text or '')[:4000]}"
    scores: Dict[str, int] = {}
    for line, keywords in BUSINESS_LINE_KEYWORDS.items():
        # 标题命中权重更高
        score = sum(haystack.count(kw) for kw in keywords)
        score += sum(title.count(kw) * 3 for kw in keywords) if title else 0
        if score:
            scores[line] = score
    if not scores:
        return BusinessLine.GENERAL.value
    return max(scores.items(), key=lambda kv: kv[1])[0]


def infer_doc_type(title: str, text: str = "", doc_no: str = "") -> str:
    """推断文件类型/效力层级。

    分层判定，避免正文里「根据《中华人民共和国XX法》」这类**引用**把
    本文件的效力层级误判为法律：

    1. 先看标题与文号（强信号）：``中华人民共和国XX法`` / ``国务院令`` /
       ``法释`` / ``XX监督管理委员会令`` / 行业协会自律规则；
    2. 再看文首 300 字符（发文标识区）；
    3. 最后才看全文的规范性文件关键词。
    """
    clean_title = (title or "").strip()
    doc_no = doc_no or ""

    # ---- 第 1 层：标题 / 文号的强信号 ----
    if re.match(r"^中华人民共和国.{2,20}(法|法典|条例)$", clean_title) or "主席令" in doc_no:
        return DocType.LAW.value
    if "国务院令" in doc_no or re.match(r"^国务院.{0,10}(条例|规定|办法)$", clean_title):
        return DocType.ADMIN_REGULATION.value
    if doc_no.startswith("法释") or "最高人民法院" in clean_title[:20]:
        return DocType.JUDICIAL.value
    if re.search(r"监督管理委员会令|人民银行令|行长令", doc_no + clean_title):
        return DocType.DEPARTMENT_RULE.value
    if re.search(r"(协会|交易所).{0,10}(自律|规则|准则|指引)", clean_title):
        return DocType.SELF_REGULATION.value

    # ---- 第 2 层：文首发文标识区 ----
    header = f"{clean_title}\n{doc_no}\n{(text or '')[:300]}"
    for doc_type in (DocType.JUDICIAL.value, DocType.ADMIN_REGULATION.value,
                     DocType.DEPARTMENT_RULE.value, DocType.SELF_REGULATION.value):
        for keyword in DOC_TYPE_KEYWORDS[doc_type]:
            if keyword in header:
                return doc_type

    # ---- 第 3 层：全文规范性文件关键词 ----
    haystack = f"{clean_title}\n{doc_no}\n{(text or '')[:2000]}"
    for keyword in DOC_TYPE_KEYWORDS[DocType.NORMATIVE.value]:
        if keyword in haystack:
            return DocType.NORMATIVE.value
    return DocType.OTHER.value


def extract_tags(
    title: str,
    text: str = "",
    business_line: str = "",
    doc_type: str = "",
    top_n: int = 8,
) -> List[str]:
    """抽取知识点标签：业务条线 + 类型 + 关键词。

    使用 jieba 关键词抽取（若未安装则退化为词频统计），
    标签写入 ``kb_tag`` / ``kb_chunk_tag``，支撑标签分类管理。
    """
    tags: List[str] = []
    if business_line and business_line != BusinessLine.GENERAL.value:
        tags.append(business_line)
    if doc_type and doc_type != DocType.OTHER.value:
        tags.append(doc_type)

    keywords = _extract_keywords(title, text, top_n=top_n)
    for word in keywords:
        if word not in tags and len(word) >= 2:
            tags.append(word)
    return tags[: top_n + 2]


def _extract_keywords(title: str, text: str, top_n: int = 8) -> List[str]:
    """关键词抽取，优先 jieba.analyse，缺失时退化为词频。

    抽取结果会再过一遍共享停用词表：jieba 自带的停用词表不覆盖
    「应当」「不得」「相关」这类制度文本高频虚词，若不过滤会污染标签。
    """
    from app.retrieval.tokenizer import STOPWORDS

    corpus = f"{title}。{(text or '')[:6000]}"
    if not corpus.strip():
        return []

    def _is_noise(word: str) -> bool:
        return word in STOPWORDS or len(word) < 2

    try:
        import jieba.analyse

        candidates = jieba.analyse.extract_tags(corpus, topK=top_n * 3)
        keywords = [w for w in candidates if not _is_noise(w)]
        if keywords:
            return keywords[:top_n]
    except Exception:  # pragma: no cover - jieba 缺失分支
        pass

    from app.retrieval.tokenizer import tokenize

    counts: Dict[str, int] = {}
    for token in tokenize(corpus):
        if not _is_noise(token):
            counts[token] = counts.get(token, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [word for word, _ in ranked[:top_n]]


def effective_timestamp(value: Optional[date], default: int = 0) -> int:
    """日期转秒级时间戳；``None`` -> ``default``。"""
    if value is None:
        return default
    try:
        return int(datetime(value.year, value.month, value.day).timestamp())
    except (ValueError, OverflowError, OSError):  # pragma: no cover
        return default


def extract_metadata(
    text: str,
    file_name: str = "",
    source_url: str = "",
    today: Optional[date] = None,
) -> DocumentMeta:
    """从文档正文一次性抽取全部元数据。

    :param text: 已清洗的全文。
    :param file_name: 原始文件名（标题回退来源）。
    :param source_url: 来源链接（公开数据溯源）。
    :param today: 用于状态推断的"今天"，便于测试注入。
    """
    title = extract_title(text, file_name)
    doc_no = extract_doc_no(text)
    issuer = extract_issuer(text, doc_no)
    publish_date = extract_publish_date(text)
    effective_date = extract_effective_date(text)
    if effective_date is None and RE_EFFECTIVE_IMMEDIATE.search(text or ""):
        # 「自公布之日起施行」
        effective_date = publish_date
    expiry_date = extract_expiry_date(text)
    status = infer_status(text, effective_date, expiry_date, publish_date, today)
    business_line = infer_business_line(title, text)
    doc_type = infer_doc_type(title, text, doc_no)
    tags = extract_tags(title, text, business_line, doc_type)

    confidence = {
        "title": 1.0 if title else 0.0,
        "doc_no": 1.0 if doc_no else 0.0,
        "issuer": 1.0 if issuer else 0.0,
        "publish_date": 1.0 if publish_date else 0.0,
        "effective_date": 1.0 if effective_date else 0.0,
        "status": 0.0 if status == DocStatus.UNKNOWN.value else 1.0,
    }

    return DocumentMeta(
        title=title,
        doc_no=doc_no,
        issuer=issuer,
        doc_type=doc_type,
        business_line=business_line,
        status=status,
        publish_date=publish_date,
        effective_date=effective_date,
        expiry_date=expiry_date,
        tags=tags,
        source_url=source_url,
        confidence=confidence,
    )


def next_expiry_hint(effective_date: Optional[date], years: int = 3) -> Optional[date]:
    """按「有效期 N 年」惯例给出到期提醒日期（用于制度复审）。"""
    if not effective_date:
        return None
    try:
        return effective_date.replace(year=effective_date.year + years) - timedelta(days=1)
    except ValueError:  # 2 月 29 日
        return effective_date.replace(year=effective_date.year + years, day=28) - timedelta(days=1)


__all__ = [
    "DocumentMeta",
    "extract_metadata",
    "extract_doc_no",
    "extract_title",
    "extract_issuer",
    "extract_publish_date",
    "extract_effective_date",
    "extract_expiry_date",
    "infer_status",
    "infer_business_line",
    "infer_doc_type",
    "extract_tags",
    "parse_cn_date",
    "effective_timestamp",
    "next_expiry_hint",
    "strip_file_name",
    "KNOWN_ISSUERS",
    "DOC_NO_PREFIX_ISSUER",
]
