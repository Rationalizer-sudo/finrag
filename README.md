# FinRAG 使用流程说明

> 基于 RAG 的金融制度类知识问答系统 —— **从零跑通的操作手册**
> 项目位置：`C:\Users\28212\Desktop\校企合作\finrag`
> 详细的课题对照、架构设计与接口清单见项目内的 `README.md`；本文件只讲**怎么用**。

---

## 0. 先搞清楚三层界面入口

系统跑起来后有四个入口，作用不同：

| 入口 | 地址 | 用途 | 需要 Node.js？ |
|---|---|---|---|
| **免构建控制台** | `http://127.0.0.1:8000/ui/` | 日常使用全功能（问答/知识库/历史/收藏），零依赖 | 不需要 |
| Swagger 接口文档 | `http://127.0.0.1:8000/docs` | 调试接口、看请求响应结构 | 不需要 |
| Vue 正式前端（开发模式） | `http://127.0.0.1:5173` | 完整交互体验，需 `npm run dev` | 需要 |
| Vue 正式前端（Docker/Nginx） | `http://localhost` | 生产部署形态 | 不需要（镜像内已构建） |

**建议**：第一次跑通就用 `/ui/`，不用装 Node.js。

---

## 1. 前置条件自查（第 0 步，别跳过）

在项目根目录执行：

```powershell
cd C:\Users\28212\Desktop\校企合作\finrag
python scripts/check_env.py
```

它会一次性列出 7 类检查项：Python 版本、第三方依赖、MySQL、Milvus、通义千问、OCR、目录配置。
**每一项都会告诉你缺什么、怎么修**，比逐个试错快得多。

### 依赖清单

| 项 | 必需？ | 说明 |
|---|---|---|
| Python **3.10+** | 必需 | 3.12 已验证；LangChain 1.x 不支持 3.8 |
| MySQL **8.0** | 必需 | 存结构化数据（文档/条文/标签/历史/收藏） |
| Milvus **2.4** | 必需 | 存向量；standalone 模式还需 etcd + MinIO（Docker 编排已含） |
| `DASHSCOPE_API_KEY` | 必需 | 通义千问密钥，在 https://bailian.console.aliyun.com/ 申请 |
| Tesseract OCR | 可选 | 只在导入**图片/扫描件**时需要 |
| Node.js 20+ | 可选 | 只在构建 Vue 前端时需要 |

> 缺 OCR 只会禁用图片入库；缺 Node.js 只会没有 `/app/` 下的 Vue 页面。**两者都不影响问答主链路。**

---

## 2. 选择部署方式

| 方式 | 适用场景 | 耗时 |
|---|---|---|
| **A. Docker 一键** | 首次部署、答辩演示、要一次装齐 MySQL+Milvus+前端 | 约 10-20 分钟（镜像下载为主） |
| **B. 本地手工** | 开发调试、已有 MySQL 想复用 | 约 15 分钟 |
| **C. 只跑测试** | 只想验证代码质量与覆盖率 | 约 1 分钟 |

---

## 3. 流程 A：Docker 一键部署

### 步骤 1 — 准备环境变量

```powershell
cd C:\Users\28212\Desktop\校企合作\finrag
Copy-Item .env.example .env
notepad .env
```

**必须改这 3 项**（其余可保持默认）：

```ini
DASHSCOPE_API_KEY=sk-你的真实密钥
MYSQL_PASSWORD=finrag_pwd
MYSQL_ROOT_PASSWORD=root_pwd_change_me
```

### 步骤 2 — 启动全栈

```powershell
docker compose up -d --build
```

会依次拉起 6 个服务：`mysql` → `etcd` + `minio` → `milvus` → `backend` → `frontend`。
依赖关系已用健康检查串好，**不需要手动控制启动顺序**。

首次启动时 MySQL 容器会自动执行 `sql/01_schema.sql` 建好 9 张表。

### 步骤 3 — 等健康检查通过

```powershell
docker compose ps
```

等到 `mysql`、`milvus`、`backend` 都显示 `healthy`（Milvus 首次启动约需 30-60 秒）。

```powershell
# 后端日志，确认无报错
docker compose logs -f --tail=100 backend
```

### 步骤 4 — 创建 Milvus 向量集合

