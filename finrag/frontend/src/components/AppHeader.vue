<script setup>
/**
 * 顶部导航栏。
 *
 * 职责：
 * 1. 页面导航（问答 / 知识库 / 历史 / 收藏 / 关于）；
 * 2. 展示后端健康状态（轮询 /health），点击可查看各组件明细；
 * 3. 展示并允许修改当前 user_id（问答历史与收藏按 user_id 归属）。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { getHealth } from '@/api/system'
import { useUserStore } from '@/stores/user'
import { healthStatusMeta } from '@/utils'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

/** 组件名称（与后端 /health 的 components[].name 对应） */
const COMPONENT_LABELS = {
  mysql: 'MySQL 业务库',
  milvus: 'Milvus 向量库',
  'qwen-chat': '通义千问（对话）',
  'qwen-embedding': '通义千问（向量化）',
  'tesseract-ocr': 'Tesseract OCR'
}

const menus = [
  { path: '/chat', label: '智能问答', icon: 'ChatDotRound' },
  { path: '/knowledge', label: '知识库管理', icon: 'Collection' },
  { path: '/history', label: '问答历史', icon: 'Clock' },
  { path: '/favorites', label: '我的收藏', icon: 'Star' },
  { path: '/about', label: '关于系统', icon: 'InfoFilled' }
]

const health = ref(null)
const healthLoading = ref(false)
const drawerVisible = ref(false)
/** 窄屏（≤768px）切换为抽屉菜单 */
const isNarrow = ref(false)

let healthTimer = null

const activeMenu = computed(() => route.path)
const statusMeta = computed(() =>
  healthStatusMeta(health.value ? health.value.status : 'unknown')
)

/** 用户标识编辑副本 */
const userIdDraft = ref(userStore.userId)

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

function handleResize() {
  isNarrow.value = window.innerWidth <= 992
}

function goto(path) {
  drawerVisible.value = false
  if (route.path !== path) router.push(path)
}

function saveUserId() {
  const next = String(userIdDraft.value || '').trim()
  if (!next) {
    ElMessage.warning('用户标识不能为空')
    return
  }
  if (next.length > 64) {
    ElMessage.warning('用户标识长度不能超过 64 个字符')
    return
  }
  userStore.setUserId(next)
  userIdDraft.value = next
  ElMessage.success(`已切换为「${next}」，问答历史与收藏将按该标识归档`)
}

onMounted(() => {
  handleResize()
  window.addEventListener('resize', handleResize)
  loadHealth()
  const interval = Number(import.meta.env.VITE_HEALTH_INTERVAL || 30000)
  healthTimer = window.setInterval(loadHealth, Math.max(interval, 10000))
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  if (healthTimer) window.clearInterval(healthTimer)
  healthTimer = null
})
</script>

