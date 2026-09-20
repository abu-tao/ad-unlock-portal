# AD 域控自助解锁平台 — 运维文档

| 项目 | 内容 |
|---|---|
| 文档版本 | v1.0（2026-09-20） |
| 适用对象 | IT 运维 / 桌面支持 / 安全管理员 |
| 系统名称 | AD 域账户自助解锁平台（ad-unlock-portal） |
| 代码仓库 | https://github.com/abu-tao/ad-unlock-portal（私有，MIT） |
| 生产服务器 | 10.0.10.64（主机名 XWOAUnLOCkAD，Rocky Linux） |
| 部署方式 | Docker Compose（应用 + 反向代理双容器） |

---

## 1. 系统概述

### 1.1 用途

员工域账户因密码输错被 AD 锁定后，无需联系 IT，自助在网页输入工号即可解锁，减轻服务台压力。

### 1.2 功能清单

| 功能 | 入口 | 说明 |
|---|---|---|
| 自助解锁 | 首页 `http(s)://10.0.10.64/` | 输入工号 → 一键解锁 → 返回结果 |
| 解锁记录查询 | `/admin` 管理页 | 按工号 / 结果 / 日期分页查询审计记录 |
| 健康检查 | `/healthz` | 供监控 / Docker healthcheck 使用 |
| 审计日志 | `logs/audit.jsonl` | 每次解锁操作落盘，永久保留 |

### 1.3 架构拓扑

```
                        ┌─────────────────────────────────────┐
 用户浏览器             │  10.0.10.64 (Rocky Linux)           │
   │                   │                                     │
   ├─ http://IP ──────▶│  ad-unlock-proxy 容器 (python:3.11) │
   │  (80)             │  80  http 明文                       │
   │                   │  443 https 自签证书                  │
   ├─ https://IP ─────▶│      │                              │
   │  (443, 自签警告)  │      ▼ 转发 :5000 (内部网络)         │
   │                   │  ad-unlock-portal 容器 (Flask+LDAP) │
   │                   │      │                              │
   │                   │      ▼                             │
   │                   │  AD 域控 10.0.10.1:389 (ldap)       │
   └───────────────────┴─────────────────────────────────────┘
```

- **ad-unlock-portal**：Flask 应用（waitress 生产 WSGI），通过 ldap3 访问 AD，仅内部网络暴露 5000 端口（不映射宿主机）。
- **ad-unlock-proxy**：Python 标准库实现的极简反向代理，监听宿主机 80/443，转发到 `ad-unlock-portal:5000`。HTTPS 使用自签证书（10 年有效期）。

> 选型说明：原计划用 nginx 容器/宿主机 nginx，因服务器无法访问 Docker Hub 及 yum 源超时，改用本机已有的 `python:3.11-slim` 镜像 + 纯标准库反代脚本，零新增依赖。

---

## 2. 服务器与访问信息

| 项 | 值 |
|---|---|
| IP / 主机名 | `10.0.10.64` / `XWOAUnLOCkAD` |
| 访问地址（HTTP） | `http://10.0.10.64` |
| 访问地址（HTTPS） | `https://10.0.10.64`（浏览器首次提示证书不受信任，点「高级 → 继续访问」） |
| 工程目录 | `/opt/ad-unlock-portal/` |
| 日志目录 | `/opt/ad-unlock-portal/logs/` |
| SSH | root 账号 + 密钥登录（已配置 authorized_keys） |

### 2.1 容器状态

| 容器名 | 镜像 | 端口 | 职责 |
|---|---|---|---|
| `ad-unlock-portal` | `ad-unlock-portal:latest`（自建） | 仅内部 5000 | 解锁业务 |
| `ad-unlock-proxy` | `python:3.11-slim` | 80 / 443 | 反向代理 + TLS |

---

## 3. 工程目录结构

```
/opt/ad-unlock-portal/
├── app.py                 # Flask 主程序（路由、限流、审计、管理页）
├── ad_unlock.py           # AD 解锁核心逻辑（ldap3）
├── config.py              # 配置读取（读环境变量）
├── Dockerfile             # 应用镜像构建
├── docker-compose.yml     # 编排（app + proxy）
├── .env                   # 实际配置（含密码，权限 600，勿提交仓库）
├── .env.example           # 配置模板
├── requirements.txt       # flask / ldap3 / python-dotenv / waitress
├── templates/
│   ├── index.html         # 首页（自助解锁）
│   ├── login.html         # 管理页登录
│   └── admin.html         # 解锁记录查询
├── proxy/
│   └── proxy.py           # 反向代理脚本（标准库，监听 80/443）
├── nginx/
│   ├── nginx.conf         # 遗留的 nginx 配置（当前未使用，可忽略）
│   └── certs/             # 自签证书 server.crt / server.key
└── logs/
    ├── app.log            # 应用运行日志
    └── audit.jsonl        # 解锁审计记录（管理页数据源）
```

