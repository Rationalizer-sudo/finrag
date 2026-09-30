<script setup>
/**
 * 知识库管理页。
 *
 * 组成：
 * 1. 统计卡片：文档数 / 知识点数 / 向量数 / 标签数 / OCR 文档数；
 * 2. 达标进度：1000 知识点目标进度条 + 响应时间、准确率、向量索引状态；
 * 3. 组件状态：OCR / 大模型 / 向量化模型 / 向量库的可用性标签；
 * 4. 分布图表：时效状态分布（饼图）与业务条线分布（柱图，ECharts）；
 * 5. 文档管理：搜索筛选、分页表格、元数据编辑、删除、条文查看；
 * 6. 入库：上传文件（multipart）与文本入库（不受 python-multipart 影响）；
 * 7. 向量索引重建与入库任务列表。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import * as echarts from 'echarts'
import { ElMessage, ElMessageBox } from 'element-plus'

import {
  deleteDocument,
  getOcrStatus,
  getUploadLimits,
  ingestText,
  listChunks,
  listTasks,
  rebuildVectors,
  updateDocument,
  uploadDocument
} from '@/api/knowledge'
import EmptyState from '@/components/EmptyState.vue'
import { useKnowledgeStore } from '@/stores/knowledge'
import {
  BUSINESS_LINES,
  DOC_STATUSES,
  DOC_TYPES,
  PROJECT_TARGETS,
  docStatusTagType,
  formatDateTime,
  formatLatency,
  formatNumber,
  taskStatusMeta
} from '@/utils'

const store = useKnowledgeStore()

/* ------------------------------------------------------------------ 文档列表 */

const currentPage = ref(1)
const pageSize = ref(10)

const statusOptions = computed(() =>
  store.docStatuses.length ? store.docStatuses : DOC_STATUSES
)
const businessLineOptions = computed(() =>
  store.businessLines.length ? store.businessLines : BUSINESS_LINES
)
const docTypeOptions = computed(() => (store.docTypes.length ? store.docTypes : DOC_TYPES))

/** 加载当前页文档 */
async function loadDocuments() {
  store.query.offset = (currentPage.value - 1) * pageSize.value
  store.query.limit = pageSize.value
  await store.loadDocuments()
}

function handleSearch() {
  currentPage.value = 1
  loadDocuments()
}

function handleResetQuery() {
  store.resetQuery()
  currentPage.value = 1
  pageSize.value = store.query.limit
  loadDocuments()
}

function handlePageChange(page) {
  currentPage.value = page
  loadDocuments()
}

function handleSizeChange(size) {
  pageSize.value = size
  currentPage.value = 1
  loadDocuments()
}

/* ------------------------------------------------------------------ 元数据编辑 */

const editDialog = ref(false)
const editSaving = ref(false)
const editOriginal = ref({})
const editForm = reactive({
  id: null,
  title: '',
  doc_no: '',
  issuer: '',
  doc_type: '',
  business_line: '',
  status: '',
  publish_date: '',
  effective_date: '',
  expiry_date: '',
  source_url: '',
  remark: ''
})

function openEdit(row) {
  editOriginal.value = { ...row }
  Object.assign(editForm, {
    id: row.id,
    title: row.title || '',
    doc_no: row.doc_no || '',
    issuer: row.issuer || '',
    doc_type: row.doc_type || '',
    business_line: row.business_line || '',
    status: row.status || '',
    publish_date: row.publish_date || '',
    effective_date: row.effective_date || '',
    expiry_date: row.expiry_date || '',
    source_url: row.source_url || '',
    remark: row.remark || ''
  })
  editDialog.value = true
}

/** 只提交发生变化的字段，避免把未填写的字段误清空 */
function buildEditPayload() {
  const original = editOriginal.value
  const payload = {}
  const textFields = [
    'title',
    'doc_no',
    'issuer',
    'doc_type',
    'business_line',
    'status',
    'source_url',
    'remark'
  ]
  const dateFields = ['publish_date', 'effective_date', 'expiry_date']

  textFields.forEach((key) => {
    const next = editForm[key] === null || editForm[key] === undefined ? '' : editForm[key]
    if (next !== (original[key] || '')) payload[key] = next
  })
  dateFields.forEach((key) => {
    // 日期字段传空串表示清除该日期（后端 parse_date('') -> None）
    const next = editForm[key] || ''
    if (next !== (original[key] || '')) payload[key] = next
  })
  return payload
}

async function submitEdit() {
  const payload = buildEditPayload()
  if (!Object.keys(payload).length) {
    ElMessage.info('没有检测到修改内容')
    return
  }
  if (!payload.title && editForm.title !== undefined && !editForm.title) {
    ElMessage.warning('制度名称不能为空')
    return
  }
  editSaving.value = true
  try {
    await updateDocument(editForm.id, payload)
    ElMessage.success('元数据已更新（时效状态变更会同步到条文与向量库）')
    editDialog.value = false
    await Promise.all([loadDocuments(), store.loadStats()])
  } catch (error) {
    // request.js 已提示
  } finally {
    editSaving.value = false
  }
}

/* ------------------------------------------------------------------ 删除文档 */

