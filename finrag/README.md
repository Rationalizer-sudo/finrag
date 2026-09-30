# FinRAG · 基于 RAG 的金融制度类知识问答系统

> **校企合作创新实践课题** · 人工智能应用（企业命题）／软件开发（学生选题任务书）
> 技术难度：**中级（技术整合 + 局部创新）** · 预期周期：16 周 · 建议组队：3-4 人

解决金融制度知识（银行监管政策、证券行业规范、保险制度等）查询效率低、精准度不足的问题，
实现**知识点快速检索与精准问答**：支持关键词检索、自然语言问答、条文关联查询三种核心场景。

---

## 一、课题要求落实情况

| 课题要求 | 落实位置 | 说明 |
|---|---|---|
| Python 3.8+ | 全项目 | 实际要求 **3.10+**（LangChain 1.x 的下限），Docker 镜像用 3.11 |
| LangChain 搭建 RAG 架构 | `backend/app/rag/answer_chain.py` | `ChatPromptTemplate \| ChatModel \| StrOutputParser` 真正的 LCEL 链 |
| FastAPI 开发后端接口 | `backend/app/api/v1/` | 21 个接口，含 SSE 流式问答 |
| MySQL 存储结构化数据 | `backend/app/db/models.py`、`sql/01_schema.sql` | 9 张表 + 1 个现行有效条文视图 |
| Milvus 作为向量数据库 | `backend/app/db/milvus_store.py` | HNSW + COSINE，标量过滤下推 |
| 结构化与向量数据协同查询 | `backend/app/retrieval/hybrid.py` | 向量主键 = `kb_chunk.id`，命中后回查正文 |
| 文档自动解析/分段/embedding/向量存储 | `backend/app/ingest/` | 文本 / PDF / docx / 图片 OCR 全支持 |
| 知识点标签分类管理 | `kb_tag` / `kb_chunk_tag` + `metadata.extract_tags` | 业务条线 + 文件类型 + jieba 关键词 |
| 向量检索 + 关键词检索结合 | `backend/app/retrieval/` | 向量 + BM25，RRF 融合 + 归一化重排 |
| 答案溯源（标注对应制度条文） | `backend/app/rag/citation.py` | 解析回答中的 `[n]`，映射为带链接的引用对象 |
| Web 前端交互界面 | `frontend/`（Vue 3 + Vite）、`backend/app/static/`（免构建控制台） | 见下文「前端」章节 |
| Docker 容器化部署 | `docker-compose.yml`、`docker/*.Dockerfile` | MySQL + etcd + MinIO + Milvus + backend + frontend |
| **多模态**：图片扫描件 OCR | `backend/app/ingest/ocr.py` | Tesseract，`/knowledge/ocr/status` 可查可用性 |
| **时效性**：生效/失效标注，优先返回有效条文 | `core/constants.py`、`db/milvus_store.build_filter_expr`、`db/repository.apply_chunk_filters` | 过滤在 Milvus 与 MySQL 两侧同口径下推 |
| **交互优化**：历史记录、收藏、可信度评分、溯源链接 | `qa_history` / `qa_favorite` / `rag/credibility.py` | 可信度 0~100，5 个可解释维度 |
| PEP8 规范 | `pyproject.toml`（ruff + black） | 行宽 100，中文注释 |
| 单元测试覆盖率 ≥ 70% | `tests/`（493 个用例）+ `pyproject.toml` | `--cov-fail-under=70`，**实测 88.92%** |
| 1000+ 条金融制度核心知识点 | `scripts/generate_demo_corpus.py` 实测 1451 条 | 验收请用真实公开制度文本（见 `data/corpus/README.md`） |
| 问答响应 ≤ 2 秒 | `RETRIEVE_TOP_K` 等可调；响应含 `within_budget` 标记与慢请求告警 | `settings.response_time_budget_ms` |
| 数据安全：只用公开数据 | `data/corpus/README.md` 列出监管官网来源 | 不内置任何涉密或企业敏感数据 |

---

## 二、系统架构