---

## 4. 配置说明（.env）

所有配置通过环境变量注入容器，镜像内不含 `.env`。修改 `.env` 后需重建容器生效。

| 变量 | 当前值（生产） | 说明 |
|---|---|---|
| `AD_SERVER` | `ldap://10.0.10.1:389` | 域控地址（当前为明文 ldap，仅内网） |
| `AD_DOMAIN` | `AMS.COM` | AD 域名（大写） |
| `AD_BIND_USER` | `CN=svc_adunlock,OU=INFRA,OU=信息管理部,OU=AMS_Users,DC=ams,DC=com` | LDAP 绑定账号完整 DN |
| `AD_ADMIN_USER` | `svc_adunlock` | 解锁服务账号（sAMAccountName） |
| `AD_ADMIN_PASSWORD` | （存于 .env，勿外传） | 服务账号密码 |
| `AD_SEARCH_BASE` | `DC=ams,DC=com` | 用户搜索根（整域） |
| `AD_USER_ID_ATTR` | `sAMAccountName` | 工号对应 AD 属性（贵司工号=域登录名） |
| `AD_LOCKOUT_WINDOW_MINUTES` | `60` | 锁定判定窗口，与域策略一致 |
| `AD_CONNECT_TIMEOUT` / `AD_RECEIVE_TIMEOUT` | `5` / `10` | LDAP 连接/读取超时（秒） |
| `WORKER_ID_PATTERN` | `^[A-Za-z0-9_\-\.]{3,40}$` | 工号格式白名单 |
| `RATE_LIMIT_MAX` / `RATE_LIMIT_WINDOW_SECONDS` | `10` / `60` | 单 IP 60 秒内最多 10 次 |
| `TRUST_PROXY` | `true` | 信任反代 X-Forwarded-For（有 proxy 容器，必须 true） |
| `ADMIN_PASSWORD` | （存于 .env） | 管理页登录密码 |
| `APP_SECRET_KEY` | （存于 .env） | Flask session 密钥，建议 `openssl rand -hex 32` 生成 |
| `ADMIN_SESSION_HOURS` | `8` | 管理员会话有效期（小时） |
| `HOST` / `PORT` | `0.0.0.0` / `5000` | 容器内监听（勿改） |
| `DEBUG` | `false` | 生产必须 false |
| `TZ` | `CST-8` | 时区 |

---

## 5. 日常运维操作

> 服务器上 docker-compose 为**独立版**，命令必须用带连字符的 `docker-compose`，`docker compose`（空格）不可用。

### 5.1 查看状态

```bash
cd /opt/ad-unlock-portal
docker-compose ps
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
```

### 5.2 启动 / 停止 / 重启

```bash
cd /opt/ad-unlock-portal
docker-compose up -d          # 启动（已构建过）
docker-compose restart        # 重启全部
docker-compose restart ad-unlock-proxy    # 只重启反代
docker-compose down           # 停止并删除容器（数据在挂载卷，不丢）
docker-compose up -d          # 再次拉起
```

### 5.3 查看日志

```bash
# 应用运行日志
tail -f /opt/ad-unlock-portal/logs/app.log

# 解锁审计记录（实时）
tail -f /opt/ad-unlock-portal/logs/audit.jsonl

# 反代容器日志
docker logs -f ad-unlock-proxy

# 最近 20 条解锁记录
tail -20 /opt/ad-unlock-portal/logs/audit.jsonl
```

### 5.4 解锁记录查询（管理页）

1. 访问 `https://10.0.10.64/admin`（或 `http://10.0.10.64/admin`）
2. 输入管理密码登录（`.env` 中 `ADMIN_PASSWORD`）
3. 支持按 **工号 / 结果码 / 日期范围** 筛选，分页展示
4. 结果码中文对照见附录 A

### 5.5 更新首页/代码（模板改动）

模板与代码打进镜像，**宿主机直接改文件不生效**，需重建：

```bash
cd /opt/ad-unlock-portal
# 1. 先更新宿主机文件（templates/ 或 *.py）
# 2. 重建并重启应用容器
docker-compose up -d --build ad-unlock-portal
# 3. 验证
curl -s http://10.0.10.64/healthz
```

---

## 6. 解锁工作原理

