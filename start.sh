#!/usr/bin/env bash
# ============================================================
# AD 域控自助解锁平台 - Linux + Docker 一键启动脚本
# 首次运行：./start.sh  （需已安装 docker + docker compose 插件）
# 后续更新代码后重新执行即可重建镜像
# ============================================================
set -e
cd "$(dirname "$0")"

if [ ! -f .env ]; then
    cp .env.example .env
    echo "已生成 .env 配置模板，请先编辑填写（AD_SERVER / AD_ADMIN_PASSWORD / ADMIN_PASSWORD 等）后重新运行本脚本。"
    exit 1
fi

echo "==> 构建并启动服务..."
docker compose up -d --build

echo "==> 容器状态："
docker compose ps

PORT=$(grep -E '^PORT=' .env | cut -d= -f2 | tr -d '[:space:]')
PORT=${PORT:-5000}
echo ""
echo "==> 访问地址： http://<服务器IP>:${PORT}"
echo "    员工解锁页： /               管理记录查询： /admin"
echo "    日志查看：   docker compose logs -f"
