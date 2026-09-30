# =============================================================================
#  FinRAG · 后端镜像（FastAPI + MySQL/SQLAlchemy + Milvus/pymilvus + Tesseract OCR）
# =============================================================================
#
#  构建命令示例（注意：构建上下文必须是【项目根目录】，不是 docker/ 目录）
#  ---------------------------------------------------------------------------
#    # 方式一：直接使用 docker build
#    docker build -f docker/backend.Dockerfile -t finrag-backend:latest .
#
#    # 方式二：使用 docker compose（推荐，compose 已写好 context: .）
#    docker compose build backend
#
#    # 方式三：Windows PowerShell 下若路径含中文，先切到项目根目录再执行
#    cd C:\Users\28212\Desktop\校企合作\finrag
#    docker build -f docker/backend.Dockerfile -t finrag-backend:latest .
#
#  为什么上下文是项目根目录？
#  ---------------------------------------------------------------------------
#    Dockerfile 需要同时访问 backend/ 源码、backend/requirements.txt（或根目录
#    requirements.txt）以及 scripts/ 下的运维脚本。Docker 的 COPY 无法越过
#    构建上下文，因此必须把上下文设为项目根目录，并在 COPY 中带上前缀
#    （如 `COPY backend/ ...`）。运行 `docker build` 时最后的那个 `.` 即上下文。
#
#  建议在项目根目录放置 .dockerignore 排除 frontend/node_modules、.git、
#  var/、data/、__pycache__ 等，可显著缩短构建上下文传输时间。
# =============================================================================

FROM python:3.11-slim

# -----------------------------------------------------------------------------
# 构建期元信息（便于 `docker inspect` 追溯镜像来源）
# -----------------------------------------------------------------------------
LABEL org.opencontainers.image.title="FinRAG Backend" \
      org.opencontainers.image.description="基于 RAG 的金融制度类知识问答系统 - FastAPI 后端" \
      org.opencontainers.image.licenses="MIT"

# -----------------------------------------------------------------------------
# 1. 系统级依赖
#    换用国内 apt 源以加速（网络受限环境可保留；如在境外构建可删除该 RUN）
# -----------------------------------------------------------------------------
RUN set -eux; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources; \
    fi; \
    if [ -f /etc/apt/sources.list ]; then \
        sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list; \
    fi

