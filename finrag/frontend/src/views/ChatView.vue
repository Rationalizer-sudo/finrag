<script setup>
/**
 * 智能问答页（主页面）。
 *
 * 组成：
 * - 提问区：问题输入（支持 Ctrl+Enter 提交）、检索参数（模式 / Top-K / 业务条线 / 文件类型 /
 *   时效状态 / 时效过滤开关 / 流式开关 / 保存历史开关）；
 * - 结果区：消息流（回答气泡 + 条文溯源 + 可信度 + 性能提示）；
 * - 侧栏：本轮会话记录、参数说明与常见问题示例。
 */
import { computed, nextTick, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import { addFavorite } from '@/api/favorites'
import AnswerBubble from '@/components/AnswerBubble.vue'
import EmptyState from '@/components/EmptyState.vue'
import { useChatStore } from '@/stores/chat'
import { useKnowledgeStore } from '@/stores/knowledge'
import { useUserStore } from '@/stores/user'
import { BUSINESS_LINES, DOC_STATUSES, DOC_TYPES, RETRIEVAL_MODES } from '@/utils'

const chatStore = useChatStore()
const knowledgeStore = useKnowledgeStore()
const userStore = useUserStore()

/** 输入框内容与引用 */
const question = ref('')
const questionRef = ref(null)
/** 消息区滚动容器 */
const scrollRef = ref(null)
/** 最近一次提问（失败后可一键重试） */
const lastQuestion = ref('')

const TOP_K_OPTIONS = [3, 5, 8, 10, 15, 20]

/** 常见问题示例（点击直接填入输入框） */
const EXAMPLES = [
  '商业银行流动性风险管理的报送频率是多少',
  '银行理财产品的销售起点金额有何规定',
  '证券公司信息披露的时限要求',
  '反洗钱客户身份识别的保存期限',
  '保险公司偿付能力充足率的最低要求'
]

/** 下拉选项：优先后端 /capabilities 下发，缺失时用本地兜底常量 */
const businessLineOptions = computed(() =>
  knowledgeStore.businessLines.length ? knowledgeStore.businessLines : BUSINESS_LINES
)
const docTypeOptions = computed(() =>
  knowledgeStore.docTypes.length ? knowledgeStore.docTypes : DOC_TYPES
)
const statusOptions = computed(() =>
  knowledgeStore.docStatuses.length ? knowledgeStore.docStatuses : DOC_STATUSES
)

const options = computed(() => chatStore.options)
const messages = computed(() => chatStore.messages)
const asking = computed(() => chatStore.asking)

/** 滚动到消息区底部 */
async function scrollToBottom() {
  await nextTick()
  const container = scrollRef.value
  if (container) container.scrollTop = container.scrollHeight
}

/** 滚动到指定消息 */
function scrollToMessage(id) {
  const element = document.getElementById(`msg-${id}`)
  if (element) element.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

/** 提交提问 */
async function handleAsk() {
  const text = question.value.trim()
  if (!text) {
    ElMessage.warning('请输入问题后再提交')
    return
  }
  if (text.length > 500) {
    ElMessage.warning('问题长度不能超过 500 个字符')
    return
  }
  if (asking.value) {
    ElMessage.info('上一条问题仍在生成中，请稍候或点击「中断生成」')
    return
  }

  lastQuestion.value = text
  question.value = ''
  scrollToBottom()

  const message = await chatStore.ask(text)
  scrollToBottom()

  if (message && message.error) {
    // 失败时把问题回填，便于修改后重试
    question.value = text
  }
}

/** 中断流式生成 */
function handleAbort() {
  chatStore.abort()
  ElMessage.info('已中断本次生成')
}

/** 重试最近一次提问 */
function handleRetry() {
  if (!lastQuestion.value) return
  if (asking.value) {
    ElMessage.info('当前仍在生成中')
    return
  }
  chatStore.ask(lastQuestion.value).then(scrollToBottom)
}

/** 点击示例问题 */
function useExample(text) {
  question.value = text
  if (questionRef.value) questionRef.value.focus()
}

/** 清空当前会话 */
async function handleClear() {
  if (!chatStore.hasMessages) {
    ElMessage.info('当前会话还没有内容')
    return
  }
  try {
    await ElMessageBox.confirm(
      '将清空当前页面的对话内容并开启新会话（不影响服务端已保存的历史记录），是否继续？',
      '清空当前会话',
      { type: 'warning', confirmButtonText: '清空', cancelButtonText: '取消' }
    )
  } catch (error) {
    return
  }
  chatStore.clearSession()
  lastQuestion.value = ''
  ElMessage.success('已开启新会话')
}

/** 收藏单条回答 */
async function handleFavorite(message) {
  if (message.favorited) return
  try {
    await addFavorite({
      history_id: message.historyId || null,
      question: message.question || '',
      answer: message.answer || '',
      note: '',
      user_id: userStore.userId
    })
    chatStore.markFavorited(message.id, true)
    ElMessage.success('已加入收藏，可在「我的收藏」页查看与补充备注')
  } catch (error) {
    // request.js 已统一提示错误
  }
}

/** 恢复默认检索参数 */
function handleResetOptions() {
  chatStore.resetOptions()
  ElMessage.success('已恢复默认检索参数')
}

onMounted(() => {
  // 能力清单用于下拉选项；失败时静默回落到本地常量
  knowledgeStore.loadCapabilities()
})
</script>

<template>
  <div class="page-container chat-page">
    <div class="page-title">
      <h2>
        金融制度智能问答
        <span class="subtitle">基于条文检索的答案生成，回答均标注出处与可信度</span>
      </h2>
      <div class="flex-wrap-gap">
        <el-tag type="info" effect="plain">
          <el-icon><User /></el-icon>{{ userStore.displayName }}
        </el-tag>
        <el-tag type="info" effect="plain">
          <el-icon><Connection /></el-icon>会话 {{ userStore.sessionId.slice(0, 12) }}
        </el-tag>
        <el-button :disabled="!chatStore.hasMessages" @click="handleClear">
          <el-icon><Delete /></el-icon>清空当前会话
        </el-button>
      </div>
    </div>

    <!-- 提问区 -->
    <el-card class="card-block ask-card" shadow="never">
      <el-input
        ref="questionRef"
        v-model="question"
        type="textarea"
        :rows="3"
        maxlength="500"
        show-word-limit
        resize="none"
        placeholder="请输入金融制度相关问题，例如「商业银行流动性风险管理的报送频率是多少」"
        @keydown.ctrl.enter.prevent="handleAsk"
        @keydown.meta.enter.prevent="handleAsk"
      />

      <div class="ask-actions">
        <div class="flex-wrap-gap">
          <el-button type="primary" :loading="asking" @click="handleAsk">
            <el-icon><Promotion /></el-icon>提交提问
          </el-button>
          <el-button v-if="asking && options.stream" @click="handleAbort">
            <el-icon><VideoPause /></el-icon>中断生成
          </el-button>
          <el-button :disabled="!lastQuestion" @click="handleRetry">
            <el-icon><RefreshRight /></el-icon>重试上一问
          </el-button>
        </div>
        <span class="keyboard-tip text-muted">
          <el-icon><Operation /></el-icon>快捷键：Ctrl + Enter 提交
        </span>
      </div>

      <!-- 检索参数 -->
      <el-divider content-position="left">
        <span class="divider-title">检索参数</span>
      </el-divider>

      <el-form label-position="top" class="options-form">
        <el-row :gutter="14">
          <el-col :xs="24" :sm="12" :md="7">
            <el-form-item>
              <template #label>检索模式</template>
              <el-radio-group
                :model-value="options.retrievalMode"
                size="default"
                @update:model-value="(val) => chatStore.patchOptions({ retrievalMode: val })"
              >
                <el-tooltip
                  v-for="item in RETRIEVAL_MODES"
                  :key="item.value"
                  :content="item.tip"
                  placement="top"
                >
                  <el-radio-button :value="item.value">{{ item.label }}</el-radio-button>
                </el-tooltip>
              </el-radio-group>
            </el-form-item>
          </el-col>

          <el-col :xs="12" :sm="12" :md="4">
            <el-form-item>
              <template #label>Top-K（证据条数）</template>
              <el-select
                :model-value="options.topK"
                @update:model-value="(val) => chatStore.patchOptions({ topK: val })"
              >
                <el-option v-for="num in TOP_K_OPTIONS" :key="num" :label="`Top-${num}`" :value="num" />
              </el-select>
            </el-form-item>
          </el-col>

          <el-col :xs="12" :sm="12" :md="6">
            <el-form-item>
              <template #label>业务条线过滤（可多选）</template>
              <el-select
                :model-value="options.businessLines"
                multiple
                collapse-tags
                collapse-tags-tooltip
                clearable
                placeholder="不限"
                @update:model-value="(val) => chatStore.patchOptions({ businessLines: val })"
              >
                <el-option v-for="item in businessLineOptions" :key="item" :label="item" :value="item" />
              </el-select>
            </el-form-item>
          </el-col>

          <el-col :xs="12" :sm="12" :md="4">
            <el-form-item>
              <template #label>文件类型过滤</template>
              <el-select
                :model-value="options.docTypes"
                multiple
                collapse-tags
                collapse-tags-tooltip
                clearable
                placeholder="不限"
                @update:model-value="(val) => chatStore.patchOptions({ docTypes: val })"
              >
                <el-option v-for="item in docTypeOptions" :key="item" :label="item" :value="item" />
              </el-select>
            </el-form-item>
          </el-col>

          <el-col :xs="12" :sm="12" :md="3">
            <el-form-item>
              <template #label>时效状态</template>
              <el-select
                :model-value="options.statuses"
                multiple
                collapse-tags
                clearable
                placeholder="不限"
                @update:model-value="(val) => chatStore.patchOptions({ statuses: val })"
              >
                <el-option v-for="item in statusOptions" :key="item" :label="item" :value="item" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>

        <el-row>
          <el-col :span="24">
            <div class="switch-row">
              <el-tooltip content="仅召回「现行有效」条文，已废止/已修订条文被过滤" placement="top">
                <span class="switch-item">
                  <el-switch
                    :model-value="options.onlyValid"
                    @update:model-value="(val) => chatStore.patchOptions({ onlyValid: val })"
                  />
                  <span>仅检索现行有效条文</span>
                </span>
              </el-tooltip>

              <el-tooltip content="按施行/失效日期过滤掉已过时条文" placement="top">
                <span class="switch-item">
                  <el-switch
                    :model-value="options.excludeExpired"
                    @update:model-value="(val) => chatStore.patchOptions({ excludeExpired: val })"
                  />
                  <span>过滤已过时条文</span>
                </span>
              </el-tooltip>

              <el-tooltip content="逐字返回生成结果；注意流式接口不会写入问答历史" placement="top">
                <span class="switch-item">
                  <el-switch
                    :model-value="options.stream"
                    @update:model-value="(val) => chatStore.patchOptions({ stream: val })"
                  />
                  <span>流式输出（SSE）</span>
                </span>
              </el-tooltip>

              <el-tooltip content="非流式模式下是否把本次问答写入历史记录" placement="top">
                <span class="switch-item">
                  <el-switch
                    :model-value="options.saveHistory"
                    :disabled="options.stream"
                    @update:model-value="(val) => chatStore.patchOptions({ saveHistory: val })"
                  />
                  <span>保存到问答历史</span>
                </span>
              </el-tooltip>

              <el-tooltip content="返回全部召回条文（含未被回答引用的），便于核对召回质量" placement="top">
                <span class="switch-item">
                  <el-switch
                    :model-value="options.includeAllCitations"
                    @update:model-value="(val) => chatStore.patchOptions({ includeAllCitations: val })"
                  />
                  <span>返回全部召回条文</span>
                </span>
              </el-tooltip>

              <el-button link type="primary" size="small" @click="handleResetOptions">
                <el-icon><Refresh /></el-icon>恢复默认
              </el-button>
            </div>
          </el-col>
        </el-row>
      </el-form>

      <!-- 示例问题 -->
      <div class="example-row">
        <span class="text-muted">示例问题：</span>
        <el-tag
          v-for="item in EXAMPLES"
          :key="item"
          class="example-tag"
          type="info"
          effect="plain"
          @click="useExample(item)"
        >
          {{ item }}
        </el-tag>
      </div>
    </el-card>

    <!-- 结果区 + 侧栏 -->
    <el-row :gutter="16">
      <el-col :xs="24" :md="17" :lg="18">
        <div ref="scrollRef" class="message-scroll">
          <template v-if="chatStore.hasMessages">
            <div v-for="item in messages" :id="`msg-${item.id}`" :key="item.id">
              <!-- 用户提问气泡 -->
              <div v-if="item.role === 'user'" class="user-bubble">
                <div class="user-avatar">
                  <el-icon><UserFilled /></el-icon>
                </div>
                <div class="user-content">{{ item.content }}</div>
              </div>
              <!-- 助手回答 -->
              <AnswerBubble v-else :message="item" @favorite="handleFavorite" />
            </div>
          </template>

          <el-card v-else class="card-block" shadow="never">
            <EmptyState
              title="开始你的第一次提问"
              description="系统会先混合检索知识库中的制度条文（向量 + BM25 + RRF 融合重排），再由大模型基于条文生成带出处的回答，并给出 0~100 的可信度评分。"
              action-text="使用示例问题"
              @action="useExample(EXAMPLES[0])"
            />
          </el-card>
        </div>
      </el-col>

      <el-col :xs="24" :md="7" :lg="6">
        <!-- 本轮会话 -->
        <el-card class="card-block" shadow="never">
          <div class="section-title">本轮会话（{{ chatStore.sessionLog.length }}）</div>
          <template v-if="chatStore.sessionLog.length">
            <div
              v-for="log in chatStore.sessionLog"
              :key="log.id"
              class="log-item"
              @click="scrollToMessage(log.id)"
            >
              <div class="log-question">{{ log.question }}</div>
              <div class="log-meta">
                <el-tag v-if="log.credibilityScore !== null" size="small" effect="plain" type="info">
                  {{ log.credibilityScore }} 分
                </el-tag>
                <span class="text-muted">{{ log.citations }} 条引用</span>
              </div>
            </div>
          </template>
          <div v-else class="text-muted sidebar-tip">尚未提问，提交问题后这里会显示本轮对话索引。</div>
        </el-card>

        <!-- 使用提示 -->
        <el-card class="card-block" shadow="never">
          <div class="section-title">使用提示</div>
          <ul class="tips-list">
            <li>回答中的 <span class="cite-ref">1</span> 角标可点击，会定位到下方对应条文卡片。</li>
            <li>可信度徽章 hover 可查看 5 个维度的得分与扣分说明。</li>
            <li>耗时为黄色表示超过课题要求的 <strong>2000 ms</strong> 响应预算。</li>
            <li>流式模式不写入历史记录（后端行为），需要在「历史」页回溯时请关闭流式。</li>
            <li>企业涉密制度请勿上传，本系统仅面向公开制度文件。</li>
          </ul>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<style scoped>
.chat-page {
  padding-bottom: 24px;
}

.ask-card :deep(.el-card__body) {
  padding: 16px;
}

.ask-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 12px;
}

.keyboard-tip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12.5px;
}

