/**
 * 问答相关接口。
 *
 * 非流式走 axios（自动解包 `{code,message,data}`）；
 * 流式走原生 fetch 手动解析 SSE —— axios 在浏览器端无法读取流式响应体。
 */
import request, { BASE_URL, cleanParams } from './request'

/**
 * 提问（非流式）。
 * @param {object} payload AskRequest 请求体
 * @returns {Promise<object>} AskResponse.data
 */
export function askQuestion(payload) {
  return request.post('/qa/ask', payload, { timeout: 120000 })
}

/**
 * 流式提问（SSE）。
 *
 * 后端事件序列：`meta`（条文+证据数） -> `delta`（增量文本，多个） -> `done`（可信度） / `error`。
 * 注意：后端 `/qa/ask/stream` **不写入问答历史**，因此流式结果没有 history_id，
 * 界面上需要提示用户「如需保存历史请关闭流式」。
 *
 * @param {object} payload AskRequest 请求体
 * @param {object} handlers 回调集合：onMeta / onDelta / onDone / onError
 * @returns {Promise<{aborted:boolean}>}
 */
export async function askQuestionStream(payload, handlers = {}) {
  const { onMeta, onDelta, onDone, onError, signal } = handlers
  const url = `${String(BASE_URL).replace(/\/$/, '')}/qa/ask/stream`
  let response

  try {
    response = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream'
      },
      body: JSON.stringify(payload),
      signal
    })
  } catch (error) {
    if (error && error.name === 'AbortError') return { aborted: true }
    const message = '无法连接后端服务，请确认 FastAPI 已在 http://127.0.0.1:8000 运行'
    if (onError) onError(new Error(message))
    return { aborted: false }
  }

  if (!response.ok) {
    let message = `流式请求失败（HTTP ${response.status}）`
    try {
      const body = await response.json()
      if (body && body.message) message = body.message
    } catch (error) {
      // 响应体非 JSON，沿用默认提示
    }
    if (onError) onError(new Error(message))
    return { aborted: false }
  }

  if (!response.body) {
    if (onError) onError(new Error('当前浏览器不支持流式响应读取，请切换为非流式模式'))
    return { aborted: false }
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  let aborted = false

  /** 解析单个 SSE 事件块（形如 "event: delta\ndata: {...}"） */
  const dispatch = (block) => {
    const lines = block.split('\n')
    let event = 'message'
    const dataLines = []
    lines.forEach((line) => {
      if (line.startsWith('event:')) event = line.slice(6).trim()
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
    })
    const raw = dataLines.join('\n')
    let data = null
    if (raw) {
      try {
        data = JSON.parse(raw)
      } catch (error) {
        data = { text: raw }
      }
    }
    if (event === 'meta' && onMeta) onMeta(data || {})
    else if (event === 'delta' && onDelta) onDelta((data && data.text) || '')
    else if (event === 'done' && onDone) onDone(data || {})
    else if (event === 'error' && onError) onError(new Error((data && data.message) || '流式生成失败'))
  }

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      buffer = buffer.replace(/\r\n/g, '\n')
      let separator = buffer.indexOf('\n\n')
      while (separator !== -1) {
        const block = buffer.slice(0, separator)
        buffer = buffer.slice(separator + 2)
        if (block.trim()) dispatch(block)
        separator = buffer.indexOf('\n\n')
      }
    }
    if (buffer.trim()) dispatch(buffer)
  } catch (error) {
    if (error && error.name === 'AbortError') {
      aborted = true
    } else if (onError) {
      onError(error instanceof Error ? error : new Error(String(error)))
    }
  } finally {
    try {
      reader.releaseLock()
    } catch (error) {
      // 释放失败不影响主流程
    }
  }

  return { aborted }
}

/**
 * 问答历史列表（分页）。
 * @returns {Promise<{items:Array, meta:{total,limit,offset,has_more}}>}
 */
export function listHistory(params) {
  return request.get('/qa/history', { params: cleanParams(params) })
}

/** 单条历史详情（含完整引用） */
export function getHistory(historyId, userId) {
  return request.get(`/qa/history/${historyId}`, { params: cleanParams({ user_id: userId }) })
}

/** 删除单条历史 */
export function deleteHistory(historyId, userId) {
  return request.delete(`/qa/history/${historyId}`, { params: cleanParams({ user_id: userId }) })
}

/** 清空当前用户全部历史 */
export function clearHistory(userId) {
  return request.delete('/qa/history', { params: cleanParams({ user_id: userId }) })
}