```
                        ┌──────────────────────────────┐
   浏览器 ─────────────▶│  Vue 3 + Vite（frontend/）    │
                        │  或免构建控制台（/ui/）        │
                        └──────────────┬───────────────┘
                                       │ HTTP / SSE
                        ┌──────────────▼───────────────┐
                        │   FastAPI（backend/app/api）  │
                        │   21 个接口 · 统一错误体       │
                        └──────────────┬───────────────┘
                                       │
        ┌──────────────────────────────┼──────────────────────────────┐
        │                              │                              │
┌───────▼────────┐          ┌──────────▼──────────┐        ┌──────────▼─────────┐
│  入库链路       │          │    问答链路          │        │   用户数据          │
│  ingest/       │          │    retrieval/ + rag/ │        │   services/        │
├────────────────┤          ├─────────────────────┤        ├────────────────────┤
│ 解析(txt/pdf/  │          │ ① 向量检索(Milvus)   │        │ 问答历史            │
│ docx/图片OCR)  │          │ ② BM25(MySQL 条文)   │        │ 收藏                │
│ → 清洗         │          │ ③ RRF 融合           │        │ 入库任务审计        │
│ → 元数据抽取    │          │ ④ MySQL 回查正文     │        └────────┬───────────┘
│ → 条文切分      │          │ ⑤ 相关度下限 + 重排   │                 │
│ → MySQL 落库    │          │ ⑥ LangChain → 通义千问│                │
│ → 通义千问向量化 │          │ ⑦ 条文溯源 + 可信度   │                │
│ → Milvus 写入   │          └──────────┬──────────┘                 │
└───────┬────────┘                     │                            │
        │                              │                            │
┌───────▼──────────────────────────────▼────────────────────────────▼──────────┐
│  MySQL 8.0（结构化：文档/条文/标签/历史/收藏/任务）                            │
│  Milvus 2.4（向量：embedding + status/business_line/effective_ts/expiry_ts）  │
│  通义千问 DashScope（text-embedding-v3 向量化 + qwen-plus 生成）              │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 分层与目录

```
finrag/
├── backend/
│   ├── main.py                     uvicorn 启动入口
│   └── app/
│       ├── core/                   配置、日志、异常、领域常量、能力探测
│       ├── db/                     ORM 模型、Repository、Milvus 封装、会话管理
│       ├── schemas/                Pydantic 请求/响应模型
│       ├── ingest/                 解析(含 OCR) → 清洗 → 元数据 → 切分 → 入库流水线
│       ├── llm/                    通义千问 对话 / 向量化接入
│       ├── retrieval/              tokenizer、向量检索、BM25、RRF 融合、重排
│       ├── rag/                    Prompt、LangChain 生成链、条文溯源、可信度评分
│       ├── services/               容器(DI)、知识库服务、问答服务、上传落盘
│       ├── api/v1/                 health / qa / knowledge / favorites / upload
│       ├── static/index.html       免构建控制台（离线可用）
│       └── main.py                 FastAPI 应用工厂（中间件 + 全局异常处理）
├── frontend/                       Vue 3 + Vite + Element Plus + Pinia 正式前端
├── sql/01_schema.sql               MySQL 建表脚本（含注释、索引、视图）
├── scripts/                        init_mysql / init_milvus / ingest / check_env /
│                                   check_schema / analyze_corpus /
│                                   generate_demo_corpus / run_tests / smoke_app
├── tests/                          493 个用例（unit + integration），覆盖率门槛 70%
├── postman/FinRAG.postman_collection.json   27 个请求的调试集合
├── docker/                         backend.Dockerfile / frontend.Dockerfile
├── nginx/default.conf              前端托管 + API 反代 + SSE 专用配置
├── docker-compose.yml              一键编排（含健康检查与依赖顺序）
├── Makefile                        常用命令封装
└── pyproject.toml                  PEP8 / pytest / 覆盖率 / 打包 配置
```

---

## 三、快速开始

### 方式 A：Docker 一键部署（推荐）

```bash
cp .env.example .env
# 必填：DASHSCOPE_API_KEY（阿里云百炼控制台申请）
# 必填：MYSQL_PASSWORD、MYSQL_ROOT_PASSWORD
docker compose up -d --build

# 容器就绪后初始化向量集合（MySQL 建表由容器自动完成）
docker compose exec backend python /app/scripts/init_milvus.py
```

访问：

| 地址 | 说明 |
|---|---|
| http://localhost | Vue 前端（Nginx 托管） |
| http://localhost:8000/docs | Swagger 接口文档 |
| http://localhost:8000/ui/ | 免构建控制台（备用） |

### 方式 B：本地开发（无需 Docker）

```bash
# 1. 依赖
pip install -r requirements.txt -r requirements-dev.txt

