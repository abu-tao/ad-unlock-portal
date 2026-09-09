# -*- coding: utf-8 -*-
"""
全局配置：全部通过环境变量 / .env 文件读取，禁止把密码硬编码进代码。
复制 .env.example 为 .env 并填写真实值即可。
"""

import os

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# AD / LDAP 配置
# ---------------------------------------------------------------------------
# 域控地址，建议使用 LDAPS 加密：ldaps://dc01.company.local
AD_SERVER = os.getenv("AD_SERVER", "")

# AD 域名（用于拼接 NTLM 管理员账号，形如 COMPANY.LOCAL）
AD_DOMAIN = os.getenv("AD_DOMAIN", "")

# 解锁用管理员服务账号（最低权限委派，见 README「权限委派」一节）
AD_ADMIN_USER = os.getenv("AD_ADMIN_USER", "svc_adunlock")
# 服务账号密码 —— 只从环境变量 / .env 读取
AD_ADMIN_PASSWORD = os.getenv("AD_ADMIN_PASSWORD", "")

# 绑定账号（默认自动拼接 DOMAIN\user）。
# 支持两种格式，后端自动识别：
#   - 完整 DN：CN=S_LdapAuth,CN=Users,DC=ams,DC=com（LDAP 简单绑定，推荐用于已有 LDAP 集成账号）
#   - DOMAIN\账号：AMS\svc_adunlock（NTLM 绑定）
# 如需自定义可直接设置 AD_BIND_USER
AD_BIND_USER = os.getenv("AD_BIND_USER", (AD_DOMAIN + "\\" + AD_ADMIN_USER) if AD_DOMAIN else "")

# 用户搜索根，例如 DC=company,DC=local；可细化到指定 OU
AD_SEARCH_BASE = os.getenv("AD_SEARCH_BASE", "")

# 工号对应 AD 属性（默认 employeeID，很多企业用 sAMAccountName 当工号时改为 sAMAccountName）
# 后端会先按此属性查找，找不到再按 sAMAccountName 兜底
AD_USER_ID_ATTR = os.getenv("AD_USER_ID_ATTR", "employeeID")

# 锁定判定窗口（分钟）：lockoutTime 距今超过该窗口视为已自动解锁。
# 建议与域策略中的「账户锁定时间」保持一致。
AD_LOCKOUT_WINDOW_MINUTES = int(os.getenv("AD_LOCKOUT_WINDOW_MINUTES", "60"))

# 连接超时（秒）
# 注意：必须为 int —— ldap3 2.9.1 在 Python 3.12 下传 float 会报 struct.error
AD_CONNECT_TIMEOUT = int(os.getenv("AD_CONNECT_TIMEOUT", "5"))
AD_RECEIVE_TIMEOUT = int(os.getenv("AD_RECEIVE_TIMEOUT", "10"))

# ---------------------------------------------------------------------------
# 输入校验 / 安全
# ---------------------------------------------------------------------------
# 工号格式白名单（默认字母数字 + _ - .，长度 3-40），按贵司工号规则调整
WORKER_ID_PATTERN = os.getenv("WORKER_ID_PATTERN", r"^[A-Za-z0-9_\-\.]{3,40}$")

# 单 IP 限流：窗口内最大请求次数，防暴力枚举
RATE_LIMIT_MAX = int(os.getenv("RATE_LIMIT_MAX", "10"))
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))

# 是否信任反向代理（nginx/IIS ARR）传递的 X-Forwarded-For，用于取真实客户端 IP
TRUST_PROXY = os.getenv("TRUST_PROXY", "false").lower() == "true"

# ---------------------------------------------------------------------------
# 管理页面（解锁记录查询）
# ---------------------------------------------------------------------------
# 管理密码：设置后启用 /admin 管理页面（含登录保护）；留空则禁用管理功能
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

# Flask session 密钥：生产环境务必设置固定随机值，否则服务重启后管理员需重新登录
APP_SECRET_KEY = os.getenv("APP_SECRET_KEY", "")

# 管理员会话有效期（小时）
ADMIN_SESSION_HOURS = int(os.getenv("ADMIN_SESSION_HOURS", "8"))

# ---------------------------------------------------------------------------
# 服务运行
# ---------------------------------------------------------------------------
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "5000"))
DEBUG = os.getenv("DEBUG", "false").lower() == "true"

# 审计日志目录（app.log + audit.jsonl）
AUDIT_LOG_DIR = os.getenv("AUDIT_LOG_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs"))
