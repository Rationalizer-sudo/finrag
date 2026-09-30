<script setup>
/**
 * 回答气泡：回答正文 + 条文溯源卡片 + 可信度评分 + 性能提示。
 *
 * 关键实现：
 * 1. Markdown 渲染使用 `marked`；渲染前先转义 `&` 与 `<` 防止 HTML 注入，
 *    同时保留 `>` 以便引用块（blockquote）语法正常工作；
 * 2. 回答中的 `[n]` 引用标记被替换为可点击的上标徽章 `<sup class="cite-ref">`，
 *    点击后滚动并高亮对应的条文卡片（事件委托，避免为每个角标绑定监听）；
 * 3. 流式模式展示打字光标；客户端实测耗时以标签区分（流式接口不返回 latency_ms）。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { marked } from 'marked'
import { ElMessage } from 'element-plus'

import CitationCard from '@/components/CitationCard.vue'
import CredibilityBadge from '@/components/CredibilityBadge.vue'
import { RETRIEVAL_MODES, copyText, formatLatency, formatNumber } from '@/utils'

const props = defineProps({
  /** 助手消息对象（见 stores/chat.js 的消息结构） */
  message: {
    type: Object,
    required: true
  },
  /** 是否在气泡内展示问题（历史/收藏详情弹窗复用时可开启） */
  showQuestion: {
    type: Boolean,
    default: false
  },
  /** 只读模式：隐藏收藏等交互按钮 */
  readonly: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['favorite'])

// 全局 Markdown 配置：支持表格（gfm）与单换行（breaks）
marked.setOptions({ gfm: true, breaks: true })

const rootRef = ref(null)
/** 当前高亮的引用序号 */
const activeIndex = ref(null)
let highlightTimer = null

const citations = computed(() => props.message.citations || [])
const credibility = computed(() => props.message.credibility || null)
const latencyMs = computed(() => props.message.latencyMs)
/** 是否超出课题要求的 2 秒响应预算 */
const overBudget = computed(
  () => props.message.withinBudget === false || (latencyMs.value && latencyMs.value > 2000)
)
const retrievalLabel = computed(() => {
  const hit = RETRIEVAL_MODES.find((item) => item.value === props.message.retrievalMode)
  return hit ? hit.label : props.message.retrievalMode || '未知'
})
const pendingWithoutText = computed(() => props.message.pending && !props.message.answer)
const hasAnswer = computed(() => Boolean(props.message.answer))

/** HTML 转义：只转义可能构成标签的字符，保留 Markdown 语法字符 */
function escapeHtml(text) {
  return String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;')
}

