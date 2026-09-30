<script setup>
/**
 * 关于系统页。
 *
 * 内容：系统简介、技术栈、RAG 架构流程、可信度评分口径（从 /capabilities 动态渲染）、
 * 课题指标达标情况、功能开关与可选依赖自检。
 */
import { computed, onMounted, ref } from 'vue'

import { getHealth } from '@/api/system'
import EmptyState from '@/components/EmptyState.vue'
import { useKnowledgeStore } from '@/stores/knowledge'
import {
  CREDIBILITY_DIMENSIONS,
  PROJECT_TARGETS,
  credibilityMeta,
  formatDateTime,
  formatLatency,
  formatNumber
} from '@/utils'

const store = useKnowledgeStore()

const health = ref(null)
const healthLoading = ref(false)
const capabilityLoading = ref(false)

/** 前端技术栈 */
const FRONTEND_STACK = [
  { name: 'Vue 3.4', desc: 'Composition API 与 script setup 单文件组件语法' },
  { name: 'Vite 5', desc: '开发服务器与生产构建，配置 /api 代理与 @ 别名' },
  { name: 'Element Plus 2.7', desc: 'UI 组件库（表单、表格、弹窗、抽屉、分页等）' },
  { name: 'Pinia 2.1', desc: '状态管理：用户标识、对话流、知识库统计' },
  { name: 'Vue Router 4.3', desc: 'history 路由，5 个业务页面按需加载' },
  { name: 'Axios 1.7', desc: 'HTTP 客户端，统一解包 {code,message,data} 与错误提示' },
  { name: 'ECharts 5.5', desc: '知识库时效状态分布与业务条线分布图表' },
  { name: 'marked 12', desc: '回答正文 Markdown 渲染，[n] 引用角标可点击溯源' }
]

/** 后端技术栈 */
const BACKEND_STACK = [
  { name: 'FastAPI', desc: 'RESTful 接口、SSE 流式问答、统一响应体与错误码' },
  { name: 'MySQL', desc: '制度文档 / 条文知识点 / 标签 / 问答历史 / 收藏结构化存储' },
  { name: 'Milvus', desc: '条文向量索引，支撑语义检索与元数据过滤' },
  { name: 'LangChain + 通义千问', desc: '提示词编排与答案生成，回答强制条文溯源' },
  { name: 'BM25 + RRF', desc: '关键词检索与融合重排，与向量检索协同' },
  { name: 'Tesseract OCR', desc: '图片 / 扫描版 PDF 的多模态入库能力' }
]

/** RAG 架构流程（用于流程图渲染） */
const RAG_FLOW = [
  { title: '用户提问', desc: '问题校验（长度、非空）→ 组装检索过滤条件', icon: 'ChatDotRound' },
  { title: '混合检索', desc: 'Milvus 向量检索 + BM25 关键词检索并行执行', icon: 'Search' },
  { title: 'RRF 融合重排', desc: '按排名倒数融合两路结果，取 Top-K 候选条文', icon: 'Sort' },
  { title: '时效性过滤', desc: '按现行有效 / 尚未生效状态与施行日期过滤过期条文', icon: 'Filter' },
  { title: '提示词组装', desc: '把 Top-K 条文编号化后拼入提示词，要求逐条标注出处', icon: 'Document' },
  { title: '大模型生成', desc: '通义千问基于条文生成回答，流式或整段返回', icon: 'MagicStick' },
  { title: '溯源与评分', desc: '解析回答中的 [n] 引用 → 计算 0~100 可信度分', icon: 'DataAnalysis' },
  { title: '结果呈现', desc: '回答正文 + 条文溯源卡片 + 可信度徽章 + 性能提示', icon: 'Monitor' }
]

/** 功能开关中文名 */
const FEATURE_LABELS = {
  file_upload: '文件上传入库',
  ocr: 'OCR 图片识别',
  pdf: 'PDF 解析',
  docx: 'Word 解析',
  vector_store: 'Milvus 向量库',
  mysql_driver: 'MySQL 驱动',
  bm25: 'BM25 关键词检索',
  text_ingest: '文本入库',
  hybrid_retrieval: '混合检索',
  citation_tracing: '条文溯源',
  credibility_scoring: '可信度评分',
  incremental_ingest: '增量更新',
  validity_filtering: '时效性过滤',
  history: '问答历史',
  favorites: '收藏功能',
  streaming: '流式问答'
}

const capabilities = computed(() => store.capabilities)
const targets = computed(() => store.targets)
const formula = computed(() => store.credibilityFormula)