.divider-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--fin-text-regular);
}

.options-form :deep(.el-form-item) {
  margin-bottom: 10px;
}

.options-form :deep(.el-form-item__label) {
  padding-bottom: 2px;
  font-size: 12.5px;
  color: var(--fin-text-secondary);
  line-height: 1.4;
}

.switch-row {
  display: flex;
  align-items: center;
  gap: 18px;
  flex-wrap: wrap;
  padding: 8px 10px;
  background-color: var(--fin-bg);
  border-radius: var(--fin-radius-sm);
}

.switch-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--fin-text-regular);
}

.example-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: 12px;
  font-size: 12.5px;
}

.example-tag {
  cursor: pointer;
  transition: all 0.15s ease;
}

.example-tag:hover {
  color: var(--fin-primary);
  border-color: var(--fin-primary);
  background-color: var(--fin-primary-light);
}

.message-scroll {
  /* 宽屏下限制高度并内部滚动，避免整页被长回答撑開 */
  max-height: calc(100vh - var(--fin-header-height) - 120px);
  overflow-y: auto;
  padding-right: 4px;
}

.user-bubble {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin-bottom: 16px;
}

.user-avatar {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  flex-shrink: 0;
  color: #ffffff;
  background-color: #7a828e;
  border-radius: 50%;
}

