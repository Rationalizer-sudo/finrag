# =============================================================================
#  FinRAG · 前端镜像（Vue3 + Vite 多阶段构建 -> Nginx 静态托管）
# =============================================================================
#
#  构建命令示例（构建上下文同样是【项目根目录】）
#  ---------------------------------------------------------------------------
#    # 方式一：直接使用 docker build
#    docker build -f docker/frontend.Dockerfile -t finrag-frontend:latest .
#
#    # 方式二：使用 docker compose（推荐，compose 已写好 context: .）
#    docker compose build frontend
#
#    # 方式三：Windows PowerShell
#    cd C:\Users\28212\Desktop\校企合作\finrag
#    docker build -f docker/frontend.Dockerfile -t finrag-frontend:latest .
#
#  为什么上下文是项目根目录？
#  ---------------------------------------------------------------------------
#    构建阶段需要访问 frontend/（源码 + package.json）以及 nginx/default.conf，
#    二者分属不同目录且 Docker 的 COPY 不能越过上下文，因此上下文必须是项目根，
#    COPY 里带 frontend/ 与 nginx/ 前缀。
#
#  【构建前置条件】
#    1) frontend/package.json 必须存在；
#    2) frontend/package-lock.json 建议提交（保证 `npm ci` 可复现构建）。
#       若缺失，构建会打印告警并自动退化为 `npm install`，不会中断构建，
#       但依赖版本可能漂移，建议尽快执行一次 `npm install` 并提交 lock 文件。
#    3) 若 frontend 目录使用 pnpm/yarn，请相应替换 COPY 与安装命令。
# =============================================================================

# =============================================================================
# 阶段一：构建（node:20-alpine）
# =============================================================================
FROM node:20-alpine AS builder

LABEL org.opencontainers.image.title="FinRAG Frontend Builder" \
      org.opencontainers.image.description="Vue3 + Vite 构建阶段"

WORKDIR /build

# 国内网络可启用 npm 镜像源（境外构建可删除该 RUN）
RUN npm config set registry https://registry.npmmirror.com

# 先拷贝依赖清单，利用层缓存：源码改动不会导致重新安装依赖
COPY frontend/package.json ./package.json
# 可选拷贝 package-lock.json（该文件可能尚未提交到仓库）：
#   先 touch 一个占位文件，因此本层结束时 package-lock.json 一定存在；
#   若构建上下文里确实有该文件，则被真实内容覆盖。
RUN touch package-lock.json
COPY frontend/package-lock.json ./package-lock.json

# 有 lock 文件则用 npm ci（严格按 lock 安装，构建可复现）；
# 没有 lock 文件时退化为 npm install 并打印告警，保证首次也能构建成功。
# 强烈建议执行一次 `npm install` 并提交 package-lock.json。
RUN if [ -s package-lock.json ]; then \
        echo "检测到 package-lock.json -> 使用 npm ci"; \
        npm ci --no-audit --no-fund; \
    else \
        echo "警告：未找到 frontend/package-lock.json，退化为 npm install（依赖版本可能漂移）"; \
        npm install --no-audit --no-fund; \
    fi

# 再拷贝源码并构建（产物默认输出到 /build/dist）
COPY frontend/ ./

# Vite 构建时注入的变量
#   VITE_API_BASE_URL：前端 axios 基址。这里用同源前缀 /api/v1，
#     由 Nginx 反向代理到 backend 容器，浏览器侧无跨域。
#   VITE_PUBLIC_BASE：静态资源基址（对应 vite.config.js 的 base）。
#     【重要】frontend/.env.production 里写的是 /app/，那是给「后端 FastAPI 把
#     dist 挂到 /app 路径」这种部署方式用的（见 backend/app/main.py 的
#     FRONTEND_DIST 逻辑）。本镜像改用 Nginx 在根路径托管，因此**必须覆盖为 /**，
#     否则 index.html 会引用 /app/assets/xxx.js，而 Nginx 根目录下并无该路径 -> 白屏。
#     注意优先级：命令行传入的环境变量高于 .env.production 中的同名变量。
#   NODE_OPTIONS 视前端依赖体积（element-plus + echarts）酌情加大堆内存。
ARG VITE_API_BASE_URL=/api/v1
ARG VITE_PUBLIC_BASE=/
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL \
    VITE_PUBLIC_BASE=$VITE_PUBLIC_BASE

RUN npm run build

# =============================================================================
# 阶段二：运行（nginx:1.27-alpine）
# =============================================================================
FROM nginx:1.27-alpine AS runtime

LABEL org.opencontainers.image.title="FinRAG Frontend" \
      org.opencontainers.image.description="Vue3 静态资源 Nginx 托管" \
      org.opencontainers.image.licenses="MIT"

# tzdata + curl：时区正确、容器健康检查可用
RUN set -eux; \
    apk add --no-cache tzdata curl; \
    ln -snf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime; \
    echo "Asia/Shanghai" > /etc/timezone

# 用项目自带配置覆盖默认站点配置（含 SPA 回退与 SSE 关闭缓冲）
RUN rm -f /etc/nginx/conf.d/default.conf
COPY nginx/default.conf /etc/nginx/conf.d/default.conf

# 拷贝构建产物
COPY --from=builder /build/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost/ >/dev/null || exit 1

# 前台运行，保证容器生命周期与 nginx 主进程一致
CMD ["nginx", "-g", "daemon off;"]
