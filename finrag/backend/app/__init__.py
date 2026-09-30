"""FinRAG 后端应用包。

基于 RAG 的金融制度类知识问答系统
=================================

分层结构::

    app.core        配置、日志、异常、领域常量
    app.db          MySQL(SQLAlchemy) + Milvus 双存储访问层
    app.schemas     Pydantic 请求/响应模型
    app.ingest      文档解析、OCR、清洗、条文切分、元数据抽取、入库流水线
    app.llm         通义千问 Chat / Embedding 接入（LangChain）
    app.retrieval   向量检索 + BM25 + RRF 混合检索 + 时效过滤
    app.rag         Prompt 组装、答案生成、条文溯源、可信度评分
    app.services    业务服务层（知识库/问答/文档/用户数据）
    app.api         FastAPI 路由层
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
