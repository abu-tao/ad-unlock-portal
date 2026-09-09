# -*- coding: utf-8 -*-
"""
AD 域控自助解锁平台 —— Flask 应用入口

路由：
  GET  /             前端页面
  GET  /healthz      健康检查
  POST /api/unlock   解锁接口（JSON: {"employee_id": "工号"}）

运行：
  python app.py            （生产模式默认使用 waitress）
  DEBUG=true python app.py （开发调试模式）
"""

import json
import logging
import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta
from functools import wraps
from logging.handlers import TimedRotatingFileHandler

from flask import Flask, jsonify, redirect, render_template, request, session, url_for

import ad_unlock
import config

app = Flask(__name__)
# session 密钥：未配置时每次启动随机生成（重启后管理员需重新登录）
app.secret_key = config.APP_SECRET_KEY or secrets.token_hex(32)
app.permanent_session_lifetime = timedelta(hours=config.ADMIN_SESSION_HOURS)

# 管理页面是否启用（设置了 ADMIN_PASSWORD 才启用），运行时动态判断
def _admin_enabled() -> bool:
    return bool(config.ADMIN_PASSWORD)

# 结果码 -> 中文名（管理页面下拉与表格展示）
CODE_LABELS = {
    "UNLOCKED": "解锁成功",
    "NOT_LOCKED": "未锁定",
    "NOT_FOUND": "未找到账户",
    "DISABLED": "账户已禁用",
    "MULTIPLE_MATCH": "工号多账户",
    "BAD_INPUT": "输入格式错误",
    "FORBIDDEN": "非法请求",
    "RATE_LIMITED": "触发限流",
    "AD_NOT_CONFIGURED": "AD 未配置",
    "AD_BIND_FAILED": "管理员认证失败",
    "AD_CONNECT_FAILED": "连接域控失败",
    "AD_ERROR": "AD 异常",
    "INTERNAL": "内部异常",
}

# ---------------------------------------------------------------------------
# 日志（控制台 + 滚动文件）
# ---------------------------------------------------------------------------
os.makedirs(config.AUDIT_LOG_DIR, exist_ok=True)
_log_fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")

_file_handler = TimedRotatingFileHandler(
    os.path.join(config.AUDIT_LOG_DIR, "app.log"),
    when="midnight", encoding="utf-8", backupCount=30,
)
_file_handler.setFormatter(_log_fmt)

_console = logging.StreamHandler()
_console.setFormatter(_log_fmt)

_root = logging.getLogger()
_root.setLevel(logging.INFO)
if not _root.handlers:
    _root.addHandler(_file_handler)
    _root.addHandler(_console)

logger = logging.getLogger("app")

# ---------------------------------------------------------------------------
# 基础安全响应头
# ---------------------------------------------------------------------------
@app.after_request
def _security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    return resp


# ---------------------------------------------------------------------------
# 单 IP 滑动窗口限流（内存实现，单实例部署足够）
# ---------------------------------------------------------------------------
_hits = defaultdict(deque)
_hits_lock = threading.Lock()


def _is_rate_limited(ip: str) -> bool:
    now = time.time()
    with _hits_lock:
        dq = _hits[ip]
        while dq and now - dq[0] > config.RATE_LIMIT_WINDOW_SECONDS:
            dq.popleft()
        if len(dq) >= config.RATE_LIMIT_MAX:
            return True
        dq.append(now)
        return False


