<script setup>
/**
 * 应用根组件：顶部导航 + 路由视图 + 页脚。
 */
import AppHeader from '@/components/AppHeader.vue'
</script>

<template>
  <div class="app-shell">
    <AppHeader />

    <main class="app-main">
      <router-view v-slot="{ Component }">
        <transition name="fade-slide" mode="out-in">
          <component :is="Component" />
        </transition>
      </router-view>
    </main>

    <footer class="app-footer">
      <span>FinRAG · 基于 RAG 的金融制度类知识问答系统</span>
      <el-divider direction="vertical" />
      <span>条文溯源 · 时效性管理 · 可信度评分</span>
      <el-divider direction="vertical" />
      <span class="text-muted">数据仅限公开制度文件，禁止上传涉密材料</span>
    </footer>
  </div>
</template>

<style scoped>
.app-shell {
  display: flex;
  flex-direction: column;
  min-height: 100vh;
}

.app-main {
  flex: 1 1 auto;
  min-height: 0;
}

.app-footer {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-wrap: wrap;
  gap: 4px;
  padding: 14px 16px;
  font-size: 12.5px;
  color: var(--fin-text-secondary);
  background-color: #ffffff;
  border-top: 1px solid var(--fin-border);
}
</style>

<style>
/* 路由切换动画（非 scoped，作用于过渡类名） */
.fade-slide-enter-active,
.fade-slide-leave-active {
  transition: opacity 0.18s ease, transform 0.18s ease;
}

.fade-slide-enter-from {
  opacity: 0;
  transform: translateY(6px);
}

.fade-slide-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
