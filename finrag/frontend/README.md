# FinRAG 前端 · 基于 RAG 的金融制度类知识问答系统

Vue 3 + Vite 5 + Element Plus + Pinia + Vue Router + Axios + ECharts 实现的前端工程，
对接后端 FastAPI 服务（默认 `http://127.0.0.1:8000`，接口前缀 `/api/v1`）。

---

## 一、快速开始

```bash
# 1. 安装依赖（需要 Node.js >= 18）
npm install

# 2. 启动开发服务器（默认 http://127.0.0.1:5173）
npm run dev
```

启动前请确认后端已运行：

```bash
# 在 finrag/backend 目录下
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
# 或
python -m uvicorn app.main:app --reload --port 8000
```

浏览器打开 <http://127.0.0.1:5173> 即可。开发服务器已配置 `/api` 代理，
所有 `/api/v1/**` 请求会被转发到 `http://127.0.0.1:8000`，**无需处理跨域**。

### 其他命令

| 命令 | 说明 |
| --- | --- |
| `npm run dev` | 启动开发服务器（含 HMR 与接口代理） |
| `npm run build` | 生产构建，输出到 `frontend/dist` |
| `npm run preview` | 预览生产构建产物（同样带 `/api` 代理） |
| `npm run lint` | ESLint 检查（`--fix` 可自动修复） |

---

## 二、目录结构

```
frontend/
├── package.json            依赖与脚本
├── vite.config.js          Vite 配置（@ 别名、/api 代理、生产基址）
├── index.html              应用入口 HTML
├── .env.development        开发环境变量（接口基址、代理目标、超时）
├── .env.production         生产环境变量（默认部署在 /app/）
├── .eslintrc.cjs           ESLint 配置（Vue 3 + Vite）
├── public/favicon.svg      站点图标
└── src/
    ├── main.js             应用入口：注册 Element Plus / Pinia / Router / 图标
    ├── App.vue             根组件（顶部导航 + 路由视图 + 页脚）
    ├── router/index.js     路由表（5 个业务页面，history 模式）
    ├── api/                接口层：所有 axios 调用集中在此，视图层不直接发请求
    │   ├── request.js      axios 实例：baseURL、拦截器、统一解包与错误提示
    │   ├── system.js       /health、/capabilities
    │   ├── qa.js           /qa/ask（含 SSE 流式）、/qa/history
    │   ├── knowledge.js    /knowledge/**（统计、文档、条文、标签、任务、入库）
    │   └── favorites.js    /favorites
    ├── stores/             Pinia 状态
    │   ├── user.js         user_id / session_id（持久化到 localStorage）
    │   ├── chat.js         当前会话消息、检索参数、提问编排（流式/非流式）
    │   └── knowledge.js    知识库统计、文档列表、能力清单
    ├── styles/main.css     全局样式（设计变量、Markdown 正文、响应式）
    ├── utils/index.js      工具函数与兜底枚举（状态配色、格式化、复制等）
    ├── components/
    │   ├── AppHeader.vue       顶部导航 + 后端健康状态 + 用户标识切换
    │   ├── AnswerBubble.vue    回答气泡（Markdown + 引用角标 + 可信度 + 溯源卡片）
    │   ├── CitationCard.vue    条文溯源卡片（文号/条款/时效状态/正文/来源链接）
    │   ├── CredibilityBadge.vue 可信度徽章（分数 + 等级 + 维度明细 popover）
    │   └── EmptyState.vue      空状态占位
    └── views/
        ├── ChatView.vue        智能问答页（主页面）
        ├── KnowledgeView.vue   知识库管理（统计、图表、文档增删改查、入库）
        ├── HistoryView.vue     问答历史（搜索、分页、详情、删除、收藏）
        ├── FavoriteView.vue    我的收藏（备注编辑、删除、展开详情）
        └── AboutView.vue       系统介绍（技术栈、RAG 架构、评分口径、达标情况）
```

---

## 三、接口契约与解包约定

后端统一响应体：

```jsonc
// 成功
{ "code": 0, "message": "ok", "data": { /* 业务数据 */ } }
// 失败（HTTP 状态码非 2xx）
{ "code": 40400, "message": "资源不存在", "detail": null, "request_id": "..." }
```

`src/api/request.js` 的响应拦截器负责：

1. **解包**：`code === 0` 时直接把 `data` 返回给调用方，视图层无需再取 `.data.data`；
2. **业务错误**：`code !== 0`（可能出现在 HTTP 200 上，例如上传依赖缺失返回 `50010`）
   统一弹出 `ElMessage` 并 `reject`，错误对象上带有 `code` / `detail` 字段；
3. **网络/超时**：给出「后端未启动」「请求超时」等可操作提示；
4. **裸响应兼容**：`/health` 与 `/capabilities` 返回的是未经包装的对象，
   拦截器检测到没有 `code` 字段时原样返回；