```powershell
docker compose exec backend python /app/scripts/init_milvus.py
```

预期看到：`集合名称 finrag_clause`、`向量维度 1024`、`索引类型 HNSW`、`距离度量 COSINE`。

> 这一步只做一次。集合已存在时重复执行是安全的（不会清数据）。
> 只有改了 embedding 模型/维度才需要 `--recreate`（会清空全部向量！）。

### 步骤 5 — 导入制度语料

**重要**：把从监管官网下载的**公开**制度文件放进 `data/corpus/`（子目录会被递归扫描），
支持 `.txt` `.md` `.pdf` `.docx` `.png` `.jpg`。下载来源清单见 `data/corpus/README.md`。

```powershell
docker compose exec backend python /app/scripts/ingest.py --path /app/data/corpus
```

想先看看解析质量再入库（强烈建议）：

```powershell
docker compose exec backend python /app/scripts/analyze_corpus.py --path /app/data/corpus
```

> **暂时拿不到真实语料？** 先用合成数据把系统跑起来验证功能：
> ```powershell
> docker compose exec backend python /app/scripts/generate_demo_corpus.py --documents 60
> docker compose exec backend python /app/scripts/ingest.py --path /app/data/demo
> ```
> ⚠️ 合成数据**不是真实法规**，文件标题已标注「演示数据集·合成文本」，只能用于功能与性能测试，
> **不能作为考核依据**。

### 步骤 6 — 打开界面使用

浏览器访问 **`http://localhost`**（Vue 前端）或 `http://localhost:8000/ui/`（免构建控制台）。

看到顶部状态栏显示 `mysql ✓ milvus ✓ qwen-chat ✓ qwen-embedding ✓` 即部署成功。

### 步骤 7 — 验证（可选但推荐）

```powershell
# 知识库应达到课题要求的 1000+ 知识点
docker compose exec backend python /app/scripts/ingest.py --stats

# 测试与覆盖率（课题要求 ≥ 70%）
docker compose exec backend sh -c "cd /app && python scripts/run_tests.py"
```

---

## 4. 流程 B：本地手工部署

适合已有 MySQL、想直接在宿主机开发调试的场景。

### 步骤 1 — 安装依赖

```powershell
cd C:\Users\28212\Desktop\校企合作\finrag
pip install -r requirements.txt -r requirements-dev.txt
```

### 步骤 2 — 配置环境变量

```powershell
Copy-Item .env.example .env
notepad .env
```

**本地方式必改**（指向你已有的 MySQL）：

```ini
DASHSCOPE_API_KEY=sk-你的真实密钥
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=finrag
MYSQL_PASSWORD=finrag_pwd
MYSQL_DB=finrag
```

### 步骤 3 — 环境预检

```powershell
python scripts/check_env.py
```

### 步骤 4 — 初始化 MySQL

```powershell
# 用 root 建库 + 建表 + 创建应用账号（会提示你填 root 密码）
python scripts/init_mysql.py --admin-user root --create-user
```

预期输出 `数据表数量: 9` 与 9 张表名。
只建表、不创建账号：去掉 `--create-user`。

> 想先看看会执行哪些 SQL：`python scripts/init_mysql.py --dry-run`

### 步骤 5 — 初始化 Milvus

Milvus 需要单独跑起来（本机装或用 Docker 只跑存储层）：

```powershell
docker compose up -d etcd minio milvus
python scripts/init_milvus.py
```

### 步骤 6 — 导入语料

```powershell
# 先看解析质量
python scripts/analyze_corpus.py --path data/corpus
# 再入库
python scripts/ingest.py --path data/corpus
# 建议记录公开来源链接，用于条文溯源
python scripts/ingest.py --path data/corpus --source-url https://www.pbc.gov.cn/xxx
```

### 步骤 7 — 启动后端

```powershell
python backend/main.py
```

默认监听 `http://127.0.0.1:8000`，开发模式自带热重载。

启动日志会做一次自检，明确告诉你 MySQL 表建了没、Milvus 通不通、API Key 配了没。

### 步骤 8 — 打开界面

访问 **`http://127.0.0.1:8000/ui/`**。

想用 Vue 正式前端（需要 Node.js）：

