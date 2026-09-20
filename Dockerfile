# ============================================================
# AD 域控自助解锁平台 - Docker 镜像（离线构建版）
# 构建：docker build -t ad-unlock-portal .
# 运行：见 docker-compose.yml（推荐）或：
#   docker run -d -p 5000:5000 --env-file .env -v ./logs:/app/logs ad-unlock-portal
#
# 说明：
#   - 依赖目录 deps/sp 由运行中的容器导出（docker cp 站点包），
#     规避服务器无法访问 PyPI/Docker Hub 的网络限制。
#   - 基础镜像 python:3.11-slim 需本地已存在。
# ============================================================

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # POSIX 时区串（UTC+8）：不依赖 tzdata 系统包，glibc 直接解析
    TZ=CST-8 \
    # 依赖从 /deps 加载（纯 Python 包，3.12 编译扩展已剔除）
    PYTHONPATH=/deps

# 非 root 用户运行（最小权限，避免容器内提权风险）
RUN groupadd -r appuser && useradd -r -g appuser -d /app appuser

WORKDIR /app

# 依赖目录（docker cp 导出的 site-packages，含 flask/ldap3/dotenv/waitress）
COPY --chown=appuser:appuser deps/sp /deps

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
