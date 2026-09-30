# =============================================================================
#  FinRAG · 基于 RAG 的金融制度类知识问答系统 —— 常用运维命令
# =============================================================================
#
#  适用环境
#  ---------------------------------------------------------------------------
#    * Linux / macOS：系统自带或 `apt install make` 后直接 `make <目标>`
#    * Windows：安装 GNU Make（choco install make / scoop install make）后
#      在项目根目录执行 `make <目标>`；
#      【没有 make 的环境】每个目标下方都写了等价的原始命令，可手动逐条执行。
#    * 所有 docker 相关目标统一使用 `docker compose`（V2 子命令形式），
#      若本机是旧版 docker-compose v1，把 `docker compose` 换成 `docker-compose`。
#
#  使用前准备
#  ---------------------------------------------------------------------------
#    cp .env.example .env    # 并填写 DASHSCOPE_API_KEY 等真实配置
#
#  常用流程
#  ---------------------------------------------------------------------------
#    make up      -> 启动全栈（首次会自动构建镜像并初始化 MySQL 建表）
#    make logs    -> 查看后端日志
#    make ingest  -> 批量入库语料（FILE=./data/corpus/xxx.txt）
#    make down    -> 停止服务（保留数据）
#
#  注意：Windows 下 make 默认用 cmd.exe 执行命令，因此本文件刻意不使用
#        shell 变量赋值、$(shell ...) 嵌套等功能，保证跨平台可直接运行。
# =============================================================================

# 禁止隐式规则、声明伪目标，避免与同名文件冲突
.SUFFIXES:
.PHONY: help up down logs build init-db init-milvus ingest test lint fmt clean

# 关闭 make 自带的命令回显，输出更干净（想看每条命令加 -n 或改成 @ 去掉）
MAKEFLAGS += --no-print-directory

# 默认目标：什么都不做时打印帮助
.DEFAULT_GOAL := help

# 可覆盖参数（用法：make ingest FILE=./data/corpus/商业银行法.txt）
FILE ?= ./data/corpus/example.txt
SERVICE ?= backend

# -----------------------------------------------------------------------------
# help：列出全部可用目标及说明（默认目标）
#   等价命令：make help
# -----------------------------------------------------------------------------
help:
	@echo "FinRAG 常用命令："
	@echo ""
	@echo "  make up          启动全栈（mysql/etcd/minio/milvus/backend/frontend），后台运行"
	@echo "  make down        停止全栈并移除容器（保留数据卷 mysql_data/etcd_data/...）"
	@echo "  make logs        实时查看后端日志（SERVICE=frontend 可切换服务）"
	@echo "  make build       重新构建后端与前端镜像（不使用缓存：make build NO_CACHE=1）"
	@echo "  make init-db     初始化 MySQL（建库 + 执行 sql/01_schema.sql）"
	@echo "  make init-milvus 创建 Milvus 集合（按 .env 中的 MILVUS_COLLECTION 与维度）"
	@echo "  make ingest      批量入库语料（FILE=路径，默认 ./data/corpus/example.txt）"
	@echo "  make test        运行单元测试与覆盖率（pytest + pytest-cov）"
	@echo "  make lint        代码静态检查（ruff + flake8 + mypy）"
	@echo "  make fmt         代码格式化（black + isort + ruff --fix）"
	@echo "  make clean       清理临时文件与 Python 缓存（不删数据库数据）"
	@echo ""
	@echo "常用变量：FILE=入库文件  SERVICE=日志服务名  NO_CACHE=1 构建不走缓存"
	@echo "首次使用请先执行：cp .env.example .env 并填写 DASHSCOPE_API_KEY"

# -----------------------------------------------------------------------------
# up：启动全栈（后台运行）
#   --build 保证镜像与当前代码一致；首次启动 MySQL 会自动执行 01_schema.sql 建表。
#   等价命令：docker compose up -d --build
# -----------------------------------------------------------------------------
up:
	docker compose up -d --build
	@echo ""
	@echo "启动完成，等待各服务健康检查通过后即可访问："
	@echo "  前端页面       http://localhost/"
	@echo "  后端接口       http://localhost:8000/api/v1/health"
	@echo "  接口文档       http://localhost:8000/docs"
	@echo "  备用静态 UI    http://localhost:8000/ui"
	@echo "查看状态： docker compose ps"

# -----------------------------------------------------------------------------
# down：停止并移除容器与网络（数据卷保留）
#   等价命令：docker compose down
#   如需彻底清空数据库与向量数据：docker compose down -v（危险，勿轻易执行）
# -----------------------------------------------------------------------------
down:
	docker compose down
	@echo "服务已停止。数据卷仍保留；如需彻底清空请执行：docker compose down -v"

# -----------------------------------------------------------------------------
# logs：实时查看服务日志（默认 backend，可用 SERVICE=xxx 切换）
#   等价命令：docker compose logs -f --tail=200 backend
# -----------------------------------------------------------------------------
logs:
	docker compose logs -f --tail=200 $(SERVICE)

