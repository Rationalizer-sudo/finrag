"""中文分词与停用词处理。

BM25 关键词检索、标签抽取、可信度评分都依赖分词结果，
因此统一在此处收口，保证三种检索链路口径一致。
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Iterable, List, Set

# 金融制度场景停用词（在通用停用词基础上补充制度文本高频虚词）
STOPWORDS: Set[str] = {
    "的", "了", "和", "是", "在", "有", "与", "或", "及", "等", "对", "为", "以", "被",
    "并", "而", "其", "该", "本", "此", "由", "从", "向", "将", "已", "应", "可", "能",
    "不", "无", "未", "非", "如", "若", "则", "但", "且", "因", "所", "之", "者", "也",
    "任何", "按照", "根据", "依据", "关于", "以及", "并且", "或者", "如果", "可以",
    "应当", "必须", "不得", "予以", "进行", "相关", "有关", "下列", "以上", "以下",
    "我们", "你们", "他们", "什么", "怎么", "如何", "请问", "一下", "的话",
    " ", "\n", "\t", "", "，", "。", "、", "；", "：", "？", "！", "（", "）", "《", "》",
}
# 需要从词元中剔除的标点/符号
RE_NON_WORD = re.compile(r"^[\W_]+$", re.UNICODE)
RE_SPACES = re.compile(r"\s+")

# 常见无效单字（避免噪声）
MIN_TOKEN_LEN = 1


@lru_cache(maxsize=1)
def _get_jieba() -> object:
    """惰性加载 jieba（首次加载会构建词典缓存，故做单例）。"""
    import jieba

    # 补充金融领域词典，提升「资本充足率」「反洗钱」等术语的切分质量
    for term in (
        "资本充足率", "反洗钱", "客户身份识别", "可疑交易", "大额交易", "受益所有人",
        "信息披露", "投资者适当性", "净值化管理", "刚性兑付", "影子银行", "同业业务",
        "存款准备金", "流动性覆盖率", "净稳定资金比例", "杠杆率", "拨备覆盖率",
        "不良贷款率", "关联交易", "内幕交易", "操纵市场", "资产管理产品",
        "非银行支付机构", "备付金", "系统性金融风险", "宏观审慎管理", "金融消费者权益保护",
    ):
        jieba.add_word(term, freq=1000)
    return jieba


def tokenize(text: str, *, drop_stopwords: bool = True) -> List[str]:
    """中文分词并过滤停用词/标点。

    :param text: 待分词文本。
    :param drop_stopwords: 是否过滤停用词。
    :returns: 词元列表（保持原顺序，不做去重）。
    """
    if not text:
        return []
    cleaned = RE_SPACES.sub(" ", text)
    jieba = _get_jieba()
    raw = jieba.lcut(cleaned, cut_all=False)  # type: ignore[attr-defined]

    tokens: List[str] = []
    for token in raw:
        word = token.strip().lower()
        if not word or len(word) < MIN_TOKEN_LEN:
            continue
        if RE_NON_WORD.match(word):
            continue
        if drop_stopwords and word in STOPWORDS:
            continue
        tokens.append(word)
    return tokens


def tokenize_query(text: str) -> List[str]:
    """查询分词：额外做去重，避免长问句里重复词拉高 BM25 权重。"""
    seen: Set[str] = set()
    result: List[str] = []
    for token in tokenize(text):
        if token not in seen:
            seen.add(token)
            result.append(token)
    return result


def term_set(text: str) -> Set[str]:
    """返回文本的词元集合，用于集合相似度计算。"""
    return set(tokenize(text))


def jaccard(left: Iterable[str], right: Iterable[str]) -> float:
    """Jaccard 相似度，用于证据覆盖度评估。"""
    set_l, set_r = set(left), set(right)
    if not set_l or not set_r:
        return 0.0
    union = set_l | set_r
    return len(set_l & set_r) / len(union) if union else 0.0


def coverage(needle: Iterable[str], haystack: Iterable[str]) -> float:
    """needle 中有多少比例出现在 haystack 中（0~1）。"""
    set_n, set_h = set(needle), set(haystack)
    if not set_n:
        return 0.0
    return len(set_n & set_h) / len(set_n)


def top_terms(text: str, limit: int = 20) -> List[str]:
    """按词频返回高频词，用于标签与关键词展示。"""
    counts: dict = {}
    for token in tokenize(text):
        counts[token] = counts.get(token, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [word for word, _ in ranked[:limit]]


def tokenizer_name() -> str:
    """返回当前分词器名称，便于在响应中标注实现。"""
    return "jieba"


__all__ = [
    "tokenize",
    "tokenize_query",
    "term_set",
    "jaccard",
    "coverage",
    "top_terms",
    "tokenizer_name",
    "STOPWORDS",
]
