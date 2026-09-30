/**
 * 知识库状态：总览统计、文档列表、系统能力清单。
 *
 * 统计与文档列表会被「知识库管理」和「关于」两个页面复用，
 * 放在 store 里避免重复请求，也便于入库/删除后统一刷新。
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { listDocuments, getStats } from '@/api/knowledge'
import { getCapabilities } from '@/api/system'

export const useKnowledgeStore = defineStore('knowledge', () => {
  /** /knowledge/stats 返回的总览数据 */
  const stats = ref(null)
  const loadingStats = ref(false)
  const statsError = ref('')

  /** 文档分页数据 */
  const documents = ref([])
  const docTotal = ref(0)
  const loadingDocs = ref(false)

  /** 文档查询条件（下拉选项优先取 capabilities，其次用本地兜底常量） */
  const query = ref({
    keyword: '',
    status: '',
    business_line: '',
    doc_type: '',
    limit: 10,
    offset: 0
  })

  /** /capabilities 返回的能力清单（枚举、指标、评分口径、功能开关） */
  const capabilities = ref(null)
  const loadingCapabilities = ref(false)

  /* ------------------------------------------------------------ 计算属性 */

  const documentCount = computed(() => (stats.value && stats.value.document_count) || 0)
  const chunkCount = computed(() => (stats.value && stats.value.chunk_count) || 0)
  const vectorCount = computed(() => (stats.value && stats.value.vector_count) || 0)
  const tagCount = computed(() => (stats.value && stats.value.tag_count) || 0)
  const ocrDocumentCount = computed(() => (stats.value && stats.value.ocr_document_count) || 0)
  const meetsTarget = computed(() => (stats.value && stats.value.meets_target) || {})

  /** 知识点达标进度（0~100，用于进度条） */
  const knowledgeProgress = computed(() => {
    const target = Number(meetsTarget.value.knowledge_points_target || 1000)
    const actual = Number(meetsTarget.value.knowledge_points_actual || chunkCount.value || 0)
    if (!target) return 0
    return Math.min(100, Math.round((actual / target) * 100))
  })

  /** 时效状态分布：{ '现行有效': 12, ... } */
  const statusCounts = computed(() => (stats.value && stats.value.status_counts) || {})
  /** 业务条线分布：{ '银行': 8, ... } */
  const businessLineCounts = computed(
    () => (stats.value && stats.value.business_line_counts) || {}
  )

  /** 下拉枚举：优先后端 capabilities，缺失时由调用方兜底 */
  const businessLines = computed(
    () => (capabilities.value && capabilities.value.business_lines) || []
  )
  const docStatuses = computed(() => (capabilities.value && capabilities.value.doc_statuses) || [])
  const docTypes = computed(() => (capabilities.value && capabilities.value.doc_types) || [])
  const retrievalModes = computed(
    () => (capabilities.value && capabilities.value.retrieval_modes) || []
  )
  const features = computed(() => (capabilities.value && capabilities.value.features) || {})
  const dependencies = computed(() => (capabilities.value && capabilities.value.dependencies) || {})
  const targets = computed(() => (capabilities.value && capabilities.value.targets) || {})
  const credibilityFormula = computed(
    () => (capabilities.value && capabilities.value.credibility_formula) || null
  )

  /* ---------------------------------------------------------------- 动作 */

  /** 拉取知识库总览 */
  async function loadStats() {
    loadingStats.value = true
    statsError.value = ''
    try {
      stats.value = await getStats()
      return stats.value
    } catch (error) {
      statsError.value = (error && error.message) || '知识库统计加载失败'
      return null
    } finally {
      loadingStats.value = false
    }
  }

  /** 拉取文档列表（沿用 query 中的分页与筛选条件） */
  async function loadDocuments(patch = null) {
    if (patch) query.value = { ...query.value, ...patch }
    loadingDocs.value = true
    try {
      const data = await listDocuments(query.value)
      documents.value = (data && data.items) || []
      docTotal.value = (data && data.meta && data.meta.total) || 0
      return data
    } catch (error) {
      documents.value = []
      docTotal.value = 0
      return null
    } finally {
      loadingDocs.value = false
    }
  }

  /** 重置查询条件 */
  function resetQuery() {
    query.value = {
      keyword: '',
      status: '',
      business_line: '',
      doc_type: '',
      limit: query.value.limit || 10,
      offset: 0
    }
  }

  /** 拉取系统能力清单（失败静默，界面回落到本地兜底枚举） */
  async function loadCapabilities(force = false) {
    if (capabilities.value && !force) return capabilities.value
    loadingCapabilities.value = true
    try {
      capabilities.value = await getCapabilities()
      return capabilities.value
    } catch (error) {
      return null
    } finally {
      loadingCapabilities.value = false
    }
  }

  /** 入库/删除后统一刷新统计与文档列表 */
  async function refreshAll() {
    await Promise.all([loadStats(), loadDocuments()])
  }

  return {
    // 状态
    stats,
    loadingStats,
    statsError,
    documents,
    docTotal,
    loadingDocs,
    query,
    capabilities,
    loadingCapabilities,
    // 计算属性
    documentCount,
    chunkCount,
    vectorCount,
    tagCount,
    ocrDocumentCount,
    meetsTarget,
    knowledgeProgress,
    statusCounts,
    businessLineCounts,
    businessLines,
    docStatuses,
    docTypes,
    retrievalModes,
    features,
    dependencies,
    targets,
    credibilityFormula,
    // 动作
    loadStats,
    loadDocuments,
    resetQuery,
    loadCapabilities,
    refreshAll
  }
})