# 2. 环境变量
cp .env.example .env      # 填 DASHSCOPE_API_KEY

# 3. 环境预检（一次性看清缺什么）
python scripts/check_env.py

# 4. 初始化存储
python scripts/init_mysql.py        # 建库 + 建表
python scripts/init_milvus.py       # 创建 Milvus 集合

# 5. 导入制度语料
python scripts/ingest.py --path data/corpus

# 6. 启动后端
python backend/main.py              # http://127.0.0.1:8000
```

前端（可选，需要 Node.js）：

```bash
cd frontend
npm install
npm run dev                         # http://127.0.0.1:5173（已配 /api 代理）
```

> **没有 Node.js 也能用**：`http://127.0.0.1:8000/ui/` 是零依赖的原生控制台，
> 覆盖问答、知识库管理、历史、收藏、系统信息全部核心功能。

### 常用脚本

```bash
python scripts/check_env.py                    # 环境预检
python scripts/check_schema.py                 # 校验建表脚本与 ORM 模型一致性
python scripts/analyze_corpus.py --path data/demo   # 入库前先看解析质量
python scripts/ingest.py --path data/corpus    # 批量入库
python scripts/ingest.py --stats               # 知识库统计
python scripts/ingest.py --rebuild-vectors     # 重建向量索引
python scripts/ingest.py --delete-document 12  # 删除文档（连带清理向量）
python scripts/run_tests.py                    # 测试 + 覆盖率
python scripts/smoke_app.py                    # 应用与接口注册冒烟检查
```

---

## 四、核心设计说明

### 1. 知识库构建：条文感知切分

金融制度以「第 X 条」为最小规范单元，因此切分策略是**条文优先 + 递归兜底**：

* 按行首 `第X条` 切分，保留所属**章节**上下文；
* 抽取 `【小标题】` 作为条文主题；
* 单条过长（> `CHUNK_SIZE × 1.6`）时用 LangChain `RecursiveCharacterTextSplitter`
  按 `段落 → 句号 → 分号 → 逗号` 递归二次切分，并给每片补回条文号前缀；
* 无条文结构时整体回退递归切分，段落编号（一、/（一）/1.）作为伪条文号；
* 清洗阶段去除跨页重复页眉、页码、水印行，修正被空格拆散的条文号与中文日期。

> 实测（`data/demo` 60 篇合成语料）：60/60 篇条文切分成功，0 篇回退递归切分，
> 文号/发文机关/施行日期识别率 100%，共切出 **1451 条**知识点。

### 2. 时效性管理（差异化要求）

`现行有效 / 尚未生效 / 已修订 / 已废止 / 状态待核` 五态贯穿全链路：

* **抽取**：正则识别「自 X 年 X 月 X 日起施行／废止」，结合文件名与文首标识推断；
* **存储**：MySQL 冗余 `status / effective_ts / expiry_ts`，Milvus 存同名标量字段；
* **过滤下推**：检索时同时在 Milvus 表达式与 SQL 条件中排除过时条文
  （`expiry_ts == 0 or expiry_ts >= now`），**两条链路口径必须一致**；
* **防误导**：召回的条文若全为过时，系统直接返回「相关条文均已废止，不予作答」，
  不调用大模型；
* **可查历史**：传 `only_valid=false` 可显式检索已废止条文，用于制度沿革研究。

### 3. 混合检索与 RRF 融合

```
向量检索（Milvus, COSINE）─┐
                            ├─▶ RRF: score(d) = Σ w_r / (k + rank_r(d))   k=60
BM25 关键词（MySQL 条文）  ─┘         │
                                      ├─▶ MySQL 回查正文（协同查询）
                                      ├─▶ 相关度下限过滤（MIN_VECTOR_SCORE）
                                      └─▶ 归一化 + 词汇覆盖重排 → Top-K
```

* 向量与 BM25 分数**量纲不同**，直接线性加权需反复调参且对新语料不稳定；
  RRF 只利用排名信息，稳健且无超参敏感问题；
* 重排得分 = `0.8 × 归一化RRF + 0.2 × 词汇覆盖率`，既有主信号又有可解释性；
* **相关度下限**（默认 0.30）：关键词命中视为强证据直接保留；仅靠向量召回的
  候选必须达到相似度下限，否则丢弃——这是「资料不足就如实说不知道」的工程保障，
  避免拿不相关条文让大模型硬凑答案；
