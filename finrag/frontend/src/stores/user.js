/**
 * 用户状态：user_id / session_id，持久化到 localStorage。
 *
 * 后端不做登录鉴权，用 `user_id` 区分问答历史与收藏的归属，
 * `session_id` 用于把同一轮对话的多条问答归入同一会话。
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { USER_ID_KEY, cleanParams } from '@/api/request'
import { randomId } from '@/utils'

const SESSION_KEY = 'finrag_session_id'
const USER_NAME_KEY = 'finrag_user_name'

export const useUserStore = defineStore('user', () => {
  /** 用户标识：默认 anonymous，可在顶栏修改 */
  const userId = ref(localStorage.getItem(USER_ID_KEY) || 'anonymous')
  /** 会话标识：进入页面时自动创建，用于串联同一轮对话 */
  const sessionId = ref(localStorage.getItem(SESSION_KEY) || randomId('s-'))
  /** 展示用昵称 */
  const userName = ref(localStorage.getItem(USER_NAME_KEY) || '')

  const isAnonymous = computed(() => !userId.value || userId.value === 'anonymous')
  const displayName = computed(() => userName.value || userId.value)

  /** 写入 localStorage 的小工具 */
  function persist(key, value) {
    try {
      if (value) localStorage.setItem(key, value)
      else localStorage.removeItem(key)
    } catch (error) {
      // 隐私模式下 localStorage 可能不可写，忽略即可
    }
  }

  function setUserId(value) {
    const next = String(value || '').trim() || 'anonymous'
    userId.value = next
    persist(USER_ID_KEY, next)
  }

  function setUserName(value) {
    const next = String(value || '').trim()
    userName.value = next
    persist(USER_NAME_KEY, next)
  }

  /** 新建会话（清空当前会话消息后调用） */
  function resetSession() {
    sessionId.value = randomId('s-')
    persist(SESSION_KEY, sessionId.value)
    return sessionId.value
  }

  /** 请求参数中携带的用户信息 */
  function identityParams() {
    return cleanParams({ user_id: userId.value, session_id: sessionId.value })
  }

  function init() {
    persist(USER_ID_KEY, userId.value)
    persist(SESSION_KEY, sessionId.value)
    persist(USER_NAME_KEY, userName.value)
  }

  return {
    userId,
    sessionId,
    userName,
    isAnonymous,
    displayName,
    setUserId,
    setUserName,
    resetSession,
    identityParams,
    init
  }
})
