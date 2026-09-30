/**
 * Vite 构建配置。
 *
 * 关键点：
 * 1. `@` 别名指向 src，避免出现 `../../` 这类脆弱相对路径；
 * 2. 开发环境把 `/api` 反向代理到 FastAPI（默认 http://127.0.0.1:8000），
 *    因此前端代码里 baseURL 写 `/api/v1` 即可，无需处理跨域；
 * 3. 生产构建的静态资源基址由 VITE_PUBLIC_BASE 控制——后端
 *    `app/main.py` 会把 `frontend/dist` 挂载到 `/app`，故生产默认 `/app/`。
 */
import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig, loadEnv } from 'vite'

export default defineConfig(({ mode }) => {
  // 加载 .env / .env.[mode] 中的变量（第三个参数为空表示不限制前缀）
  const env = loadEnv(mode, process.cwd(), '')
  const proxyTarget = env.VITE_PROXY_TARGET || 'http://127.0.0.1:8000'

  return {
    // 部署基址：开发为 '/'，生产构建默认 '/app/'（与后端静态挂载一致）
    base: env.VITE_PUBLIC_BASE || '/',

    plugins: [vue()],

    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url))
      },
      extensions: ['.mjs', '.js', '.json', '.vue']
    },

    server: {
      host: '127.0.0.1',
      port: 5173,
      strictPort: false,
      open: false,
      proxy: {
        // 所有 /api/v1/** 请求转发给 FastAPI，不做路径重写
        '/api': {
          target: proxyTarget,
          changeOrigin: true,
          ws: false
        }
      }
    },

    preview: {
      host: '127.0.0.1',
      port: 5173,
      proxy: {
        '/api': {
          target: proxyTarget,
          changeOrigin: true,
          ws: false
        }
      }
    },

    build: {
      outDir: 'dist',
      assetsDir: 'assets',
      sourcemap: false,
      chunkSizeWarningLimit: 2000
    },

    esbuild: {
      // 生产构建剔除调试语句
      drop: mode === 'production' ? ['debugger'] : []
    }
  }
})
