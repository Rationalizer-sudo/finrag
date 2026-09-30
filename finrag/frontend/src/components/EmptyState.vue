<script setup>
/**
 * 空状态占位组件。
 * 统一「无数据 / 无结果 / 需先操作」三类提示的视觉与文案结构。
 */
defineProps({
  /** 主标题 */
  title: {
    type: String,
    default: '暂无数据'
  },
  /** 补充说明 */
  description: {
    type: String,
    default: ''
  },
  /** 操作按钮文案，为空则不渲染 */
  actionText: {
    type: String,
    default: ''
  },
  /** 图标尺寸 */
  imageSize: {
    type: Number,
    default: 90
  }
})

defineEmits(['action'])
</script>

<template>
  <div class="empty-state">
    <el-empty :description="title" :image-size="imageSize">
      <template #description>
        <div class="empty-title">{{ title }}</div>
        <div v-if="description" class="empty-desc">{{ description }}</div>
      </template>
      <el-button v-if="actionText" type="primary" plain @click="$emit('action')">
        {{ actionText }}
      </el-button>
      <slot />
    </el-empty>
  </div>
</template>

<style scoped>
.empty-state {
  padding: 12px 0;
}

.empty-title {
  font-size: 14.5px;
  font-weight: 600;
  color: var(--fin-text-regular);
}

.empty-desc {
  max-width: 520px;
  margin: 6px auto 0;
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--fin-text-secondary);
}
</style>
