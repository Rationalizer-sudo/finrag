/**
 * 对话状态：当前会话消息、检索参数、提问编排。
 *
 * 设计说明：
 * - `messages` 保存当前会话的完整消息流（用户提问 + 助手回答），驱动问答页渲染；
 * - `options` 保存检索参数（模式 / Top-K / 业务条线 / 时效过滤 / 流式开关），持久化到 localStorage；
 * - 提问编排（非流式 + 流式 SSE）集中在 store 内，视图层只负责交互与展示，
 *   保证「API 层 - 状态层 - 视图层」职责分离。
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { askQuestion, askQuestionStream } from '@/api/qa'
import { useUserStore } from '@/stores/user'
import { randomId } from '@/utils'

const OPTIONS_KEY = 'finrag_chat_options'

/** 课题要求：端到端响应 ≤ 2 秒 */
const RESPONSE_BUDGET_MS = 2000

const DEFAULT_OPTIONS = {
  topK: 5,
  retrievalMode: 'hybrid',
  onlyValid: true,
  excludeExpired: true,
  statuses: [],
  businessLines: [],
  docTypes: [],
  stream: true,
  saveHistory: true,
  includeAllCitations: false
}

/** 读取本地保存的检索参数 */
function loadOptions() {
  try {
    const raw = localStorage.getItem(OPTIONS_KEY)
    if (!raw) return { ...DEFAULT_OPTIONS }
    const parsed = JSON.parse(raw)
    return { ...DEFAULT_OPTIONS, ...parsed }
  } catch (error) {
    return { ...DEFAULT_OPTIONS }
  }
}

