# AD 域控自助解锁平台

面向公司内网的 AD 账户**自助解锁**工具：员工在网页输入工号（域登录名），后端以专用服务账号通过 LDAP 查询并解除账户锁定，实时返回解锁结果；另含「解锁记录查询」管理页面。

- 前端：输入工号 → 点击解锁 → 展示结果（解锁成功 / 未被锁定 / 未找到 / 已禁用 / 服务异常）
- 后端：Flask + ldap3（SIMPLE 绑定）→ 按 `sAMAccountName` 定位用户 → 判定锁定 → **`lockoutTime` 置 0** 解锁
- 管理端：登录后可查询全部解锁记录（按工号 / 结果码 / 日期筛选、分页）
- 部署：Docker 一键部署，容器内非 root 运行，审计日志持久化

---

## 目录结构

```
ad-unlock-portal/
├── app.py               # Flask 入口（页面 + /api/unlock + 限流 + 审计 + 管理端）
├── ad_unlock.py         # AD/LDAP 核心逻辑（查询、锁定判定、解锁）
├── config.py            # 配置读取（环境变量 / .env）
├── requirements.txt     # Python 依赖
├── Dockerfile           # 镜像（多阶段构建 / 非 root / 健康检查）
├── docker-compose.yml   # 编排（环境变量注入 / 日志卷 / 健康检查）
├── .dockerignore        # 镜像构建排除项
├── .env.example         # 配置模板（复制为 .env 填写）
├── start.sh             # Linux + Docker 一键启动
├── start.bat            # Windows 直接运行（备选）
├── test_api.py          # 自动化测试（模拟 AD，无需真实域控）
└── templates/
    ├── index.html       # 员工自助解锁页
    ├── login.html       # 管理端登录页
    └── admin.html       # 管理端：解锁记录查询
```

> 注意：`deploy/`（远程自动化部署脚本，内含服务器口令）、`.env`、`logs/`、`.venv/` **不提交**到代码仓库。

---

## 功能特性

- **自助解锁**：`POST /api/unlock`，Body `{"employee_id": "工号"}`，工号即域登录名（`sAMAccountName`）
- **结果区分**：`UNLOCKED` / `NOT_LOCKED` / `NOT_FOUND` / `DISABLED` / `MULTIPLE_MATCH` / 各类服务异常码
- **安全内置**：输入格式白名单、单 IP 限流（防暴力枚举）、轻量 CSRF 防护、管理端会话
- **审计**：每次解锁写 `logs/audit.jsonl`（时间 / 工号 / 结果 / 消息 / 来源 IP / 耗时），管理页实时可查
- **健康检查**：`/healthz` 供 Docker / 监控轮询

---

## 环境要求

| 项 | 要求 |
|---|---|
| 服务器 | Linux + Docker Engine 20.10+（含 compose 插件）或独立 `docker-compose` v2 |
| 网络 | 服务器可访问域控 389（LDAP）或 636（LDAPS）端口 |
| AD 账号 | 一个解锁专用服务账号（如 `svc_adunlock`），已做最小权限委派（见下文） |
| 域策略 | 锁定阈值、锁定持续时间与 `AD_LOCKOUT_WINDOW_MINUTES` 配合 |

---

## 快速部署（Linux + Docker，推荐）

```bash
cd ad-unlock-portal
cp .env.example .env          # 填写真实 AD / 管理配置
chmod +x start.sh
./start.sh                    # 构建镜像并启动
```

或手动执行：

```bash
docker compose up -d --build
docker compose ps             # 状态 healthy 即正常
```

启动后访问：

```
员工解锁页：  http://<服务器IP>:<PORT>/
管理页：      http://<服务器IP>:<PORT>/admin/login
```

### 指定端口部署（例如 443）

```bash
# .env 中设置
PORT=443
```

```bash
docker compose up -d --build
# 宿主机 0.0.0.0:443 -> 容器 5000
```

### 常用运维命令