* BM25 索引带**指纹缓存**：以「条文数 + 最近更新时间」为键，入库后自动失效重建，
  新制度立即可检索。

### 4. 条文溯源

* Prompt 强制模型用 `[1] [2]` 标注依据，并禁止编造条文编号；
* 解析回答中的引用标记（兼容 `[n]`／`［n］`／`【n】`），映射为引用对象；
* 模型未标注引用时**兜底返回全部召回条文**并告警，不丢失溯源信息；
* 检测「长句无引用」的实质陈述并提示人工核对；
* 每个引用对象携带制度名、文号、条文号、时效状态、**官方来源链接**
  （无官方链接时回退到 `/api/v1/knowledge/chunks/{id}` 条文详情页）。

### 5. 答案可信度评分（技术创新点）

满分 100，5 个可解释维度（纯函数、可复现、可单测）：

| 维度 | 权重 | 含义 |
|---|---|---|
| `retrieval_strength` | 25 | 检索相关度（Top 条文的融合得分，max 与 mean 加权） |
| `term_alignment` | 25 | 问题术语在证据中的覆盖比例 |
| `citation_coverage` | 20 | 回答标注的引用条数是否充分 |
| `evidence_validity` | 15 | 证据中现行有效条文占比 |
| `answer_grounding` | 15 | 回答用词有多少能在证据中找到出处（幻觉代理指标） |

等级：≥80 高 / ≥60 中 / ≥40 低 / <40 不可信。
未基于知识库作答时直接判 0 分并说明原因。每个扣分项都会产出**人类可读的理由**，
前端以 popover 展示，用户可自行判断是否采信。

### 6. 知识库增量更新（技术创新点）

* 以**文件 SHA-256** 判重：内容未变 → 直接 `skipped`，不做重复计算与写入；
* 文件名相同但内容变化 → 识别为**同一制度的新版本**，清理旧条文与旧向量后写入，
  版本号自增，避免知识库里出现两份同名制度；
* 向量写入失败**不影响 MySQL 落库**，任务标记 `partial`，之后可
  `--rebuild-vectors` 一键恢复，避免一次网络抖动毁掉整批数据；
* 每次入库写入 `kb_ingest_task`，记录阶段、条文数、耗时与结果，可审计。

### 7. 多模态输入（差异化要求）

* `.txt/.md` → 自动尝试 `utf-8 / utf-8-sig / gb18030 / gbk`；
* `.pdf` → `pypdf` 取文本层，文本层不足 60 字判定为扫描件并回退图片 OCR；
* `.docx` → 段落 + 表格文本；
* `.png/.jpg/.bmp/.tif` → Tesseract OCR（`chi_sim+eng`），并内置金融场景
  错字修正表（`第—条` → `第一条`、`笫` → `第` 等）；
* OCR 依赖缺失时**优雅降级**：接口返回可用状态与安装指引，不影响其他能力。

---

## 五、接口清单

统一响应信封：成功 `{"code":0,"message":"ok","data":{...}}`；
失败 HTTP 非 2xx + `{"code":<int>,"message":"...","detail":...,"request_id":"..."}`。

> 例外：`/health`、`/health/live`、`/api` 为探针／自描述端点，返回**裸对象**，
> 便于容器健康检查与监控直接消费。

