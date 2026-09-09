# ============================================================
# AD 域控自助解锁平台 - Docker 镜像
# 构建：docker build -t ad-unlock-portal .
# 运行：见 docker-compose.yml（推荐）或：
#   docker run -d -p 5000:5000 --env-file .env -v ./logs:/app/logs ad-unlock-portal
# ============================================================

# ---------- 阶段 1：安装 Python 依赖 ----------
FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --prefix=/install -r requirements.txt

# ---------- 阶段 2：运行镜像 ----------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # POSIX 时区串（UTC+8）：不依赖 tzdata 系统包，glibc 直接解析
    TZ=CST-8

# 非 root 用户运行（最小权限，避免容器内提权风险）
RUN groupadd -r appuser && useradd -r -g appuser -d /app appuser

WORKDIR /app

# 依赖从构建阶段复制，镜像更小
COPY --from=builder /install /usr/local

# 应用代码（.env 已被 .dockerignore 排除，配置全部来自环境变量）
COPY --chown=appuser:appuser . .

# 审计日志目录
RUN mkdir -p /app/logs && chown -R appuser:appuser /app

USER appuser

EXPOSE 5000
VOLUME ["/app/logs"]

# 健康检查：依赖 /healthz 接口，不额外安装 curl
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/healthz', timeout=3)" || exit 1

CMD ["python", "app.py"]
