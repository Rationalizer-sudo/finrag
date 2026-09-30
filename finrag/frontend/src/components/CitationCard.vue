<script setup>
/**
 * 条文溯源卡片。
 *
 * 展示单条引用条文的完整出处信息：
 * 制度名 / 文号 / 发文机关 / 章节条款 / 时效状态标签 / 施行日期 / 正文 / 来源链接 / 召回得分。
 */
import { computed, ref } from 'vue'

import { docStatusColor, docStatusTagType, formatDate, formatNumber, truncate } from '@/utils'

const props = defineProps({
  /** 后端 Citation 对象 */
  citation: {
    type: Object,
    required: true
  },
  /** 该条文是否处于高亮态（正文中点击引用角标后短暂高亮） */
  highlight: {
    type: Boolean,
    default: false
  },
  /** 默认是否折叠长正文 */
  collapsed: {
    type: Boolean,
    default: false
  }
})

const expanded = ref(!props.collapsed)

const COLLAPSE_LIMIT = 260

const content = computed(() => props.citation.content || '（该条文无正文内容）')
const needCollapse = computed(() => content.value.length > COLLAPSE_LIMIT)
const displayContent = computed(() =>
  expanded.value || !needCollapse.value ? content.value : truncate(content.value, COLLAPSE_LIMIT)
)

/** 标题：优先使用后端拼好的 citation_label，缺失时本地拼接 */
const title = computed(() => {
  const item = props.citation
  if (item.citation_label) return item.citation_label
  const parts = []
  if (item.doc_title) parts.push(`《${item.doc_title}》`)
  if (item.clause_no) parts.push(item.clause_no)
  return parts.join('') || '未命名条文'
})

/** 来源链接：优先 source_link（可能指向条文详情的落地页） */
const sourceLink = computed(() => props.citation.source_link || props.citation.source_url || '')

/** 召回得分（混合检索时展示 RRF / 向量 / BM25 三项） */
const scoreRows = computed(() => {
  const item = props.citation
  return [
    { label: '融合得分', value: item.score },
    { label: '向量得分', value: item.vector_score },
    { label: 'BM25 得分', value: item.bm25_score }
  ].filter((row) => Number(row.value) > 0)
})

const borderColor = computed(() => docStatusColor(props.citation.status))
</script>

<template>
  <div
    class="citation-card"
    :class="{ 'is-highlight': highlight }"
    :style="{ borderLeftColor: borderColor }"
    :data-citation-index="citation.index"
  >
    <div class="citation-head">
      <div class="citation-index">[{{ citation.index }}]</div>
      <div class="citation-title" :title="title">{{ title }}</div>
      <el-tag
        :type="docStatusTagType(citation.status)"
        size="small"
        effect="dark"
        class="citation-status"
      >
        {{ citation.status || '状态待核' }}
      </el-tag>
    </div>

    <div class="citation-meta">
      <span v-if="citation.doc_no" class="meta-item">
        <el-icon><Document /></el-icon>{{ citation.doc_no }}
      </span>
      <span v-if="citation.issuer" class="meta-item">
        <el-icon><OfficeBuilding /></el-icon>{{ citation.issuer }}
      </span>
      <span v-if="citation.chapter" class="meta-item">
        <el-icon><Notebook /></el-icon>{{ citation.chapter }}
      </span>
      <span v-if="citation.clause_no" class="meta-item">
        <el-icon><Tickets /></el-icon>{{ citation.clause_no }}
      </span>
      <span class="meta-item">
        <el-icon><Calendar /></el-icon>施行：{{ formatDate(citation.effective_date) }}
      </span>
      <span v-if="citation.expiry_date" class="meta-item">
        <el-icon><Timer /></el-icon>失效：{{ formatDate(citation.expiry_date) }}
      </span>
      <span v-if="citation.business_line" class="meta-item">
        <el-icon><Files /></el-icon>{{ citation.business_line }}
      </span>
      <span v-if="citation.doc_type" class="meta-item">
        <el-icon><Collection /></el-icon>{{ citation.doc_type }}
      </span>
    </div>

    <div v-if="citation.article_title" class="citation-article">
      条文标题：{{ citation.article_title }}
    </div>

    <div class="citation-content">{{ displayContent }}</div>
    <el-button
      v-if="needCollapse"
      link
      type="primary"
      size="small"
      class="expand-btn"
      @click="expanded = !expanded"
    >
      {{ expanded ? '收起' : '展开全文' }}
      <el-icon><component :is="expanded ? 'ArrowUp' : 'ArrowDown'" /></el-icon>
    </el-button>

    <div v-if="citation.tags && citation.tags.length" class="citation-tags">
      <el-tag v-for="tag in citation.tags" :key="tag" size="small" type="info" effect="plain">
        {{ tag }}
      </el-tag>
    </div>

    <div class="citation-foot">
      <div class="score-group">
        <el-tooltip
          v-for="row in scoreRows"
          :key="row.label"
          :content="`${row.label}：${row.value}`"
          placement="top"
        >
          <span class="score-chip">{{ row.label }} {{ formatNumber(row.value, 3) }}</span>
        </el-tooltip>
        <el-tag v-if="citation.is_valid === false" type="danger" size="small" effect="plain">
          非现行有效
        </el-tag>
      </div>
      <a v-if="sourceLink" :href="sourceLink" target="_blank" rel="noopener noreferrer" class="source-link">
        <el-icon><Link /></el-icon>查看来源
      </a>
      <span v-else class="text-muted">无公开来源链接</span>
    </div>
  </div>
