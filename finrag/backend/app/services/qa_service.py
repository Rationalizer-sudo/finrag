"""问答服务：检索 -> 生成 -> 溯源 -> 可信度 -> 历史/收藏。"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import BadRequestError, NotFoundError
from app.core.logging_conf import get_logger
from app.db import repository
from app.db.models import QaFavorite, QaHistory
from app.rag.answer_chain import RagAnswer
from app.retrieval.types import RetrievalFilters
from app.schemas.qa import (
    AskRequest,
    AskResponse,
    Citation,
    Credibility,
    FavoriteCreateRequest,
    FavoriteItem,
    HistoryItem,
    RetrievalTrace,
)
from app.services.container import Container

logger = get_logger(__name__)


def filters_from_request(payload: AskRequest) -> RetrievalFilters:
    """把请求参数转换为检索过滤条件。"""
    as_of_ts: Optional[int] = None
    if payload.as_of is not None:
        from app.ingest.metadata import effective_timestamp

        as_of_ts = effective_timestamp(payload.as_of)
    return RetrievalFilters(
        statuses=list(payload.statuses),
        business_lines=list(payload.business_lines),
        doc_types=list(payload.doc_types),
        document_ids=list(payload.document_ids),
        only_valid=payload.only_valid,
        exclude_expired=payload.exclude_expired,
        as_of=as_of_ts,
    )


class QaService:
    """问答业务服务。"""

    def __init__(self, container: Container, settings: Optional[Settings] = None) -> None:
        self.container = container
        self.settings: Settings = settings or container.settings

    # ==================================================================
    #  提问
    # ==================================================================
    def ask(self, db: Session, payload: AskRequest) -> AskResponse:
        """执行一次完整问答。"""
        started = time.perf_counter()
        filters = filters_from_request(payload)

        # ---- 1. 检索 ----
        retrieval_started = time.perf_counter()
        evidences = self.container.hybrid_retriever.retrieve(
            db,
            payload.question,
            top_k=payload.top_k,
            filters=filters,
            mode=payload.retrieval_mode,
        )
        retrieval_ms = int((time.perf_counter() - retrieval_started) * 1000)

        # ---- 2. 生成 + 溯源 + 可信度 ----
        generation_started = time.perf_counter()
        result: RagAnswer = self.container.rag_engine.generate(
            payload.question,
            evidences,
            # 用户显式关闭有效性过滤时，允许依据历史（已废止）条文作答
            strict_validity=payload.only_valid,
        )
        generation_ms = int((time.perf_counter() - generation_started) * 1000)

        total_ms = int((time.perf_counter() - started) * 1000)

        # ---- 3. 引用完整性 ----
        citations = self._build_citations(result, evidences, payload.include_all_citations)

        # ---- 4. 落历史 ----
        history_id: Optional[int] = None
        if payload.save_history:
            history_id = self._save_history(db, payload, result, citations, total_ms)

        trace = RetrievalTrace(
            retrieval_ms=retrieval_ms,
            generation_ms=generation_ms,
            total_ms=total_ms,
            candidate_k=max(int(self.settings.retrieve_candidate_k), payload.top_k * 3),
            evidence_count=len(evidences),
            vector_used=payload.retrieval_mode in ("vector", "hybrid"),
            bm25_used=payload.retrieval_mode in ("bm25", "hybrid"),
            context_chars=result.context_chars,
        )

        logger.info(
            "问答完成：q=%r 模式=%s 证据=%d 耗时=%dms 可信度=%.1f",
            payload.question[:30], payload.retrieval_mode, len(evidences),
            total_ms, result.credibility.score,
        )

        return AskResponse(
            question=payload.question,
            answer=result.answer,
            has_answer=result.has_answer,
            citations=citations,
            credibility=Credibility(**result.credibility.to_dict()),
            evidence_count=len(evidences),
            retrieval_mode=payload.retrieval_mode,
            top_k=payload.top_k,
            latency_ms=total_ms,
            within_budget=total_ms <= self.settings.response_time_budget_ms,
            history_id=history_id,
            warnings=list(result.warnings),
            trace=trace,
        )

    @staticmethod
    def _build_citations(
        result: RagAnswer,
        evidences: List[Any],
        include_all: bool,
    ) -> List[Citation]:
        """组装引用列表；``include_all`` 时补齐未被引用的召回条文。"""
        payloads: List[Dict[str, Any]] = [dict(item) for item in result.citations]
        if include_all:
            cited = {item.get("index") for item in payloads}
            for index, clause in enumerate(evidences, start=1):
                if index not in cited:
                    payloads.append(clause.to_citation(index))
            payloads.sort(key=lambda item: int(item.get("index", 0)))
        return [Citation(**item) for item in payloads]

    def _save_history(
        self,
        db: Session,
        payload: AskRequest,
        result: RagAnswer,
        citations: List[Citation],
        latency_ms: int,
    ) -> Optional[int]:
        """写入问答历史（失败不影响主流程）。"""
        try:
            record = repository.create_history(
                db,
                session_id=payload.session_id or uuid.uuid4().hex[:16],
                user_id=payload.user_id or "anonymous",
                question=payload.question,
                answer=result.answer,
                retrieval_mode=payload.retrieval_mode,
                top_k=payload.top_k,
                evidence_count=result.evidence_count,
                latency_ms=latency_ms,
                credibility_score=result.credibility.score,
                credibility_label=result.credibility.label,
                citations=[item.model_dump() for item in citations],
                has_answer=result.has_answer,
            )
            db.commit()
            return int(record.id)
        except Exception as exc:  # pragma: no cover - 历史写入不应阻断问答
            db.rollback()
            logger.warning("问答历史写入失败（忽略）：%s", exc)
            return None

    # ==================================================================
    #  历史
    # ==================================================================
    def list_history(
        self,
        db: Session,
        user_id: str,
        *,
        keyword: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Tuple[List[HistoryItem], int]:
        """分页查询问答历史，并标记已收藏项。"""
        records, total = repository.list_history(
            db, user_id, keyword=keyword, limit=limit, offset=offset
        )
        favorites, _ = repository.list_favorites(db, user_id, limit=1000)
        favorited_ids = {item.history_id for item in favorites if item.history_id}

        items: List[HistoryItem] = []
        for record in records:
            data = record.to_dict()
            data["favorited"] = record.id in favorited_ids
            items.append(HistoryItem(**data))
        return items, total

    def get_history(self, db: Session, history_id: int, user_id: str) -> HistoryItem:
        """获取单条历史详情。"""
        record = repository.get_history(db, history_id)
        if record is None or record.user_id != user_id:
            raise NotFoundError(f"历史记录 {history_id} 不存在")
        favorite = repository.find_favorite_by_history(db, user_id, history_id)
        data = record.to_dict()
        data["favorited"] = favorite is not None
        return HistoryItem(**data)

    def delete_history(self, db: Session, history_id: int, user_id: str) -> bool:
        """删除一条历史。"""
        if not repository.delete_history(db, history_id, user_id):
            raise NotFoundError(f"历史记录 {history_id} 不存在或无权删除")
        db.commit()
        return True

    def clear_history(self, db: Session, user_id: str) -> int:
        """清空用户全部历史，返回删除条数。"""
        count = repository.clear_history(db, user_id)
        db.commit()
        return count

    # ==================================================================
    #  收藏
    # ==================================================================
    def add_favorite(self, db: Session, payload: FavoriteCreateRequest) -> FavoriteItem:
        """新增收藏。

        若指定 ``history_id`` 且已收藏，则直接返回已有收藏（幂等）。
        """
        user_id = payload.user_id or "anonymous"
        question = (payload.question or "").strip()
        answer = payload.answer or ""
        citations: List[Dict[str, Any]] = []
        score = 0.0

        if payload.history_id is not None:
            history = repository.get_history(db, payload.history_id)
            if history is None or history.user_id != user_id:
                raise NotFoundError(f"历史记录 {payload.history_id} 不存在")
            question = question or history.question
            answer = answer or history.answer
            citations = list(history.citations or [])
            score = float(history.credibility_score or 0.0)

            existing = repository.find_favorite_by_history(db, user_id, payload.history_id)
            if existing is not None:
                return FavoriteItem(**existing.to_dict())

        if not question:
            raise BadRequestError("收藏内容不能为空：请提供 history_id 或 question")

        record = repository.create_favorite(
            db,
            user_id=user_id,
            history_id=payload.history_id,
            question=question,
            answer=answer,
            note=payload.note or "",
            citations=citations,
            credibility_score=score,
        )
        db.commit()
        return FavoriteItem(**record.to_dict())

    def list_favorites(
        self,
        db: Session,
        user_id: str,
        *,
        keyword: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Tuple[List[FavoriteItem], int]:
        """分页查询收藏。"""
        records, total = repository.list_favorites(
            db, user_id, keyword=keyword, limit=limit, offset=offset
        )
        return [FavoriteItem(**item.to_dict()) for item in records], total

    def update_favorite_note(
        self, db: Session, favorite_id: int, note: str, user_id: str
    ) -> FavoriteItem:
        """更新收藏备注。"""
        record = repository.get_favorite(db, favorite_id)
        if record is None or record.user_id != user_id:
            raise NotFoundError(f"收藏 {favorite_id} 不存在或无权修改")
        record.note = (note or "")[:512]
        db.commit()
        return FavoriteItem(**record.to_dict())

    def delete_favorite(self, db: Session, favorite_id: int, user_id: str) -> bool:
        """删除收藏。"""
        if not repository.delete_favorite(db, favorite_id, user_id):
            raise NotFoundError(f"收藏 {favorite_id} 不存在或无权删除")
        db.commit()
        return True


def history_to_item(record: QaHistory, favorited: bool = False) -> HistoryItem:
    """ORM 历史 -> 响应模型。"""
    data = record.to_dict()
    data["favorited"] = favorited
    return HistoryItem(**data)


def favorite_to_item(record: QaFavorite) -> FavoriteItem:
    """ORM 收藏 -> 响应模型。"""
    return FavoriteItem(**record.to_dict())


__all__ = ["QaService", "filters_from_request", "history_to_item", "favorite_to_item"]