```bash
docker compose logs -f                      # 实时日志
docker compose restart                      # 重启
docker compose up -d --build                # 代码更新后重建（日志不丢）
docker compose exec ad-unlock-portal python -m unittest test_api -v   # 容器内跑测试
docker compose down                         # 停止并删除容器（保留镜像与日志）
```

### 服务器端 .env 权限

```bash
chmod 600 .env    # 含密码，仅 root 可读
```

---

## 快速部署（Windows 直接运行，备选）

```bat
cd ad-unlock-portal
copy .env.example .env
start.bat          :: 自动建虚拟环境、装依赖、启动（waitress 生产模式）
```

访问 `http://127.0.0.1:5000`。

---

## 配置说明（.env）

| 变量 | 说明 | 示例 |
|---|---|---|
| `AD_SERVER` | 域控地址；**建议 `ldaps://`**；内网明文可用 `ldap://` | `ldaps://dc01.company.local` |
| `AD_DOMAIN` | AD 域名（大写） | `COMPANY.LOCAL` |
| `AD_BIND_USER` | 绑定账号：**填完整 DN（LDAP 简单绑定）**；留空则用 `域\账号` NTLM | `CN=svc_adunlock,OU=Users,DC=company,DC=local` |
| `AD_ADMIN_USER` / `AD_ADMIN_PASSWORD` | 服务账号名 / 密码（只放 .env / 环境变量） | `svc_adunlock` / `***` |
| `AD_SEARCH_BASE` | 用户搜索根，建议限定到用户 OU | `OU=Users,DC=company,DC=local` |
| `AD_USER_ID_ATTR` | 工号对应属性：`employeeID` 或 `sAMAccountName` | `sAMAccountName` |
| `AD_LOCKOUT_WINDOW_MINUTES` | 锁定判定窗口（分钟），建议 ≥ 域策略「账户锁定时间」 | `60` |
| `WORKER_ID_PATTERN` | 工号格式白名单 | `^[A-Za-z0-9_\-\.]{3,40}$` |
| `RATE_LIMIT_MAX` / `RATE_LIMIT_WINDOW_SECONDS` | 单 IP 限流 | `10` / `60` |
| `TRUST_PROXY` | 有反向代理时置 `true` | `false` |
| `ADMIN_PASSWORD` | 管理密码（必填，留空则禁用管理页） | `***` |
| `APP_SECRET_KEY` | 会话密钥，生产设固定随机值（`openssl rand -hex 32`） | `***` |
| `ADMIN_SESSION_HOURS` | 管理会话有效期（小时） | `8` |
| `HOST` / `PORT` / `DEBUG` | 监听地址 / 端口 / 调试 | `0.0.0.0` / `5000` / `false` |

> 说明：默认按 `AD_USER_ID_ATTR` 查找工号，未命中再用 `sAMAccountName` 兜底——工号未写入 `employeeID` 的企业，员工直接用域登录名即可解锁。

---

## 权限委派（重要安全项）

**不要用域管理员账号跑本服务。** 创建专用服务账号并做最小权限委派：

1. 在 ADUC 创建服务账号 `svc_adunlock`（普通用户即可，不加任何管理组）
2. 右键用户所在 OU（或整个域）→ **委派控制** → 添加 `svc_adunlock`
3. 「创建自定义任务去委派」→ 下一步
4. 「仅此文件夹中的以下对象」→ 勾选 **用户对象** → 下一步
5. 「属性特定」→ 勾选「写入」→ 属性列表勾选 **lockoutTime** → 下一步 → 完成

或命令行（在域控执行，委派在父 OU 自动向下继承）：

```bat
dsacls "OU=Users,DC=company,DC=local" /G "COMPANY\svc_adunlock:WP;lockoutTime;user"
```

### 两个关键经验（踩坑结论）

