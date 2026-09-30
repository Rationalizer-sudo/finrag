"""收藏接口。"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import DbSession, QaServiceDep
from app.schemas.common import ApiResponse, PageResult
from app.schemas.qa import FavoriteCreateRequest, FavoriteItem, FavoriteUpdateRequest

router = APIRouter(prefix="/favorites", tags=["收藏"])


@router.post("", response_model=ApiResponse[FavoriteItem], summary="新增收藏")
def add_favorite(
    payload: FavoriteCreateRequest, db: DbSession, service: QaServiceDep
) -> ApiResponse[FavoriteItem]:
    """收藏一条问答结果。

    * 传 ``history_id``：自动带出该次问答的问题、回答与引用；
    * 重复收藏同一历史记录时幂等返回已有收藏。
    """
    return ApiResponse.ok(service.add_favorite(db, payload))


@router.get("", response_model=ApiResponse[PageResult[FavoriteItem]], summary="收藏列表")
def list_favorites(
    db: DbSession,
    service: QaServiceDep,
    user_id: str = Query(default="anonymous", max_length=64),
    keyword: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ApiResponse[PageResult[FavoriteItem]]:
    """分页查询收藏。"""
    items, total = service.list_favorites(
        db, user_id, keyword=keyword, limit=limit, offset=offset
    )
    return ApiResponse.ok(PageResult.create(items, total, limit, offset))


@router.patch(
    "/{favorite_id}",
    response_model=ApiResponse[FavoriteItem],
    summary="更新收藏备注",
)
def update_favorite(
    favorite_id: int,
    payload: FavoriteUpdateRequest,
    db: DbSession,
    service: QaServiceDep,
    user_id: str = Query(default="anonymous", max_length=64),
) -> ApiResponse[FavoriteItem]:
    """修改收藏备注。"""
    return ApiResponse.ok(
        service.update_favorite_note(db, favorite_id, payload.note, user_id)
    )


@router.delete("/{favorite_id}", response_model=ApiResponse[dict], summary="取消收藏")
def delete_favorite(
    favorite_id: int,
    db: DbSession,
    service: QaServiceDep,
    user_id: str = Query(default="anonymous", max_length=64),
) -> ApiResponse[dict]:
    """删除收藏。"""
    service.delete_favorite(db, favorite_id, user_id)
    return ApiResponse.ok({"deleted": True, "favorite_id": favorite_id})
