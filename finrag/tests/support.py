"""测试基础设施：环境准备、数据播种、容器装配。

设计原则：**测试不使用任何替代生产逻辑的开关**，只是把外部依赖
（Milvus / 通义千问）通过依赖注入换成 :mod:`tests.fakes` 中的替身，
被验证的仍然是真实的清洗、切分、检索、融合、溯源、评分与接口代码。
"""

from __future__ import annotations

import os
import shutil
import uuid
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# 环境变量必须在导入 app.* 之前设置：app.core.config 会在模块导入时实例化配置
# ---------------------------------------------------------------------------
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL_OVERRIDE", "sqlite://")
os.environ.setdefault("MYSQL_ECHO", "false")
os.environ.setdefault("DASHSCOPE_API_KEY", "")
os.environ.setdefault("OCR_ENABLED", "true")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"

import sys  # noqa: E402

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import Settings, reset_settings_cache  # noqa: E402
from app.core.constants import (  # noqa: E402
    BusinessLine,
    DocStatus,
    DocType,
)
from app.core.logging_conf import reset_logging  # noqa: E402
from app.db.models import KnowledgeChunk, KnowledgeDocument  # noqa: E402
from app.db.milvus_store import ClauseVector  # noqa: E402
from app.db.session import init_db, reset_engine, session_scope  # noqa: E402
from app.ingest.metadata import effective_timestamp  # noqa: E402
from app.services.container import Container, build_container  # noqa: E402
from tests.fakes import FakeChatClient, FakeEmbedder, FakeVectorStore  # noqa: E402

_TEMP_DIRS: List[str] = []


def make_settings(**overrides: Any) -> Settings:
    """构造测试配置（默认落 SQLite、关掉真实 API Key）。"""
    base: Dict[str, Any] = {
        "app_env": "test",
        "database_url_override": "sqlite://",
        "dashscope_api_key": "",
        "mysql_echo": False,
        "chunk_size": 500,
        "chunk_overlap": 80,
        "retrieve_top_k": 5,
        "retrieve_candidate_k": 20,
        "retrieve_mode": "hybrid",
        "rrf_k": 60,
        "ocr_enabled": True,
        "upload_dir": "./var/test-uploads",
    }
    base.update(overrides)
    return Settings(**base)


def temp_dir(prefix: str = "test-") -> Path:
    """创建临时目录（位于工作区 ``var/test-tmp/`` 下，进程退出时统一清理）。

    刻意不使用系统临时目录：部分受限环境下系统临时目录不可写，
    而项目工作区始终可写。
    """
    base = PROJECT_ROOT / "var" / "test-tmp"
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{prefix}{uuid.uuid4().hex[:10]}"
    path.mkdir(parents=True, exist_ok=True)
    _TEMP_DIRS.append(str(path))
    return path


def cleanup_temp_dirs() -> None:
    """清理全部临时目录。"""
    while _TEMP_DIRS:
        path = _TEMP_DIRS.pop()
        shutil.rmtree(path, ignore_errors=True)


def reset_app_state() -> None:
    """重置全局单例（配置 / 引擎 / 日志），保证用例之间互不污染。"""
    reset_settings_cache()
    reset_engine()
    reset_logging()


@contextmanager
def fresh_database():
    """提供一个已建表的干净数据库（SQLite 内存库）。"""
    reset_app_state()
    init_db(drop=True)
    try:
        yield
    finally:
        reset_engine()


# ---------------------------------------------------------------------------
# 数据播种
# ---------------------------------------------------------------------------