### 6.1 请求流程

```
浏览器 POST /api/unlock {employee_id}
  → 限流检查（IP 维度，60s/10 次）
  → CSRF 检查（必须携带 X-Requested-With 头）
  → 工号格式白名单校验
  → LDAP 绑定（svc_adunlock）→ 搜索用户（sAMAccountName 精确匹配）
  → 读取 lockoutTime / badPwdCount / userAccountControl
  → 判断是否锁定 → 锁定则清除 lockoutTime 并重置 badPwdCount
  → 写审计日志 → 返回 JSON 结果
```

### 6.2 AD 侧解锁的关键操作

- **解锁**：将 `lockoutTime` 属性**置 0**（Microsoft 规定只能置 0 清除）。
- **badPwdCount**：为 AD 的**操作属性（operational attribute）**，域控在解锁时会自动归零，**无需也不可手动写入**。
- 账户若被禁用（`userAccountControl` 含 `ACCOUNTDISABLE`），平台拒绝解锁并提示联系 IT。

### 6.3 返回码一览

| code | 含义 | 页面提示 |
|---|---|---|
| `UNLOCKED` | 解锁成功 | 解锁成功，请用正确密码重新登录 |
| `NOT_LOCKED` | 账户未锁定 | 无需解锁 |
| `NOT_FOUND` | 工号无对应账户 | 请核对工号 |
| `DISABLED` | 账户被禁用 | 联系 IT 服务台 |
| `MULTIPLE_MATCH` | 工号对应多个账户 | 联系 IT 管理员 |
| `BAD_INPUT` | 格式不合法 | 核对后重输 |
| `RATE_LIMITED` | 触发限流 | 稍后再试 |
| `FORBIDDEN` | 非法请求（缺自定义头） | 非法请求 |
| `AD_BIND_FAILED` / `AD_CONNECT_FAILED` / `AD_ERROR` | AD 侧异常 | 联系 IT 管理员 |
| `INTERNAL` | 未预期异常 | 联系 IT 管理员 |

---

## 7. 安全机制

| 机制 | 实现 |
|---|---|
| 限流 | 单 IP 60 秒内 10 次（无效请求也计数，防绕过） |
| CSRF 防护 | 要求 `X-Requested-With: XMLHttpRequest` 头（跨站表单无法伪造） |
| 输入白名单 | 工号正则校验（3-40 位字母数字 `_-.`） |
| 审计留痕 | 每次操作写入 `audit.jsonl`（IP / 工号 / 结果 / 耗时） |
| 管理页保护 | 独立密码 + session（8 小时过期）+ 登录限流 |
| LDAP 注入防护 | 搜索值经 `_ldap_escape` 转义 |
| 安全响应头 | 全接口注入（HSTS 等） |
| 凭据管理 | `.env` 权限 600，不进镜像、不进仓库 |

---

## 8. 备份与恢复

### 8.1 需备份的内容

| 内容 | 路径 | 频率建议 |
|---|---|---|
| 配置 | `/opt/ad-unlock-portal/.env` | 变更后立即 |
| 审计日志 | `/opt/ad-unlock-portal/logs/` | 每周（或按合规要求） |
| 证书 | `/opt/ad-unlock-portal/nginx/certs/` | 首次部署后归档 |
| 源码 | GitHub 仓库（main 分支） | 变更即推送 |

### 8.2 一键备份

```bash
mkdir -p /opt/backup/ad-unlock-portal
cp -r /opt/ad-unlock-portal/.env /opt/backup/ad-unlock-portal/
cp -r /opt/ad-unlock-portal/logs /opt/backup/ad-unlock-portal/
cp -r /opt/ad-unlock-portal/nginx/certs /opt/backup/ad-unlock-portal/
tar czf /opt/backup/ad-unlock-portal-$(date +%F).tar.gz -C /opt/backup ad-unlock-portal
```

### 8.3 恢复（迁移到新服务器）

1. 在新机器安装 Docker + docker-compose（独立版）
2. 从 GitHub clone 仓库（`git clone https://github.com/abu-tao/ad-unlock-portal.git`）
3. 恢复 `.env`（改服务器 IP 相关项）、`logs/`、`nginx/certs/`
4. `docker-compose up -d` 启动
5. 开放防火墙端口：`firewall-cmd --permanent --add-port=80/tcp --add-port=443/tcp && firewall-cmd --reload`

---

## 9. AD 侧准备（一次性）

平台依赖域内服务账号与测试账号，由域管理员在域控执行：