```powershell
cd frontend
npm install
npm run dev          # http://127.0.0.1:5173，已配好 /api 代理，无需处理跨域
```

---

## 5. 流程 C：只跑测试（不需要 MySQL / Milvus / API Key）

```powershell
cd C:\Users\28212\Desktop\校企合作\finrag
python scripts/run_tests.py
```

这个脚本会**自动选择**运行模式：

* 装了 `pytest` + `pytest-cov` → 用精确覆盖率；
* 都没装 → 用自实现的逐行追踪器（**零额外依赖**），照样能验证覆盖率门槛。

其他校验：

```powershell
python scripts/smoke_app.py      # 冒烟：应用能否创建、21 个接口是否注册成功
python scripts/check_schema.py   # 校验 sql/01_schema.sql 与 ORM 模型是否漂移
```

---

## 6. 日常使用流程

### 6.1 提问（核心场景）

1. 打开「智能问答」页，输入问题，例如
   `商业银行流动性风险指标的报送频率要求是什么`
2. 按需调整参数：

   | 参数 | 建议 |
   |---|---|
   | 检索模式 | `混合检索`（默认，向量+关键词，效果最稳）；想对比效果时切 `仅向量` / `仅关键词` |
   | 召回条数 | 5（默认）；问题复杂时提到 8-10 |
   | 业务条线 | 想限定领域时填，如 `银行`、`保险`、`证券`、`反洗钱` |
   | 时效过滤 | **保持「仅现行有效」**；只有查制度沿革时才切到「包含已废止」 |
   | 输出方式 | `完整返回` 或 `流式返回`（长回答体验更好） |

3. 点「提问」（或 `Ctrl + Enter`）
4. 看结果：
   * **回答正文** —— 里面的 `[1]` `[2]` 是可点击角标，点了会滚动并高亮到对应条文卡片
   * **可信度徽章** —— 鼠标悬停可看 5 个维度的得分与扣分理由
   * **耗时** —— 超过 2 秒会黄标告警（课题要求 ≤ 2 秒）
   * **条文溯源卡片** —— 制度名、文号、条文号、时效状态、正文、官方来源链接
   * 点「收藏」把这次问答存下来

### 6.2 入库新制度

在「知识库管理」页二选一：

* **上传文件**：选文件 → 填公开来源链接（建议填，用于溯源）→ 「上传并入库」
* **粘贴全文**：把制度原文贴进文本框 → 「文本入库」

系统会自动完成：解析 → 清洗 → 抽取文号/发文机关/施行日期/时效状态 → 按「第 X 条」切分 → 写 MySQL → 向量化写 Milvus。

**增量更新规则**（省算力也避免重复）：

| 情况 | 行为 |
|---|---|
| 文件内容完全没变 | 直接跳过，提示「已跳过」 |
| 文件名相同、内容变了 | 识别为**同一制度的新版本**，清掉旧条文与旧向量后写入，版本号 +1 |
| 文件名不同 | 当作新文档入库 |

### 6.3 查历史与收藏

* **问答历史**：按问题/回答内容搜索；可查看详情、收藏、单条删除、一键清空
* **收藏**：可编辑备注（例如「重点：报送频率」）、取消收藏
* 注意：**流式问答不写入历史**（页面会提示），需要留存请用「完整返回」模式

### 6.4 日常维护

| 要做的事 | 怎么做 |
|---|---|
| 查看知识库规模是否达标 | 「知识库管理」页顶部统计卡片 + 1000 知识点进度条 |
| 人工确认时效状态 | 文档行「查看条文」旁可修订元数据；**人工修改会同步更新条文与向量库过滤字段**，下次检索立即生效 |
| 条文全显示「状态待核」 | 说明原文中没找到施行/废止日期，用上面的元数据修订人工确认 |
| Milvus 集合被误删 / 向量写入失败 | 点「重建向量索引」（或 `python scripts/ingest.py --rebuild-vectors`） |
| 下线某份制度 | 删除文档，会连带清理其条文与 Milvus 向量 |
| 排查入库问题 | 「知识库管理」页底部有**入库任务记录**，含阶段、条文数、耗时与错误信息 |

---

## 7. 验证清单：怎么确认真的成功了

