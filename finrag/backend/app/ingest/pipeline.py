"""知识库入库流水线。

完整链路::

    解析(文本/PDF/docx/图片OCR)
      └─▶ 文本清洗
            └─▶ 元数据抽取(文号/机关/日期/时效/条线)
                  └─▶ 条文感知切分
                        └─▶ MySQL 落库(文档 + 条文 + 标签)
                              └─▶ 通义千问 embedding
                                    └─▶ Milvus 向量写入

增量更新（技术创新点）
----------------------
* 以**文件 SHA-256** 判重：内容未变则直接 ``skipped``，不做重复计算与写入；
* ``replace=True`` 时先删除该文档的旧条文与旧向量，再写入新版本（``upsert`` 语义），
  保证「同一制度只保留一份现行知识」；
* 向量写入失败不影响 MySQL 落库，任务标记 ``partial``，
  之后可用 ``scripts/ingest.py --rebuild-vectors`` 重建，避免一次网络抖动毁掉整批数据。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.constants import BusinessLine, DocStatus, DocType, IngestStatus
from app.core.errors import FinRAGError
from app.core.logging_conf import get_logger
from app.db import repository
from app.db.milvus_store import ClauseVector, MilvusVectorStore, get_vector_store
from app.db.models import IngestTask, KnowledgeChunk, KnowledgeDocument
from app.ingest import metadata as meta_tools
from app.ingest.clause_splitter import ClauseChunk, split_document, summarize_chunks
from app.ingest.cleaner import clean_text, dedupe_paragraphs
from app.ingest.loaders import ParsedDocument, load_document, sha256_text
from app.llm.embeddings import Embedder, QwenEmbedder

logger = get_logger(__name__)

# 清洗后的有效正文短于该长度即判定为无效文档（扫描噪声、空壳文件等），
# 避免把 1~2 个字的碎片写进知识库污染检索。
MIN_DOCUMENT_CHARS = 20


@dataclass
class IngestResult:
    """一次入库的完整结果。"""

    file_name: str = ""
    status: str = IngestStatus.PENDING.value
    stage: str = ""
    document_id: Optional[int] = None
    task_id: Optional[int] = None
    title: str = ""
    doc_no: str = ""
    issuer: str = ""
    doc_type: str = DocType.OTHER.value
    business_line: str = BusinessLine.GENERAL.value
    doc_status: str = DocStatus.UNKNOWN.value
    chunk_count: int = 0
    vector_count: int = 0
    ocr_used: bool = False
    parser_name: str = ""
    file_hash: str = ""
    cost_ms: int = 0
    message: str = ""
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """是否成功（含「内容未变而跳过」）。"""
        return self.status in (IngestStatus.SUCCESS.value, IngestStatus.SKIPPED.value)

    def to_dict(self) -> Dict[str, Any]:
        """转换为可序列化字典。"""
        return {
            "file_name": self.file_name,
            "status": self.status,
            "stage": self.stage,
            "document_id": self.document_id,
            "task_id": self.task_id,
            "title": self.title,
            "doc_no": self.doc_no,
            "issuer": self.issuer,
            "doc_type": self.doc_type,
            "business_line": self.business_line,
            "doc_status": self.doc_status,
            "chunk_count": self.chunk_count,
            "vector_count": self.vector_count,
            "ocr_used": self.ocr_used,
            "parser_name": self.parser_name,
            "file_hash": self.file_hash,
            "cost_ms": self.cost_ms,
            "message": self.message,
            "warnings": self.warnings,
        }


class IngestPipeline:
    """知识库入库流水线。"""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        vector_store: Optional[MilvusVectorStore] = None,
        embedder: Optional[Embedder] = None,
    ) -> None:
        self.settings: Settings = settings or get_settings()
        self.vector_store = vector_store or get_vector_store(self.settings)
        self.embedder: Embedder = embedder or QwenEmbedder(self.settings)

    # ==================================================================
    #  对外入口
    # ==================================================================
    def ingest_file(
        self,
        db: Session,
        path: str | Path,
        *,
        source_url: str = "",
        replace: bool = True,
        force: bool = False,
    ) -> IngestResult:
        """解析并入库一个文件。

        :param db: MySQL 会话（由调用方控制事务）。
        :param path: 文件路径。
        :param source_url: 公开来源链接（数据安全要求：仅使用公开数据）。
        :param replace: 同一文件已存在时是否覆盖旧版本。
        :param force: 内容哈希未变时是否强制重新入库。
        """
        file_path = Path(path)
        started = time.perf_counter()
        task = repository.create_ingest_task(
            db,
            task_no=self._new_task_no(),
            file_name=file_path.name,
            status=IngestStatus.RUNNING.value,
            stage="parsing",
        )
        db.commit()

        try:
            parsed = load_document(file_path, self.settings)
        except FinRAGError as exc:
            return self._fail(db, task, file_path.name, exc.message, started)

        result = self._ingest_parsed_document(
            db,
            parsed=parsed,
            task=task,
            source_url=source_url,
            replace=replace,
            force=force,
            started=started,
        )
        return result

    def ingest_text(
        self,
        db: Session,
        text: str,
        file_name: str,
        *,
        source_url: str = "",
        replace: bool = True,
        force: bool = False,
        doc_type: str = "",
        business_line: str = "",
        status: str = "",
    ) -> IngestResult:
        """直接入库文本内容（便于接口粘贴文本与批量脚本复用）。

        :param doc_type / business_line / status: 人工指定的元数据，
            非空时覆盖自动抽取结果（人工兜底优先于启发式规则）。
        """
        started = time.perf_counter()
        task = repository.create_ingest_task(
            db,
            task_no=self._new_task_no(),
            file_name=file_name,
            status=IngestStatus.RUNNING.value,
            stage="parsing",
        )
        db.commit()

        parsed = ParsedDocument(
            file_name=file_name,
            file_type=Path(file_name).suffix.lower() or ".txt",
            text=text or "",
            source_path="",
            parser_name="inline-text",
            file_hash=sha256_text(text or ""),
        )
        if not (text or "").strip():
            return self._fail(db, task, file_name, "文本内容为空", started)

        return self._ingest_parsed_document(
            db,
            parsed=parsed,
            task=task,
            source_url=source_url,
            replace=replace,
            force=force,
            started=started,
            override={
                "doc_type": doc_type,
                "business_line": business_line,
                "status": status,
            },
        )

    # ==================================================================
    #  核心流程
    # ==================================================================
    def _ingest_parsed_document(
        self,
        db: Session,
        *,
        parsed: ParsedDocument,
        task: IngestTask,
        source_url: str,
        replace: bool,
        force: bool,
        started: float,
        override: Optional[Dict[str, str]] = None,
    ) -> IngestResult:
        """解析结果 -> 清洗 -> 切分 -> 落库 -> 向量化。"""
        override = override or {}

        # ---- 1. 增量判重 ----
        # 判重分两级：
        #   a) 文件内容哈希一致  -> 内容没变
        #   b) 原始文件名一致    -> 同一制度的新版本（修订后重新上传）
        existing_by_hash = repository.get_document_by_hash(db, parsed.file_hash)
        existing = existing_by_hash or repository.get_document_by_file_name(
            db, parsed.file_name
        )

        if existing_by_hash is not None and not force and not replace:
            result = self._result_from_document(
                existing_by_hash, task, status=IngestStatus.SKIPPED.value,
                message="文件内容未变化（SHA-256 一致），已跳过入库。",
            )
            self._finish_task(db, task, IngestStatus.SKIPPED.value, "skipped",
                              message=result.message, document_id=existing_by_hash.id)
            return result

        # ---- 2. 清洗 ----
        self._set_stage(db, task, "cleaning")
        cleaned = dedupe_paragraphs(clean_text(parsed.text))
        if not cleaned.strip():
            return self._fail(
                db, task, parsed.file_name,
                "清洗后无有效文本（可能是空文件或扫描件未启用 OCR）", started,
            )
        if len(cleaned) < MIN_DOCUMENT_CHARS:
            return self._fail(
                db, task, parsed.file_name,
                f"文档有效内容仅 {len(cleaned)} 字，不足 {MIN_DOCUMENT_CHARS} 字，"
                "无法构成有效的制度知识片段",
                started,
            )

        # ---- 3. 元数据抽取 ----
        self._set_stage(db, task, "extracting-metadata")
        meta = meta_tools.extract_metadata(
            cleaned, file_name=parsed.file_name, source_url=source_url
        )
        for key, value in override.items():
            if value:
                setattr(meta, key, value)

        # ---- 4. 条文切分 ----
        self._set_stage(db, task, "splitting")
        clause_chunks = split_document(cleaned, self.settings)
        if not clause_chunks:
            return self._fail(db, task, parsed.file_name, "未切分出任何知识片段", started)
        stats = summarize_chunks(clause_chunks)
        repository.update_ingest_task(db, task, total_chunks=stats["count"])
        db.commit()

        # ---- 5. 落库 ----
        self._set_stage(db, task, "persisting")
        document = self._persist_document(
            db, parsed=parsed, meta=meta, clause_chunks=clause_chunks,
            existing=existing,
        )
        # ---- 6. 向量化 ----
        self._set_stage(db, task, "embedding")
        vector_ok, vector_count, vector_error = self._index_vectors(db, document, clause_chunks)

        # ---- 7. 收尾 ----
        cost_ms = int((time.perf_counter() - started) * 1000)
        if vector_ok:
            status = IngestStatus.SUCCESS.value
            message = (
                f"入库成功：{document.chunk_count} 条条文，"
                f"{vector_count} 条向量，耗时 {cost_ms}ms。"
            )
        else:
            status = IngestStatus.PARTIAL.value
            message = (
                f"条文已写入 MySQL（{document.chunk_count} 条），"
                f"但向量写入失败：{vector_error}。"
                "可稍后执行 scripts/ingest.py --rebuild-vectors 重建向量索引。"
            )

        self._finish_task(
            db, task, status, "done", message=message,
            document_id=document.id, processed_chunks=document.chunk_count,
            ocr_used=parsed.ocr_used, cost_ms=cost_ms,
        )
        self._invalidate_bm25()

        return IngestResult(
            file_name=parsed.file_name,
            status=status,
            stage="done",
            document_id=document.id,
            task_id=task.id,
            title=document.title,
            doc_no=document.doc_no,
            issuer=document.issuer,
            doc_type=document.doc_type,
            business_line=document.business_line,
            doc_status=document.status,
            chunk_count=document.chunk_count,
            vector_count=vector_count,
            ocr_used=parsed.ocr_used,
            parser_name=parsed.parser_name,
            file_hash=parsed.file_hash or "",
            cost_ms=cost_ms,
            message=message,
            warnings=list(parsed.warnings),
        )

    # ---------------------------------------------------------- 落库细节
    def _persist_document(
        self,
        db: Session,
        *,
        parsed: ParsedDocument,
        meta: meta_tools.DocumentMeta,
        clause_chunks: Sequence[ClauseChunk],
        existing: Optional[KnowledgeDocument],
    ) -> KnowledgeDocument:
        """写入文档与条文（含旧版本清理）。

        只要命中了已有文档（同哈希或同文件名），就一定先清空旧条文与旧向量：
        既保证「一份制度只保留一个版本」，也避免
        ``UNIQUE(document_id, content_hash)`` 冲突。
        """
        effective_ts = meta_tools.effective_timestamp(meta.effective_date)
        expiry_ts = meta_tools.effective_timestamp(meta.expiry_date)

        document = existing
        if document is not None:
            removed = repository.delete_chunks_by_document(db, document.id)
            try:
                self.vector_store.delete_by_document(document.id)
            except Exception as exc:  # pragma: no cover - 需要真实 Milvus
                logger.warning("删除文档 %s 旧向量失败（忽略并继续）：%s", document.id, exc)
            logger.info("文档 %s 旧版本已清理：%s 条条文", document.id, removed)

        fields: Dict[str, Any] = {
            "doc_no": meta.doc_no,
            "title": meta.title or parsed.file_name,
            "issuer": meta.issuer,
            "doc_type": meta.doc_type,
            "business_line": meta.business_line,
            "status": meta.status,
            "publish_date": meta.publish_date,
            "effective_date": meta.effective_date,
            "expiry_date": meta.expiry_date,
            "source_url": meta.source_url,
            "file_name": parsed.file_name,
            "file_type": parsed.file_type,
            "file_hash": parsed.file_hash or None,
            "ocr_used": parsed.ocr_used,
            "parser_name": parsed.parser_name,
            "chunk_count": 0,
        }

        if document is None:
            document = repository.create_document(db, **fields)
        else:
            fields["version"] = (document.version or 1) + 1
            repository.update_document(db, document, **fields)
        db.flush()

        rows: List[KnowledgeChunk] = []
        for chunk in clause_chunks:
            content = (chunk.content or "").strip()
            if not content:
                continue
            rows.append(
                KnowledgeChunk(
                    document_id=document.id,
                    chunk_index=chunk.index,
                    clause_no=chunk.display_clause_no,
                    chapter=chunk.chapter,
                    article_title=chunk.article_title,
                    content=content,
                    content_hash=sha256_text(f"{document.id}:{chunk.index}:{content}"),
                    char_count=chunk.char_count,
                    token_count=chunk.token_count,
                    status=meta.status,
                    effective_date=meta.effective_date,
                    expiry_date=meta.expiry_date,
                    effective_ts=effective_ts,
                    expiry_ts=expiry_ts,
                    business_line=meta.business_line,
                    doc_type=meta.doc_type,
                )
            )
        repository.bulk_insert_chunks(db, rows)
        db.flush()

        repository.attach_tags_to_document(db, document, meta.tags)
        for row in rows:
            repository.attach_tags_to_chunk(db, row, meta.tags)

        document.chunk_count = len(rows)
        db.flush()
        logger.info(
            "文档落库完成：id=%s title=%r 条文=%d 状态=%s",
            document.id, document.title, document.chunk_count, document.status,
        )
        return document

    def _index_vectors(
        self,
        db: Session,
        document: KnowledgeDocument,
        clause_chunks: Sequence[ClauseChunk],
    ) -> Tuple[bool, int, str]:
        """向量化并写入 Milvus。返回 ``(是否成功, 条数, 错误信息)``。"""
        from sqlalchemy import select

        chunks = list(
            db.execute(
                select(KnowledgeChunk)
                .where(KnowledgeChunk.document_id == document.id)
                .order_by(KnowledgeChunk.chunk_index)
            ).scalars().all()
        )
        if not chunks:
            return True, 0, ""

        try:
            self.vector_store.ensure_collection()
            vectors = self.embedder.embed_documents([chunk.content for chunk in chunks])
            # 向量主键直接复用 MySQL 条文 id，检索命中后即可回查正文（协同查询）
            rows = [
                ClauseVector(
                    pk=int(chunk.id),
                    document_id=int(document.id),
                    embedding=vectors[index],
                    chunk_index=int(chunk.chunk_index or 0),
                    status=chunk.status or DocStatus.UNKNOWN.value,
                    business_line=chunk.business_line or BusinessLine.GENERAL.value,
                    doc_type=chunk.doc_type or DocType.OTHER.value,
                    effective_ts=int(chunk.effective_ts or 0),
                    expiry_ts=int(chunk.expiry_ts or 0),
                    clause_no=chunk.clause_no or "",
                )
                for index, chunk in enumerate(chunks)
            ]
            written = self.vector_store.upsert_clauses(rows)
            for chunk in chunks:
                chunk.milvus_pk = int(chunk.id)
            db.flush()
            logger.info("文档 %s 向量写入完成：%d 条", document.id, written)
            return True, written, ""
        except Exception as exc:
            logger.error("文档 %s 向量写入失败：%s", document.id, exc)
            return False, 0, str(exc)

    # ---------------------------------------------------------- 删除/重建
    def remove_document(self, db: Session, document_id: int) -> Dict[str, Any]:
        """删除文档：MySQL 级联删除 + Milvus 向量删除 + BM25 缓存失效。"""
        document = repository.get_document(db, document_id)
        if document is None:
            return {"deleted": False, "message": "文档不存在"}
        chunk_count = document.chunk_count
        title = document.title

        vector_error = ""
        try:
            self.vector_store.delete_by_document(document_id)
        except Exception as exc:  # pragma: no cover - 需要真实 Milvus
            vector_error = str(exc)
            logger.warning("删除文档 %s 向量失败：%s", document_id, exc)

        repository.delete_document(db, document_id)
        self._invalidate_bm25()
        return {
            "deleted": True,
            "document_id": document_id,
            "title": title,
            "chunk_count": chunk_count,
            "vector_error": vector_error,
        }

    def rebuild_vectors(self, db: Session, batch_size: int = 64) -> Dict[str, Any]:
        """按 MySQL 现有条文重建全部向量（集合被删或写入失败后的恢复手段）。"""
        total = repository.count_chunks(db, None)
        if total == 0:
            return {"total": 0, "written": 0, "failed": 0}

        self.vector_store.ensure_collection()
        written = 0
        failed = 0
        offset = 0
        while offset < total:
            chunks, _ = repository.list_chunks(db, filters=None, limit=batch_size, offset=offset)
            if not chunks:
                break
            try:
                vectors = self.embedder.embed_documents([c.content for c in chunks])
                rows = [
                    ClauseVector(
                        pk=int(chunk.id),
                        document_id=int(chunk.document_id),
                        embedding=vectors[index],
                        chunk_index=int(chunk.chunk_index or 0),
                        status=chunk.status or DocStatus.UNKNOWN.value,
                        business_line=chunk.business_line or BusinessLine.GENERAL.value,
                        doc_type=chunk.doc_type or DocType.OTHER.value,
                        effective_ts=int(chunk.effective_ts or 0),
                        expiry_ts=int(chunk.expiry_ts or 0),
                        clause_no=chunk.clause_no or "",
                    )
                    for index, chunk in enumerate(chunks)
                ]
                written += self.vector_store.upsert_clauses(rows)
                for chunk in chunks:
                    chunk.milvus_pk = int(chunk.id)
                db.commit()
            except Exception as exc:
                failed += len(chunks)
                logger.error("重建向量失败（offset=%d）：%s", offset, exc)
            offset += batch_size

        self._invalidate_bm25()
        return {"total": total, "written": written, "failed": failed}

    # ==================================================================
    #  辅助
    # ==================================================================
    @staticmethod
    def _new_task_no() -> str:
        """生成入库任务号。"""
        return f"ING{time.strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:6]}"

    @staticmethod
    def _set_stage(db: Session, task: IngestTask, stage: str) -> None:
        """更新任务阶段并提交，便于前端轮询进度。"""
        task.stage = stage
        db.commit()

    @staticmethod
    def _invalidate_bm25() -> None:
        """入库后失效 BM25 索引缓存，保证检索立即可见新条文。"""
        try:
            from app.retrieval.bm25_retriever import get_bm25_retriever

            get_bm25_retriever().invalidate()
        except Exception:  # pragma: no cover - 循环导入保护
            logger.debug("BM25 缓存失效跳过", exc_info=True)

    def _finish_task(
        self,
        db: Session,
        task: IngestTask,
        status: str,
        stage: str,
        *,
        message: str = "",
        document_id: Optional[int] = None,
        processed_chunks: int = 0,
        ocr_used: bool = False,
        cost_ms: int = 0,
    ) -> None:
        """写入任务终态。"""
        repository.update_ingest_task(
            db,
            task,
            status=status,
            stage=stage,
            message=message[:2000],
            document_id=document_id,
            processed_chunks=processed_chunks,
            ocr_used=ocr_used,
            cost_ms=cost_ms,
        )
        db.commit()

    def _fail(
        self,
        db: Session,
        task: IngestTask,
        file_name: str,
        message: str,
        started: float,
    ) -> IngestResult:
        """记录失败结果。"""
        cost_ms = int((time.perf_counter() - started) * 1000)
        logger.warning("入库失败：%s -> %s", file_name, message)
        self._finish_task(
            db, task, IngestStatus.FAILED.value, "failed",
            message=message, cost_ms=cost_ms,
        )
        return IngestResult(
            file_name=file_name,
            status=IngestStatus.FAILED.value,
            stage="failed",
            task_id=task.id,
            cost_ms=cost_ms,
            message=message,
        )

    @staticmethod
    def _result_from_document(
        document: KnowledgeDocument,
        task: IngestTask,
        status: str,
        message: str,
    ) -> IngestResult:
        """由已存在文档构造结果（跳过场景）。"""
        return IngestResult(
            file_name=document.file_name,
            status=status,
            stage="skipped",
            document_id=document.id,
            task_id=task.id,
            title=document.title,
            doc_no=document.doc_no,
            issuer=document.issuer,
            doc_type=document.doc_type,
            business_line=document.business_line,
            doc_status=document.status,
            chunk_count=document.chunk_count,
            ocr_used=bool(document.ocr_used),
            parser_name=document.parser_name,
            message=message,
        )


__all__ = ["IngestPipeline", "IngestResult"]