```powershell
# 1. 创建解锁服务账号（或复用已建的 svc_adunlock）
Get-ADUser svc_adunlock -Properties * |
  Select-Object SamAccountName, DistinguishedName, Enabled, LockedOut

# 2. 验证域控与域信息
Get-ADDomain | Select-Object DNSRoot, NetBIOSName, DistinguishedName
Get-ADDomainController -Filter * | Select-Object Name, IPv4Address, Site

# 3. 测试账号（用于解锁演练，密码错 5 次即锁定）
Get-ADUser unlocktest01 -Properties * | Select-Object SamAccountName, LockedOut
```

**权限建议**：为 `svc_adunlock` 委派「重置密码 / 解锁」最低权限，不要给域管理员权限。当前实现按整域 `DC=ams,DC=com` 搜索，如需收紧可改 `AD_SEARCH_BASE` 到指定 OU。

---

## 10. 故障排查手册

### 10.1 页面打不开（http/https 均不通）

```bash
# 1. 容器是否在跑
docker ps
# 2. 端口是否监听
ss -lntp | grep -E ':(80|443)\s'
# 3. 防火墙是否放行（80/443 由 docker 自动开，一般无需手动）
firewall-cmd --list-all
```

### 10.2 HTTPS 打不开 / 证书异常

- 浏览器提示「您的连接不是私密连接」→ **属正常**（自签证书），点「高级 → 继续前往」。
- 彻底打不开：检查 `ad-unlock-proxy` 是否健康、证书文件是否在 `nginx/certs/`。
- 换正式证书：替换 `server.crt` / `server.key` 后 `docker-compose restart ad-unlock-proxy`。

### 10.3 解锁报「AD 连接失败 / 绑定失败」

```bash
# 查看应用日志定位具体原因
tail -50 /opt/ad-unlock-portal/logs/app.log
# 从服务器直接测 LDAP 端口
nc -zv 10.0.10.1 389
```

常见原因：域控不可达、`svc_adunlock` 密码过期（`PasswordLastSet` 后过期时间到期需改密）、DN 拼写错误。

### 10.4 解锁了但用户仍无法登录

- AD 有**锁定阈值 + 自动解锁时间**（默认 30 分钟），若用户在窗口内反复试错会再次锁定。
- 确认用户密码本身正确（解锁 ≠ 重置密码）。
- 确认 `badPwdCount` 是否已归零（解锁后域控自动处理，通常无需干预）。

### 10.5 修改代码/模板不生效

模板和代码在镜像内。宿主机改文件后必须：

```bash
docker-compose up -d --build ad-unlock-portal
```

### 10.6 管理页提示「管理功能未启用」

`.env` 中 `ADMIN_PASSWORD` 为空即禁用管理页。配置后重建容器。

### 10.7 拉取镜像失败（Docker Hub 不可达）

本机已配置 daocloud 加速器（`/etc/docker/daemon.json`），且 proxy 用的是本地已有镜像，一般无需新拉取。若需新镜像仍失败，改用 `docker.m.daocloud.io/library/<name>` 完整路径。

---

## 11. 升级指南

```bash
cd /opt/ad-unlock-portal
# 1. 从 GitHub 拉取最新代码（或在服务器上直接改文件）
git pull origin main        # 若服务器是 git 部署；否则手动同步
# 2. 备份当前 compose
cp docker-compose.yml docker-compose.yml.bak.$(date +%Y%m%d)
# 3. 重建应用
docker-compose up -d --build ad-unlock-portal
# 4. 验证
curl -s http://127.0.0.1:5000/healthz && echo OK
```

---

## 附录 A：审计记录字段（audit.jsonl）

每行一条 JSON：

```json
{
  "time": "2026-09-20T10:30:00+08:00",
  "ip": "10.0.55.168",
  "employee_id": "zhangsan",
  "code": "UNLOCKED",
  "success": true,
  "message": "解锁成功，请使用正确的密码重新登录",
  "details": {"sAMAccountName": "zhangsan", "displayName": "张三"},
  "cost_ms": 45
}
```

## 附录 B：变更记录

| 日期 | 变更 | 说明 |
|---|---|---|
| 2026-09-09 | 首次部署 | 部署于 10.0.10.25，443→5000 直连 |
| 2026-09-17 | 迁移服务器 | 迁移至 10.0.10.64 |
| 2026-09-17 | 增加反代 | 新增 ad-unlock-proxy（80 http + 443 https 自签），实现不带端口直接访问 |
| 2026-09-17 | 首页文案 | 标签统一为「工号」，去掉「/ 登录名」 |
| 2026-09-20 | 本文档 | 运维文档 v1.0 |