| 分组 | 方法 | 路径 | 说明 |
|---|---|---|---|
| 系统 | GET | `/api/v1/health` | 各依赖组件健康状态 |
| 系统 | GET | `/api/v1/health/live` | 存活探针（不访问外部依赖） |
| 系统 | GET | `/api/v1/capabilities` | 能力清单、评分口径、功能开关、可选依赖 |
| 问答 | POST | `/api/v1/qa/ask` | **制度条文问答**（含溯源与可信度） |
| 问答 | POST | `/api/v1/qa/ask/stream` | 流式问答（SSE：meta/delta/done/error） |
| 问答 | GET/DELETE | `/api/v1/qa/history` | 历史列表／清空 |
| 问答 | GET/DELETE | `/api/v1/qa/history/{id}` | 历史详情／删除 |
| 知识库 | GET | `/api/v1/knowledge/stats` | 总览统计与达标情况 |
| 知识库 | POST | `/api/v1/knowledge/documents/text` | 文本入库 |
| 知识库 | POST | `/api/v1/knowledge/documents/upload` | 文件上传入库（txt/pdf/docx/图片） |
| 知识库 | GET | `/api/v1/knowledge/documents` | 文档列表（搜索/筛选/分页） |
| 知识库 | GET/PATCH/DELETE | `/api/v1/knowledge/documents/{id}` | 详情／人工修订元数据／删除 |
| 知识库 | GET | `/api/v1/knowledge/chunks` | 条文列表 |
| 知识库 | GET | `/api/v1/knowledge/chunks/{id}` | 条文详情（**溯源落地页**） |
| 知识库 | GET | `/api/v1/knowledge/tags` | 知识点标签 |
| 知识库 | GET | `/api/v1/knowledge/tasks` | 入库任务记录 |
| 知识库 | POST | `/api/v1/knowledge/vectors/rebuild` | 重建向量索引 |
| 知识库 | GET | `/api/v1/knowledge/ocr/status` | OCR 能力状态 |
| 收藏 | POST/GET | `/api/v1/favorites` | 新增／列表 |
| 收藏 | PATCH/DELETE | `/api/v1/favorites/{id}` | 更新备注／删除 |

Postman 调试集合：`postman/FinRAG.postman_collection.json`（27 个请求，含自动串联脚本）。

### 问答请求示例

```bash
curl -X POST http://127.0.0.1:8000/api/v1/qa/ask \
  -H "Content-Type: application/json" \
  -d '{
    "question": "商业银行流动性风险指标的报送频率要求是什么",
    "top_k": 5,
    "retrieval_mode": "hybrid",
    "only_valid": true,
    "business_lines": ["银行"]
  }'
```

响应关键字段：

```json
{
  "code": 0, "message": "ok",
  "data": {
    "answer": "……[1][2]",
    "has_answer": true,
    "citations": [{
      "index": 1, "chunk_id": 12, "doc_title": "……管理办法",
      "doc_no": "银发〔2020〕15号", "clause_no": "第八条",
      "status": "现行有效", "is_valid": true,
      "source_link": "https://www.pbc.gov.cn/...", "citation_label": "《……》第八条"
    }],
    "credibility": {"score": 86.5, "label": "高", "components": {...}, "reasons": []},
    "latency_ms": 1234, "within_budget": true,
    "trace": {"retrieval_ms": 120, "generation_ms": 1100, "vector_used": true, "bm25_used": true}
  }
}
```

---

## 六、配置项

关键环境变量（完整见 `.env.example`）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `DASHSCOPE_API_KEY` | — | **必填**，通义千问 API Key |
| `QWEN_CHAT_MODEL` | `qwen-plus` | 可选 `qwen-turbo` / `qwen-max` |
| `QWEN_EMBEDDING_MODEL` | `text-embedding-v3` | |
| `QWEN_EMBEDDING_DIM` | `1024` | **必须与 Milvus 集合维度一致** |
| `MYSQL_*` | 见模板 | MySQL 连接 |
| `MILVUS_*` | 见模板 | Milvus 连接、集合、索引、度量 |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | 500 / 80 | 切分参数 |
| `RETRIEVE_TOP_K` / `RETRIEVE_CANDIDATE_K` | 5 / 20 | 返回条数 / 候选池 |
| `RETRIEVE_MODE` | `hybrid` | `vector` / `bm25` / `hybrid` |
| `MIN_VECTOR_SCORE` | `0.30` | 相关度下限，低于则丢弃候选 |
| `OCR_ENABLED` / `TESSERACT_CMD` | true / — | OCR 开关与可执行文件路径 |
| `MAX_UPLOAD_MB` | 50 | 上传大小上限 |

> 修改 `QWEN_EMBEDDING_DIM` 后必须重建 Milvus 集合并重新入库：
> `python scripts/init_milvus.py --recreate && python scripts/ingest.py --rebuild-vectors`

---

## 七、测试与覆盖率

```bash
python scripts/run_tests.py              # 一键：测试 + 覆盖率 + 门槛校验
python scripts/run_tests.py --report     # 附带未覆盖行明细
python -m pytest                         # 已装 pytest 时直接使用
python -m unittest discover -s tests -t .  # 零依赖的纯标准库方式
```