def seed_document(
    db: Any,
    *,
    title: str = "商业银行流动性风险管理办法",
    doc_no: str = "银发〔2020〕15号",
    issuer: str = "中国人民银行",
    status: str = DocStatus.EFFECTIVE.value,
    business_line: str = BusinessLine.BANKING.value,
    doc_type: str = DocType.NORMATIVE.value,
    effective_date: Optional[date] = date(2020, 6, 1),
    expiry_date: Optional[date] = None,
    source_url: str = "https://www.pbc.gov.cn/example",
    ocr_used: bool = False,
    clauses: Sequence[Tuple[str, str, str]] = (),
) -> Tuple[KnowledgeDocument, List[KnowledgeChunk]]:
    """写入一篇文档及其条文。

    :param clauses: ``[(条文号, 小标题, 正文), ...]``
    """
    document = KnowledgeDocument(
        doc_no=doc_no,
        title=title,
        issuer=issuer,
        doc_type=doc_type,
        business_line=business_line,
        status=status,
        publish_date=date(2020, 3, 1),
        effective_date=effective_date,
        expiry_date=expiry_date,
        source_url=source_url,
        file_name=f"{title}.txt",
        file_type=".txt",
        file_hash=uuid.uuid4().hex,
        ocr_used=ocr_used,
        parser_name="text:utf-8",
        chunk_count=0,
    )
    db.add(document)
    db.flush()

    effective_ts = effective_timestamp(effective_date)
    expiry_ts = effective_timestamp(expiry_date)
    rows: List[KnowledgeChunk] = []
    for index, (clause_no, article_title, content) in enumerate(clauses):
        row = KnowledgeChunk(
            document_id=document.id,
            chunk_index=index,
            clause_no=clause_no,
            article_title=article_title,
            content=content,
            content_hash=uuid.uuid4().hex,
            char_count=len(content),
            token_count=len(content.split()),
            status=status,
            effective_date=effective_date,
            expiry_date=expiry_date,
            effective_ts=effective_ts,
            expiry_ts=expiry_ts,
            business_line=business_line,
            doc_type=doc_type,
        )
        db.add(row)
        rows.append(row)
    document.chunk_count = len(rows)
    db.flush()
    return document, rows


def seed_vectors(
    store: FakeVectorStore,
    embedder: FakeEmbedder,
    chunks: Iterable[KnowledgeChunk],
) -> int:
    """把 MySQL 条文写入假向量库（模拟入库流水线第 6 步）。"""
    rows = list(chunks)
    if not rows:
        return 0
    vectors = embedder.embed_documents([row.content for row in rows])
    payload = [
        ClauseVector(
            pk=int(row.id),
            document_id=int(row.document_id),
            embedding=vectors[index],
            chunk_index=int(row.chunk_index or 0),
            status=row.status,
            business_line=row.business_line,
            doc_type=row.doc_type,
            effective_ts=int(row.effective_ts or 0),
            expiry_ts=int(row.expiry_ts or 0),
            clause_no=row.clause_no or "",
        )
        for index, row in enumerate(rows)
    ]
    return store.upsert_clauses(payload)


# ---------------------------------------------------------------------------
# 容器装配
# ---------------------------------------------------------------------------

DEFAULT_ANSWER = (
    "依据《商业银行流动性风险管理办法》第十二条，商业银行应当至少每季度"
    "向监管机构报送流动性风险指标。[1]\n\n"
    "以上回答依据公开制度文本生成，具体业务办理请以正式发文及监管口径为准。"
)


def make_test_container(
    settings: Optional[Settings] = None,
    *,
    answer: str = DEFAULT_ANSWER,
    dim: int = 64,
) -> Tuple[Container, FakeVectorStore, FakeEmbedder, FakeChatClient]:
    """构造注入了替身的依赖容器。

    :returns: ``(容器, 假向量库, 假向量器, 假对话客户端)``
    """
    cfg = settings or make_settings()
    store = FakeVectorStore(dim=dim)
    embedder = FakeEmbedder(dim=dim)
    chat = FakeChatClient(answer=answer)
    container = build_container(
        cfg, vector_store=store, embedder=embedder, chat_client=chat
    )
    return container, store, embedder, chat


