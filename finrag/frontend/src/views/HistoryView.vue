<script setup>
/**
 * 问答历史页。
 *
 * 功能：关键词搜索、分页、查看详情（含条文引用与可信度）、单条删除、清空全部、一键收藏。
 * 详情弹窗复用 AnswerBubble 组件，保证与问答页的展示口径完全一致。
 */
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { addFavorite } from '@/api/favorites'
import { clearHistory, deleteHistory, getHistory, listHistory } from '@/api/qa'
import AnswerBubble from '@/components/AnswerBubble.vue'
import EmptyState from '@/components/EmptyState.vue'
import { useUserStore } from '@/stores/user'
import {
  RETRIEVAL_MODES,
  credibilityMeta,
  formatDateTime,
  formatLatency,
  labelFromScore,
  truncate
} from '@/utils'

const userStore = useUserStore()

const items = ref([])
const total = ref(0)
const loading = ref(false)
const keyword = ref('')
const currentPage = ref(1)
const pageSize = ref(10)

const detailVisible = ref(false)
const detailLoading = ref(false)
const detailItem = ref(null)
const favoritingId = ref(null)

/** 检索模式英文值 -> 中文名 */
function modeLabel(mode) {
  const hit = RETRIEVAL_MODES.find((item) => item.value === mode)
  return hit ? hit.label : mode || '—'
}

/** 耗时是否超出 2 秒预算 */
function overBudget(ms) {
  return Number(ms || 0) > 2000
}

/** 历史记录 -> AnswerBubble 需要的消息结构 */
function toMessage(item) {
  const label = item.credibility_label || (item.credibility_score ? labelFromScore(item.credibility_score) : '')
  return {
    id: `history-${item.id}`,
    question: item.question,
    answer: item.answer,
    citations: item.citations || [],
    credibility: label
      ? {
          score: item.credibility_score || 0,
          label,
          components: {},
          reasons: ['历史记录仅保存综合得分，维度明细请以当次回答为准。']
        }
      : null,
    evidenceCount: item.evidence_count || 0,
    retrievalMode: item.retrieval_mode || '',
    topK: item.top_k || 0,
    latencyMs: item.latency_ms || 0,
    withinBudget: !overBudget(item.latency_ms),
    hasAnswer: item.has_answer !== false,
    warnings: [],
    trace: null,
    historyId: item.id,
    favorited: Boolean(item.favorited),
    pending: false,
    streaming: false,
    error: ''
  }
}

const detailMessage = computed(() => (detailItem.value ? toMessage(detailItem.value) : null))

