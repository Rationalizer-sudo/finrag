<script setup>
/**
 * 收藏页。
 *
 * 功能：分页列表、关键词搜索、备注编辑、取消收藏、展开查看完整回答与条文引用。
 * 收藏数据由后端按 user_id 隔离。
 */
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { deleteFavorite, listFavorites, updateFavoriteNote } from '@/api/favorites'
import AnswerBubble from '@/components/AnswerBubble.vue'
import EmptyState from '@/components/EmptyState.vue'
import { useUserStore } from '@/stores/user'
import { credibilityMeta, formatDateTime, labelFromScore, truncate } from '@/utils'

const userStore = useUserStore()

const items = ref([])
const total = ref(0)
const loading = ref(false)
const keyword = ref('')
const currentPage = ref(1)
const pageSize = ref(10)

/** 备注编辑状态：{ [id]: { editing, draft, saving } } */
const noteState = ref({})

const detailVisible = ref(false)
const detailItem = ref(null)

/** 收藏记录 -> AnswerBubble 的消息结构 */
function toMessage(item) {
  const label = item.credibility_score ? labelFromScore(item.credibility_score) : ''
  return {
    id: `favorite-${item.id}`,
    question: item.question,
    answer: item.answer,
    citations: item.citations || [],
    credibility: label
      ? {
          score: item.credibility_score,
          label,
          components: {},
          reasons: ['收藏记录仅保存综合得分，维度明细请以当次回答为准。']
        }
      : null,
    evidenceCount: (item.citations || []).length,
    retrievalMode: '',
    topK: (item.citations || []).length,
    latencyMs: null,
    withinBudget: true,
    hasAnswer: Boolean(item.answer),
    warnings: [],
    trace: null,
    historyId: item.history_id,
    favorited: true,
    pending: false,
    streaming: false,
    error: ''
  }
}

const detailMessage = computed(() => (detailItem.value ? toMessage(detailItem.value) : null))