/** Markdown -> HTML，并把 [n] 变成可点击的引用角标 */
function renderMarkdown(text) {
  if (!text) return ''
  const escaped = escapeHtml(text)
  // 负向断言排除 Markdown 链接语法 [1](url)
  const withRefs = escaped.replace(
    /\[(\d{1,3})\](?!\()/g,
    (match, num) => `<sup class="cite-ref" data-cite-index="${num}">[${num}]</sup>`
  )
  try {
    return marked.parse(withRefs)
  } catch (error) {
    return `<p>${escaped}</p>`
  }
}

const renderedAnswer = computed(() => renderMarkdown(props.message.answer))

/** 点击正文中的引用角标 -> 滚动并高亮对应条文卡片 */
function handleContentClick(event) {
  const target = event.target && event.target.closest ? event.target.closest('.cite-ref') : null
  if (!target) return
  scrollToCitation(Number(target.dataset.citeIndex))
}

function scrollToCitation(index) {
  if (!index) return
  const root = rootRef.value
  const element = root ? root.querySelector(`[data-citation-index="${index}"]`) : null
  if (!element) {
    ElMessage.info(`本次回答未包含第 [${index}] 条条文卡片`)
    return
  }
  activeIndex.value = index
  element.scrollIntoView({ behavior: 'smooth', block: 'center' })
  if (highlightTimer) window.clearTimeout(highlightTimer)
  highlightTimer = window.setTimeout(() => {
    activeIndex.value = null
  }, 2600)
}

/** 复制回答正文 */
async function handleCopy() {
  const ok = await copyText(props.message.answer || '')
  if (ok) ElMessage.success('回答已复制到剪贴板')
  else ElMessage.warning('复制失败，请手动选择文本复制')
}

function handleFavorite() {
  emit('favorite', props.message)
}

// 问题变化时重置高亮状态（同一条消息对象被复用时）
watch(
  () => props.message.question,
  () => {
    activeIndex.value = null
  }
)

onBeforeUnmount(() => {
  if (highlightTimer) window.clearTimeout(highlightTimer)
})
</script>

<template>
  <div ref="rootRef" class="answer-bubble">
    <!-- 问题（历史 / 收藏详情复用） -->
    <div v-if="showQuestion && message.question" class="question-block">
      <div class="question-label">
        <el-icon><ChatLineSquare /></el-icon>提问
      </div>
      <div class="question-text">{{ message.question }}</div>
    </div>

    <!-- 回答卡片 -->
    <div class="answer-card">
      <div class="answer-head">
        <div class="head-left">
          <el-icon class="bot-icon"><MagicStick /></el-icon>
          <span class="bot-name">FinRAG 回答</span>
          <CredibilityBadge v-if="credibility" :credibility="credibility" size="small" />
          <el-tag v-else type="info" size="small" effect="plain">可信度未评定</el-tag>
        </div>
        <div class="head-right">
          <el-tag size="small" effect="plain" type="info">
            <el-icon><Search /></el-icon>{{ retrievalLabel }}
          </el-tag>
          <el-tag size="small" effect="plain" type="info">
            Top-{{ message.topK || '—' }}
          </el-tag>
          <el-tag size="small" effect="plain" type="info">
            <el-icon><Files /></el-icon>证据 {{ message.evidenceCount || citations.length }} 条
          </el-tag>
          <el-tooltip
            v-if="latencyMs !== null && latencyMs !== undefined"
            :content="
              message.latencyClientSide
                ? '流式模式无服务端耗时，此处为浏览器实测端到端耗时'
                : '后端返回的端到端耗时（retrieval_ms + generation_ms）'
            "
            placement="top"
          >
            <el-tag :type="overBudget ? 'warning' : 'success'" size="small" effect="plain">
              <el-icon><Timer /></el-icon>{{ formatLatency(latencyMs) }}
            </el-tag>
          </el-tooltip>
          <el-tag v-if="message.latencyClientSide" size="small" effect="plain" type="info">
            客户端实测
          </el-tag>
        </div>
      </div>

      <!-- 性能预警：课题要求响应 ≤2 秒 -->
      <el-alert
        v-if="overBudget"
        class="budget-alert"
        type="warning"
        :closable="false"
        show-icon
        title="响应耗时超出课题要求（≤ 2000 ms）"
        :description="`本次耗时 ${formatLatency(latencyMs)}。可减少 Top-K、关闭混合检索重排，或检查大模型服务与向量库的网络延迟。`"
      />

      <!-- 未基于知识库作答提示 -->
      <el-alert
        v-if="message.hasAnswer === false"
        class="budget-alert"
        type="info"
        :closable="false"
        show-icon
        title="未能基于知识库条文作答"
        description="当前召回条文中没有可支撑该问题的有效依据，建议先确认知识库已导入相关制度文件，或调整检索模式与过滤条件。"
      />

      <!-- 错误 -->
      <el-alert
        v-if="message.error"
        class="budget-alert"
        type="error"
        :closable="false"
        show-icon
        title="生成失败"
        :description="message.error"
      />

      <!-- 加载骨架屏 -->
      <div v-if="pendingWithoutText" class="answer-loading">
        <el-skeleton :rows="3" animated />
        <div class="loading-tip text-muted">
          <el-icon class="is-loading"><Loading /></el-icon>
          正在检索条文并生成回答，请稍候…
        </div>
      </div>

      <!-- 回答正文 -->
      <div v-else class="answer-body">
        <div
          class="markdown-body"
          v-html="renderedAnswer"
          @click="handleContentClick"
        />
        <span v-if="message.streaming" class="typing-cursor" />
      </div>

      <!-- 警告信息（例如流式模式不写历史） -->
      <el-alert
        v-for="(warning, index) in message.warnings || []"
        :key="index"
        class="budget-alert"
        type="warning"
        :closable="false"
        show-icon
        :title="warning"
      />

      <!-- 条文溯源 -->
      <div class="citations-section">
        <div class="section-head">
          <span class="section-name">
            <el-icon><DocumentCopy /></el-icon>
            条文溯源
            <span class="text-muted">（共 {{ citations.length }} 条，点击正文角标可跳转）</span>
          </span>
        </div>
        <template v-if="citations.length">
          <CitationCard
            v-for="item in citations"
            :key="`${message.id}-${item.index}`"
            :citation="item"
            :highlight="activeIndex === item.index"
          />
        </template>
        <div v-else class="no-citation text-muted">
          本次回答未返回引用条文。可尝试切换检索模式、调大 Top-K，或关闭「仅检索现行有效条文」。
        </div>
      </div>

      <!-- 检索链路观测 -->
      <el-collapse v-if="message.trace" class="trace-collapse">
        <el-collapse-item name="trace">
          <template #title>
            <span class="trace-title">
              <el-icon><DataAnalysis /></el-icon>检索链路明细
            </span>
          </template>
          <div class="trace-grid">
            <div class="trace-item">
              <span class="trace-label">检索耗时</span>
              <span class="trace-value">{{ formatNumber(message.trace.retrieval_ms, 0) }} ms</span>
            </div>
            <div class="trace-item">
              <span class="trace-label">生成耗时</span>
              <span class="trace-value">{{ formatNumber(message.trace.generation_ms, 0) }} ms</span>
            </div>
            <div class="trace-item">
              <span class="trace-label">总耗时</span>
              <span class="trace-value">{{ formatNumber(message.trace.total_ms, 0) }} ms</span>
            </div>
            <div class="trace-item">
              <span class="trace-label">候选条文数</span>
              <span class="trace-value">{{ message.trace.candidate_k }}</span>
            </div>
            <div class="trace-item">
              <span class="trace-label">最终证据数</span>
              <span class="trace-value">{{ message.trace.evidence_count }}</span>
            </div>
            <div class="trace-item">
              <span class="trace-label">上下文长度</span>
              <span class="trace-value">{{ message.trace.context_chars }} 字</span>
            </div>
            <div class="trace-item">
              <span class="trace-label">向量检索</span>
              <el-tag :type="message.trace.vector_used ? 'success' : 'info'" size="small" effect="plain">
                {{ message.trace.vector_used ? '已启用' : '未启用' }}
              </el-tag>
            </div>
            <div class="trace-item">
              <span class="trace-label">BM25 检索</span>
              <el-tag :type="message.trace.bm25_used ? 'success' : 'info'" size="small" effect="plain">
                {{ message.trace.bm25_used ? '已启用' : '未启用' }}
              </el-tag>
            </div>
          </div>
        </el-collapse-item>
      </el-collapse>

      <!-- 操作区 -->
      <div v-if="!readonly" class="answer-actions">
        <el-button link type="primary" size="small" :disabled="!hasAnswer" @click="handleCopy">
          <el-icon><CopyDocument /></el-icon>复制回答
        </el-button>
        <el-button
          link
          :type="message.favorited ? 'success' : 'primary'"
          size="small"
          :disabled="!hasAnswer || message.favorited"
          @click="handleFavorite"
        >
          <el-icon><component :is="message.favorited ? 'StarFilled' : 'Star'" /></el-icon>
          {{ message.favorited ? '已收藏' : '收藏该回答' }}
        </el-button>
        <span v-if="message.historyId" class="text-muted history-hint">
          历史记录 ID：{{ message.historyId }}
        </span>
        <span v-else-if="!message.streaming" class="text-muted history-hint">
          本次问答未返回历史记录 ID（流式模式或已关闭「保存到问答历史」）
        </span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.answer-bubble {
  margin-bottom: 18px;
}

.question-block {
  margin-bottom: 10px;
  padding: 10px 14px;
  background-color: var(--fin-primary-light);
  border-radius: var(--fin-radius-sm);
}

.question-label {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-bottom: 2px;
  font-size: 12px;
  color: var(--fin-primary);
  font-weight: 600;
}

.question-text {
  font-size: 14.5px;
  font-weight: 600;
  color: var(--fin-text);
  word-break: break-word;
}

.answer-card {
  padding: 14px 16px;
  background-color: var(--fin-bg-card);
  border: 1px solid var(--fin-border);
  border-radius: var(--fin-radius);
  box-shadow: var(--fin-shadow);
}

.answer-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
  padding-bottom: 10px;
  margin-bottom: 10px;
  border-bottom: 1px dashed var(--fin-border);
}