/** 加载历史列表 */
async function loadHistory() {
  loading.value = true
  try {
    const data = await listHistory({
      user_id: userStore.userId,
      keyword: keyword.value,
      limit: pageSize.value,
      offset: (currentPage.value - 1) * pageSize.value
    })
    items.value = (data && data.items) || []
    total.value = (data && data.meta && data.meta.total) || 0
  } catch (error) {
    items.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

function handleSearch() {
  currentPage.value = 1
  loadHistory()
}

function handleReset() {
  keyword.value = ''
  currentPage.value = 1
  loadHistory()
}

function handlePageChange(page) {
  currentPage.value = page
  loadHistory()
}

function handleSizeChange(size) {
  pageSize.value = size
  currentPage.value = 1
  loadHistory()
}

/** 查看详情：拉取单条记录以获得完整引用 */
async function openDetail(row) {
  detailVisible.value = true
  detailLoading.value = true
  detailItem.value = row
  try {
    detailItem.value = await getHistory(row.id, userStore.userId)
  } catch (error) {
    // 保留列表中的精简数据
  } finally {
    detailLoading.value = false
  }
}

/** 删除单条 */
async function handleDelete(row) {
  try {
    await ElMessageBox.confirm(`确定删除该条问答历史吗？\n\n问：${truncate(row.question, 60)}`, '删除确认', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消'
    })
  } catch (error) {
    return
  }
  try {
    await deleteHistory(row.id, userStore.userId)
    ElMessage.success('已删除该条历史')
    if (items.value.length === 1 && currentPage.value > 1) currentPage.value -= 1
    await loadHistory()
  } catch (error) {
    // request.js 已提示
  }
}

/** 清空全部历史 */
async function handleClearAll() {
  if (!total.value) {
    ElMessage.info('当前没有可清空的历史记录')
    return
  }
  try {
    await ElMessageBox.confirm(
      `将清空用户「${userStore.userId}」的全部 ${total.value} 条问答历史，且不可恢复，是否继续？`,
      '清空全部历史',
      { type: 'warning', confirmButtonText: '全部清空', cancelButtonText: '取消' }
    )
  } catch (error) {
    return
  }
  try {
    const result = await clearHistory(userStore.userId)
    ElMessage.success(`已清空 ${result.deleted_count} 条历史记录`)
    currentPage.value = 1
    await loadHistory()
  } catch (error) {
    // request.js 已提示
  }
}

/** 一键收藏 */
async function handleFavorite(row) {
  if (row.favorited) {
    ElMessage.info('该条问答已收藏')
    return
  }
  favoritingId.value = row.id
  try {
    await addFavorite({
      history_id: row.id,
      question: row.question,
      answer: row.answer,
      note: '',
      user_id: userStore.userId
    })
    ElMessage.success('已加入收藏')
    await loadHistory()
  } catch (error) {
    // request.js 已提示
  } finally {
    favoritingId.value = null
  }
}

onMounted(loadHistory)
</script>

<template>
  <div class="page-container">
    <div class="page-title">
      <h2>
        问答历史
        <span class="subtitle">
          用户「{{ userStore.userId }}」共 {{ total }} 条记录
        </span>
      </h2>
      <div class="flex-wrap-gap">
        <el-button :loading="loading" @click="loadHistory">
          <el-icon><Refresh /></el-icon>刷新
        </el-button>
        <el-button type="danger" plain :disabled="!total" @click="handleClearAll">
          <el-icon><Delete /></el-icon>清空全部
        </el-button>
      </div>
    </div>

    <el-card class="card-block" shadow="never">
      <div class="filter-row">
        <el-input
          v-model="keyword"
          placeholder="按问题或回答关键词搜索"
          clearable
          style="width: 300px"
          @keyup.enter="handleSearch"
        >
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
        <el-button type="primary" @click="handleSearch">
          <el-icon><Search /></el-icon>查询
        </el-button>
        <el-button @click="handleReset">重置</el-button>
      </div>

      <el-table v-loading="loading" :data="items" stripe border style="width: 100%">
        <el-table-column prop="id" label="ID" width="70" align="center" />
        <el-table-column label="问题 / 回答摘要" min-width="300">
          <template #default="{ row }">
            <div class="q-text">
              <el-icon class="q-icon"><ChatLineSquare /></el-icon>{{ row.question }}
            </div>
            <div class="a-text text-muted">{{ truncate(row.answer, 90) || '（无回答内容）' }}</div>
          </template>
        </el-table-column>
        <el-table-column label="检索模式" width="112" align="center">
          <template #default="{ row }">
            <el-tag size="small" effect="plain" type="info">{{ modeLabel(row.retrieval_mode) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="证据" width="72" align="center">
          <template #default="{ row }">{{ row.evidence_count }}</template>
        </el-table-column>
        <el-table-column label="耗时" width="100" align="center">
          <template #default="{ row }">
            <span :class="overBudget(row.latency_ms) ? 'text-warning' : ''">
              {{ formatLatency(row.latency_ms) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="可信度" width="140" align="center">
          <template #default="{ row }">
            <el-tag
              v-if="row.credibility_label"
              :type="credibilityMeta(row.credibility_label).type"
              size="small"
              effect="dark"
            >
              {{ row.credibility_score }} · {{ row.credibility_label }}
            </el-tag>
            <span v-else class="text-muted">未评定</span>
          </template>
        </el-table-column>
        <el-table-column label="收藏" width="80" align="center">
          <template #default="{ row }">
            <el-icon v-if="row.favorited" class="text-success"><StarFilled /></el-icon>
            <span v-else class="text-muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="时间" width="164" align="center">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="190" fixed="right" align="center">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openDetail(row)">
              <el-icon><View /></el-icon>详情
            </el-button>
            <el-button
              link
              :type="row.favorited ? 'success' : 'primary'"
              size="small"
              :disabled="row.favorited"
              :loading="favoritingId === row.id"
              @click="handleFavorite(row)"
            >
              <el-icon><component :is="row.favorited ? 'StarFilled' : 'Star'" /></el-icon>
              {{ row.favorited ? '已收藏' : '收藏' }}
            </el-button>
            <el-button link type="danger" size="small" @click="handleDelete(row)">
              <el-icon><Delete /></el-icon>删除
            </el-button>
          </template>
        </el-table-column>
        <template #empty>
          <EmptyState
            title="暂无问答历史"
            description="历史记录由非流式问答自动写入（流式模式不落库）。前往问答页提问后即可在此回溯、收藏与管理。"
          />
        </template>
      </el-table>

      <div class="pagination-row">
        <el-pagination
          :current-page="currentPage"
          :page-size="pageSize"
          :page-sizes="[10, 20, 50]"
          :total="total"
          layout="total, sizes, prev, pager, next, jumper"
          background
          @current-change="handlePageChange"
          @size-change="handleSizeChange"
        />
      </div>
    </el-card>

    <!-- 详情弹窗 -->
    <el-dialog v-model="detailVisible" title="问答详情" width="880px" top="5vh">
      <div v-loading="detailLoading">
        <AnswerBubble v-if="detailMessage" :message="detailMessage" show-question readonly />
        <EmptyState v-else title="暂无数据" description="未能加载该条历史详情。" />
      </div>
      <template #footer>
        <el-button @click="detailVisible = false">关闭</el-button>
        <el-button
          v-if="detailItem && !detailItem.favorited"
          type="primary"
          @click="handleFavorite(detailItem)"
        >
          <el-icon><Star /></el-icon>收藏该回答
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.filter-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}

.q-text {
  display: flex;
  align-items: flex-start;
  gap: 4px;
  font-weight: 600;
  line-height: 1.5;
  color: var(--fin-text);
}

.q-icon {
  margin-top: 3px;
  color: var(--fin-primary);
  flex-shrink: 0;
}

.a-text {
  margin-top: 3px;
  font-size: 12.5px;
  line-height: 1.6;
}

.pagination-row {
  display: flex;
  justify-content: flex-end;
  margin-top: 14px;
}

@media (max-width: 768px) {
  .filter-row :deep(.el-input) {
    width: 100% !important;
  }
}
</style>