* 全部用例以 `unittest.TestCase` 编写，**pytest 与标准库 unittest 均可运行**；
* `scripts/run_tests.py` 会自动选择：装了 pytest-cov 用精确覆盖率，
  否则降级为自实现的 `sys.settrace` 逐行追踪器（同时覆盖工作线程，
  因为 FastAPI `TestClient` 会把 ASGI 应用跑在独立线程里），
  保证在任何机器上都能验证覆盖率门槛；
* 覆盖率门槛 **70%** 同时写在 `pyproject.toml`（`--cov-fail-under=70`、`fail_under = 70`）
  与运行脚本中，不达标直接失败。

### 实测结果（本机，未安装 Docker/Milvus/API Key）

```
Ran 493 tests in 599.3s
OK
总体覆盖率：88.92%   （门槛 70%，达标）
```

覆盖率最高的模块：`retrieval/rerank.py`、`retrieval/tokenizer.py`、
`retrieval/vector_retriever.py`、`rag/prompts.py`、`core/errors.py`、
`api/v1/knowledge.py`、`api/v1/favorites.py`、`api/deps.py`、
`schemas/common.py`、`schemas/knowledge.py` 均为 **100%**；
`db/repository.py` 98.3%、`ingest/pipeline.py` 96.5%、`services/qa_service.py` 96.4%、
`retrieval/hybrid.py` 97.4%、`rag/credibility.py` 97.3%、`core/config.py` 96.8%。

覆盖率偏低且已确认原因（**均依赖本机缺失的外部服务／凭证**，属于环境限制而非测试缺口）：

| 文件 | 覆盖率 | 原因 |
|---|---|---|
| `api/v1/upload.py` | 0% | 未安装 `python-multipart`，FastAPI 不会注册该路由（已在 `router.py` 降级为提示接口） |
| `core/logging_conf.py` | 42.6% | 文件轮转、非 TTY 配色等分支需真实文件系统与终端环境 |
| `db/milvus_store.py` | 53.5% | 建连、建集合、写入、删除等路径需真实 Milvus 服务；过滤表达式构造、返回值解析等纯逻辑已全测 |
| `ingest/ocr.py` | 57.1% | 实际识别需 Tesseract 可执行文件；可用性探测、错字修正等纯逻辑已全测 |
| `llm/qwen.py` / `llm/embeddings.py` | 65% / 61.8% | 真实调用需 `DASHSCOPE_API_KEY`；状态查询、消息转换、维度校验、批处理等已全测 |

> 在配备完整依赖（Docker + Milvus + API Key + Tesseract）的环境执行
> `pytest --cov=app --cov-report=term-missing` 可获得更高且含分支统计的覆盖率。

覆盖范围：清洗/元数据/切分/加载器/OCR、分词与相似度、RRF 与重排、Milvus 封装、
BM25 与向量检索、混合检索与时效过滤、Prompt/溯源/可信度/生成链、
Repository、上传落盘、入库流水线、知识库与问答服务、全部 HTTP 接口、端到端流程。

> 测试**不修改任何生产逻辑**：Milvus 与通义千问通过依赖注入替换为
> `tests/fakes.py` 中的测试替身（内存向量库 + 确定性哈希向量器 +
> LangChain 官方 `GenericFakeChatModel`），生产链路严格为「Milvus + 通义千问」。

### 其他校验脚本

```bash
python scripts/check_schema.py    # 校验 sql/01_schema.sql 与 ORM 模型是否漂移（9 表全部一致）
python scripts/smoke_app.py       # 冒烟：应用能否创建、21 个接口是否注册成功
python scripts/check_env.py       # 环境预检（依赖 / MySQL / Milvus / API Key / OCR / 目录）
```

> `check_schema.py` 解决的是一个很实际的隐患：生产/Docker 走 `01_schema.sql` 建表，
> 测试走 ORM `create_all()` 建表，两条路径一旦漂移就会出现「本地全绿、上线报
> Unknown column」。该脚本无需连数据库即可比对，适合放进 CI。

---

## 八、数据来源与合规

严格遵循课题实施约束「禁止使用企业敏感数据」：

* **不内置**任何涉密、企业敏感或未公开数据；
* `data/corpus/` 用于放置从监管官网下载的**公开制度文件**，
  来源清单与下载指引见 `data/corpus/README.md`（人民银行、金融监管总局、
  证监会、中国政府网、外汇局、证券交易所、行业协会）；