按顺序打这几个接口，全绿才算跑通：

```powershell
# 1. 进程活着（不查外部依赖）
curl http://127.0.0.1:8000/api/v1/health/live
#    → {"status":"alive",...}

# 2. 五个依赖组件的状态
curl http://127.0.0.1:8000/api/v1/health
#    → components 里 mysql/milvus/qwen-chat/qwen-embedding 应为 healthy:true
#      （tesseract-ocr 为 false 是正常的，仅影响图片入库）

# 3. 知识库规模
curl http://127.0.0.1:8000/api/v1/knowledge/stats
#    → chunk_count ≥ 1000，vector_ready:true，meets_target.knowledge_points_ok:true

# 4. 问答闭环
curl -X POST http://127.0.0.1:8000/api/v1/qa/ask ^
  -H "Content-Type: application/json" ^
  -d "{\"question\":\"商业银行流动性风险指标的报送频率要求是什么\",\"top_k\":5}"
#    → data.answer 非空、data.citations 非空、data.credibility.score > 0
```

最后在 `/ui/` 里提一个真问题，确认：**回答有 `[n]` 标注 → 点角标能跳到条文卡片 → 点来源链接能打开官方页面**。这条链路通了，就说明溯源功能真的在工作。

---

## 8. 常见问题

| 现象 | 原因与处理 |
|---|---|
| 提问返回 **502 / 错误码 50030** | 没配或配错 `DASHSCOPE_API_KEY`。访问 `/api/v1/health` 看 `qwen-chat` / `qwen-embedding` 的 `configured` 字段 |
| **Milvus 连接失败** | `docker compose ps` 看容器；`docker compose logs milvus` 看日志。Milvus 依赖 etcd 与 MinIO，首次启动要等 30-60 秒 |
| **MySQL 连接失败 / 表不存在** | 先 `python scripts/init_mysql.py`。启动日志会直接打印「尚未创建数据表」的提示 |
| 上传接口返回 **50010** | 服务端缺 `python-multipart`：`pip install python-multipart` 后重启。**该依赖缺失不会导致服务起不来**（路由会自动降级为提示接口），临时可改用「文本入库」 |
| **OCR 不可用** | 访问 `/api/v1/knowledge/ocr/status` 看具体原因。Windows：`winget install UB-Mannheim.TesseractOCR` 并在 `.env` 配 `TESSERACT_CMD`；Ubuntu：`apt-get install -y tesseract-ocr tesseract-ocr-chi-sim poppler-utils` |
| 知识点数 **不足 1000** | 多导入语料：`python scripts/ingest.py --path data/corpus` |
| 改了 `QWEN_EMBEDDING_DIM` 后写入失败 | 集合维度与配置不一致。`python scripts/init_milvus.py --recreate` 后 `python scripts/ingest.py --rebuild-vectors` |
| 提问总答「未检索到相关规定」 | 两种可能：① 知识库确实没有相关制度；② 问题表述与制度用语差异太大 —— 换用制度术语（如「资本充足率」「投资者适当性」「可疑交易报告」）再试 |
| 回答里条文被标「已废止」而没作答 | 这是**刻意设计**：检索到的条文全为过时，系统拒绝基于废止条文作答以免误导。要查历史沿革请把「时效过滤」切到「包含已废止」 |

---

## 9. 命令速查表

在项目根目录 `C:\Users\28212\Desktop\校企合作\finrag` 执行：