SAMPLE_CLAUSES: List[Tuple[str, str, str]] = [
    ("第一条", "制定依据", "为加强商业银行流动性风险管理，维护银行体系安全稳健运行，根据《中华人民共和国商业银行法》等法律法规，制定本办法。"),
    ("第二条", "适用范围", "本办法适用于在中华人民共和国境内依法设立的商业银行，包括中资商业银行、外商独资银行和中外合资银行。"),
    ("第三条", "治理架构", "商业银行董事会应当承担流动性风险管理的最终责任，高级管理层负责组织实施流动性风险管理工作。"),
    ("第四条", "专门部门", "商业银行应当设立或者指定专门部门负责流动性风险管理，配备与业务规模相适应的专职人员。"),
    ("第五条", "压力测试", "商业银行应当至少每季度开展一次流动性风险压力测试，压力测试结果应当作为制定风险偏好和限额的重要依据。"),
    ("第六条", "限额管理", "商业银行应当对流动性风险设置限额，限额一经确定不得随意突破；确需调整的，应当经高级管理层审批。"),
    ("第七条", "系统支持", "商业银行应当建立满足流动性风险管理需要的信息系统，实现数据的集中管理与自动监测。"),
    ("第八条", "报告义务", "商业银行应当至少每季度向监管机构报送流动性风险指标及分析报告，报送时间不得迟于季度结束后十个工作日。"),
    ("第九条", "关联交易", "商业银行开展流动性风险管理涉及关联交易的，应当按照商业原则进行，不得优于对非关联方同类交易的条件。"),
    ("第十条", "信息披露", "商业银行应当通过官方网站等渠道披露流动性风险管理相关信息，披露内容应当真实、准确、完整、及时。"),
    ("第十一条", "监管措施", "商业银行违反本办法规定的，监管机构可以采取责令改正、监管谈话、出具警示函等监管措施。"),
    ("第十二条", "法律责任", "商业银行违反本办法规定，依法应当给予行政处罚的，由监管机构依照有关法律法规予以处罚。"),
    ("第十三条", "附则", "本办法自2020年6月1日起施行，由中国人民银行负责解释。"),
]

REPEALED_CLAUSES: List[Tuple[str, str, str]] = [
    ("第一条", "制定依据", "为规范商业银行同业业务，根据有关法律法规，制定本规定。"),
    ("第二条", "过渡期安排", "本规定自2014年5月1日起施行，过渡期至2014年12月31日止。"),
    ("第三条", "废止条款", "本规定自2018年1月1日起废止，相关业务按照新的监管规定执行。"),
]


def sample_knowledge_base(db: Any) -> Dict[str, Any]:
    """播种一套完整的测试知识库（1 篇现行有效 + 1 篇已废止）。"""
    doc, chunks = seed_document(db, clauses=SAMPLE_CLAUSES)
    stale_doc, stale_chunks = seed_document(
        db,
        title="商业银行同业业务管理规定（已废止）",
        doc_no="银发〔2014〕127号",
        status=DocStatus.REPEALED.value,
        effective_date=date(2014, 5, 1),
        expiry_date=date(2018, 1, 1),
        source_url="https://www.pbc.gov.cn/example-old",
        clauses=REPEALED_CLAUSES,
    )
    insurance_doc, insurance_chunks = seed_document(
        db,
        title="保险公司偿付能力管理规定",
        doc_no="金规〔2021〕1号",
        issuer="国家金融监督管理总局",
        business_line=BusinessLine.INSURANCE.value,
        effective_date=date(2021, 3, 1),
        clauses=[
            ("第一条", "制定依据", "为加强保险公司偿付能力监管，根据《中华人民共和国保险法》制定本规定。"),
            ("第二条", "核心偿付能力充足率", "保险公司核心偿付能力充足率不得低于百分之五十，综合偿付能力充足率不得低于百分之一百。"),
        ],
    )
    return {
        "document": doc,
        "chunks": chunks,
        "stale_document": stale_doc,
        "stale_chunks": stale_chunks,
        "insurance_document": insurance_doc,
        "insurance_chunks": insurance_chunks,
        "all_chunks": list(chunks) + list(stale_chunks) + list(insurance_chunks),
    }


__all__ = [
    "PROJECT_ROOT",
    "BACKEND_DIR",
    "make_settings",
    "temp_dir",
    "cleanup_temp_dirs",
    "reset_app_state",
    "fresh_database",
    "seed_document",
    "seed_vectors",
    "make_test_container",
    "sample_knowledge_base",
    "session_scope",
    "SAMPLE_CLAUSES",
    "REPEALED_CLAUSES",
    "DEFAULT_ANSWER",
]
