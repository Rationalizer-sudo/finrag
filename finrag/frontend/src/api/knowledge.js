/**
 * 知识库管理接口。
 */
import request, { cleanParams } from './request'

/** 知识库总览（文档数/知识点数/向量数/标签数/组件状态/达标情况） */
export function getStats() {
  return request.get('/knowledge/stats')
}

/**
 * 以文本方式入库（不受 python-multipart 依赖影响，上传不可用时的兜底通道）。
 * @param {object} data {text,file_name,source_url,doc_type,business_line,status,replace,force}
 */
export function ingestText(data) {
  return request.post('/knowledge/documents/text', data, { timeout: 300000 })
}

/**
 * 上传制度文件（multipart/form-data）。
 * @param {FormData} formData 字段：file、source_url、replace、force、keep_file
 * @param {Function} [onUploadProgress] 上传进度回调
 */
export function uploadDocument(formData, onUploadProgress) {
  return request.post('/knowledge/documents/upload', formData, {
    timeout: 600000,
    onUploadProgress
  })
}

/** 上传限制（允许的扩展名与体积上限），用于前端前置校验 */
export function getUploadLimits() {
  return request.get('/knowledge/documents/upload/limits')
}

/** 重建向量索引（按 MySQL 现有条文重新向量化写入 Milvus） */
export function rebuildVectors() {
  return request.post('/knowledge/vectors/rebuild', {}, { timeout: 1800000 })
}

/** 文档列表（分页） */
export function listDocuments(params) {
  return request.get('/knowledge/documents', { params: cleanParams(params) })
}

/** 文档详情 */
export function getDocument(documentId) {
  return request.get(`/knowledge/documents/${documentId}`)
}

/**
 * 修订文档元数据（人工校核时效状态等）。
 * @param {object} data 可含 {title,doc_no,issuer,doc_type,business_line,status,publish_date,effective_date,expiry_date,source_url,remark}
 */
export function updateDocument(documentId, data) {
  return request.patch(`/knowledge/documents/${documentId}`, data, { timeout: 300000 })
}

/** 删除文档（同时清理条文与向量） */
export function deleteDocument(documentId) {
  return request.delete(`/knowledge/documents/${documentId}`, { timeout: 300000 })
}

/** 条文（知识点）列表（分页） */
export function listChunks(params) {
  return request.get('/knowledge/chunks', { params: cleanParams(params) })
}

/** 条文详情（引用溯源落地数据） */
export function getChunk(chunkId) {
  return request.get(`/knowledge/chunks/${chunkId}`)
}

/** 标签列表 */
export function listTags(params) {
  return request.get('/knowledge/tags', { params: cleanParams(params) })
}

/** 入库任务记录 */
export function listTasks(params) {
  return request.get('/knowledge/tasks', { params: cleanParams(params) })
}

/** OCR 能力状态 */
export function getOcrStatus() {
  return request.get('/knowledge/ocr/status')
}