* `data/demo/` 是 `scripts/generate_demo_corpus.py` 生成的**合成演示语料**
  （文件内与标题均标注「合成文本·非真实法规」），仅用于功能与性能测试，
  **不得用于合规判断**；课题验收请使用真实公开文件；
* 所有条文引用均保留 `source_url`，问答结果给出官方来源链接。

**课程思政落实**：条文溯源与可信度评分对应「严谨处理金融制度数据的准确性」；
不编造条文、资料不足即如实说明对应「知识产权保护与诚实守信」；
对标通义千问等国产开源模型、分析国内 RAG 应用水平对应「科技强国使命感」；
代码从零实现、仅依赖开源组件并遵守许可，体现原创性。

---

## 九、常见问题

**Q：问答接口返回 502 / 50030？**
未配置 `DASHSCOPE_API_KEY`，或 Key 无效、网络不通。`GET /api/v1/health` 会明确标出
`qwen-chat` 与 `qwen-embedding` 的状态。

**Q：Milvus 连接失败？**
`docker compose ps` 看容器状态，`docker compose logs milvus` 看日志。
注意 Milvus standalone 需要 etcd 与 MinIO 就绪，首次启动约需 30-60 秒。

**Q：上传接口返回 50010？**
服务端缺少 `python-multipart`（FastAPI 的 multipart 解析依赖）：
`pip install python-multipart` 后重启即可，或改用文本入库接口。
该依赖缺失**不会**导致服务无法启动——路由会自动降级为提示接口。

**Q：OCR 不可用？**
`GET /api/v1/knowledge/ocr/status` 会给出具体原因。
Windows：`winget install UB-Mannheim.TesseractOCR`，并在 `.env` 配置 `TESSERACT_CMD`；
Ubuntu：`apt-get install -y tesseract-ocr tesseract-ocr-chi-sim poppler-utils`。

**Q：知识库不足 1000 条知识点？**
按 `data/corpus/README.md` 下载公开制度文件后
`python scripts/ingest.py --path data/corpus`；临时演示可用
`python scripts/generate_demo_corpus.py --documents 60`（约 1450 条）。

**Q：条文全部显示「状态待核」？**
说明自动抽取未能从文本中找到施行或废止日期。可在知识库管理页对文档执行
「修订元数据」人工确认时效状态——人工校核优先于启发式规则，且修改会
**同步更新 MySQL 条文与 Milvus 过滤字段**，下一次检索立即生效。

**Q：改了 `QWEN_EMBEDDING_DIM` 之后写入失败？**
集合维度与配置不一致。`python scripts/init_milvus.py --recreate` 重建集合后
`python scripts/ingest.py --rebuild-vectors` 重建向量。

---

## 十、技术栈

| 类别 | 选型 |
|---|---|
| 语言 | Python 3.11（≥3.10） |
| 开发框架 | FastAPI、LangChain 1.x、Pydantic v2 |
| 数据库 / 存储 | MySQL 8.0、Milvus 2.4 |
| 大模型 | 通义千问 `text-embedding-v3` + `qwen-plus`（DashScope OpenAI 兼容端点） |
| 检索增强 | `rank-bm25`、`jieba`、RRF 融合、词汇覆盖重排 |
| 文档解析 | `pypdf`、`python-docx`、`pytesseract`、`pdf2image`、Pillow |
| 前端 | Vue 3 + Vite 5 + Element Plus + Pinia + Axios + ECharts |
| 部署 / 测试 | Docker Compose、Nginx、pytest + pytest-cov、Postman |
| 代码规范 | ruff + black + isort（PEP8，行宽 100） |

---

## 十一、参考

* LangChain 官方文档 — https://python.langchain.com/docs
* FAISS 向量检索库（本课题使用 Milvus，FAISS 作为方案对比参考）— https://faiss.ai
* 中国人民银行公开制度文档 — https://www.pbc.gov.cn
* 国家金融监督管理总局 — https://www.nfra.gov.cn
* 中国证券监督管理委员会 — https://www.csrc.gov.cn
* 通义千问开源模型调用文档 — https://help.aliyun.com/zh/model-studio/
* 《RAG 技术原理与实践》— 机械工业出版社