</template>

<style scoped>
.citation-card {
  padding: 12px 14px;
  margin-bottom: 10px;
  background-color: #ffffff;
  border: 1px solid var(--fin-border);
  border-left: 4px solid var(--fin-primary);
  border-radius: var(--fin-radius-sm);
  transition: box-shadow 0.25s ease, background-color 0.25s ease, transform 0.25s ease;
}

.citation-card:last-child {
  margin-bottom: 0;
}

.citation-card:hover {
  box-shadow: var(--fin-shadow);
}

/* 点击正文引用角标后短暂高亮 */
.citation-card.is-highlight {
  background-color: #fffaf0;
  box-shadow: 0 0 0 3px rgba(230, 162, 60, 0.28);
  transform: translateX(2px);
}

.citation-head {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}

.citation-index {
  flex-shrink: 0;
  min-width: 26px;
  font-weight: 700;
  color: var(--fin-primary);
}

.citation-title {
  flex: 1 1 auto;
  font-size: 14.5px;
  font-weight: 600;
  line-height: 1.5;
  color: var(--fin-text);
  word-break: break-word;
}

.citation-status {
  flex-shrink: 0;
}

.citation-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  margin: 6px 0 8px;
  font-size: 12.5px;
  color: var(--fin-text-secondary);
}

.meta-item {
  display: inline-flex;
  align-items: center;
  gap: 3px;
}

.citation-article {
  margin-bottom: 6px;
  font-size: 13px;
  color: var(--fin-text-regular);
}

.citation-content {
  padding: 10px 12px;
  font-size: 13.5px;
  line-height: 1.8;
  color: var(--fin-text-regular);
  background-color: #fafbfc;
  border-radius: var(--fin-radius-sm);
  white-space: pre-wrap;
  word-break: break-word;
}

.expand-btn {
  margin-top: 4px;
}

.citation-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}

.citation-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px dashed var(--fin-border);
  font-size: 12px;
}

.score-group {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}

.score-chip {
  padding: 1px 6px;
  color: var(--fin-text-secondary);
  background-color: var(--fin-bg);
  border-radius: 10px;
  cursor: help;
}

.source-link {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-weight: 500;
}

/* 窄屏适配 */
@media (max-width: 768px) {
  .citation-head {
    flex-wrap: wrap;
  }

  .citation-title {
    font-size: 14px;
  }

  .citation-meta {
    gap: 4px 10px;
  }
}
</style>