/** 加载收藏列表 */
async function loadFavorites() {
  loading.value = true
  try {
    const data = await listFavorites({
      user_id: userStore.userId,
      keyword: keyword.value,
      limit: pageSize.value,
      offset: (currentPage.value - 1) * pageSize.value
    })
    items.value = (data && data.items) || []
    total.value = (data && data.meta && data.meta.total) || 0
    // 重置备注编辑状态
    const next = {}
    items.value.forEach((item) => {
      next[item.id] = { editing: false, draft: item.note || '', saving: false }
    })
    noteState.value = next
  } catch (error) {
    items.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

function handleSearch() {
  currentPage.value = 1
  loadFavorites()
}

function handleReset() {
  keyword.value = ''
  currentPage.value = 1
  loadFavorites()
}

function handlePageChange(page) {
  currentPage.value = page
  loadFavorites()
}

function handleSizeChange(size) {
  pageSize.value = size
  currentPage.value = 1
  loadFavorites()
}

/** 进入备注编辑 */
function startEditNote(item) {
  if (!noteState.value[item.id]) {
    noteState.value[item.id] = { editing: false, draft: '', saving: false }
  }
  noteState.value[item.id].draft = item.note || ''
  noteState.value[item.id].editing = true
}

function cancelEditNote(item) {
  if (noteState.value[item.id]) {
    noteState.value[item.id].editing = false
    noteState.value[item.id].draft = item.note || ''
  }
}

/** 保存备注 */
async function saveNote(item) {
  const state = noteState.value[item.id]
  if (!state) return
  const note = String(state.draft || '').trim()
  if (note.length > 512) {
    ElMessage.warning('备注长度不能超过 512 个字符')
    return
  }
  state.saving = true
  try {
    const updated = await updateFavoriteNote(item.id, note, userStore.userId)
    item.note = updated && updated.note !== undefined ? updated.note : note
    state.editing = false
    ElMessage.success('备注已保存')
  } catch (error) {
    // request.js 已提示
  } finally {
    state.saving = false
  }
}

/** 取消收藏 */
async function handleDelete(item) {
  try {
    await ElMessageBox.confirm(
      `确定取消收藏「${truncate(item.question, 50)}」吗？`,
      '取消收藏',
      { type: 'warning', confirmButtonText: '取消收藏', cancelButtonText: '再想想' }
    )
  } catch (error) {
    return
  }
  try {
    await deleteFavorite(item.id, userStore.userId)
    ElMessage.success('已取消收藏')
    if (items.value.length === 1 && currentPage.value > 1) currentPage.value -= 1
    await loadFavorites()
  } catch (error) {
    // request.js 已提示
  }
}

/** 查看完整回答与引用 */
function openDetail(item) {
  detailItem.value = item
  detailVisible.value = true
}

onMounted(loadFavorites)
</script>

<template>
  <div class="page-container">
    <div class="page-title">
      <h2>
        我的收藏
        <span class="subtitle">用户「{{ userStore.userId }}」共收藏 {{ total }} 条问答</span>
      </h2>
      <div class="flex-wrap-gap">
        <el-button :loading="loading" @click="loadFavorites">
          <el-icon><Refresh /></el-icon>刷新
        </el-button>
      </div>
    </div>

    <el-card class="card-block" shadow="never">
      <div class="filter-row">
        <el-input
          v-model="keyword"
          placeholder="按问题 / 回答 / 备注关键词搜索"
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

      <div v-loading="loading" class="favorite-list">
        <template v-if="items.length">
          <el-card v-for="item in items" :key="item.id" class="favorite-item" shadow="hover">
            <div class="item-head">
              <div class="item-question">
                <el-icon class="q-icon"><StarFilled /></el-icon>
                {{ item.question }}
              </div>
              <div class="item-tags">
                <el-tag
                  v-if="item.credibility_score"
                  :type="credibilityMeta(labelFromScore(item.credibility_score)).type"
                  size="small"
                  effect="dark"
                >
                  可信度 {{ item.credibility_score }} ·
                  {{ labelFromScore(item.credibility_score) }}
                </el-tag>
                <el-tag size="small" effect="plain" type="info">
                  {{ (item.citations || []).length }} 条引用
                </el-tag>
                <span class="text-muted time-text">{{ formatDateTime(item.created_at) }}</span>
              </div>
            </div>

            <div class="item-answer text-muted">{{ truncate(item.answer, 160) || '（无回答内容）' }}</div>

            <!-- 备注 -->
            <div class="note-row">
              <template v-if="noteState[item.id] && noteState[item.id].editing">
                <el-input
                  v-model="noteState[item.id].draft"
                  maxlength="512"
                  show-word-limit
                  size="small"
                  placeholder="记录这条制度要点的备注，例如「适用于 2024 年报送要求」"
                />
                <el-button
                  type="primary"
                  size="small"
                  :loading="noteState[item.id].saving"
                  @click="saveNote(item)"
                >
                  保存
                </el-button>
                <el-button size="small" @click="cancelEditNote(item)">取消</el-button>
              </template>
              <template v-else>
                <div class="note-text">
                  <el-icon><Memo /></el-icon>
                  <span v-if="item.note">{{ item.note }}</span>
                  <span v-else class="text-muted">暂无备注</span>
                </div>
                <el-button link type="primary" size="small" @click="startEditNote(item)">
                  <el-icon><EditPen /></el-icon>{{ item.note ? '编辑备注' : '添加备注' }}
                </el-button>
              </template>
            </div>

            <div class="item-actions">
              <el-button link type="primary" size="small" @click="openDetail(item)">
                <el-icon><View /></el-icon>展开完整回答与条文
              </el-button>
              <el-button link type="danger" size="small" @click="handleDelete(item)">
                <el-icon><Delete /></el-icon>取消收藏
              </el-button>
              <span v-if="item.history_id" class="text-muted source-hint">
                来源历史 ID：{{ item.history_id }}
              </span>
            </div>
          </el-card>
        </template>

        <el-card v-else-if="!loading" shadow="never">
          <EmptyState
            title="还没有收藏任何问答"
            description="在问答页点击回答下方的「收藏该回答」，或前往历史页对某条记录点击「收藏」，即可在此集中管理与补充备注。"
          />
        </el-card>
      </div>

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
    <el-dialog v-model="detailVisible" title="收藏详情" width="880px" top="5vh">
      <div v-if="detailItem && detailItem.note" class="detail-note">
        <el-icon><Memo /></el-icon>
        <span>我的备注：{{ detailItem.note }}</span>
      </div>
      <AnswerBubble v-if="detailMessage" :message="detailMessage" show-question readonly />
      <EmptyState v-else title="暂无数据" description="未能加载收藏详情。" />
      <template #footer>
        <el-button @click="detailVisible = false">关闭</el-button>
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

.favorite-list {
  min-height: 200px;
}

.favorite-item {
  margin-bottom: 12px;
  border-radius: var(--fin-radius-sm);
}

.favorite-item :deep(.el-card__body) {
  padding: 14px 16px;
}

.item-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
}

.item-question {
  display: flex;
  align-items: flex-start;
  gap: 5px;
  flex: 1 1 320px;
  font-size: 14.5px;
  font-weight: 600;
  line-height: 1.5;
  color: var(--fin-text);
}

.q-icon {
  margin-top: 3px;
  color: #e6a23c;
  flex-shrink: 0;
}

.item-tags {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.time-text {
  font-size: 12px;
}

.item-answer {
  margin: 8px 0;
  font-size: 13px;
  line-height: 1.7;
}

.note-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 8px 10px;
  background-color: var(--fin-bg);
  border-radius: var(--fin-radius-sm);
}

.note-text {
  display: flex;
  align-items: center;
  gap: 5px;
  flex: 1 1 240px;
  font-size: 13px;
  color: var(--fin-text-regular);
  word-break: break-word;
}

.item-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px dashed var(--fin-border);
}

.source-hint {
  font-size: 12px;
}

.pagination-row {
  display: flex;
  justify-content: flex-end;
  margin-top: 14px;
}

.detail-note {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  padding: 8px 12px;
  margin-bottom: 12px;
  font-size: 13px;
  color: #8a6d3b;
  background-color: #fdf6ec;
  border: 1px solid #f5dab1;
  border-radius: var(--fin-radius-sm);
}

@media (max-width: 768px) {
  .filter-row :deep(.el-input) {
    width: 100% !important;
  }

  .item-question {
    font-size: 14px;
  }
}
</style>
