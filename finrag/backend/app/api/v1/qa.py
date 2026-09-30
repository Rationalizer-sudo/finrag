"""智能问答接口。"""

from __future__ import annotations

import json

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from app.api.deps import DbSession, QaServiceDep
from app.schemas.common import ApiResponse, PageResult
from app.schemas.qa import AskRequest, AskResponse, HistoryItem
from app.services.qa_service import filters_from_request

router = APIRouter(prefix="/qa", tags=["问答"])


@router.post("/ask", response_model=ApiResponse[AskResponse], summary="制度条文问答")
def ask(payload: AskRequest, db: DbSession, service: QaServiceDep) -> ApiResponse[AskResponse]:
    """基于知识库检索并生成回答。

    * 支持 ``vector`` / ``bm25`` / ``hybrid`` 三种检索模式；
    * 默认只召回**现行有效**条文，已废止/已修订条文被过滤；
    * 返回条文溯源列表与 0~100 可信度评分。
    """
    return ApiResponse.ok(service.ask(db, payload))


@router.post("/ask/stream", summary="流式问答（SSE）")
def ask_stream(payload: AskRequest, db: DbSession, service: QaServiceDep) -> StreamingResponse:
    """流式返回回答。

    事件序列：

    1. ``event: meta``   —— 检索到的条文与可信度（先于正文，便于前端立即渲染依据）；
    2. ``event: delta``  —— 增量文本；
    3. ``event: done``   —— 结束标记与历史记录 ID。
    """
    container = service.container
    filters = filters_from_request(payload)
    evidences = container.hybrid_retriever.retrieve(
        db, payload.question, top_k=payload.top_k, filters=filters,
        mode=payload.retrieval_mode,
    )
    engine = container.rag_engine

    def event_stream():
        """SSE 事件生成器。"""
        meta = {
            "evidence_count": len(evidences),
            "citations": [
                clause.to_citation(index) for index, clause in enumerate(evidences, start=1)
            ],
        }
        yield f"event: meta\ndata: {json.dumps(meta, ensure_ascii=False)}\n\n"

        usable = engine.valid_evidences(evidences) if payload.only_valid else list(evidences)
        if not usable:
            from app.rag import prompts

            answer = (
                prompts.NO_EVIDENCE_ANSWER if not evidences else prompts.STALE_EVIDENCE_ANSWER
            )
            yield f"event: delta\ndata: {json.dumps({'text': answer}, ensure_ascii=False)}\n\n"
            yield f"event: done\ndata: {json.dumps({'finished': True}, ensure_ascii=False)}\n\n"
            return

        collected = []
        try:
            for piece in engine.stream(payload.question, usable):
                collected.append(piece)
                yield f"event: delta\ndata: {json.dumps({'text': piece}, ensure_ascii=False)}\n\n"
        except Exception as exc:  # pragma: no cover - 需要真实模型
            yield (
                "event: error\ndata: "
                + json.dumps({"message": str(exc)}, ensure_ascii=False)
                + "\n\n"
            )
            return

        result = engine.finalize(payload.question, "".join(collected), usable)
        yield (
            "event: done\ndata: "
            + json.dumps(
                {
                    "credibility": result.credibility.to_dict(),
                    "cited_indexes": result.cited_indexes,
                },
                ensure_ascii=False,
            )
            + "\n\n"
        )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get(
    "/history",
    response_model=ApiResponse[PageResult[HistoryItem]],
    summary="问答历史",
)
def list_history(
    db: DbSession,
    service: QaServiceDep,
    user_id: str = Query(default="anonymous", max_length=64),
    keyword: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ApiResponse[PageResult[HistoryItem]]:
    """分页查询当前用户的问答历史。"""
    items, total = service.list_history(
        db, user_id, keyword=keyword, limit=limit, offset=offset
    )
    return ApiResponse.ok(PageResult.create(items, total, limit, offset))


@router.get(
    "/history/{history_id}",
    response_model=ApiResponse[HistoryItem],
    summary="历史详情",
)
def get_history(
    history_id: int,
    db: DbSession,
    service: QaServiceDep,
    user_id: str = Query(default="anonymous", max_length=64),
) -> ApiResponse[HistoryItem]:
    """获取单条问答历史（含完整引用）。"""
    return ApiResponse.ok(service.get_history(db, history_id, user_id))


@router.delete("/history/{history_id}", response_model=ApiResponse[dict], summary="删除历史")
def delete_history(
    history_id: int,
    db: DbSession,
    service: QaServiceDep,
    user_id: str = Query(default="anonymous", max_length=64),
) -> ApiResponse[dict]:
    """删除一条问答历史。"""
    service.delete_history(db, history_id, user_id)
    return ApiResponse.ok({"deleted": True, "history_id": history_id})


@router.delete("/history", response_model=ApiResponse[dict], summary="清空历史")
def clear_history(
    db: DbSession,
    service: QaServiceDep,
    user_id: str = Query(default="anonymous", max_length=64),
) -> ApiResponse[dict]:
    """清空当前用户的全部问答历史。"""
    return ApiResponse.ok({"deleted_count": service.clear_history(db, user_id)})