async function handleDelete(row) {
  try {
    await ElMessageBox.confirm(
      `确定删除《${row.title}》吗？将同时删除其 ${row.chunk_count} 条条文与 Milvus 向量，且不可恢复。`,
      '删除确认',
      { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' }
    )
  } catch (error) {
    return
  }
  try {
    const result = await deleteDocument(row.id)
    ElMessage.success(
      `已删除文档「${result.title || row.title}」，清理条文 ${result.chunk_count} 条` +
        (result.vector_error ? `（向量清理告警：${result.vector_error}）` : '')
    )
    if (store.documents.length === 1 && currentPage.value > 1) currentPage.value -= 1
    await Promise.all([loadDocuments(), store.loadStats(), loadTasks()])
  } catch (error) {
    // request.js 已提示
  }
}

/* -------------------------------------------------------------------- 上传入库 */

const uploadDialog = ref(false)
const uploadRef = ref(null)
const uploading = ref(false)
const uploadPercent = ref(0)
const uploadLimits = ref({ allowed_extensions: [], max_upload_mb: 50 })
const uploadForm = reactive({
  source_url: '',
  replace: true,
  force: false,
  keep_file: true
})

const acceptExtensions = computed(() =>
  (uploadLimits.value.allowed_extensions || []).map((item) => `.${item}`).join(',')
)

/** 上传功能是否可用（后端缺少 python-multipart 时不可用） */
const uploadEnabled = computed(() => store.features.file_upload !== false)

async function openUpload() {
  uploadDialog.value = true
  uploadPercent.value = 0
  if (uploadRef.value) uploadRef.value.clearFiles()
  try {
    uploadLimits.value = await getUploadLimits()
  } catch (error) {
    // 使用默认限制
  }
}

function handleFileChange(file) {
  const maxBytes = Number(uploadLimits.value.max_upload_mb || 50) * 1024 * 1024
  if (file.size && file.size > maxBytes) {
    ElMessage.error(`文件超过大小限制（${uploadLimits.value.max_upload_mb} MB）`)
    if (uploadRef.value) uploadRef.value.clearFiles()
  }
}

async function submitUpload() {
  const files = uploadRef.value ? uploadRef.value.uploadFiles : []
  if (!files || !files.length) {
    ElMessage.warning('请先选择要上传的制度文件')
    return
  }
  const raw = files[0].raw
  if (!raw) {
    ElMessage.warning('未获取到文件内容，请重新选择')
    return
  }

  const formData = new FormData()
  formData.append('file', raw)
  formData.append('source_url', uploadForm.source_url || '')
  formData.append('replace', String(uploadForm.replace))
  formData.append('force', String(uploadForm.force))
  formData.append('keep_file', String(uploadForm.keep_file))

  uploading.value = true
  uploadPercent.value = 0
  try {
    const result = await uploadDocument(formData, (event) => {
      if (event.total) uploadPercent.value = Math.round((event.loaded / event.total) * 100)
    })
    ElMessage.success(
      `入库完成：${result.title || result.file_name}，切分条文 ${result.chunk_count} 条，` +
        `写入向量 ${result.vector_count} 条，耗时 ${formatLatency(result.cost_ms)}`
    )
    if (result.warnings && result.warnings.length) {
      result.warnings.forEach((item) => ElMessage.warning(item))
    }
    uploadDialog.value = false
    await Promise.all([loadDocuments(), store.loadStats(), loadTasks()])
  } catch (error) {
    if (error && error.code === 50010) {
      ElMessage.warning('服务端缺少 python-multipart，已切换提示：请改用「文本入库」通道。')
    }
  } finally {
    uploading.value = false
  }
}

/* -------------------------------------------------------------------- 文本入库 */

const textDialog = ref(false)
const textSubmitting = ref(false)
const textForm = reactive({
  text: '',
  file_name: 'inline.txt',
  source_url: '',
  doc_type: '',
  business_line: '',
  status: '',
  replace: true,
  force: false
})

function openText() {
  textDialog.value = true
}

function resetTextForm() {
  Object.assign(textForm, {
    text: '',
    file_name: 'inline.txt',
    source_url: '',
    doc_type: '',
    business_line: '',
    status: '',
    replace: true,
    force: false
  })
}

async function submitText() {
  const text = String(textForm.text || '').trim()
  if (text.length < 10) {
    ElMessage.warning('制度正文至少需要 10 个字符')
    return
  }
  textSubmitting.value = true
  try {
    const result = await ingestText({
      text,
      file_name: textForm.file_name || 'inline.txt',
      source_url: textForm.source_url || '',
      doc_type: textForm.doc_type || '',
      business_line: textForm.business_line || '',
      status: textForm.status || '',
      replace: textForm.replace,
      force: textForm.force
    })
    ElMessage.success(
      `入库完成：${result.title || result.file_name}，识别文号 ${result.doc_no || '未识别'}，` +
        `条文 ${result.chunk_count} 条，向量 ${result.vector_count} 条`
    )
    if (result.warnings && result.warnings.length) {
      result.warnings.forEach((item) => ElMessage.warning(item))
    }
    textDialog.value = false
    resetTextForm()
    await Promise.all([loadDocuments(), store.loadStats(), loadTasks()])
  } catch (error) {
    // request.js 已提示
  } finally {
    textSubmitting.value = false
  }
}

/* ---------------------------------------------------------------- 条文查看抽屉 */

const chunkDrawer = ref(false)
const chunkLoading = ref(false)
const chunkDocument = ref(null)
const chunkList = ref([])
const chunkTotal = ref(0)
const chunkPage = ref(1)
const chunkPageSize = ref(5)
const chunkKeyword = ref('')

async function openChunks(row) {
  chunkDocument.value = row
  chunkPage.value = 1
  chunkKeyword.value = ''
  chunkDrawer.value = true
  await loadChunks()
}

async function loadChunks() {
  chunkLoading.value = true
  try {
    const data = await listChunks({
      document_id: chunkDocument.value ? chunkDocument.value.id : undefined,
      keyword: chunkKeyword.value,
      limit: chunkPageSize.value,
      offset: (chunkPage.value - 1) * chunkPageSize.value
    })
    chunkList.value = (data && data.items) || []
    chunkTotal.value = (data && data.meta && data.meta.total) || 0
  } catch (error) {
    chunkList.value = []
    chunkTotal.value = 0
  } finally {
    chunkLoading.value = false
  }
}

/* ------------------------------------------------------------------ 向量索引重建 */

const rebuilding = ref(false)

async function handleRebuild() {
  try {
    await ElMessageBox.confirm(
      '将按 MySQL 中现有条文重新生成向量并写入 Milvus。数据量大时耗时较长，是否继续？',
      '重建向量索引',
      { type: 'warning', confirmButtonText: '开始重建', cancelButtonText: '取消' }
    )
  } catch (error) {
    return
  }
  rebuilding.value = true
  try {
    const result = await rebuildVectors()
    ElMessage.success(
      `向量索引重建完成：总数 ${result.total}，写入 ${result.written}，失败 ${result.failed}`
    )
    await Promise.all([store.loadStats(), loadTasks()])
  } catch (error) {
    // request.js 已提示
  } finally {
    rebuilding.value = false
  }
}

/* -------------------------------------------------------------------- 入库任务 */

const tasks = ref([])
const tasksLoading = ref(false)

async function loadTasks() {
  tasksLoading.value = true
  try {
    tasks.value = (await listTasks({ limit: 10 })) || []
  } catch (error) {
    tasks.value = []
  } finally {
    tasksLoading.value = false
  }
}

/* ------------------------------------------------------------------ 组件状态标签 */

const ocrStatus = ref(null)

async function loadOcrStatus() {
  try {
    ocrStatus.value = await getOcrStatus()
  } catch (error) {
    ocrStatus.value = null
  }
}

/** 大模型 / 向量化模型 / 向量库 / OCR 状态汇总 */
const componentStatus = computed(() => {
  const stats = store.stats || {}
  const llm = stats.llm_status || {}
  const embedding = stats.embedding_status || {}
  return [
    {
      name: 'MySQL 业务库',
      ok: store.documentCount >= 0 && !store.statsError,
      detail: store.statsError || `文档 ${store.documentCount} 篇 / 条文 ${store.chunkCount} 条`
    },
    {
      name: 'Milvus 向量库',
      ok: Boolean(stats.vector_ready),
      detail: stats.vector_ready
        ? `向量数 ${stats.vector_count}`
        : stats.vector_error || '未就绪或连接失败'
    },
    {
      name: '通义千问（对话）',
      ok: Boolean(llm.configured),
      detail: llm.configured ? `模型 ${llm.model || '—'}` : '未配置 DASHSCOPE_API_KEY'
    },
    {
      name: '通义千问（向量化）',
      ok: Boolean(embedding.configured),
      detail: embedding.configured
        ? `模型 ${embedding.model || '—'}，维度 ${embedding.dimension || '—'}`
        : '未配置向量化模型'
    },
    {
      name: 'Tesseract OCR',
      ok: Boolean(ocrStatus.value && ocrStatus.value.available),
      detail:
        ocrStatus.value && ocrStatus.value.available
          ? `语言 ${ocrStatus.value.lang}，DPI ${ocrStatus.value.dpi}`
          : (ocrStatus.value && ocrStatus.value.reason) || '不可用（图片/扫描件入库将跳过）'
    }
  ]
})

/* -------------------------------------------------------------------- 达标情况 */

const knowledgeTarget = computed(
  () => Number(store.meetsTarget.knowledge_points_target || PROJECT_TARGETS.knowledgePoints)
)
const knowledgeActual = computed(() =>
  Number(store.meetsTarget.knowledge_points_actual || store.chunkCount || 0)
)
const responseTarget = computed(
  () => Number(store.meetsTarget.response_time_target_ms || PROJECT_TARGETS.responseTimeMs)
)
const accuracyTarget = computed(
  () => Number(store.meetsTarget.accuracy_target || PROJECT_TARGETS.answerAccuracy)
)

/* ---------------------------------------------------------------------- 图表 */

const statusChartRef = ref(null)
const lineChartRef = ref(null)
let statusChart = null
let lineChart = null

const STATUS_COLOR_MAP = {
  现行有效: '#67c23a',
  尚未生效: '#409eff',
  已修订: '#e6a23c',
  已废止: '#f56c6c',
  状态待核: '#909399'
}

function renderCharts() {
  // 时效状态分布（饼图）
  if (statusChartRef.value) {
    const counts = store.statusCounts || {}
    const data = Object.keys(counts).map((key) => ({
      name: key,
      value: counts[key],
      itemStyle: { color: STATUS_COLOR_MAP[key] || '#909399' }
    }))
    if (!statusChart) statusChart = echarts.init(statusChartRef.value)
    statusChart.setOption(
      {
        title: { text: '时效状态分布', left: 'center', textStyle: { fontSize: 13 } },
        tooltip: { trigger: 'item', formatter: '{b}：{c} 篇（{d}%）' },
        legend: { bottom: 0, textStyle: { fontSize: 11 } },
        series: [
          {
            type: 'pie',
            radius: ['38%', '62%'],
            center: ['50%', '48%'],
            avoidLabelOverlap: true,
            label: { fontSize: 11, formatter: '{b}\n{c}' },
            data: data.length ? data : [{ name: '暂无数据', value: 0, itemStyle: { color: '#dcdfe6' } }]
          }
        ]
      },
      true
    )
    statusChart.resize()
  }

  // 业务条线分布（柱图）
  if (lineChartRef.value) {
    const counts = store.businessLineCounts || {}
    const names = Object.keys(counts)
    const values = names.map((key) => counts[key])
    if (!lineChart) lineChart = echarts.init(lineChartRef.value)
    lineChart.setOption(
      {
        title: { text: '业务条线分布', left: 'center', textStyle: { fontSize: 13 } },
        tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
        grid: { left: 40, right: 16, top: 44, bottom: 34 },
        xAxis: {
          type: 'category',
          data: names.length ? names : ['暂无数据'],
          axisLabel: { fontSize: 11, rotate: names.length > 6 ? 30 : 0 }
        },
        yAxis: { type: 'value', minInterval: 1, axisLabel: { fontSize: 11 } },
        series: [
          {
            type: 'bar',
            barMaxWidth: 38,
            itemStyle: { color: '#1f5fbf', borderRadius: [4, 4, 0, 0] },
            label: { show: true, position: 'top', fontSize: 11 },
            data: values.length ? values : [0]
          }
        ]
      },
      true
    )
    lineChart.resize()
  }
}

function handleResize() {
  if (statusChart) statusChart.resize()
  if (lineChart) lineChart.resize()
}

/** 统计数据变化后重绘图表 */
watch(
  () => [store.statusCounts, store.businessLineCounts],
  async () => {
    await nextTick()
    renderCharts()
  },
  { deep: true }
)

/* ---------------------------------------------------------------------- 生命周期 */

const pageLoading = ref(false)

async function loadAll() {
  pageLoading.value = true
  try {
    await Promise.all([
      store.loadStats(),
      loadDocuments(),
      loadTasks(),
      loadOcrStatus(),
      store.loadCapabilities()
    ])
    await nextTick()
    renderCharts()
  } finally {
    pageLoading.value = false
  }
}

onMounted(() => {
  loadAll()
  window.addEventListener('resize', handleResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  if (statusChart) {
    statusChart.dispose()
    statusChart = null
  }
  if (lineChart) {
    lineChart.dispose()
    lineChart = null
  }
})
</script>

<template>
  <div v-loading="pageLoading" class="page-container">
    <div class="page-title">
      <h2>
        知识库管理
        <span class="subtitle">制度文档入库、元数据校核与向量索引维护</span>
      </h2>
      <div class="flex-wrap-gap">
        <el-button :loading="store.loadingStats" @click="loadAll">
          <el-icon><Refresh /></el-icon>刷新
        </el-button>
        <el-button type="warning" plain :loading="rebuilding" @click="handleRebuild">
          <el-icon><RefreshRight /></el-icon>重建向量索引
        </el-button>
        <el-button type="primary" plain @click="openText">
          <el-icon><EditPen /></el-icon>文本入库
        </el-button>
        <el-button type="primary" @click="openUpload">
          <el-icon><UploadFilled /></el-icon>上传文件
        </el-button>
      </div>
    </div>

    <!-- 统计卡片 -->
    <el-row :gutter="14" class="mb-16">
      <el-col :xs="12" :sm="8" :md="4">
        <el-card class="stat-card" shadow="never">
          <div class="stat-label">制度文档</div>
          <div class="stat-value">{{ formatNumber(store.documentCount) }}</div>
          <div class="stat-unit">篇</div>
        </el-card>
      </el-col>
      <el-col :xs="12" :sm="8" :md="5">
        <el-card class="stat-card" shadow="never">
          <div class="stat-label">知识点（条文）</div>
          <div class="stat-value" :class="{ 'text-warning': knowledgeActual < knowledgeTarget }">
            {{ formatNumber(store.chunkCount) }}
          </div>
          <div class="stat-unit">条 / 目标 {{ knowledgeTarget }}</div>
        </el-card>
      </el-col>
      <el-col :xs="12" :sm="8" :md="5">
        <el-card class="stat-card" shadow="never">
          <div class="stat-label">Milvus 向量</div>
          <div class="stat-value">{{ formatNumber(store.vectorCount) }}</div>
          <div class="stat-unit">
            <el-tag :type="store.stats && store.stats.vector_ready ? 'success' : 'danger'" size="small" effect="plain">
              {{ store.stats && store.stats.vector_ready ? '索引就绪' : '索引未就绪' }}
            </el-tag>
          </div>
        </el-card>
      </el-col>
      <el-col :xs="12" :sm="8" :md="4">
        <el-card class="stat-card" shadow="never">
          <div class="stat-label">知识标签</div>
          <div class="stat-value">{{ formatNumber(store.tagCount) }}</div>
          <div class="stat-unit">个</div>
        </el-card>
      </el-col>
      <el-col :xs="12" :sm="8" :md="3">
        <el-card class="stat-card" shadow="never">
          <div class="stat-label">OCR 入库文档</div>
          <div class="stat-value">{{ formatNumber(store.ocrDocumentCount) }}</div>
          <div class="stat-unit">篇</div>
        </el-card>
      </el-col>
      <el-col :xs="12" :sm="8" :md="3">
        <el-card class="stat-card" shadow="never">
          <div class="stat-label">最近更新</div>
          <div class="stat-time">{{ formatDateTime(store.stats && store.stats.latest_updated_at) }}</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 达标进度 -->
    <el-card class="card-block" shadow="never">
      <div class="section-title">课题指标达标情况</div>
      <el-row :gutter="20">
        <el-col :xs="24" :md="12">
          <div class="target-row">
            <div class="flex-between">
              <span>知识点规模（目标 ≥ {{ knowledgeTarget }} 条）</span>
              <span :class="knowledgeActual >= knowledgeTarget ? 'text-success' : 'text-warning'">
                {{ knowledgeActual }} / {{ knowledgeTarget }}（{{ store.knowledgeProgress }}%）
              </span>
            </div>
            <el-progress
              :percentage="store.knowledgeProgress"
              :stroke-width="16"
              :status="knowledgeActual >= knowledgeTarget ? 'success' : undefined"
              :color="knowledgeActual >= knowledgeTarget ? '#67c23a' : '#e6a23c'"
              text-inside
            />
          </div>
        </el-col>
        <el-col :xs="24" :md="12">
          <div class="target-list">
            <div class="target-item">
              <span>响应时间（目标 ≤ {{ responseTarget }} ms）</span>
              <el-tag :type="responseTarget ? 'success' : 'info'" size="small" effect="plain">
                预算 {{ formatLatency(responseTarget) }}
              </el-tag>
            </div>
            <div class="target-item">
              <span>回答准确率（目标 ≥ {{ (accuracyTarget * 100).toFixed(0) }}%）</span>
              <el-tag type="success" size="small" effect="plain">
                评测目标 {{ (accuracyTarget * 100).toFixed(0) }}%
              </el-tag>
            </div>
            <div class="target-item">
              <span>向量索引就绪</span>
              <el-tag :type="store.meetsTarget.vector_index_ok ? 'success' : 'danger'" size="small" effect="plain">
                {{ store.meetsTarget.vector_index_ok ? '已就绪' : '未就绪' }}
              </el-tag>
            </div>
          </div>
        </el-col>
      </el-row>

      <!-- 组件状态 -->
      <el-divider content-position="left"><span class="divider-title">依赖组件状态</span></el-divider>
      <div class="component-grid">
        <div v-for="item in componentStatus" :key="item.name" class="component-item">
          <el-tag :type="item.ok ? 'success' : 'danger'" size="small" effect="dark">
            {{ item.ok ? '可用' : '异常' }}
          </el-tag>
          <div class="component-body">
            <div class="component-name">{{ item.name }}</div>
            <div class="component-detail text-muted">{{ item.detail }}</div>
          </div>
        </div>
      </div>
    </el-card>

    <!-- 分布图表 -->
    <el-row :gutter="14" class="mb-16">
      <el-col :xs="24" :md="10">
        <el-card class="card-block chart-card" shadow="never">
          <div ref="statusChartRef" class="chart-box" />
        </el-card>
      </el-col>
      <el-col :xs="24" :md="14">
        <el-card class="card-block chart-card" shadow="never">
          <div ref="lineChartRef" class="chart-box" />
        </el-card>
      </el-col>
    </el-row>

    <!-- 文档表格 -->
    <el-card class="card-block" shadow="never">
      <div class="section-title">制度文档（共 {{ store.docTotal }} 篇）</div>

      <div class="filter-row">
        <el-input
          v-model="store.query.keyword"
          placeholder="按制度名称 / 文号 / 发文机关搜索"
          clearable
          style="width: 260px"
          @keyup.enter="handleSearch"
        >
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
        <el-select v-model="store.query.status" placeholder="时效状态" clearable style="width: 140px">
          <el-option v-for="item in statusOptions" :key="item" :label="item" :value="item" />
        </el-select>
        <el-select v-model="store.query.business_line" placeholder="业务条线" clearable style="width: 140px">
          <el-option v-for="item in businessLineOptions" :key="item" :label="item" :value="item" />
        </el-select>
        <el-select v-model="store.query.doc_type" placeholder="文件类型" clearable style="width: 150px">
          <el-option v-for="item in docTypeOptions" :key="item" :label="item" :value="item" />
        </el-select>
        <el-button type="primary" @click="handleSearch">
          <el-icon><Search /></el-icon>查询
        </el-button>
        <el-button @click="handleResetQuery">重置</el-button>
      </div>

      <el-table
        v-loading="store.loadingDocs"
        :data="store.documents"
        stripe
        border
        style="width: 100%"
        :row-key="(row) => row.id"
      >
        <el-table-column prop="id" label="ID" width="66" align="center" />
        <el-table-column prop="title" label="制度名称" min-width="220" show-overflow-tooltip>
          <template #default="{ row }">
            <div class="doc-title">{{ row.title }}</div>
            <div v-if="row.tags && row.tags.length" class="doc-tags">
              <el-tag v-for="tag in row.tags.slice(0, 4)" :key="tag" size="small" type="info" effect="plain">
                {{ tag }}
              </el-tag>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="doc_no" label="文号" width="150" show-overflow-tooltip />
        <el-table-column prop="doc_type" label="类型" width="110" show-overflow-tooltip />
        <el-table-column prop="business_line" label="业务条线" width="100" />
        <el-table-column label="时效状态" width="108" align="center">
          <template #default="{ row }">
            <el-tag :type="docStatusTagType(row.status)" size="small" effect="dark">
              {{ row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="施行日期" width="112" align="center">
          <template #default="{ row }">{{ row.effective_date || '—' }}</template>
        </el-table-column>
        <el-table-column label="条文数" width="88" align="center">
          <template #default="{ row }">
            <el-tag type="info" size="small" effect="plain">{{ row.chunk_count }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="OCR" width="72" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.ocr_used" type="warning" size="small" effect="plain">是</el-tag>
            <span v-else class="text-muted">否</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="210" fixed="right" align="center">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openChunks(row)">
              <el-icon><View /></el-icon>条文
            </el-button>
            <el-button link type="primary" size="small" @click="openEdit(row)">
              <el-icon><Edit /></el-icon>编辑
            </el-button>
            <el-button link type="danger" size="small" @click="handleDelete(row)">
              <el-icon><Delete /></el-icon>删除
            </el-button>
          </template>
        </el-table-column>
        <template #empty>
          <EmptyState
            title="知识库中还没有匹配的制度文档"
            description="可以点击右上角「文本入库」粘贴制度全文，或「上传文件」导入 txt / pdf / docx / 图片（需 OCR）。"
          />
        </template>
      </el-table>

      <div class="pagination-row">
        <el-pagination
          :current-page="currentPage"
          :page-size="pageSize"
          :page-sizes="[10, 20, 50, 100]"
          :total="store.docTotal"
          layout="total, sizes, prev, pager, next, jumper"
          background
          @current-change="handlePageChange"
          @size-change="handleSizeChange"
        />
      </div>
    </el-card>

    <!-- 入库任务 -->
    <el-card class="card-block" shadow="never">
      <div class="flex-between mb-8">
        <div class="section-title" style="margin-bottom: 0">最近入库任务</div>
        <el-button link type="primary" size="small" :loading="tasksLoading" @click="loadTasks">
          <el-icon><Refresh /></el-icon>刷新
        </el-button>
      </div>
      <el-table v-loading="tasksLoading" :data="tasks" size="small" stripe style="width: 100%">
        <el-table-column prop="task_no" label="任务号" width="200" show-overflow-tooltip />
        <el-table-column prop="file_name" label="文件名" min-width="180" show-overflow-tooltip />
        <el-table-column label="状态" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="taskStatusMeta(row.status).type" size="small" effect="plain">
              {{ taskStatusMeta(row.status).label }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="stage" label="阶段" width="120" show-overflow-tooltip />
        <el-table-column label="条文进度" width="120" align="center">
          <template #default="{ row }">
            {{ row.processed_chunks }} / {{ row.total_chunks }}
          </template>
        </el-table-column>
        <el-table-column label="OCR" width="72" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.ocr_used" type="warning" size="small" effect="plain">是</el-tag>
            <span v-else class="text-muted">否</span>
          </template>
        </el-table-column>
        <el-table-column label="耗时" width="92" align="center">
          <template #default="{ row }">{{ formatLatency(row.cost_ms) }}</template>
        </el-table-column>
        <el-table-column prop="message" label="说明" min-width="180" show-overflow-tooltip />
        <el-table-column label="创建时间" width="160" align="center">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <template #empty>
          <div class="text-muted" style="padding: 12px">暂无入库任务记录</div>
        </template>
      </el-table>
    </el-card>

    <!-- 上传对话框 -->
    <el-dialog v-model="uploadDialog" title="上传制度文件入库" width="620px" :close-on-click-modal="false">
      <el-alert
        v-if="!uploadEnabled"
        type="warning"
        :closable="false"
        show-icon
        class="mb-12"
        title="服务端未安装 python-multipart，文件上传不可用"
        description="请在后端环境执行 pip install python-multipart 后重启服务，或改用「文本入库」通道（不依赖该组件）。"
      />
      <el-alert
        type="info"
        :closable="false"
        show-icon
        class="mb-12"
        title="仅支持公开制度文件"
        description="禁止上传涉密或企业敏感材料。入库链路：解析 -> 清洗 -> 元数据抽取 -> 条文切分 -> MySQL 落库 -> 向量化写入 Milvus。"
      />

      <el-upload
        ref="uploadRef"
        drag
        :auto-upload="false"
        :limit="1"
        :accept="acceptExtensions"
        :on-change="handleFileChange"
      >
        <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
        <div class="el-upload__text">将文件拖到此处，或<em>点击选择文件</em></div>
        <template #tip>
          <div class="el-upload__tip">
            支持格式：{{ (uploadLimits.allowed_extensions || []).join(' / ') || '加载中…' }}，
            单个文件不超过 {{ uploadLimits.max_upload_mb }} MB
          </div>
        </template>
      </el-upload>

      <el-form label-width="110px" class="mt-12">
        <el-form-item label="公开来源链接">
          <el-input v-model="uploadForm.source_url" placeholder="如 https://www.pbc.gov.cn/...（选填）" />
        </el-form-item>
        <el-form-item label="入库选项">
          <div class="flex-wrap-gap">
            <el-checkbox v-model="uploadForm.replace">同文件覆盖旧版本</el-checkbox>
            <el-checkbox v-model="uploadForm.force">内容未变也强制重新入库</el-checkbox>
            <el-checkbox v-model="uploadForm.keep_file">保留上传原文件</el-checkbox>
          </div>
        </el-form-item>
        <el-form-item v-if="uploading" label="上传进度">
          <el-progress :percentage="uploadPercent" :stroke-width="12" style="width: 100%" />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="uploadDialog = false">取消</el-button>
        <el-button type="primary" :loading="uploading" :disabled="!uploadEnabled" @click="submitUpload">
          开始入库
        </el-button>
      </template>
    </el-dialog>

    <!-- 文本入库对话框 -->
    <el-dialog
      v-model="textDialog"
      title="文本入库（粘贴制度全文）"
      width="760px"
      top="6vh"
      :close-on-click-modal="false"
    >
      <el-form label-width="110px">
        <el-form-item label="制度全文" required>
          <el-input
            v-model="textForm.text"
            type="textarea"
            :rows="12"
            maxlength="200000"
            show-word-limit
            placeholder="请粘贴制度全文（至少 10 个字符）。系统会自动清洗、按「第X条」切分、抽取文号/发文机关/施行日期并向量化。"
          />
        </el-form-item>
        <el-row :gutter="12">
          <el-col :xs="24" :sm="12">
            <el-form-item label="文件名标识">
              <el-input v-model="textForm.file_name" placeholder="例如 商业银行流动性风险管理办法.txt" />
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="12">
            <el-form-item label="公开来源链接">
              <el-input v-model="textForm.source_url" placeholder="选填" />
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="12">
            <el-form-item label="文件类型">
              <el-select v-model="textForm.doc_type" placeholder="留空则自动推断" clearable style="width: 100%">
                <el-option v-for="item in docTypeOptions" :key="item" :label="item" :value="item" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="12">
            <el-form-item label="业务条线">
              <el-select v-model="textForm.business_line" placeholder="留空则自动推断" clearable style="width: 100%">
                <el-option v-for="item in businessLineOptions" :key="item" :label="item" :value="item" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="12">
            <el-form-item label="时效状态">
              <el-select v-model="textForm.status" placeholder="留空则自动推断" clearable style="width: 100%">
                <el-option v-for="item in statusOptions" :key="item" :label="item" :value="item" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="12">
            <el-form-item label="入库选项">
              <div class="flex-wrap-gap">
                <el-checkbox v-model="textForm.replace">覆盖旧版本</el-checkbox>
                <el-checkbox v-model="textForm.force">强制重新入库</el-checkbox>
              </div>
            </el-form-item>
          </el-col>
        </el-row>
      </el-form>

      <template #footer>
        <el-button @click="textDialog = false">取消</el-button>
        <el-button @click="resetTextForm">清空表单</el-button>
        <el-button type="primary" :loading="textSubmitting" @click="submitText">
          提交入库
        </el-button>
      </template>
    </el-dialog>

    <!-- 元数据编辑对话框 -->
    <el-dialog
      v-model="editDialog"
      :title="`编辑元数据：${editOriginal.title || ''}`"
      width="720px"
      :close-on-click-modal="false"
    >
      <el-alert
        type="info"
        :closable="false"
        show-icon
        class="mb-12"
        title="人工校核优先于自动抽取"
        description="修改时效状态或施行/失效日期后，系统会同步更新该文档所有条文与向量库的过滤字段，下一次检索立即生效。"
      />
      <el-form label-width="110px">
        <el-form-item label="制度名称">
          <el-input v-model="editForm.title" maxlength="512" />
        </el-form-item>
        <el-row :gutter="12">
          <el-col :xs="24" :sm="12">
            <el-form-item label="文号">
              <el-input v-model="editForm.doc_no" maxlength="128" placeholder="如 银发〔2020〕15号" />
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="12">
            <el-form-item label="发文机关">
              <el-input v-model="editForm.issuer" maxlength="255" />
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="12">
            <el-form-item label="文件类型">
              <el-select v-model="editForm.doc_type" clearable style="width: 100%">
                <el-option v-for="item in docTypeOptions" :key="item" :label="item" :value="item" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="12">
            <el-form-item label="业务条线">
              <el-select v-model="editForm.business_line" clearable style="width: 100%">
                <el-option v-for="item in businessLineOptions" :key="item" :label="item" :value="item" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="12">
            <el-form-item label="时效状态">
              <el-select v-model="editForm.status" clearable style="width: 100%">
                <el-option v-for="item in statusOptions" :key="item" :label="item" :value="item" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="12">
            <el-form-item label="来源链接">
              <el-input v-model="editForm.source_url" maxlength="512" />
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="8">
            <el-form-item label="发布日期">
              <el-date-picker
                v-model="editForm.publish_date"
                type="date"
                value-format="YYYY-MM-DD"
                placeholder="选择日期"
                clearable
                style="width: 100%"
              />
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="8">
            <el-form-item label="施行日期">
              <el-date-picker
                v-model="editForm.effective_date"
                type="date"
                value-format="YYYY-MM-DD"
                placeholder="选择日期"
                clearable
                style="width: 100%"
              />
            </el-form-item>
          </el-col>
          <el-col :xs="24" :sm="8">
            <el-form-item label="失效日期">
              <el-date-picker
                v-model="editForm.expiry_date"
                type="date"
                value-format="YYYY-MM-DD"
                placeholder="选择日期"
                clearable
                style="width: 100%"
              />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="备注">
          <el-input
            v-model="editForm.remark"
            type="textarea"
            :rows="2"
            maxlength="512"
            show-word-limit
            placeholder="如人工校核结论、数据来源说明"
          />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="editDialog = false">取消</el-button>
        <el-button type="primary" :loading="editSaving" @click="submitEdit">保存修改</el-button>
      </template>
    </el-dialog>

    <!-- 条文抽屉 -->
    <el-drawer v-model="chunkDrawer" size="62%" :title="`条文列表：${chunkDocument ? chunkDocument.title : ''}`">
      <div class="filter-row">
        <el-input
          v-model="chunkKeyword"
          placeholder="在本文档内搜索条文内容"
          clearable
          style="width: 240px"
          @keyup.enter="chunkPage = 1; loadChunks()"
        >
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
        <el-button type="primary" @click="chunkPage = 1; loadChunks()">搜索</el-button>
      </div>

      <div v-loading="chunkLoading" class="chunk-list">
        <template v-if="chunkList.length">
          <div v-for="chunk in chunkList" :key="chunk.id" class="chunk-item">
            <div class="chunk-head">
              <span class="chunk-index">#{{ chunk.chunk_index }}</span>
              <span v-if="chunk.clause_no" class="chunk-clause">{{ chunk.clause_no }}</span>
              <span v-if="chunk.chapter" class="text-muted">{{ chunk.chapter }}</span>
              <el-tag :type="docStatusTagType(chunk.status)" size="small" effect="dark">
                {{ chunk.status }}
              </el-tag>
              <span class="text-muted chunk-stat">{{ chunk.char_count }} 字</span>
              <span class="text-muted chunk-stat">
                向量：{{ chunk.milvus_pk ? `已写入(#${chunk.milvus_pk})` : '未写入' }}
              </span>
            </div>
            <div v-if="chunk.article_title" class="chunk-article">{{ chunk.article_title }}</div>
            <div class="chunk-content">{{ chunk.content }}</div>
          </div>
        </template>
        <EmptyState v-else title="本文档暂无条文" description="可能尚未完成切分，或关键词未匹配到任何条文。" />
      </div>

      <div class="pagination-row">
        <el-pagination
          :current-page="chunkPage"
          :page-size="chunkPageSize"
          :total="chunkTotal"
          layout="total, prev, pager, next"
          small
          background
          @current-change="
            (page) => {
              chunkPage = page
              loadChunks()
            }
          "
        />
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.stat-card {
  margin-bottom: 14px;
  border-radius: var(--fin-radius);
}

.stat-card :deep(.el-card__body) {
  padding: 12px 14px;
}

.stat-label {
  font-size: 12.5px;
  color: var(--fin-text-secondary);
}

.stat-value {
  margin: 2px 0;
  font-size: 24px;
  font-weight: 700;
  line-height: 1.2;
  color: var(--fin-primary);
}

.stat-unit {
  font-size: 12px;
  color: var(--fin-text-secondary);
}

.stat-time {
  margin-top: 6px;
  font-size: 12.5px;
  line-height: 1.5;
  color: var(--fin-text-regular);
}

.divider-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--fin-text-regular);
}

.target-row {
  padding-right: 12px;
  font-size: 13px;
}

.target-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.target-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 6px 12px;
  font-size: 13px;
  background-color: var(--fin-bg);
  border-radius: var(--fin-radius-sm);
}

.component-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 10px;
}

.component-item {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 10px 12px;
  background-color: #fafbfc;
  border: 1px solid var(--fin-border);
  border-radius: var(--fin-radius-sm);
}

.component-name {
  font-size: 13px;
  font-weight: 600;
}

.component-detail {
  font-size: 12px;
  line-height: 1.5;
  word-break: break-word;
}

.chart-card {
  min-height: 280px;
}

.chart-box {
  width: 100%;
  height: 260px;
}

.filter-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}

.doc-title {
  font-weight: 500;
  line-height: 1.5;
}

.doc-tags {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
  margin-top: 3px;
}

.pagination-row {
  display: flex;
  justify-content: flex-end;
  margin-top: 14px;
}

.chunk-list {
  min-height: 200px;
}

.chunk-item {
  padding: 10px 12px;
  margin-bottom: 10px;
  border: 1px solid var(--fin-border);
  border-radius: var(--fin-radius-sm);
}

.chunk-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 6px;
  font-size: 12.5px;
}

.chunk-index {
  font-weight: 700;
  color: var(--fin-primary);
}

.chunk-clause {
  font-weight: 600;
}

.chunk-stat {
  font-size: 11.5px;
}

.chunk-article {
  margin-bottom: 4px;
  font-size: 13px;
  font-weight: 600;
  color: var(--fin-text-regular);
}

.chunk-content {
  font-size: 13px;
  line-height: 1.8;
  color: var(--fin-text-regular);
  white-space: pre-wrap;
  word-break: break-word;
}

@media (max-width: 768px) {
  .chart-box {
    height: 220px;
  }

  .filter-row :deep(.el-input),
  .filter-row :deep(.el-select) {
    width: 100% !important;
  }
}
</style>