5. **静默模式**：请求配置里传 `silent: true` 可关闭自动错误提示（用于轮询类请求）。

> 需要关闭自动提示的写法：`request.get('/health', { silent: true })`。

---

## 四、功能点与课题验收对照

| 验收点 | 实现位置 |
| --- | --- |
| 问题输入（Ctrl+Enter 提交）、检索模式/Top-K/业务条线/时效过滤/流式切换 | `views/ChatView.vue` |
| Markdown 回答渲染，`[n]` 渲染为可点击上标并定位条文卡片 | `components/AnswerBubble.vue` |
| 条文溯源卡片（制度名/文号/条款/状态/正文/来源链接） | `components/CitationCard.vue` |
| 可信度徽章（分数 + 等级配色 + 维度明细与扣分说明） | `components/CredibilityBadge.vue` |
| 性能提示（`latency_ms`，超 2000 ms 黄色警告） | `components/AnswerBubble.vue` |
| 知识库统计卡片、1000 知识点达标进度、组件状态、文档表格、上传/文本入库、删除、元数据编辑、重建向量索引、入库任务 | `views/KnowledgeView.vue` |
| 历史列表、搜索、分页、详情、删除、清空、一键收藏 | `views/HistoryView.vue` |
| 收藏列表、备注编辑、删除、展开完整回答与引用 | `views/FavoriteView.vue` |
| 系统介绍、技术栈、RAG 架构流程、评分口径（动态渲染 `/capabilities`）、达标情况 | `views/AboutView.vue` |
| 骨架屏 / 错误提示 / 空状态 / 响应式布局 | 各视图 + `components/EmptyState.vue` |

---

## 五、若干实现说明（排障时有用）

### 1. 流式问答不写入历史

后端 `/qa/ask/stream` **不会**写入 `qa_history` 表，因此：

- 流式回答没有 `history_id`，无法在「历史」页回溯；
- 界面上会给出黄色提示，且「保存到问答历史」开关在流式模式下自动置灰。

需要在历史页回溯时，请关闭「流式输出（SSE）」后重新提问。

### 2. 流式模式的耗时来自浏览器实测

SSE 的 `done` 事件不返回 `latency_ms`，因此流式回答的耗时标签会额外显示「客户端实测」，
表示该数值是前端从发起到收到 `done` 的墙钟时间，与非流式接口返回的服务端耗时口径不同。

### 3. 文件上传依赖 python-multipart

后端未安装 `python-multipart` 时，上传接口会返回 `code: 50010` 并给出安装提示。
前端在知识库页会：

- 通过 `/capabilities` 的 `features.file_upload` 预判并禁用上传按钮；
- 捕获 `50010` 并引导改用「文本入库」（该通道不依赖 `python-multipart`）。

### 4. 用户身份

系统没有登录鉴权，使用 `user_id` 区分历史与收藏的归属，默认 `anonymous`。
可在顶栏右上角的用户标签处修改，值持久化在 `localStorage` 的 `finrag_user_id` 键中。

### 5. 生产部署与基址

后端 `app/main.py` 会把 `frontend/dist` 挂载到 `/app`，因此 `.env.production` 中的
`VITE_PUBLIC_BASE` 默认是 `/app/`（同时影响 `createWebHistory` 的基址）。
若改用 nginx 部署在根路径，请把它改为 `/` 后重新构建。

```bash
npm run build
# 产物：frontend/dist
# 访问：http://127.0.0.1:8000/app/
```

### 6. 枚举来源

业务条线、文件类型、时效状态等下拉选项优先取自 `/api/v1/capabilities`；
若该接口不可用，会回落到 `src/utils/index.js` 中的兜底常量（取值与后端
`app/core/constants.py` 一致），因此后端未启动时页面仍可正常渲染。

---

## 六、常见问题

**Q：页面提示「后端未连接」/ 网络异常？**
A：确认 FastAPI 已在 `127.0.0.1:8000` 启动；检查 `vite.config.js` 中 `VITE_PROXY_TARGET`
（或 `.env.development` 中的同名变量）是否与实际端口一致。

**Q：问答返回 502 / 大模型调用失败？**
A：后端未配置 `DASHSCOPE_API_KEY`。在 `finrag/.env` 中填写通义千问 API Key 后重启后端。

**Q：知识库页提示向量索引未就绪？**
A：Milvus 未启动或集合不存在。启动 Milvus 后点击「重建向量索引」。

**Q：修改了 `user_id` 后看不到之前的记录？**
A：历史与收藏按 `user_id` 隔离，改回原标识即可看到原记录。

**Q：npm install 失败？**
A：本工程依赖均为公开包。若处于离线环境，请先在可联网机器上执行 `npm install`
后整体拷贝 `node_modules`，或配置可用的 npm 私有源。
