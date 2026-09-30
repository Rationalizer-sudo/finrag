/**
 * 应用入口：注册 Element Plus、Pinia、路由与全局图标。
 */
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'

import 'element-plus/dist/index.css'
import '@/styles/main.css'

import App from '@/App.vue'
import router from '@/router'

const app = createApp(App)

// 全量注册 Element Plus 图标组件（模板中直接写 <el-icon><Star /></el-icon>）
Object.entries(ElementPlusIconsVue).forEach(([name, component]) => {
  app.component(name, component)
})

app.use(createPinia())
app.use(router)
app.use(ElementPlus, { locale: zhCn, size: 'default', zIndex: 3000 })

// 全局错误兜底，避免未捕获异常白屏
app.config.errorHandler = (err, instance, info) => {
  // eslint-disable-next-line no-console
  console.error('[FinRAG] 未捕获异常：', err, info)
}

app.mount('#app')