# -----------------------------------------------------------------------------
# 2. 安装 OCR 与运行期系统依赖
#    * tesseract-ocr / tesseract-ocr-chi-sim : 中文简体 OCR（图片、扫描版 PDF 入库）
#    * tesseract-ocr-eng                     : 英文 OCR（TESSERACT_LANG=chi_sim+eng）
#    * poppler-utils                         : pdf2image 依赖的 pdftoppm/pdftocairo
#    * libgl1 / libglib2.0-0                 : Pillow / OpenCV 运行期图形库
#    * curl                                  : HEALTHCHECK 探针使用
#    * build-essential                       : pymilvus 等在无预编译 wheel 时需本地编译
#    * tzdata                                : 设置 Asia/Shanghai 时区
#    * fonts-noto-cjk                        : 中文 PDF 渲染不乱码（可选但推荐）
# -----------------------------------------------------------------------------
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-chi-sim \
        tesseract-ocr-eng \
        poppler-utils \
        libgl1 \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender1 \
        curl \
        build-essential \
        tzdata \
        fonts-noto-cjk; \
    rm -rf /var/lib/apt/lists/*

# -----------------------------------------------------------------------------
# 3. 时区与 Python 运行环境变量
# -----------------------------------------------------------------------------
ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/backend \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

RUN set -eux; \
    ln -snf /usr/share/zoneinfo/$TZ /etc/localtime; \
    echo $TZ > /etc/timezone

# -----------------------------------------------------------------------------
# 4. 工作目录
#    /app/backend/app 即 FastAPI 应用包，因此启动命令用 app.main:app
# -----------------------------------------------------------------------------
WORKDIR /app/backend

# -----------------------------------------------------------------------------
# 5. 依赖安装（单独一层，充分利用 Docker 层缓存）
#    依赖清单的选择由构建参数 REQUIREMENTS_FILE 控制：
#      * 默认 root：使用根目录 requirements.txt（本仓库的权威清单，
#        与 backend/requirements.txt 内容一致）；
#      * 若将来两份清单出现分叉，改为后端那份即可：
#          docker build -f docker/backend.Dockerfile \
#            --build-arg REQUIREMENTS_FILE=backend/requirements.txt -t finrag-backend:latest .
#        同时把 docker-compose.yml 中 backend.build.args.REQUIREMENTS_FILE 改成同名值。
#
#    为什么用 `COPY backend/ /tmp/...` 再挑选，而不是同时 COPY 两份清单？
#      Dockerfile 的 COPY 遇到上下文中不存在的源会**直接判定构建失败**，
#      而 backend/requirements.txt 目前尚未提交到仓库（只有根目录那份）。
#      为保证「现在就能构建成功」且「将来补上后端清单后仍能正确选用」，
#      这里先整目录拷到一个临时层挑选（该层不进最终镜像，不影响体积）。
#      副作用：后端源码变动会使本层缓存失效并重装依赖；
#      若想恢复最优缓存，只需把 backend/requirements.txt 提交到仓库，
#      再把下面两行替换为：
#          COPY backend/requirements.txt /tmp/requirements/backend-requirements.txt
#          COPY requirements.txt /tmp/requirements/root-requirements.txt
# -----------------------------------------------------------------------------
ARG REQUIREMENTS_FILE=requirements.txt
COPY requirements.txt /tmp/requirements/root-requirements.txt
COPY backend/ /tmp/backend-src/
RUN set -eux; \
    python -m pip install --upgrade pip setuptools wheel; \
    if [ "$REQUIREMENTS_FILE" = "backend/requirements.txt" ]; then \
        cp /tmp/backend-src/requirements.txt /tmp/requirements/selected.txt; \
    else \
        cp /tmp/requirements/root-requirements.txt /tmp/requirements/selected.txt; \
    fi; \
    test -s /tmp/requirements/selected.txt; \
    echo "使用依赖清单: $REQUIREMENTS_FILE"; \
    pip install -r /tmp/requirements/selected.txt; \
    apt-get purge -y --auto-remove build-essential; \
    rm -rf /tmp/requirements /tmp/backend-src /root/.cache

# -----------------------------------------------------------------------------
# 6. 拷贝后端源码、运维脚本、测试与工程配置
#    上下文为项目根目录，故必须带 backend/ 前缀。
#    一并拷入 pyproject.toml 与 tests/ 的原因：
#      * pyproject.toml 是 pytest / coverage / ruff 的唯一配置来源，
#        `make test`、`make lint` 在容器内执行时依赖它被放到 /app 根；
#      * tests/ 让「容器内跑测试与覆盖率」成为可能（课题要求覆盖率 ≥ 70%，
#        验收时直接在部署环境复现比在开发机更有说服力）。
#    若追求最小生产镜像，可加 `--target` 分阶段构建，把测试相关层剥离。
# -----------------------------------------------------------------------------
COPY backend/ /app/backend/
COPY scripts/ /app/scripts/
COPY tests/ /app/tests/
COPY pyproject.toml /app/pyproject.toml
COPY requirements-dev.txt /app/requirements-dev.txt

# -----------------------------------------------------------------------------
# 6.1 可选安装开发依赖（pytest / pytest-cov / ruff 等）
#     默认 false：生产镜像保持精简；
#     需要跑 `make test`（pytest + 覆盖率）时用
#       docker compose build --build-arg INSTALL_DEV=true backend
#     注意：不装 dev 依赖也能验证覆盖率——
#       `python /app/scripts/run_tests.py` 仅依赖标准库即可运行并统计覆盖率。
# -----------------------------------------------------------------------------
ARG INSTALL_DEV=false
RUN set -eux; \
    if [ "$INSTALL_DEV" = "true" ]; then \
        pip install -r /app/requirements-dev.txt; \
    fi; \
    rm -rf /root/.cache

# -----------------------------------------------------------------------------
# 7. 非 root 用户运行
#    以 root 运行容器存在逃逸风险，且部分银行/教学环境的安全基线强制要求非 root。
# -----------------------------------------------------------------------------
RUN set -eux; \
    groupadd --gid 1000 finrag; \
    useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash finrag; \
    mkdir -p /app/var/uploads /app/data /app/logs; \
    chown -R finrag:finrag /app

USER finrag

# -----------------------------------------------------------------------------
# 8. 上传目录与数据目录
#    与 docker-compose.yml 中的挂载点保持一致：
#      ./var/uploads -> /app/var/uploads
#      ./data        -> /app/data
#    UPLOAD_DIR 用绝对路径，避免工作目录变化导致文件找不到。
#    【注意】若使用宿主机 bind mount，请先在宿主机创建目录并放开写权限：
#      mkdir -p var/uploads data && chmod -R 777 var/uploads data
# -----------------------------------------------------------------------------
ENV UPLOAD_DIR=/app/var/uploads \
    OCR_ENABLED=true \
    TESSERACT_LANG=chi_sim+eng

EXPOSE 8000

# -----------------------------------------------------------------------------
# 9. 健康检查
#    打存活探针而非 /health：/health 会真实探测 MySQL/Milvus/大模型，
#    任一依赖抖动都会让容器被反复重启。存活探针只证明进程可用。
# -----------------------------------------------------------------------------
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/v1/health/live || exit 1

# -----------------------------------------------------------------------------
# 10. 启动命令
#     单 worker 起步；生产可加 --workers 4（注意：多 worker 下内存翻倍，
#     且应用内单例容器会各自初始化，需评估 Milvus 连接数）。
# -----------------------------------------------------------------------------
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
