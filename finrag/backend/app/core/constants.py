"""跨模块共用的领域常量。

集中定义文档状态、业务条线、文档类型等枚举，避免各层出现字符串字面量漂移。
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, List


class DocStatus(str, Enum):
    """金融制度时效性状态（对应差异化要求「时效性管理」）。"""

    EFFECTIVE = "现行有效"
    PENDING = "尚未生效"
    AMENDED = "已修订"
    REPEALED = "已废止"
    UNKNOWN = "状态待核"

    @classmethod
    def values(cls) -> List[str]:
        return [item.value for item in cls]

    @classmethod
    def valid_values(cls) -> List[str]:
        """可优先返回给用户的有效状态集合。"""
        return [cls.EFFECTIVE.value, cls.PENDING.value]


class DocType(str, Enum):
    """制度文件效力层级/类型。"""

    LAW = "法律"
    ADMIN_REGULATION = "行政法规"
    JUDICIAL = "司法解释"
    DEPARTMENT_RULE = "部门规章"
    NORMATIVE = "规范性文件"
    SELF_REGULATION = "自律规则"
    STANDARD = "国家标准"
    OTHER = "其他"

    @classmethod
    def values(cls) -> List[str]:
        return [item.value for item in cls]


class BusinessLine(str, Enum):
    """金融业务条线，用于知识库标签分类管理。"""

    BANKING = "银行"
    SECURITIES = "证券"
    INSURANCE = "保险"
    FUND = "基金"
    PAYMENT = "支付结算"
    ANTI_MONEY_LAUNDERING = "反洗钱"
    TRUST = "信托"
    FUTURES = "期货"
    GENERAL = "综合"

    @classmethod
    def values(cls) -> List[str]:
        return [item.value for item in cls]


class RetrievalMode(str, Enum):
    """检索模式。"""

    VECTOR = "vector"
    BM25 = "bm25"
    HYBRID = "hybrid"

    @classmethod
    def values(cls) -> List[str]:
        return [item.value for item in cls]


class IngestStatus(str, Enum):
    """入库任务状态（支撑「知识库增量更新」创新点）。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"

    @classmethod
    def values(cls) -> List[str]:
        return [item.value for item in cls]


class CredibilityLevel(str, Enum):
    """答案可信度等级。"""

    HIGH = "高"
    MEDIUM = "中"
    LOW = "低"
    UNRELIABLE = "不可信"

    @classmethod
    def values(cls) -> List[str]:
        return [item.value for item in cls]


# --------------------------------------------------------------------------
# 关键词 -> 标签/元数据的启发式映射（用于自动打标与元数据抽取）
# --------------------------------------------------------------------------
BUSINESS_LINE_KEYWORDS: Dict[str, List[str]] = {
    BusinessLine.BANKING.value: [
        "银行", "商业银行", "存款", "贷款", "信贷", "资本充足率", "拨备",
        "同业", "理财", "表外", "流动性", "存款准备金",
    ],
    BusinessLine.SECURITIES.value: [
        "证券", "股票", "上市公司", "信息披露", "内幕交易", "保荐",
        "承销", "交易所", "券商", "证券公司", "投资者适当性",
    ],
    BusinessLine.INSURANCE.value: [
        "保险", "保险资金", "保单", "偿付能力", "再保险", "保险中介",
        "人身保险", "财产保险",
    ],
    BusinessLine.FUND.value: [
        "基金", "公募", "私募", "基金管理人", "基金份额", "私募资产管理",
    ],
    BusinessLine.PAYMENT.value: [
        "支付", "清算", "结算", "第三方支付", "非银行支付", "备付金",
        "银行卡", "预付卡",
    ],
    BusinessLine.ANTI_MONEY_LAUNDERING.value: [
        "反洗钱", "洗钱", "客户身份识别", "可疑交易", "大额交易", "受益所有人",
    ],
    BusinessLine.TRUST.value: ["信托", "信托公司", "受托人", "信托计划"],
    BusinessLine.FUTURES.value: ["期货", "期权", "衍生品", "保证金交易", "期货公司"],
}

DOC_TYPE_KEYWORDS: Dict[str, List[str]] = {
    DocType.LAW.value: ["中华人民共和国", "主席令", "全国人民代表大会"],
    DocType.ADMIN_REGULATION.value: ["国务院令", "国务院关于", "行政法规"],
    DocType.JUDICIAL.value: ["最高人民法院", "司法解释", "最高人民检察院"],
    DocType.DEPARTMENT_RULE.value: ["部门规章", "令第", "行长令", "监督管理委员会令"],
    DocType.SELF_REGULATION.value: ["自律规则", "行业协会", "中国银行业协会", "中国证券业协会"],
    DocType.NORMATIVE.value: ["通知", "办法", "规定", "指引", "意见", "公告", "银发", "证监发"],
}

# 条文序号正则（覆盖「第一条」「第十二条」「第一百零三条」及「一、」「1.」等）
CLAUSE_NUMERAL = "零一二三四五六七八九十百千两"
CLAUSE_PATTERNS: List[str] = [
    rf"第[{CLAUSE_NUMERAL}\d]{{1,8}}条",
    rf"第[{CLAUSE_NUMERAL}\d]{{1,8}}章",
    rf"第[{CLAUSE_NUMERAL}\d]{{1,8}}节",
]

__all__ = [
    "DocStatus",
    "DocType",
    "BusinessLine",
    "RetrievalMode",
    "IngestStatus",
    "CredibilityLevel",
    "BUSINESS_LINE_KEYWORDS",
    "DOC_TYPE_KEYWORDS",
    "CLAUSE_PATTERNS",
    "CLAUSE_NUMERAL",
]