```powershell
# ---------- 环境与校验 ----------
python scripts/check_env.py                  # 环境预检（缺什么、怎么修）
python scripts/check_schema.py               # 建表脚本与 ORM 模型一致性
python scripts/smoke_app.py                  # 应用与 21 个接口注册冒烟
python scripts/run_tests.py                  # 测试 + 覆盖率（门槛 70%）
python scripts/run_tests.py --report         # 附带未覆盖行明细

# ---------- 初始化（各做一次） ----------
python scripts/init_mysql.py --admin-user root --create-user   # 建库 + 9 张表 + 应用账号
python scripts/init_milvus.py                                  # 创建向量集合
python scripts/init_milvus.py --recreate                       # 重建集合（清空向量！）

# ---------- 语料 ----------
python scripts/analyze_corpus.py --path data/corpus   # 入库前先看解析质量
python scripts/ingest.py --path data/corpus           # 批量入库
python scripts/ingest.py --stats                      # 知识库统计与达标情况
python scripts/ingest.py --rebuild-vectors            # 重建向量索引
python scripts/ingest.py --delete-document 12         # 删除文档（连带清向量）
python scripts/ingest.py --dry-run --path data/corpus # 只列出将入库的文件
python scripts/generate_demo_corpus.py --documents 60 # 生成合成演示语料（非真实法规）

# ---------- 启动 ----------
python backend/main.py                                # 后端 http://127.0.0.1:8000

# ---------- Docker ----------
docker compose up -d --build
docker compose ps
docker compose logs -f --tail=200 backend
docker compose exec backend python /app/scripts/init_milvus.py
docker compose exec backend python /app/scripts/ingest.py --path /app/data/corpus
docker compose exec backend sh -c "cd /app && python scripts/run_tests.py"
docker compose down                                   # 停止（保留数据卷）
```

有 `make` 的环境也可以用封装好的目标：`make help`、`make up`、`make init-milvus`、`make ingest`、`make test`。

---

## 10. 本机当前状态（你还需要做什么）

我在这台机器上实测过，现状如下：

| 项 | 状态 | 说明 |
|---|---|---|
| Python 3.12.10 | ✅ 就绪 | |
| MySQL 8.0 服务 | ✅ 运行中 | 但**还没有 `finrag` 库与应用账号**，需执行 `init_mysql.py` |
| 已有 Python 依赖 | ✅ 部分 | FastAPI / LangChain 1.4 / SQLAlchemy / pypdf / jieba / rank-bm25 / uvicorn 等已具备 |
| 测试与覆盖率 | ✅ 已验证 | **493 个用例全部通过，覆盖率 88.92%** |
| 建表脚本一致性 | ✅ 已验证 | 9 张表逐列比对完全一致 |
| **pip 联网** | ❌ **不通** | pypi / 清华 / 阿里镜像均无法连接 —— **这是当前第一阻塞项** |
| 缺失的 Python 包 | ❌ | `pymysql`、`pymilvus`、`python-multipart`、`pytesseract`、`pdf2image` |
| Docker | ❌ 未安装 | 因此 Milvus 起不来 |
| Tesseract OCR | ❌ 未安装 | 仅影响图片/扫描件入库 |

### 要跑通完整问答链路，按这个顺序解决

1. **先解决 pip 联网**（三选一）
   * 挂代理 / 换可用镜像源：`pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple`
   * 离线安装：在能联网的机器 `pip download -r requirements.txt -d wheels`，拷过来 `pip install --no-index --find-links=wheels -r requirements.txt`
   * 直接用 Docker 方案（若有条件装 Docker Desktop，可跳过 pip 问题）

2. **装 Docker Desktop** → 用流程 A 一条命令拉起 MySQL + Milvus + 前端
   （本机已有 MySQL 8，也可以只跑存储层：`docker compose up -d etcd minio milvus`）

3. **申请通义千问 API Key** → 填进 `.env` 的 `DASHSCOPE_API_KEY`
   （未配置时 `/health` 会明确标出，问答接口会返回 502 并给出安装指引）

4. **把真实语料放进 `data/corpus/`** → 运行 `ingest.py`
   ⚠️ 现在 `data/demo/` 里的 60 篇是**合成语料**，已标注「非真实法规」，**验收前必须替换成监管官网下载的公开文件**

5. （可选）**装 Tesseract** → 才能导入图片/扫描件制度

### 在配齐环境之前，你现在就能做的事

```powershell
cd C:\Users\28212\Desktop\校企合作\finrag
python scripts/run_tests.py     # 已验证：493 通过，88.92% 覆盖率
python scripts/smoke_app.py     # 应用能创建，21 个接口全部注册
python scripts/check_schema.py  # 建表脚本与 ORM 一致
python backend/main.py          # 后端能启动；/ui/ 能打开界面
```

后端启动后 `/health` 会显示 MySQL 与 Milvus 为 `degraded`（因为没配好），
但**界面、接口文档、参数校验、错误提示都能正常看到**，可以先熟悉交互流程。
