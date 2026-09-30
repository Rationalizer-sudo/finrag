/**
 * Axios 实例与统一拦截。
 *
 * 约定（与后端 `app/schemas/common.py::ApiResponse` 一致）：
 * - 成功：`{"code": 0, "message": "ok", "data": <T>}`，拦截器直接**解包出 data**；
 * - 失败：HTTP 非 2xx，返回 `{"code": <int>, "message": "...", "detail": ..., "request_id": "..."}`，
 *   拦截器统一弹出 ElMessage 错误提示并 reject（组件层只需 catch 后收尾即可）；
 * - 例外：`/health`、`/health/live`、`/api` 是探针/自描述端点，返回**裸对象**
 *   （未包 ApiResponse），拦截器识别到没有 `code` 字段时原样返回，
 *   保证两类响应都能正确消费。`/capabilities` 使用标准信封。
 */
import axios from 'axios'
import { ElMessage } from 'element-plus'

/** 请求基址：开发环境由 vite proxy 转发到 http://127.0.0.1:8000 */
export const BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api/v1'

/** 默认超时（毫秒）：问答链路含大模型生成，需要较长等待 */
export const TIMEOUT = Number(import.meta.env.VITE_REQUEST_TIMEOUT || 60000)

/** localStorage 中用户标识的键名（与 stores/user.js 保持一致） */
export const USER_ID_KEY = 'finrag_user_id'

/**
 * 业务错误码 -> 人话提示。
 * 码值来源：后端 `app/core/errors.py::ErrorCode`。
 */
export const ERROR_HINTS = {
  40000: '请求参数不合法，请检查输入内容',
  40100: '未通过身份校验',
  40300: '没有操作权限',
  40400: '资源不存在或已被删除',
  41300: '上传文件超过大小限制',
  41500: '不支持的文件类型',
  42200: '请求参数校验失败，请检查表单字段',
  50000: '服务内部错误，请查看后端日志',
  50010:
    '接口返回 50010：文件上传场景通常是服务端缺少 python-multipart（请执行 pip install python-multipart，或改用「文本入库」）；其他场景为数据库操作失败',
  50020: '向量数据库（Milvus）操作失败，可尝试「重建向量索引」',
  50030: '大模型服务调用失败，请检查 DASHSCOPE_API_KEY 与网络',
  50040: 'OCR 识别失败，请检查 Tesseract 是否安装',
  50050: '文档解析失败，请确认文件未加密且格式受支持'
}

/** 构造带业务码的错误对象，便于组件区分处理 */
function buildError(message, code, detail) {
  const error = new Error(message)
  error.code = code
  error.detail = detail
  error.isApiError = true
  return error
}

/** 统一错误提示（grouping 避免同一错误刷屏） */
function notifyError(message, code) {
  const text = String(message || '请求失败')
  const hint = ERROR_HINTS[code]
  // 后端自带的长文案（如上传依赖缺失的安装指引）不再追加提示，避免冗长
  const needHint = Boolean(hint) && text.length < 40
  ElMessage({
    type: 'error',
    message: needHint ? `${text}（${hint}）` : text,
    duration: 6000,
    showClose: true,
    grouping: true
  })
}

/**
 * 过滤掉空值查询参数。
 * FastAPI 的 Query 默认对空串敏感（空串会当作有效筛选值），
 * 因此查询前统一剔除 `''` / `null` / `undefined` / 空数组。
 */
export function cleanParams(params = {}) {
  const result = {}
  Object.keys(params).forEach((key) => {
    const value = params[key]
    if (value === undefined || value === null || value === '') return
    if (Array.isArray(value) && value.length === 0) return
    result[key] = value
  })
  return result
}

const request = axios.create({
  baseURL: BASE_URL,
  timeout: TIMEOUT,
  headers: { 'Content-Type': 'application/json' }
})

/* ------------------------------------------------------------------ 请求拦截 */
request.interceptors.request.use(
  (config) => {
    // 附带用户标识，便于后端按用户归档历史与收藏
    const userId = localStorage.getItem(USER_ID_KEY) || 'anonymous'
    config.headers = config.headers || {}
    config.headers['X-User-ID'] = userId
    // 上传大文件时不复用默认超时
    if (config.data instanceof FormData) {
      delete config.headers['Content-Type']
    }
    return config
  },
  (error) => Promise.reject(error)
)

/* ------------------------------------------------------------------ 响应拦截 */
request.interceptors.response.use(
  (response) => {
    const body = response.data
    // 裸响应（/health、/capabilities）或非 JSON：原样返回
    if (!body || typeof body !== 'object' || !('code' in body)) {
      return body
    }
    // 标准成功响应：解包 data
    if (body.code === 0) {
      return body.data
    }
    // HTTP 200 但业务失败（例如上传依赖缺失返回 code=50010）
    const message = body.message || '请求失败'
    if (!response.config || response.config.silent !== true) {
      notifyError(message, body.code)
    }
    return Promise.reject(buildError(message, body.code, body.detail))
  },
  (error) => {
    const response = error.response
    let message = '网络异常，请确认后端服务已启动（http://127.0.0.1:8000）'
    let code = -1

    if (response) {
      const body = response.data
      code = (body && body.code) || response.status * 100
      message = (body && body.message) || `请求失败（HTTP ${response.status}）`
      if ([502, 503, 504].includes(response.status)) {
        message = `后端服务暂不可用（HTTP ${response.status}），请稍后重试`
      }
    } else if (error.code === 'ECONNABORTED' || /timeout/i.test(error.message || '')) {
      message = `请求超时（超过 ${Math.round(TIMEOUT / 1000)} 秒），大模型生成较慢时请重试或改用非流式`
    }

    const silent = error.config && error.config.silent === true
    if (!silent && error.code !== 'ERR_CANCELED') {
      notifyError(message, code)
    }
    return Promise.reject(buildError(message, code, response && response.data && response.data.detail))
  }
)

export default request
