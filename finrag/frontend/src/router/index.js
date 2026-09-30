/**
 * 路由表。
 *
 * 采用 history 模式，基址取自 Vite 的 BASE_URL：
 * 开发环境为 `/`，生产构建默认 `/app/`（后端 main.py 将 dist 挂载在 /app）。
 */
import { createRouter, createWebHistory } from 'vue-router'

import { useUserStore } from '@/stores/user'

const routes = [
  { path: '/', redirect: '/chat' },
  {
    path: '/chat',
    name: 'chat',
    component: () => import('@/views/ChatView.vue'),
    meta: { title: '智能问答', icon: 'ChatDotRound' }
  },
  {
    path: '/knowledge',
    name: 'knowledge',
    component: () => import('@/views/KnowledgeView.vue'),
    meta: { title: '知识库管理', icon: 'Collection' }
  },
  {
    path: '/history',
    name: 'history',
    component: () => import('@/views/HistoryView.vue'),
    meta: { title: '问答历史', icon: 'Clock' }
  },
  {
    path: '/favorites',
    name: 'favorites',
    component: () => import('@/views/FavoriteView.vue'),
    meta: { title: '我的收藏', icon: 'Star' }
  },
  {
    path: '/about',
    name: 'about',
    component: () => import('@/views/AboutView.vue'),
    meta: { title: '关于系统', icon: 'InfoFilled' }
  },
  // 未匹配路径回到问答页
  { path: '/:pathMatch(.*)*', redirect: '/chat' }
]

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
  scrollBehavior() {
    return { top: 0 }
  }
})

router.beforeEach((to, from, next) => {
  // 首次进入时初始化用户标识（localStorage -> store）
  const userStore = useUserStore()
  userStore.init()
  document.title = to.meta && to.meta.title ? `${to.meta.title} · FinRAG` : 'FinRAG · 金融制度智能问答系统'
  next()
})

export default router