export const useChatStore = defineStore('chat', () => {
  const userStore = useUserStore()

  /** 当前会话消息流 */
  const messages = ref([])
  /** 检索与生成参数 */
  const options = ref(loadOptions())
  /** 是否正在提问 */
  const asking = ref(false)
  /** 当前会话的问答摘要（用于侧栏「本轮会话」列表） */
  const sessionLog = ref([])
  /** 最近一次提问的错误信息 */
  const lastError = ref('')
  /** 流式中断控制器 */
  let controller = null

  const userMessages = computed(() => messages.value.filter((item) => item.role === 'user'))
  const answerMessages = computed(() => messages.value.filter((item) => item.role === 'assistant'))
  const hasMessages = computed(() => messages.value.length > 0)

  function persistOptions() {
    try {
      localStorage.setItem(OPTIONS_KEY, JSON.stringify(options.value))
    } catch (error) {
      // 忽略存储失败
    }
  }

  function patchOptions(patch) {
    options.value = { ...options.value, ...patch }
    persistOptions()
  }

  function resetOptions() {
    options.value = { ...DEFAULT_OPTIONS }
    persistOptions()
  }

  /** 追加一条用户消息 */
  function pushUserMessage(content) {
    const message = {
      id: randomId('u-'),
      role: 'user',
      content,
      createdAt: new Date().toISOString()
    }
    messages.value.push(message)
    return message
  }

  /** 追加一条助手占位消息（后续流式/整段回填） */
  function pushAssistantPlaceholder(question) {
    const message = {
      id: randomId('a-'),
      role: 'assistant',
      question,
      answer: '',
      citations: [],
      credibility: null,
      evidenceCount: 0,
      retrievalMode: options.value.retrievalMode,
      topK: options.value.topK,
      latencyMs: null,
      withinBudget: true,
      latencyClientSide: false,
      hasAnswer: true,
      warnings: [],
      trace: null,
      historyId: null,
      citedIndexes: [],
      pending: true,
      streaming: false,
      error: '',
      favorited: false,
      createdAt: new Date().toISOString()
    }
    messages.value.push(message)
    return message
  }

  /** 定位数组中最后一条助手消息（避免闭包持有响应式代理导致更新丢失） */
  function lastAssistant() {
    for (let index = messages.value.length - 1; index >= 0; index -= 1) {
      if (messages.value[index].role === 'assistant') return messages.value[index]
    }
    return null
  }

  /** 组装 AskRequest 请求体 */
  function buildPayload(question) {
    const identity = userStore.identityParams()
    return {
      question,
      top_k: Number(options.value.topK) || 5,
      retrieval_mode: options.value.retrievalMode,
      only_valid: Boolean(options.value.onlyValid),
      exclude_expired: Boolean(options.value.excludeExpired),
      statuses: options.value.statuses || [],
      business_lines: options.value.businessLines || [],
      doc_types: options.value.docTypes || [],
      document_ids: [],
      as_of: null,
      user_id: identity.user_id || 'anonymous',
      session_id: identity.session_id || '',
      save_history: Boolean(options.value.saveHistory),
      include_all_citations: Boolean(options.value.includeAllCitations)
    }
  }

  /** 把非流式接口返回的 data 回填到消息对象 */
  function applyResult(message, data) {
    message.answer = data.answer || ''
    message.hasAnswer = data.has_answer !== false
    message.citations = data.citations || []
    message.credibility = data.credibility || null
    message.evidenceCount = data.evidence_count || 0
    message.retrievalMode = data.retrieval_mode || options.value.retrievalMode
    message.topK = data.top_k || options.value.topK
    message.latencyMs = data.latency_ms || 0
    message.withinBudget = data.within_budget !== false
    message.historyId = data.history_id || null
    message.warnings = data.warnings || []
    message.trace = data.trace || null
    message.pending = false
    message.streaming = false
  }

  /** 记录本轮会话摘要 */
  function logSession(message) {
    sessionLog.value.push({
      id: message.id,
      question: message.question,
      answer: message.answer,
      historyId: message.historyId,
      credibilityScore: message.credibility ? message.credibility.score : null,
      citations: message.citations ? message.citations.length : 0,
      createdAt: message.createdAt
    })
  }

  /**
   * 提问主流程。
   * @param {string} question 用户问题
   * @returns {Promise<object|null>} 助手消息对象
   */
  async function ask(question) {
    const text = String(question || '').trim()
    if (!text) return null
    if (asking.value) return null

    lastError.value = ''
    pushUserMessage(text)
    const message = pushAssistantPlaceholder(text)
    asking.value = true

    const payload = buildPayload(text)
    const startedAt = Date.now()

    // ---------------------------------------------------------- 流式（SSE）
    if (options.value.stream) {
      message.streaming = true
      message.pending = true
      controller = new AbortController()

      await askQuestionStream(payload, {
        signal: controller.signal,
        onMeta: (meta) => {
          message.citations = (meta && meta.citations) || []
          message.evidenceCount = (meta && meta.evidence_count) || message.citations.length
        },
        onDelta: (piece) => {
          // 首个增量到达即认为已进入生成阶段
          message.streaming = true
          message.pending = false
          message.answer += piece || ''
        },
        onDone: (done) => {
          if (done && done.credibility) message.credibility = done.credibility
          if (done && done.cited_indexes) message.citedIndexes = done.cited_indexes
          message.streaming = false
          message.pending = false
          message.latencyMs = Date.now() - startedAt
          message.latencyClientSide = true
          message.withinBudget = message.latencyMs <= RESPONSE_BUDGET_MS
          if (options.value.saveHistory) {
            message.warnings = [
              ...(message.warnings || []),
              '流式模式（/qa/ask/stream）不会写入问答历史；如需在「历史」页回溯，请关闭流式后重新提问。'
            ]
          }
        },
        onError: (error) => {
          message.error = (error && error.message) || '流式生成失败'
          message.pending = false
          message.streaming = false
          lastError.value = message.error
        }
      })

      if (message.error) {
        asking.value = false
        controller = null
        return message
      }

      // 兜底：未收到 delta 或 done 时补齐状态
      message.pending = false
      message.streaming = false
      if (message.latencyMs === null) {
        message.latencyMs = Date.now() - startedAt
        message.latencyClientSide = true
        message.withinBudget = message.latencyMs <= RESPONSE_BUDGET_MS
      }
      if (!message.answer) {
        message.answer = '未获取到模型输出，请重试或切换为非流式模式。'
        message.hasAnswer = false
      }
      asking.value = false
      controller = null
      logSession(message)
      return message
    }

    // -------------------------------------------------------- 非流式（整段）
    try {
      const data = await askQuestion(payload)
      applyResult(message, data)
      logSession(message)
      return message
    } catch (error) {
      message.pending = false
      message.streaming = false
      message.error = (error && error.message) || '提问失败'
      message.latencyMs = Date.now() - startedAt
      lastError.value = message.error
      return message
    } finally {
      asking.value = false
    }
  }

  /** 中断流式生成 */
  function abort() {
    if (controller) {
      controller.abort()
      controller = null
    }
    const message = lastAssistant()
    if (message) {
      message.pending = false
      message.streaming = false
    }
    asking.value = false
  }

  /** 清空当前会话（消息 + 摘要），并新建 session_id */
  function clearSession() {
    abort()
    messages.value = []
    sessionLog.value = []
    lastError.value = ''
    userStore.resetSession()
  }

  /** 移除单条助手回答 */
  function removeMessage(id) {
    messages.value = messages.value.filter((item) => item.id !== id)
    sessionLog.value = sessionLog.value.filter((item) => item.id !== id)
  }

  /** 标记某条回答已收藏 */
  function markFavorited(id, favorited = true) {
    const target = messages.value.find((item) => item.id === id)
    if (target) target.favorited = favorited
  }

  return {
    messages,
    options,
    asking,
    sessionLog,
    lastError,
    userMessages,
    answerMessages,
    hasMessages,
    patchOptions,
    resetOptions,
    ask,
    abort,
    clearSession,
    removeMessage,
    markFavorited
  }
})