.user-content {
  flex: 1 1 auto;
  padding: 10px 14px;
  font-size: 14.5px;
  line-height: 1.7;
  color: var(--fin-text);
  background-color: #eef2f8;
  border-radius: 0 var(--fin-radius) var(--fin-radius) var(--fin-radius);
  word-break: break-word;
  white-space: pre-wrap;
}

.log-item {
  padding: 8px 10px;
  margin-bottom: 8px;
  background-color: var(--fin-bg);
  border-radius: var(--fin-radius-sm);
  cursor: pointer;
  transition: background-color 0.15s ease;
}

.log-item:hover {
  background-color: var(--fin-primary-light);
}

.log-question {
  font-size: 13px;
  color: var(--fin-text-regular);
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.log-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
  font-size: 12px;
}

.sidebar-tip {
  font-size: 12.5px;
  line-height: 1.7;
}

.tips-list {
  margin: 0;
  padding-left: 18px;
  font-size: 12.5px;
  line-height: 1.8;
  color: var(--fin-text-secondary);
}

.tips-list li {
  margin-bottom: 6px;
}

/* 窄屏取消内部滚动，交由页面整体滚动 */
@media (max-width: 768px) {
  .message-scroll {
    max-height: none;
    overflow: visible;
    padding-right: 0;
  }

  .switch-row {
    gap: 10px;
  }

  .ask-actions {
    justify-content: flex-start;
  }
}
</style>