# -----------------------------------------------------------------------------
# build：重新构建镜像
#   等价命令：docker compose build
#   不走缓存：docker compose build --no-cache（本目标支持 NO_CACHE=1 追加该参数）
# -----------------------------------------------------------------------------
build:
	docker compose build $(if $(NO_CACHE),--no-cache,)
	@echo "镜像构建完成：finrag-backend:latest / finrag-frontend:latest"

# -----------------------------------------------------------------------------
# init-db：初始化 MySQL（建库 + 建表）
#   compose 首次启动已通过挂载 sql/01_schema.sql 自动建表；
#   本目标用于「已有数据卷但表被误删/需要重建」的场景，可重复执行。
#   等价命令：docker compose exec backend python /app/scripts/init_mysql.py
# -----------------------------------------------------------------------------
init-db:
	docker compose exec backend python /app/scripts/init_mysql.py
	@echo "MySQL 初始化完成（已执行 sql/01_schema.sql）"

# -----------------------------------------------------------------------------
# init-milvus：创建 Milvus 集合与索引
#   集合名与向量维度取自 .env 的 MILVUS_COLLECTION / QWEN_EMBEDDING_DIM，
#   改了 embedding 模型或维度后必须重建集合，否则写入会因维度不匹配失败。
#   等价命令：docker compose exec backend python /app/scripts/init_milvus.py
# -----------------------------------------------------------------------------
init-milvus:
	docker compose exec backend python /app/scripts/init_milvus.py
	@echo "Milvus 集合已就绪"

# -----------------------------------------------------------------------------
# ingest：批量入库 CLI
#   用法：make ingest FILE=./data/corpus/商业银行流动性风险管理办法.txt
#   容器内 /app/data 对应宿主机 ./data，因此文件需放在 ./data 下才能被容器读到。
#   等价命令：docker compose exec backend python /app/scripts/ingest.py /app/data/corpus/xxx.txt
# -----------------------------------------------------------------------------
ingest:
	docker compose exec backend python /app/scripts/ingest.py /app/data/$(patsubst ./data/%,%,$(FILE))
	@echo "入库完成：$(FILE)"

# -----------------------------------------------------------------------------
# test：运行测试与覆盖率
#   课题要求核心模块覆盖率 ≥ 70%，不达标会返回非零退出码。
#   优先走项目自带的统一入口（会自动选择 pytest 或纯标准库模式）：
#     docker compose exec backend sh -c "cd /app && python scripts/run_tests.py"
#   注意：必须在 /app 根目录执行，pytest / coverage 的配置（pyproject.toml）在那里。
#   若想用 pytest + pytest-cov 的精确报告，先安装开发依赖（或构建时开启）：
#     docker compose build --build-arg INSTALL_DEV=true backend
#     # 或在已运行的容器里临时安装：
#     docker compose exec backend pip install -r /app/requirements-dev.txt
#   等价命令：docker compose exec backend sh -c "cd /app && python -m pytest"
# -----------------------------------------------------------------------------
test:
	docker compose exec backend sh -c "cd /app && python scripts/run_tests.py"

# -----------------------------------------------------------------------------
# lint：静态检查（不修改文件）
#   必须在 /app 根目录执行，ruff 才能读到 pyproject.toml 里的规则。
#   需要开发依赖中的 ruff / flake8 / mypy：
#     docker compose build --build-arg INSTALL_DEV=true backend
#   等价命令：docker compose exec backend sh -c "cd /app && ruff check backend/app tests scripts"
# -----------------------------------------------------------------------------
lint:
	docker compose exec backend sh -c "cd /app && ruff check backend/app tests scripts"

# -----------------------------------------------------------------------------
# fmt：自动格式化代码
#   等价命令：docker compose exec backend sh -c "cd /app && black backend tests scripts && isort backend tests scripts"
# -----------------------------------------------------------------------------
fmt:
	docker compose exec backend sh -c "cd /app && black backend tests scripts && isort backend tests scripts && ruff check --fix backend/app tests scripts"
	@echo "格式化完成"

# -----------------------------------------------------------------------------
# clean：清理临时文件与 Python 缓存（不触碰数据库数据卷与上传文件）
#   等价命令（Linux/macOS）：find . -name __pycache__ -type d -prune -exec rm -rf {} +
# -----------------------------------------------------------------------------
clean:
	docker compose exec backend sh -c "find /app -name '__pycache__' -type d -prune -exec rm -rf {} + ; find /app -name '*.pyc' -delete"
	docker compose exec backend sh -c "rm -rf /app/backend/.pytest_cache /app/backend/.mypy_cache /app/backend/.ruff_cache /app/.coverage"
	@echo "临时文件与缓存已清理（数据库数据与上传文件未受影响）"