- **解锁 = `lockoutTime` 置 0**：Microsoft 规定 lockoutTime 只能**置 0**（解锁），**不能删除该属性**——删除会被域控拒绝（`error 53 WILL_NOT_PERFORM / 00002077`）。应用已按"置 0"实现。
- **`badPwdCount` 无需也无法写入**：它是操作属性，仅域控维护；解锁（lockoutTime 置 0）时**域控自动归零**。不需要对它做任何委派。
- 若委派前尝试解锁，错误为 `error 50 insufficientAccessRights`（权限不足）；委派后同一操作若仍报 `error 53`，请先核对是否为"删除属性"写法。

---

## 管理端：解锁记录查询

设置 `ADMIN_PASSWORD` 后启用：

- 访问 `http://<服务器IP>:<PORT>/admin/login` → 输入管理密码
- 支持按 **工号（模糊）/ 结果码 / 日期范围** 筛选、分页
- 每列展示：时间、工号、成功/失败、结果码、消息、来源 IP、耗时
- 数据源 `logs/audit.jsonl`（与解锁操作实时同步），仅限 IT 管理员
- 登录接口受单 IP 限流保护；会话默认 8 小时过期（`ADMIN_SESSION_HOURS`）

---

## API 参考

```
POST /api/unlock
Body: {"employee_id": "10012345"}
Header: X-Requested-With: XMLHttpRequest
```

| code | 含义 |
|---|---|
| `UNLOCKED` | 解锁成功 |
| `NOT_LOCKED` | 账户未被锁定（可能顺带清理残留标记） |
| `NOT_FOUND` | 未找到该工号对应账户 |
| `DISABLED` | 账户已禁用，需人工处理 |
| `MULTIPLE_MATCH` | 工号对应多个账户，需人工处理 |
| `BAD_INPUT` / `FORBIDDEN` / `RATE_LIMITED` | 输入 / 请求 / 限流 |
| `AD_NOT_CONFIGURED` / `AD_BIND_FAILED` / `AD_CONNECT_FAILED` / `AD_ERROR` / `INTERNAL` | 服务端异常 |

---

## 测试

不依赖真实域控（用假 LDAP 连接模拟）：

```bash
python -m unittest test_api -v
```

---

## 生产部署建议

1. **HTTPS**：前端经 nginx / IIS ARR 反向代理启用 TLS，并设 `TRUST_PROXY=true`，避免工号明文传输
2. **LDAPS**：`AD_SERVER` 用 `ldaps://`，避免管理员密码走明文 LDAP
3. **防火墙**：仅内网网段访问服务端口；服务器到域控放行 636
4. **日志归档**：`logs/audit.jsonl` 定期归档（建议保留 ≥ 180 天，作为解锁行为审计证据）
5. **监控**：轮询 `/healthz` 做存活检测
6. **Docker**：镜像内非 root 运行；`docker-compose.yml` 对关键变量做了「未配置即报错」（`:?`）保护；`./logs` 挂载宿主机，重建不丢失

---

## 常见问题

| 现象 | 排查 |
|---|---|
| 返回「管理员认证失败」 | 检查 `AD_BIND_USER`（完整 DN）与 `AD_ADMIN_PASSWORD`；Python 3.12 下 NTLM 需 MD4 支持，建议用完整 DN 走 LDAP 简单绑定 |
| 返回「无法连接域控」 | 服务器到域控 389/636 端口连通性、`AD_SERVER` 地址 |
| 工号能登录但提示「未找到」 | `employeeID` 未填或格式不一致 → 改用域登录名测试，或将 `AD_USER_ID_ATTR` 改为 `sAMAccountName` |
| 解锁报 `AD_ERROR`（日志 error 53） | 核对 ① 是否已委派 `Write lockoutTime` ② 应用是否用"置 0"而非"删除"写法 |
| 显示未锁定但用户确实无法登录 | 可能用户密码错误而非锁定；或锁定窗口配置过小（`AD_LOCKOUT_WINDOW_MINUTES`） |

---

## 备选方案（应急）

域内机器 + RSAT，命令行手动解锁：

```powershell
Unlock-ADAccount -Identity "工号或登录名" -Server dc01.company.local
```