<template>
  <header class="app-header">
    <div class="header-inner">
      <!-- 品牌区 -->
      <div class="brand" @click="goto('/chat')">
        <div class="brand-logo">RAG</div>
        <div class="brand-text">
          <div class="brand-title">FinRAG 金融制度智能问答</div>
          <div class="brand-sub">条文溯源 · 时效过滤 · 可信度评分</div>
        </div>
      </div>

      <!-- 宽屏菜单 -->
      <el-menu
        v-if="!isNarrow"
        class="header-menu"
        mode="horizontal"
        :default-active="activeMenu"
        :ellipsis="false"
        @select="goto"
      >
        <el-menu-item v-for="item in menus" :key="item.path" :index="item.path">
          <el-icon><component :is="item.icon" /></el-icon>
          <span>{{ item.label }}</span>
        </el-menu-item>
      </el-menu>

      <!-- 右侧状态区 -->
      <div class="header-right">
        <!-- 健康状态 -->
        <el-popover placement="bottom-end" :width="340" trigger="click">
          <template #reference>
            <el-tag
              :type="statusMeta.type"
              effect="light"
              class="health-tag"
              :class="{ 'is-loading': healthLoading }"
            >
              <el-icon class="health-dot"><component :is="health ? 'CircleCheck' : 'WarningFilled'" /></el-icon>
              {{ health ? statusMeta.label : '后端未连接' }}
            </el-tag>
          </template>

          <div class="health-panel">
            <div class="flex-between mb-8">
              <strong>依赖组件状态</strong>
              <el-button link type="primary" size="small" @click="loadHealth">刷新</el-button>
            </div>
            <div v-if="!health" class="text-muted">
              无法访问 <span class="mono">/api/v1/health</span>，请确认 FastAPI 已启动。
            </div>
            <template v-else>
              <div class="health-meta">
                <span>版本：{{ health.version }}</span>
                <span>环境：{{ health.app_env }}</span>
              </div>
              <div
                v-for="item in health.components"
                :key="item.name"
                class="health-row"
              >
                <el-tag :type="item.healthy ? 'success' : 'danger'" size="small" effect="plain">
                  {{ item.healthy ? '正常' : '异常' }}
                </el-tag>
                <span class="health-name">{{ COMPONENT_LABELS[item.name] || item.name }}</span>
              </div>
            </template>
          </div>
        </el-popover>

        <!-- 用户标识 -->
        <el-popover placement="bottom-end" :width="300" trigger="click">
          <template #reference>
            <el-tag type="info" effect="plain" class="user-tag">
              <el-icon><User /></el-icon>
              {{ userStore.displayName }}
            </el-tag>
          </template>
          <div>
            <div class="mb-8"><strong>用户标识（user_id）</strong></div>
            <div class="text-muted mb-8" style="font-size: 12px">
              问答历史与收藏按该标识隔离，修改后可在历史页看到对应的记录。
            </div>
            <el-input v-model="userIdDraft" maxlength="64" placeholder="请输入用户标识" clearable />
            <div class="mt-12" style="text-align: right">
              <el-button size="small" @click="userIdDraft = userStore.userId">重置</el-button>
              <el-button size="small" type="primary" @click="saveUserId">保存</el-button>
            </div>
          </div>
        </el-popover>

        <!-- 窄屏抽屉按钮 -->
        <el-button v-if="isNarrow" text class="menu-toggle" @click="drawerVisible = true">
          <el-icon size="20"><Menu /></el-icon>
        </el-button>
      </div>
    </div>

    <!-- 窄屏抽屉菜单 -->
    <el-drawer v-model="drawerVisible" title="功能导航" direction="rtl" size="240px">
      <el-menu :default-active="activeMenu" @select="goto">
        <el-menu-item v-for="item in menus" :key="item.path" :index="item.path">
          <el-icon><component :is="item.icon" /></el-icon>
          <span>{{ item.label }}</span>
        </el-menu-item>
      </el-menu>
    </el-drawer>
  </header>
</template>

<style scoped>
.app-header {
  position: sticky;
  top: 0;
  z-index: 1000;
  background-color: #ffffff;
  border-bottom: 1px solid var(--fin-border);
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.04);
}

.header-inner {
  display: flex;
  align-items: center;
  gap: 16px;
  max-width: 1360px;
  height: var(--fin-header-height);
  margin: 0 auto;
  padding: 0 16px;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
  flex-shrink: 0;
}

.brand-logo {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  height: 38px;
  font-size: 13px;
  font-weight: 700;
  color: #ffffff;
  background: linear-gradient(135deg, #1f5fbf, #2f86d8);
  border-radius: 10px;
  letter-spacing: 0.5px;
}

.brand-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--fin-text);
  line-height: 1.25;
}

.brand-sub {
  font-size: 11.5px;
  color: var(--fin-text-secondary);
  line-height: 1.2;
}

.header-menu {
  flex: 1 1 auto;
  border-bottom: none;
  min-width: 0;
}

.header-menu :deep(.el-menu-item) {
  height: var(--fin-header-height);
  line-height: var(--fin-header-height);
  font-size: 14px;
}

.header-menu :deep(.el-menu-item .el-icon) {
  margin-right: 4px;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.health-tag,
.user-tag {
  cursor: pointer;
}

.health-dot {
  margin-right: 2px;
  vertical-align: -2px;
}

.health-panel .health-meta {
  display: flex;
  gap: 12px;
  margin-bottom: 8px;
  font-size: 12.5px;
  color: var(--fin-text-secondary);
}

.health-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
  border-top: 1px dashed var(--fin-border);
}

.health-name {
  font-size: 13px;
}

.menu-toggle {
  padding: 6px;
}

@media (max-width: 768px) {
  .header-inner {
    gap: 8px;
    padding: 0 10px;
  }

  .brand-sub {
    display: none;
  }

  .brand-title {
    font-size: 13.5px;
  }

  .user-tag {
    max-width: 96px;
    overflow: hidden;
  }
}
</style>