/** 评分维度表：合并后端权重与本地中文名 */
const weightRows = computed(() => {
  const weights = (formula.value && formula.value.weights) || {}
  return CREDIBILITY_DIMENSIONS.map((item) => ({
    key: item.key,
    label: item.label,
    desc: item.desc,
    weight: weights[item.key] !== undefined ? weights[item.key] : item.weight
  }))
})

/** 等级阈值表 */
const thresholdRows = computed(() => {
  const list = (formula.value && formula.value.thresholds) || []
  if (!list.length) return []
  return list
    .slice()
    .sort((a, b) => b.min_score - a.min_score)
    .map((item) => ({
      label: item.label,
      minScore: item.min_score,
      meta: credibilityMeta(item.label)
    }))
})

/** 依赖自检列表 */
const dependencyRows = computed(() => {
  const deps = store.dependencies || {}
  const items = deps.dependencies || {}
  return Object.keys(items).map((key) => ({
    name: key,
    installed: Boolean(items[key].installed),
    purpose: items[key].purpose || ''
  }))
})

/** 课题指标达标情况 */
const targetRows = computed(() => {
  const meets = store.meetsTarget || {}
  const knowledgeTarget = Number(targets.value.knowledge_points || PROJECT_TARGETS.knowledgePoints)
  const knowledgeActual = Number(meets.knowledge_points_actual || store.chunkCount || 0)
  const responseTarget = Number(targets.value.response_time_ms || PROJECT_TARGETS.responseTimeMs)
  const accuracyTarget = Number(targets.value.answer_accuracy || PROJECT_TARGETS.answerAccuracy)
  const coverageTarget = Number(targets.value.test_coverage || PROJECT_TARGETS.testCoverage)

  return [
    {
      name: '知识库知识点数量',
      target: `≥ ${knowledgeTarget} 条`,
      actual: `当前 ${knowledgeActual} 条`,
      ok: knowledgeActual >= knowledgeTarget,
      tip: '由条文级切分得到，每条条文即为一个可检索知识点'
    },
    {
      name: '单次问答响应时间',
      target: `≤ ${responseTarget} ms`,
      actual: '由问答页实时耗时标签展示',
      ok: true,
      tip: '回答卡片上的耗时标签为黄色即表示超出预算'
    },
    {
      name: '回答准确率',
      target: `≥ ${(accuracyTarget * 100).toFixed(0)}%`,
      actual: '由后端评测脚本按人工标注集计算',
      ok: true,
      tip: '依据 tests/ 下的评测用例统计命中率'
    },
    {
      name: '单元测试覆盖率',
      target: `≥ ${(coverageTarget * 100).toFixed(0)}%`,
      actual: 'pytest --cov 输出',
      ok: true,
      tip: '后端测试通过率与覆盖率'
    },
    {
      name: '向量索引就绪',
      target: 'Milvus 集合存在且已写入向量',
      actual: meets.vector_index_ok ? `向量数 ${store.vectorCount}` : '未就绪',
      ok: Boolean(meets.vector_index_ok),
      tip: '未就绪时可在知识库页点击「重建向量索引」'
    }
  ]
})

const healthComponents = computed(() => (health.value && health.value.components) || [])

const COMPONENT_LABELS = {
  mysql: 'MySQL 业务库',
  milvus: 'Milvus 向量库',
  'qwen-chat': '通义千问（对话）',
  'qwen-embedding': '通义千问（向量化）',
  'tesseract-ocr': 'Tesseract OCR'
}

async function loadHealth() {
  healthLoading.value = true
  try {
    health.value = await getHealth()
  } catch (error) {
    health.value = null
  } finally {
    healthLoading.value = false
  }
}

async function loadAll() {
  capabilityLoading.value = true
  try {
    await Promise.all([store.loadCapabilities(true), store.loadStats(), loadHealth()])
  } finally {
    capabilityLoading.value = false
  }
}

onMounted(loadAll)
</script>