# ---------------------------------------------------------------------------
# 审计日志（JSON Lines）
# ---------------------------------------------------------------------------
def _audit(ip: str, employee_id: str, result: dict, extra: dict = None):
    entry = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ip": ip,
        "employee_id": employee_id,
        "code": result.get("code"),
        "success": result.get("success"),
        "message": result.get("message"),
    }
    if extra:
        entry.update(extra)
    try:
        with open(os.path.join(config.AUDIT_LOG_DIR, "audit.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        logger.exception("审计日志写入失败")


def _client_ip() -> str:
    ip = request.remote_addr or "unknown"
    if config.TRUST_PROXY:
        ip = (request.headers.get("X-Forwarded-For") or ip).split(",")[0].strip() or ip
    return ip


# ---------------------------------------------------------------------------
# 管理页面鉴权
# ---------------------------------------------------------------------------
def admin_required(view):
    """未登录或未启用管理功能时跳转到登录页。"""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not _admin_enabled() or not session.get("admin_logged_in"):
            return redirect(url_for("admin_login"))
        return view(*args, **kwargs)
    return wrapped


def _query_audit(employee_id: str, code: str, date_from: str, date_to: str,
                 page: int, page_size: int):
    """从审计日志 audit.jsonl 查询记录：按条件过滤 -> 时间倒序 -> 分页。"""
    log_path = os.path.join(config.AUDIT_LOG_DIR, "audit.jsonl")
    records = []
    if os.path.exists(log_path):
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if employee_id and employee_id.lower() not in str(rec.get("employee_id", "")).lower():
                    continue
                if code and rec.get("code") != code:
                    continue
                ts = str(rec.get("ts", ""))
                if date_from and ts < date_from + " 00:00:00":
                    continue
                if date_to and ts > date_to + " 23:59:59":
                    continue
                records.append(rec)
    records.sort(key=lambda r: str(r.get("ts", "")), reverse=True)
    total = len(records)
    start = (page - 1) * page_size
    return records[start:start + page_size], total


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------
@app.get("/")
def index():
    return render_template("index.html")


@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok", "time": datetime.now().isoformat()})


# ---------------------------------------------------------------------------
# 管理页面（解锁记录查询）
# ---------------------------------------------------------------------------
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "GET":
        return render_template("login.html", enabled=_admin_enabled())

    if not _admin_enabled():
        return jsonify({"success": False, "message": "管理功能未启用，请联系管理员在 .env 配置 ADMIN_PASSWORD"}), 403
    if _is_rate_limited(_client_ip()):
        return jsonify({"success": False, "message": "尝试过于频繁，请稍后再试"}), 429
    if request.headers.get("X-Requested-With") != "XMLHttpRequest":
        return jsonify({"success": False, "message": "非法请求"}), 403

    data = request.get_json(silent=True) or {}
    password = str(data.get("password") or "")
    if password and password == config.ADMIN_PASSWORD:
        session.permanent = True
        session["admin_logged_in"] = True
        logger.info("管理员登录成功 ip=%s", _client_ip())
        return jsonify({"success": True, "redirect": url_for("admin_index")}), 200

    logger.warning("管理登录失败 ip=%s", _client_ip())
    return jsonify({"success": False, "message": "管理密码错误"}), 401


@app.get("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


@app.get("/admin")
@admin_required
def admin_index():
    return render_template("admin.html", code_labels=CODE_LABELS)


@app.get("/api/admin/records")
@admin_required
def admin_records():
    if request.headers.get("X-Requested-With") != "XMLHttpRequest":
        return jsonify({"success": False, "message": "非法请求"}), 403

    employee_id = request.args.get("employee_id", "").strip()
    code = request.args.get("code", "").strip()
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()

    # 校验日期参数格式 YYYY-MM-DD，防止乱传
    for d in (date_from, date_to):
        if d and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
            return jsonify({"success": False, "message": "日期格式应为 YYYY-MM-DD"}), 400

    try:
        page = max(1, int(request.args.get("page", "1")))
        page_size = min(100, max(1, int(request.args.get("page_size", "20"))))
    except ValueError:
        page, page_size = 1, 20

    records, total = _query_audit(employee_id, code, date_from, date_to, page, page_size)
    return jsonify({
        "success": True,
        "records": records,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if total else 0,
    })


@app.post("/api/unlock")
def api_unlock():
    client_ip = _client_ip()

    # 限流最先执行：对来源 IP 的所有请求计数，防止绕过（无效请求也计）
    if _is_rate_limited(client_ip):
        logger.warning("触发限流 ip=%s", client_ip)
        return jsonify({"success": False, "code": "RATE_LIMITED",
                        "message": "操作过于频繁，请稍后再试", "details": {}}), 429

    # 轻量 CSRF 防护：要求自定义请求头（跨站表单无法携带该头）
    if request.headers.get("X-Requested-With") != "XMLHttpRequest":
        return jsonify({"success": False, "code": "FORBIDDEN",
                        "message": "非法请求", "details": {}}), 403

    data = request.get_json(silent=True) or {}
    employee_id = str(data.get("employee_id") or "").strip()

    pattern = re.compile(config.WORKER_ID_PATTERN)
    if not pattern.fullmatch(employee_id):
        return jsonify({"success": False, "code": "BAD_INPUT",
                        "message": "工号格式不正确，请核对后重新输入", "details": {}}), 400

    start = time.time()
    try:
        result = ad_unlock.unlock_account(employee_id)
    except Exception:
        logger.exception("解锁过程发生未预期异常 employee_id=%s", employee_id)
        result = {"success": False, "code": "INTERNAL",
                  "message": "服务异常，请联系 IT 管理员", "details": {}}
    result.setdefault("details", {})
    _audit(client_ip, employee_id, result, extra={"cost_ms": int((time.time() - start) * 1000)})
    return jsonify(result), 200


if __name__ == "__main__":
    if config.DEBUG:
        logger.info("开发调试模式启动 http://%s:%s", config.HOST, config.PORT)
        app.run(host=config.HOST, port=config.PORT, debug=True)
    else:
        from waitress import serve
        logger.info("AD 自助解锁服务已启动 http://%s:%s", config.HOST, config.PORT)
        serve(app, host=config.HOST, port=config.PORT, threads=8)
