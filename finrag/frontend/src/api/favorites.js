/**
 * 收藏接口。
 * 注意：收藏相关接口的 user_id 通过 query 参数传递（见后端 app/api/v1/favorites.py）。
 */
import request, { cleanParams } from './request'

/**
 * 新增收藏。
 * @param {object} data {history_id,question,answer,note,user_id}
 * 传 history_id 时后端会自动带出问题/回答/引用；重复收藏同一历史幂等返回已有记录。
 */
export function addFavorite(data) {
  return request.post('/favorites', data)
}

/** 收藏列表（分页） */
export function listFavorites(params) {
  return request.get('/favorites', { params: cleanParams(params) })
}

/** 修改收藏备注 */
export function updateFavoriteNote(favoriteId, note, userId) {
  return request.patch(
    `/favorites/${favoriteId}`,
    { note },
    { params: cleanParams({ user_id: userId }) }
  )
}

/** 取消收藏 */
export function deleteFavorite(favoriteId, userId) {
  return request.delete(`/favorites/${favoriteId}`, {
    params: cleanParams({ user_id: userId })
  })
}
