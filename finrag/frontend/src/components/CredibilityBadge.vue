<script setup>
/**
 * 可信度评分徽章。
 *
 * 展示：分数 + 等级（高=绿 / 中=蓝 / 低=橙 / 不可信=红）。
 * 交互：hover / 点击弹出 popover，列出 5 个维度的得分与权重贡献，以及后端给出的扣分说明。
 */
import { computed } from 'vue'

import { CREDIBILITY_DIMENSIONS, credibilityMeta, formatNumber } from '@/utils'

const props = defineProps({
  /** 后端 Credibility 对象：{score,label,components,reasons} */
  credibility: {
    type: Object,
    default: null
  },
  /** 展示尺寸 */
  size: {
    type: String,
    default: 'default'
  }
})

const meta = computed(() => credibilityMeta(props.credibility ? props.credibility.label : ''))
const score = computed(() => Number((props.credibility && props.credibility.score) || 0))
const label = computed(() => (props.credibility && props.credibility.label) || '未评定')
const reasons = computed(() => (props.credibility && props.credibility.reasons) || [])
const components = computed(() => (props.credibility && props.credibility.components) || {})
const hasComponents = computed(() => Object.keys(components.value).length > 0)

/** 维度明细：原始得分（0~1）+ 该维度在百分制中的实际贡献 */
const dimensionRows = computed(() =>
  CREDIBILITY_DIMENSIONS.map((item) => {
    const raw = components.value[item.key]
    const value = raw === undefined || raw === null ? 0 : Number(raw)
    return {
      key: item.key,
      label: item.label,
      desc: item.desc,
      weight: item.weight,
      value,
      percent: Math.round(value * 100),
      contribution: (item.weight * value).toFixed(1)
    }
  })
)

/** 进度条配色：低分给出警示色 */
function progressColor(percent) {
  if (percent >= 80) return '#67c23a'
  if (percent >= 60) return '#409eff'
  if (percent >= 40) return '#e6a23c'
  return '#f56c6c'
}
</script>

<template>
  <el-popover placement="top-start" :width="360" trigger="hover" :disabled="!credibility">
    <template #reference>
      <el-tag
        v-if="credibility"
        :type="meta.type"
        effect="dark"
        round
        :size="size"
        class="credibility-badge"
      >
        <span class="badge-score">{{ formatNumber(score, 1) }}</span>
        <span class="badge-label">可信度{{ label }}</span>
      </el-tag>
      <el-tag v-else type="info" effect="plain" round :size="size" class="credibility-badge">
        可信度未评定
      </el-tag>
    </template>

    <div class="credibility-panel">
      <div class="panel-head">
        <span class="panel-score" :style="{ color: meta.color }">{{ formatNumber(score, 1) }}</span>
        <span class="panel-unit">/ 100</span>
        <el-tag :type="meta.type" size="small" effect="dark" round>可信度{{ label }}</el-tag>
      </div>
      <div class="panel-note">
        综合得分 = Σ(维度权重 × 维度得分)，等级阈值：≥80 高、≥60 中、≥40 低、&lt;40 不可信
      </div>

      <div v-if="hasComponents" class="panel-dimensions">
        <div v-for="row in dimensionRows" :key="row.key" class="dimension-row">
          <div class="dimension-head">
            <span class="dimension-label">
              {{ row.label }}
              <el-tooltip :content="row.desc" placement="top">
                <el-icon class="dimension-help"><QuestionFilled /></el-icon>
              </el-tooltip>
            </span>
            <span class="dimension-value">
              {{ row.percent }}%
              <span class="text-muted">（权重 {{ row.weight }}，贡献 {{ row.contribution }} 分）</span>
            </span>
          </div>
          <el-progress
            :percentage="row.percent"
            :color="progressColor(row.percent)"
            :stroke-width="6"
            :show-text="false"
          />
        </div>
      </div>
      <el-empty v-else description="该记录未保存维度明细" :image-size="60" />

      <div v-if="reasons.length" class="panel-reasons">
        <div class="reasons-title">
          <el-icon><Warning /></el-icon>
          扣分说明
        </div>
        <ul>
          <li v-for="(reason, index) in reasons" :key="index">{{ reason }}</li>
        </ul>
      </div>
      <div v-else class="panel-reasons">
        <div class="reasons-title">
          <el-icon><CircleCheck /></el-icon>
          各维度均正常，无可解释的扣分项
        </div>
      </div>
    </div>
  </el-popover>
</template>

<style scoped>
.credibility-badge {
  cursor: help;
  font-weight: 600;
}

.badge-score {
  margin-right: 4px;
  font-size: 13px;
}

.badge-label {
  font-size: 12px;
  font-weight: 400;
  opacity: 0.95;
}

.credibility-panel {
  font-size: 13px;
}

.panel-head {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin-bottom: 6px;
}

.panel-score {
  font-size: 26px;
  font-weight: 700;
  line-height: 1;
}

.panel-unit {
  color: var(--fin-text-secondary);
  font-size: 12px;
  margin-right: auto;
}

.panel-note {
  padding: 6px 8px;
  margin-bottom: 10px;
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--fin-text-secondary);
  background-color: var(--fin-bg);
  border-radius: var(--fin-radius-sm);
}

.dimension-row {
  margin-bottom: 8px;
}

.dimension-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 2px;
  font-size: 12.5px;
}

.dimension-label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-weight: 600;
}

.dimension-help {
  color: var(--fin-text-secondary);
  cursor: help;
}

.dimension-value {
  color: var(--fin-text-regular);
  white-space: nowrap;
}

.dimension-value .text-muted {
  font-size: 11px;
}

.panel-reasons {
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px dashed var(--fin-border);
}

.reasons-title {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-bottom: 4px;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--fin-text-regular);
}

.panel-reasons ul {
  margin: 0;
  padding-left: 18px;
}

.panel-reasons li {
  margin-bottom: 3px;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--fin-text-secondary);
}
</style>
