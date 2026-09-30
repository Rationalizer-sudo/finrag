/**
 * 通用工具函数与常量（后端未提供或不便下发时的本地兜底）。
 *
 * 说明：业务枚举（业务条线、文件类型、时效状态）优先从 `/capabilities` 动态获取，
 * 这里的常量仅作为接口不可用时的兜底，二者取值与后端 `app/core/constants.py` 保持一致。
 */

/* ------------------------------------------------------------------ 兜底常量 */

/** 时效状态（DocStatus） */
export const DOC_STATUSES = ['现行有效', '尚未生效', '已修订', '已废止', '状态待核']

/** 仅这两个状态可优先返回给用户（DocStatus.valid_values） */
export const VALID_DOC_STATUSES = ['现行有效', '尚未生效']

/** 文件类型（DocType） */
export const DOC_TYPES = [
  '法律',
  '行政法规',
  '司法解释',
  '部门规章',
  '规范性文件',
  '自律规则',
  '国家标准',
  '其他'
]

/** 业务条线（BusinessLine） */
export const BUSINESS_LINES = [
  '银行',
  '证券',
  '保险',
  '基金',
  '支付结算',
  '反洗钱',
  '信托',
  '期货',
  '综合'
]

/** 检索模式（RetrievalMode） */
export const RETRIEVAL_MODES = [
  { value: 'hybrid', label: '混合检索', tip: '向量 + BM25，RRF 融合重排（推荐）' },
  { value: 'vector', label: '向量检索', tip: '语义相似度召回，适合口语化提问' },
  { value: 'bm25', label: '关键词检索', tip: '术语精确匹配，适合文号/条款号查询' }
]

/** 可信度 5 个维度：字段名 -> 中文名与权重（与 app/rag/credibility.py::WEIGHTS 一致） */
export const CREDIBILITY_DIMENSIONS = [
  { key: 'retrieval_strength', label: '检索相关度', weight: 25, desc: 'Top 条文的融合得分' },
  { key: 'term_alignment', label: '术语对齐度', weight: 25, desc: '问题术语在证据中的覆盖比例' },
  { key: 'citation_coverage', label: '引用充分度', weight: 20, desc: '回答显式标注的引用条数' },
  { key: 'evidence_validity', label: '证据时效性', weight: 15, desc: '现行有效条文占比' },
  { key: 'answer_grounding', label: '回答接地度', weight: 15, desc: '回答用词在证据中的出处比例' }
]

/** 可信度等级阈值（与后端 LEVEL_THRESHOLDS 一致） */
export const CREDIBILITY_THRESHOLDS = [
  { min_score: 80, label: '高' },
  { min_score: 60, label: '中' },
  { min_score: 40, label: '低' },
  { min_score: 0, label: '不可信' }
]

/** 课题验收指标 */
export const PROJECT_TARGETS = {
  knowledgePoints: 1000,
  responseTimeMs: 2000,
  answerAccuracy: 0.85,
  testCoverage: 0.7
}

/* ------------------------------------------------------------------ 状态样式 */

/**
 * 时效状态 -> Element Plus 标签类型。
 * 现行有效=绿(success)、已废止=红(danger)、已修订=橙(warning)、尚未生效=蓝(primary)、状态待核=灰(info)
 */
export function docStatusTagType(status) {
  const map = {
    现行有效: 'success',
    已废止: 'danger',
    已修订: 'warning',
    尚未生效: 'primary',
    状态待核: 'info'
  }
  return map[status] || 'info'
}

/** 时效状态 -> 自定义颜色（用于卡片左边框与图表配色） */
export function docStatusColor(status) {
  const map = {
    现行有效: '#67c23a',
    已废止: '#f56c6c',
    已修订: '#e6a23c',
    尚未生效: '#409eff',
    状态待核: '#909399'
  }
  return map[status] || '#909399'
}