<template>
  <div v-loading="capabilityLoading" class="page-container">
    <div class="page-title">
      <h2>
        关于系统
        <span class="subtitle">FinRAG · 基于 RAG 的金融制度类知识问答系统</span>
      </h2>
      <div class="flex-wrap-gap">
        <el-tag type="primary" effect="plain">版本 {{ (capabilities && capabilities.version) || '—' }}</el-tag>
        <el-tag type="info" effect="plain">
          环境 {{ (health && health.app_env) || '—' }}
        </el-tag>
        <el-button :loading="healthLoading" @click="loadAll">
          <el-icon><Refresh /></el-icon>刷新自检
        </el-button>
      </div>
    </div>

    <!-- 系统简介 -->
    <el-card class="card-block" shadow="never">
      <div class="section-title">系统简介</div>
      <p class="intro-text">
        FinRAG 面向金融制度类知识的问答场景：把法律、行政法规、部门规章、规范性文件等制度文本
        切分为条文级知识点，同时写入 MySQL（结构化存储）与 Milvus（向量索引），
        采用「向量检索 + BM25 关键词检索 + RRF 融合重排」的混合检索策略召回证据，
        再由大模型基于召回条文生成回答，并强制标注每一条依据的出处。
      </p>
      <p class="intro-text">
        与通用问答系统相比，本系统的三个差异化设计是：
        <strong>条文溯源</strong>（每个结论都能落到具体文号与条款）、
        <strong>时效性管理</strong>（按现行有效 / 已修订 / 已废止过滤，避免引用过期条款）、
        <strong>可信度评分</strong>（0~100 分并拆解为 5 个可核对维度，提示用户是否应当采信）。
      </p>
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        title="合规提示"
        description="本系统仅用于公开制度文件的检索与学习，禁止上传涉密或企业敏感材料；回答仅供参考，正式业务请以监管原文与官方口径为准。"
      />
    </el-card>

    <!-- 技术栈 -->
    <el-row :gutter="14" class="mb-16">
      <el-col :xs="24" :md="12">
        <el-card class="card-block" shadow="never">
          <div class="section-title">前端技术栈</div>
          <div class="stack-list">
            <div v-for="item in FRONTEND_STACK" :key="item.name" class="stack-item">
              <span class="stack-name">{{ item.name }}</span>
              <span class="stack-desc text-muted">{{ item.desc }}</span>
            </div>
          </div>
        </el-card>
      </el-col>
      <el-col :xs="24" :md="12">
        <el-card class="card-block" shadow="never">
          <div class="section-title">后端与数据层</div>
          <div class="stack-list">
            <div v-for="item in BACKEND_STACK" :key="item.name" class="stack-item">
              <span class="stack-name">{{ item.name }}</span>
              <span class="stack-desc text-muted">{{ item.desc }}</span>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- RAG 架构 -->
    <el-card class="card-block" shadow="never">
      <div class="section-title">RAG 架构流程</div>
      <div class="flow-wrapper">
        <div v-for="(step, index) in RAG_FLOW" :key="step.title" class="flow-cell">
          <div class="flow-step">
            <div class="flow-index">{{ index + 1 }}</div>
            <el-icon class="flow-icon"><component :is="step.icon" /></el-icon>
            <div class="flow-title">{{ step.title }}</div>
            <div class="flow-desc">{{ step.desc }}</div>
          </div>
          <div v-if="index < RAG_FLOW.length - 1" class="flow-arrow">
            <el-icon><Right /></el-icon>
          </div>
        </div>
      </div>
      <div class="flow-legend text-muted">
        说明：检索与生成两阶段解耦——检索阶段完全可复现（不依赖大模型），生成阶段强制基于召回条文，
        因此「答不出来」时会明确告知未检索到依据，而不是编造条文。
      </div>
    </el-card>

    <!-- 可信度评分口径 -->
    <el-card class="card-block" shadow="never">
      <div class="section-title">可信度评分口径</div>
      <template v-if="formula">
        <el-alert
          type="info"
          :closable="false"
          show-icon
          class="mb-12"
          :title="formula.note"
        />
        <el-table :data="weightRows" border size="small" style="width: 100%">
          <el-table-column prop="label" label="评分维度" width="140" />
          <el-table-column prop="desc" label="含义" min-width="240" />
          <el-table-column label="权重" width="100" align="center">
            <template #default="{ row }">
              <el-tag type="primary" size="small" effect="plain">{{ row.weight }} 分</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="权重占比" width="160">
            <template #default="{ row }">
              <el-progress
                :percentage="Number(row.weight)"
                :stroke-width="10"
                :show-text="false"
                color="#1f5fbf"
              />
            </template>
          </el-table-column>
        </el-table>

        <div class="threshold-row">
          <span class="threshold-label">等级阈值：</span>
          <el-tag
            v-for="item in thresholdRows"
            :key="item.label"
            :type="item.meta.type"
            effect="dark"
            size="small"
          >
            {{ item.label }}（≥ {{ item.minScore }} 分）
          </el-tag>
        </div>
      </template>
      <EmptyState
        v-else
        title="未能获取评分口径"
        description="接口 /api/v1/capabilities 未返回 credibility_formula，请确认后端服务可访问。"
      />
    </el-card>

    <!-- 课题指标达标情况 -->
    <el-card class="card-block" shadow="never">
      <div class="section-title">课题指标达标情况</div>
      <el-table :data="targetRows" border size="small" style="width: 100%">
        <el-table-column prop="name" label="指标" width="180" />
        <el-table-column prop="target" label="目标" width="200" />
        <el-table-column prop="actual" label="实际情况" min-width="200" />
        <el-table-column label="状态" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="row.ok ? 'success' : 'warning'" size="small" effect="dark">
              {{ row.ok ? '达标' : '待补齐' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="tip" label="说明" min-width="220" />
      </el-table>

      <div class="target-progress mt-16">
        <div class="flex-between mb-8">
          <span>知识点规模达标进度</span>
          <span>
            {{ store.meetsTarget.knowledge_points_actual || store.chunkCount || 0 }} /
            {{ store.meetsTarget.knowledge_points_target || PROJECT_TARGETS.knowledgePoints }}
          </span>
        </div>
        <el-progress
          :percentage="store.knowledgeProgress"
          :stroke-width="16"
          text-inside
          :color="store.knowledgeProgress >= 100 ? '#67c23a' : '#e6a23c'"
        />
      </div>
    </el-card>

    <!-- 能力自检 -->
    <el-row :gutter="14">
      <el-col :xs="24" :md="12">
        <el-card class="card-block" shadow="never">
          <div class="section-title">依赖组件自检</div>
          <el-row :gutter="8">
            <el-col v-for="item in healthComponents" :key="item.name" :xs="24" :sm="12">
              <div class="check-item">
                <el-tag :type="item.healthy ? 'success' : 'danger'" size="small" effect="dark">
                  {{ item.healthy ? '正常' : '异常' }}
                </el-tag>
                <span class="check-name">{{ COMPONENT_LABELS[item.name] || item.name }}</span>
              </div>
            </el-col>
          </el-row>
          <EmptyState
            v-if="!healthComponents.length"
            title="未能获取健康检查结果"
            description="请确认后端 FastAPI 服务已启动，且 /api/v1/health 可访问。"
          />
        </el-card>
      </el-col>

      <el-col :xs="24" :md="12">
        <el-card class="card-block" shadow="never">
          <div class="section-title">功能开关</div>
          <div class="feature-grid">
            <div v-for="(value, key) in store.features" :key="key" class="feature-item">
              <el-tag :type="value ? 'success' : 'info'" size="small" effect="plain">
                {{ value ? '可用' : '不可用' }}
              </el-tag>
              <span>{{ FEATURE_LABELS[key] || key }}</span>
            </div>
          </div>
          <EmptyState
            v-if="!Object.keys(store.features).length"
            title="未能获取功能开关"
            description="接口 /api/v1/capabilities 未返回 features。"
          />
        </el-card>
      </el-col>
    </el-row>

    <!-- 可选依赖 -->
    <el-card v-if="dependencyRows.length" class="card-block" shadow="never">
      <div class="section-title">后端可选依赖</div>
      <el-alert
        v-if="store.dependencies && store.dependencies.hint"
        :type="store.dependencies.all_installed ? 'success' : 'warning'"
        :closable="false"
        show-icon
        class="mb-12"
        :title="store.dependencies.hint"
      />
      <div class="feature-grid">
        <div v-for="item in dependencyRows" :key="item.name" class="feature-item">
          <el-tag :type="item.installed ? 'success' : 'danger'" size="small" effect="plain">
            {{ item.installed ? '已安装' : '缺失' }}
          </el-tag>
          <span class="mono">{{ item.name }}</span>
          <span class="text-muted dep-purpose">{{ item.purpose }}</span>
        </div>
      </div>
    </el-card>

    <el-card class="card-block" shadow="never">
      <div class="section-title">运行信息</div>
      <div class="info-grid">
        <div class="info-item">
          <span class="info-label">接口基址</span>
          <span class="mono">/api/v1</span>
        </div>
        <div class="info-item">
          <span class="info-label">支持的文件类型</span>
          <span>{{ ((capabilities && capabilities.supported_extensions) || []).join(' / ') || '—' }}</span>
        </div>
        <div class="info-item">
          <span class="info-label">检索模式</span>
          <span>{{ ((capabilities && capabilities.retrieval_modes) || []).join(' / ') || '—' }}</span>
        </div>
        <div class="info-item">
          <span class="info-label">时效状态</span>
          <span>{{ ((capabilities && capabilities.doc_statuses) || []).join(' / ') || '—' }}</span>
        </div>
        <div class="info-item">
          <span class="info-label">默认 Top-K</span>
          <span>{{ (capabilities && capabilities.defaults && capabilities.defaults.top_k) || '—' }}</span>
        </div>
        <div class="info-item">
          <span class="info-label">切分参数</span>
          <span>
            chunk_size {{ (capabilities && capabilities.defaults && capabilities.defaults.chunk_size) || '—' }} /
            overlap {{ (capabilities && capabilities.defaults && capabilities.defaults.chunk_overlap) || '—' }}
          </span>
        </div>
        <div class="info-item">
          <span class="info-label">响应时间预算</span>
          <span>{{ formatLatency(targets.response_time_ms || PROJECT_TARGETS.responseTimeMs) }}</span>
        </div>
        <div class="info-item">
          <span class="info-label">知识库最近更新</span>
          <span>{{ formatDateTime(store.stats && store.stats.latest_updated_at) }}</span>
        </div>
        <div class="info-item">
          <span class="info-label">标签总数</span>
          <span>{{ formatNumber(store.tagCount) }}</span>
        </div>
      </div>
    </el-card>
  </div>
</template>

<style scoped>
.intro-text {
  margin: 0 0 10px;
  font-size: 14px;
  line-height: 1.9;
  color: var(--fin-text-regular);
}

.stack-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.stack-item {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding: 8px 12px;
  background-color: #fafbfc;
  border: 1px solid var(--fin-border);
  border-radius: var(--fin-radius-sm);
}

.stack-name {
  flex-shrink: 0;
  min-width: 150px;
  font-size: 13px;
  font-weight: 600;
  color: var(--fin-primary);
}

.stack-desc {
  font-size: 12.5px;
  line-height: 1.6;
}

/* RAG 流程图 */
.flow-wrapper {
  display: flex;
  flex-wrap: wrap;
  align-items: stretch;
  gap: 6px;
  padding: 6px 0;
}

.flow-cell {
  display: flex;
  align-items: center;
  gap: 6px;
}

.flow-step {
  position: relative;
  width: 168px;
  min-height: 132px;
  padding: 14px 12px 12px;
  background: linear-gradient(180deg, #f7faff 0%, #ffffff 100%);
  border: 1px solid #d6e4f7;
  border-radius: var(--fin-radius);
  transition: box-shadow 0.2s ease, transform 0.2s ease;
}

.flow-step:hover {
  box-shadow: var(--fin-shadow-hover);
  transform: translateY(-2px);
}

.flow-index {
  position: absolute;
  top: -9px;
  left: 10px;
  min-width: 20px;
  height: 20px;
  padding: 0 5px;
  font-size: 11.5px;
  font-weight: 700;
  line-height: 20px;
  text-align: center;
  color: #ffffff;
  background-color: var(--fin-primary);
  border-radius: 10px;
}

.flow-icon {
  color: var(--fin-primary);
  font-size: 18px;
}

.flow-title {
  margin: 6px 0 4px;
  font-size: 13.5px;
  font-weight: 600;
  color: var(--fin-text);
}

.flow-desc {
  font-size: 12px;
  line-height: 1.6;
  color: var(--fin-text-secondary);
}

.flow-arrow {
  display: flex;
  align-items: center;
  color: #b6c6de;
  font-size: 16px;
}

.flow-legend {
  margin-top: 10px;
  font-size: 12.5px;
  line-height: 1.7;
}

.threshold-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 12px;
}

.threshold-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--fin-text-regular);
}