.head-left,
.head-right {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.bot-icon {
  color: var(--fin-primary);
}

.bot-name {
  font-size: 14px;
  font-weight: 600;
}

.budget-alert {
  margin-bottom: 10px;
}

.answer-loading {
  padding: 6px 0;
}

.loading-tip {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 10px;
  font-size: 12.5px;
}

.answer-body {
  position: relative;
}

.typing-cursor {
  display: inline-block;
  width: 7px;
  height: 15px;
  margin-left: 2px;
  background-color: var(--fin-primary);
  vertical-align: -2px;
  animation: blink 0.9s steps(2, start) infinite;
}

@keyframes blink {
  to {
    visibility: hidden;
  }
}

.citations-section {
  margin-top: 14px;
}

.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.section-name {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 13.5px;
  font-weight: 600;
  color: var(--fin-text-regular);
}

.section-name .text-muted {
  font-weight: 400;
  font-size: 12px;
}

.no-citation {
  padding: 12px;
  font-size: 13px;
  text-align: center;
  background-color: var(--fin-bg);
  border-radius: var(--fin-radius-sm);
}

.trace-collapse {
  margin-top: 12px;
  border-top: none;
}

.trace-title {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
  color: var(--fin-text-regular);
}

.trace-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 8px;
}

.trace-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 6px 10px;
  font-size: 12.5px;
  background-color: var(--fin-bg);
  border-radius: var(--fin-radius-sm);
}

.trace-label {
  color: var(--fin-text-secondary);
}

.trace-value {
  font-weight: 600;
}

.answer-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px dashed var(--fin-border);
}

.history-hint {
  font-size: 12px;
}

@media (max-width: 768px) {
  .answer-card {
    padding: 12px;
  }

  .answer-head {
    align-items: flex-start;
  }

  .trace-grid {
    grid-template-columns: 1fr;
  }
}
</style>