/** 入库任务状态 -> 中文文案与标签类型 */
export function taskStatusMeta(status) {
  const map = {
    pending: { label: '排队中', type: 'info' },
    running: { label: '处理中', type: 'primary' },
    success: { label: '成功', type: 'success' },
    partial: { label: '部分成功', type: 'warning' },
    failed: { label: '失败', type: 'danger' },
    skipped: { label: '已跳过', type: 'info' }
  }
  return map[status] || { label: status || '未知', type: 'info' }
}

/** 健康状态 -> 标签类型与文案 */
export function healthStatusMeta(status) {
  const map = {
    ok: { label: '全部组件正常', type: 'success' },
    degraded: { label: '降级运行', type: 'warning' },
    down: { label: '服务不可用', type: 'danger' }
  }
  return map[status] || { label: '状态未知', type: 'info' }
}

/**
 * 可信度等级 -> 颜色 / 标签类型。
 * 高=绿、中=蓝、低=橙、不可信=红
 */
export function credibilityMeta(label) {
  const map = {
    高: { type: 'success', color: '#67c23a' },
    中: { type: 'primary', color: '#409eff' },
    低: { type: 'warning', color: '#e6a23c' },
    不可信: { type: 'danger', color: '#f56c6c' }
  }
  return map[label] || { type: 'info', color: '#909399' }
}

/** 按分数推断等级（收藏/历史接口只回传分数时使用） */
export function labelFromScore(score) {
  const value = Number(score || 0)
  const hit = CREDIBILITY_THRESHOLDS.find((item) => value >= item.min_score)
  return hit ? hit.label : '不可信'
}

/* ------------------------------------------------------------------ 格式化 */

/** 数字保留 n 位小数（空值返回 '—'） */
export function formatNumber(value, digits = 0) {
  const num = Number(value)
  if (value === null || value === undefined || Number.isNaN(num)) return '—'
  return num.toFixed(digits)
}

/** 0~1 的比率 -> 百分比字符串 */
export function formatPercent(value, digits = 0) {
  const num = Number(value)
  if (value === null || value === undefined || Number.isNaN(num)) return '—'
  return `${(num * 100).toFixed(digits)}%`
}

/** 耗时展示：毫秒 -> 毫秒/秒 */
export function formatLatency(ms) {
  const num = Number(ms)
  if (!num && num !== 0) return '—'
  if (num < 1000) return `${Math.round(num)} ms`
  return `${(num / 1000).toFixed(2)} s`
}

/**
 * 时间字符串格式化。
 * 后端返回 ISO8601 字符串（可能带时区），这里统一显示为 `YYYY-MM-DD HH:mm:ss`。
 */
export function formatDateTime(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  const pad = (num) => String(num).padStart(2, '0')
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
  )
}

/** 仅日期部分 */
export function formatDate(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  const pad = (num) => String(num).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

/** 文本截断（用于表格列与列表摘要） */
export function truncate(text, max = 120) {
  const str = String(text || '').replace(/\s+/g, ' ').trim()
  if (str.length <= max) return str
  return `${str.slice(0, max)}…`
}

/** 生成短随机 ID（会话标识等） */
export function randomId(prefix = '') {
  const body =
    typeof crypto !== 'undefined' && crypto.getRandomValues
      ? Array.from(crypto.getRandomValues(new Uint8Array(8)))
          .map((num) => num.toString(16).padStart(2, '0'))
          .join('')
      : Math.random().toString(16).slice(2, 18)
  return `${prefix}${body}`
}

/** 复制文本到剪贴板（降级方案兼容非安全上下文） */
export async function copyText(text) {
  const value = String(text || '')
  if (!value) return false
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(value)
      return true
    }
  } catch (error) {
    // 继续走降级方案
  }
  try {
    const textarea = document.createElement('textarea')
    textarea.value = value
    textarea.style.position = 'fixed'
    textarea.style.opacity = '0'
    document.body.appendChild(textarea)
    textarea.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(textarea)
    return ok
  } catch (error) {
    return false
  }
}