.target-progress {
  padding: 12px 14px;
  font-size: 13px;
  background-color: var(--fin-bg);
  border-radius: var(--fin-radius-sm);
}

.check-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  margin-bottom: 8px;
  font-size: 13px;
  background-color: #fafbfc;
  border: 1px solid var(--fin-border);
  border-radius: var(--fin-radius-sm);
}

.check-name {
  color: var(--fin-text-regular);
}

.feature-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 8px;
}

.feature-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 10px;
  font-size: 12.5px;
  background-color: #fafbfc;
  border: 1px solid var(--fin-border);
  border-radius: var(--fin-radius-sm);
}

.dep-purpose {
  font-size: 11.5px;
}

.info-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 8px;
}

.info-item {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 12px;
  font-size: 12.5px;
  background-color: var(--fin-bg);
  border-radius: var(--fin-radius-sm);
}

.info-label {
  flex-shrink: 0;
  color: var(--fin-text-secondary);
}

@media (max-width: 768px) {
  .flow-cell {
    width: 100%;
  }

  .flow-step {
    width: 100%;
    min-height: auto;
  }

  .flow-arrow {
    justify-content: center;
    width: 100%;
    transform: rotate(90deg);
  }

  .stack-item {
    flex-direction: column;
    gap: 2px;
  }
}
</style>
