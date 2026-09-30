"""生成演示 / 压测用合成语料。

⚠️ 重要声明
-----------
本脚本生成的是 **合成演示数据**：条文体裁仿照金融监管制度，但**不是任何真实
法规原文**，标题均带「演示数据集」标识，不得用于合规判断或业务决策。

真实课题验收必须使用**公开真实制度文本**：从中国人民银行（pbc.gov.cn）、
国家金融监督管理总局、中国证监会、中国政府网等官方网站下载公开文件，
放入 ``data/corpus/`` 后执行::

    python scripts/ingest.py --path data/corpus

本脚本的唯一用途：在没有网络、拿不到真实语料时，快速把知识库填充到
1000+ 知识点，用于验证检索性能、响应时延、分页与统计展示是否达标。

用法::

    python scripts/generate_demo_corpus.py                 # 默认 60 篇，约 1300+ 条
    python scripts/generate_demo_corpus.py --documents 80  # 自定义篇数
    python scripts/generate_demo_corpus.py --out data/demo # 自定义输出目录
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path
from typing import Dict, List, Tuple

from _common import PROJECT_ROOT, banner, info, kv, success

DISCLAIMER_HEAD = "【合成演示文本·非真实法规】\n"
DISCLAIMER_TAIL = (
    "--------------------------------------------------------------------\n"
    "【演示数据集 · 合成文本】\n"
    "本文件由 scripts/generate_demo_corpus.py 自动生成，条文体裁仿照金融监管制度，\n"
    "但不是任何真实法规原文，仅用于系统功能与性能测试，不得用于合规判断。\n"
    "真实课题验收请从监管官网下载公开制度文件。\n"
)

# ---------------------------------------------------------------------- 词库
BUSINESS_LINES: Dict[str, Dict[str, List[str]]] = {
    "银行": {
        "issuers": ["中国人民银行", "国家金融监督管理总局"],
        "prefixes": ["银发", "金规"],
        "topics": [
            "商业银行流动性风险管理", "商业银行资本管理办法实施", "商业银行内部控制",
            "商业银行信贷资产风险分类", "商业银行大额风险暴露管理", "商业银行流动性覆盖率",
            "商业银行同业业务规范", "商业银行理财业务监督", "商业银行存款利率自律",
            "商业银行小微企业金融服务",
        ],
    },
    "证券": {
        "issuers": ["中国证券监督管理委员会"],
        "prefixes": ["证监发", "证监会公告"],
        "topics": [
            "证券公司投资银行业务", "上市公司信息披露", "证券公司融资融券业务",
            "证券投资者适当性管理", "证券公司风险控制指标", "上市公司重大资产重组",
            "证券公司自营业务", "证券基金经营机构信息技术管理", "科创板股票发行上市审核",
            "证券市场程序化交易管理",
        ],
    },
    "保险": {
        "issuers": ["国家金融监督管理总局", "中国保险行业协会"],
        "prefixes": ["金规", "保监发"],
        "topics": [
            "保险公司偿付能力管理", "人身保险产品信息披露", "保险资金运用管理",
            "保险公司内部控制", "保险中介市场管理", "财产保险公司条款费率",
            "保险公司关联交易管理", "农业保险承保理赔管理", "商业养老保险业务",
            "保险公司销售行为管理",
        ],
    },
    "基金": {
        "issuers": ["中国证券监督管理委员会", "中国证券投资基金业协会"],
        "prefixes": ["证监发", "中基协发"],
        "topics": [
            "公开募集证券投资基金运作", "私募投资基金监督管理", "基金管理公司合规管理",
            "基金销售机构监督管理", "证券投资基金信息披露", "基金中基金指引",
            "私募资产管理业务运作", "基金投资顾问业务", "公募基金流动性风险管理",
            "基金托管业务管理",
        ],
    },
    "支付结算": {
        "issuers": ["中国人民银行"],
        "prefixes": ["银发", "银办发"],
        "topics": [
            "非银行支付机构监督管理", "支付机构客户备付金存管", "银行卡收单业务管理",
            "条码支付业务规范", "预付卡业务管理", "支付清算系统运行管理",
            "跨境人民币支付业务", "个人银行账户分类管理", "支付机构反洗钱与反恐怖融资",
            "小额批量支付系统业务",
        ],
    },
    "反洗钱": {
        "issuers": ["中国人民银行", "国家金融监督管理总局"],
        "prefixes": ["银发", "金规"],
        "topics": [
            "金融机构客户身份识别", "金融机构大额交易和可疑交易报告",
            "金融机构反洗钱内部控制", "法人金融机构洗钱风险自评估",
            "受益所有人身份识别", "金融机构反恐怖融资管理",
            "支付机构反洗钱义务履行", "证券期货业反洗钱工作",
            "保险业反洗钱管理", "反洗钱监督管理与检查",
        ],
    },
}

CHAPTERS = [
    "第一章 总则",
    "第二章 治理与职责",
    "第三章 业务规则",
    "第四章 风险控制",
    "第五章 信息披露与报告",
    "第六章 监督检查",
    "第七章 法律责任",
    "第八章 附则",
]

# 条文模板：(小标题, 正文模板)  ——  {} 为可替换槽位
CLAUSE_TEMPLATES: List[Tuple[str, str]] = [
    ("制定依据", "为规范{topic}，防范金融风险，保护金融消费者合法权益，根据《中华人民共和国{topic_law}》等法律法规，制定本{doc_kind}。"),
    ("适用范围", "本{doc_kind}适用于在中华人民共和国境内依法设立的{subject}及其分支机构开展{topic}相关业务的活动。"),
    ("定义", "本{doc_kind}所称{topic}，是指{subject}在经营过程中，对{topic}相关风险进行识别、计量、监测和控制的全过程管理活动。"),
    ("基本原则", "{subject}开展{topic}应当遵循依法合规、审慎经营、风险可控、权责清晰的原则，不得损害金融消费者合法权益。"),
    ("治理架构", "{subject}董事会应当承担{topic}的最终责任，高级管理层负责组织实施，并指定专门部门负责日常管理工作。"),
    ("专门部门", "{subject}应当设立或者指定专门部门负责{topic}，配备与业务规模相适应的专职人员，人员数量不得少于3人。"),
    ("制度要求", "{subject}应当建立与自身业务规模和复杂程度相适应的{topic}管理制度，并至少每{cycle}评估一次制度的有效性。"),
    ("系统支持", "{subject}应当建立满足{topic}需要的管理信息系统，实现数据的集中管理与自动监测，系统数据保存期限不得少于{retention}年。"),
    ("限额管理", "{subject}应当对{topic}设置限额，限额一经确定不得随意突破；确需调整的，应当经高级管理层审批并留存书面记录。"),
    ("压力测试", "{subject}应当至少每{cycle}开展一次{topic}压力测试，压力测试结果应当作为制定风险偏好和限额的重要依据。"),
    ("报告义务", "{subject}应当在每{cycle}结束后{report_days}个工作日内，向监管部门报送{topic}相关报表及分析报告。"),
    ("重大事项报告", "发生可能对{topic}产生重大影响的事项，{subject}应当在{event_days}小时内向监管部门报告，并说明事件情况、影响程度和已采取的措施。"),
    ("客户告知", "{subject}开展{topic}相关业务，应当以显著方式向客户提示相关风险，并留存客户确认记录，记录保存期限不得少于{retention}年。"),
    ("投资者适当性", "{subject}应当对客户的风险承受能力进行评估，将适当的产品销售给适当的客户，不得向风险承受能力不足的客户销售高于其风险等级的产品。"),
    ("信息披露", "{subject}应当通过官方网站等渠道披露{topic}相关信息，披露内容应当真实、准确、完整、及时，不得有虚假记载、误导性陈述或者重大遗漏。"),
    ("内部控制", "{subject}应当将{topic}纳入内部控制体系，建立事前防范、事中控制、事后监督的全流程控制机制。"),
    ("内部审计", "{subject}内部审计部门应当至少每{cycle}对{topic}进行一次专项审计，审计报告应当报送董事会和高级管理层。"),
    ("关联交易", "{subject}开展{topic}涉及关联交易的，应当按照商业原则进行，不得优于对非关联方同类交易的条件，并按规定履行审批与披露程序。"),
    ("外包管理", "{subject}将{topic}相关业务外包的，应当对外包服务商进行尽职调查，签订书面合同明确双方权利义务，并持续监督外包服务的质量与安全。"),
    ("数据安全", "{subject}应当采取加密、脱敏、访问控制等技术措施保护{topic}相关数据安全，防止数据泄露、篡改或者丢失。"),
    ("应急管理", "{subject}应当制定{topic}相关应急预案，并至少每{cycle}组织一次应急演练，演练情况应当形成书面记录。"),
    ("监督检查", "监管部门有权对{subject}的{topic}情况进行现场检查和非现场监管，{subject}应当予以配合，如实提供有关文件和资料。"),
    ("监管措施", "{subject}违反本{doc_kind}规定的，监管部门可以采取责令改正、监管谈话、出具警示函、责令暂停相关业务等监管措施。"),
    ("行政处罚", "{subject}违反本{doc_kind}规定，依法应当给予行政处罚的，由监管部门依照《中华人民共和国行政处罚法》等法律法规予以处罚。"),
    ("责任追究", "{subject}未按照规定履行{topic}职责，造成重大损失或者严重不良影响的，监管部门可以依法追究相关责任人员的责任。"),
    ("整改要求", "{subject}应当在收到监管意见后{rectify_days}个工作日内完成整改，并将整改情况书面报告监管部门；不能按期完成的，应当说明理由并申请延期。"),
    ("档案管理", "{subject}应当妥善保存{topic}相关业务档案，档案保存期限自业务关系终止之日起不得少于{retention}年。"),
    ("培训要求", "{subject}应当每年至少组织一次{topic}专项培训，培训对象应当覆盖相关业务人员与管理人员。"),
    ("考核评价", "{subject}应当将{topic}执行情况纳入绩效考核体系，考核结果与相关人员的薪酬、晋升挂钩。"),
    ("解释权", "本{doc_kind}由{issuer}负责解释。"),
]

LAWS = ["中国人民银行法", "商业银行法", "证券法", "保险法", "证券投资基金法", "反洗钱法", "银行业监督管理法"]
CYCLES = ["季度", "半年", "年"]
RETENTIONS = ["5", "10", "15", "20"]


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="生成演示用合成制度语料（非真实法规）")
    parser.add_argument("--documents", type=int, default=60, help="生成文档篇数（默认 60）")
    parser.add_argument("--min-articles", type=int, default=18, help="每篇最少条文数")
    parser.add_argument("--max-articles", type=int, default=26, help="每篇最多条文数")
    parser.add_argument("--out", type=str, default="data/demo", help="输出目录")
    parser.add_argument("--seed", type=int, default=20260101, help="随机种子（保证可复现）")
    return parser.parse_args()


def build_document(
    index: int,
    line: str,
    topic: str,
    rng: random.Random,
    min_articles: int,
    max_articles: int,
) -> Tuple[str, str]:
    """构造一篇合成制度文本，返回 ``(文件名, 正文)``。"""
    config = BUSINESS_LINES[line]
    issuer = rng.choice(config["issuers"])
    prefix = rng.choice(config["prefixes"])
    year = rng.choice([2021, 2022, 2023, 2024])
    number = rng.randint(1, 60)
    doc_no = f"{prefix}〔{year}〕{number}号"
    doc_kind = rng.choice(["办法", "规定", "指引", "通知"])
    subject = rng.choice(["商业银行", "证券公司", "保险公司", "基金管理公司", "支付机构", "金融机构"])
    publish_month = rng.randint(1, 10)
    effective_month = min(12, publish_month + rng.randint(1, 2))

    title = f"演示数据集 {topic}管理{doc_kind}"

    header = (
        f"{title}\n"
        f"{doc_no}\n"
        f"{issuer}\n"
        f"发布日期：{year}年{publish_month}月1日\n"
        f"施行日期：{year}年{effective_month}月1日\n"
    )

    article_count = rng.randint(min_articles, max_articles)
    templates = CLAUSE_TEMPLATES[:]
    rng.shuffle(templates)

    lines: List[str] = []
    chapter_index = 0
    numerals = "一二三四五六七八九十"
    per_chapter = max(3, article_count // len(CHAPTERS) + 1)

    for position in range(article_count):
        if position % per_chapter == 0 and chapter_index < len(CHAPTERS):
            lines.append("")
            lines.append(CHAPTERS[chapter_index])
            chapter_index += 1

        article_no = f"第{numerals[min(position, len(numerals) - 1)]}条" if position < 10 else f"第{position + 1}条"
        heading, template = templates[position % len(templates)]
        body = template.format(
            topic=topic,
            topic_law=rng.choice(LAWS),
            doc_kind=doc_kind,
            subject=subject,
            issuer=issuer,
            cycle=rng.choice(CYCLES),
            retention=rng.choice(RETENTIONS),
            report_days=rng.choice([5, 10, 15]),
            event_days=rng.choice([2, 4, 24]),
            rectify_days=rng.choice([15, 30, 60]),
        )
        lines.append(f"{article_no}　【{heading}】{body}")

    lines.append("")
    lines.append(f"第{article_count + 1}条　【施行日期】本{doc_kind}自{year}年{effective_month}月1日起施行。")

    content = DISCLAIMER_HEAD + header + "\n".join(lines) + "\n\n" + DISCLAIMER_TAIL
    safe_topic = topic.replace("/", "_")
    file_name = f"demo_{index:03d}_{line}_{safe_topic}.txt"
    return file_name, content


def main() -> int:
    """主流程。"""
    args = parse_args()
    banner("生成演示用合成语料")
    info("注意：生成的是合成演示数据，不是真实法规，仅用于功能与性能测试。")

    rng = random.Random(args.seed)
    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = (PROJECT_ROOT / out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    lines = list(BUSINESS_LINES.keys())
    generated = 0
    total_articles = 0

    for index in range(1, args.documents + 1):
        line = lines[(index - 1) % len(lines)]
        topics = BUSINESS_LINES[line]["topics"]
        topic = topics[((index - 1) // len(lines)) % len(topics)]
        file_name, content = build_document(
            index, line, topic, rng, args.min_articles, args.max_articles
        )
        (out_dir / file_name).write_text(content, encoding="utf-8")
        generated += 1
        total_articles += content.count("条　【")

    kv("输出目录", out_dir)
    kv("生成文档数", generated)
    kv("估算知识点数", total_articles, total_articles >= 1000)
    print()
    success("生成完成。下一步执行入库：")
    print(f"    python scripts/ingest.py --path {out_dir.relative_to(PROJECT_ROOT)}")
    print()
    info("提醒：验收前请用监管官网下载的真实公开制度文件替换本目录内容。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
