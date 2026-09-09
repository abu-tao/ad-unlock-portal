# -*- coding: utf-8 -*-
"""
AD 域控账户解锁核心逻辑（基于 LDAP）。

流程：
  1. 使用管理员服务账号绑定域控（NTLM，兼容未加域的服务器）
  2. 按工号（默认 employeeID，兼容 sAMAccountName）查找用户
  3. 读取 lockoutTime / badPwdCount / userAccountControl 判断锁定状态
  4. 若锁定：lockoutTime 置 0 完成解锁（badPwdCount 由域控自动归零）
  5. 返回结构化结果供前端展示

安全设计：
  - 管理员账号/密码只来自环境变量，不落代码
  - LDAP 过滤器对输入做转义，防注入
  - 对外只返回固定中文提示，细节进服务端日志
"""

import logging
from datetime import datetime, timezone, timedelta

from ldap3 import Server, Connection, SUBTREE, NTLM, SIMPLE, MODIFY_REPLACE
from ldap3.core.exceptions import (
    LDAPException,
    LDAPBindError,
    LDAPSocketOpenError,
    LDAPSocketReceiveError,
    LDAPResponseTimeoutError,
)

import config

logger = logging.getLogger("ad_unlock")

# ---------------- 结果码 ----------------
UNLOCKED = "UNLOCKED"                # 解锁成功
NOT_LOCKED = "NOT_LOCKED"            # 账户未被锁定
NOT_FOUND = "NOT_FOUND"              # 未找到工号对应账户
DISABLED = "DISABLED"                # 账户已被禁用
MULTIPLE_MATCH = "MULTIPLE_MATCH"    # 工号对应多个账户
AD_NOT_CONFIGURED = "AD_NOT_CONFIGURED"
AD_BIND_FAILED = "AD_BIND_FAILED"
AD_CONNECT_FAILED = "AD_CONNECT_FAILED"
AD_ERROR = "AD_ERROR"

# Windows FILETIME 纪元：1601-01-01 00:00:00 UTC
_FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)
# userAccountControl 标志位：账户禁用
_UF_ACCOUNTDISABLE = 0x0002


def _ldap_escape(value: str) -> str:
    """转义 LDAP 过滤器特殊字符，防止过滤器注入。"""
    return (
        value.replace("\\", "\\5c")
        .replace("*", "\\2a")
        .replace("(", "\\28")
        .replace(")", "\\29")
        .replace("\x00", "\\00")
    )


def _to_int(value, default=0):
    """兼容 ldap3 返回的 int / str / list 形态。"""
    if value is None:
        return default
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _attr(entry, name, default=None):
    """取 ldap3 条目的属性原始值（自动解包 Attribute 对象）。
    注意：直接 getattr(entry, 'lockoutTime') 拿到的是 Attribute 对象，
    int() 转换会失败被吞掉，导致锁定判断永远为 False。"""
    try:
        v = getattr(entry, name, None)
    except Exception:
        return default
    if v is None:
        return default
    if hasattr(v, "value"):
        return v.value
    return v


def _filetime_to_dt(filetime: int):
    """AD 的 lockoutTime 为 1601-01-01 起的 100 纳秒计数，换算为 datetime。"""
    try:
        return _FILETIME_EPOCH + timedelta(microseconds=filetime / 10.0)
    except (TypeError, OverflowError, ValueError):
        return None


def _lockout_dt(entry):
    """返回 lockoutTime 对应的 UTC datetime；未锁定返回 None。

    兼容三种形态（ldap3 在不同环境可能返回其中一种）：
      - datetime：ldap3 已自动把 FILETIME 解析为 UTC datetime
      - int：原始 FILETIME 计数
      - str："2026-09-09 01:03:26+00:00" 或 FILETIME 字符串
    """
    lt = _attr(entry, "lockoutTime")
    if lt is None:
        return None
    if isinstance(lt, datetime):
        if lt.tzinfo is None:
            lt = lt.replace(tzinfo=timezone.utc)
        # FILETIME 0 => 1601-01-01，视为未锁定
        if lt.year <= 1970:
            return None
        return lt
    lt_int = _to_int(lt)
    if lt_int <= 0:
        return None
    return _filetime_to_dt(lt_int)


def _is_locked(entry) -> bool:
    """判断账户是否处于锁定状态：lockoutTime 非零且距今在观察窗口内。"""
    locked_dt = _lockout_dt(entry)
    if locked_dt is None:
        return False
    window = timedelta(minutes=config.AD_LOCKOUT_WINDOW_MINUTES)
    return datetime.now(timezone.utc) - locked_dt <= window


def _check_config():
    """校验 AD 配置是否完整。返回错误描述或 None。"""
    if not config.AD_SERVER:
        return "未配置域控地址 AD_SERVER"
    if not config.AD_BIND_USER or not config.AD_ADMIN_PASSWORD:
        return "未配置管理员账号或密码"
    if not config.AD_SEARCH_BASE:
        return "未配置搜索根 AD_SEARCH_BASE"
    return None


def _connect():
    """建立管理员绑定连接。

    支持两种绑定方式（按 AD_BIND_USER 格式自动识别）：
      - 完整 DN（含 "="，如 CN=S_LdapAuth,CN=Users,DC=ams,DC=com）→ SIMPLE 简单绑定
      - DOMAIN\\账号（如 AMS\\svc_adunlock）→ NTLM 绑定
    建议 AD_SERVER 使用 ldaps:// 走加密。"""
    server = Server(config.AD_SERVER, connect_timeout=config.AD_CONNECT_TIMEOUT)
    bind_user = config.AD_BIND_USER
    authentication = SIMPLE if "=" in bind_user else NTLM
    return Connection(
        server,
        user=bind_user,
        password=config.AD_ADMIN_PASSWORD,
        authentication=authentication,
        auto_bind=True,
        receive_timeout=config.AD_RECEIVE_TIMEOUT,
        raise_exceptions=True,
    )


def _search_user(conn, employee_id):
    """按工号属性查找用户；未命中再用 sAMAccountName 兜底。
    返回 (entry, 错误码)；错误码为 None 表示查找正常。"""
    attributes = [
        "distinguishedName", "sAMAccountName", "employeeID",
        "lockoutTime", "badPwdCount", "userAccountControl",
        "displayName", "mail",
    ]
    attrs_to_try = [config.AD_USER_ID_ATTR]
    if config.AD_USER_ID_ATTR != "sAMAccountName":
        attrs_to_try.append("sAMAccountName")

    for attr in attrs_to_try:
        filt = "(&(objectClass=user)({0}={1}))".format(attr, _ldap_escape(employee_id))
        conn.search(config.AD_SEARCH_BASE, filt, search_scope=SUBTREE, attributes=attributes)
        entries = conn.entries
        if len(entries) == 1:
            return entries[0], None
        if len(entries) > 1:
            return None, MULTIPLE_MATCH
    return None, None


def _clear_lock(conn, dn):
    """清除锁定时间。返回 (是否成功, 错误信息)。

    关键：lockoutTime 只能"置 0"来解锁（Microsoft 文档规定），
    不能删除该属性——删除会被域控以 error 53 (WILL_NOT_PERFORM) 拒绝。
    badPwdCount 是操作属性，域控会在解锁时自动归零，无需也不可手动写入。"""
    try:
        conn.modify(dn, {
            "lockoutTime": [(MODIFY_REPLACE, [0])],
        })
        return True, None
    except LDAPException as exc:
        logger.error("解锁修改失败 dn=%s err=%s", dn, exc)
        return False, str(exc)


def _details(entry) -> dict:
    return {
        "sAMAccountName": str(_attr(entry, "sAMAccountName") or ""),
        "displayName": str(_attr(entry, "displayName") or ""),
    }


def _result(code: str, message: str, details: dict = None) -> dict:
    return {
        "success": code == UNLOCKED,
        "code": code,
        "message": message,
        "details": details or {},
    }


def unlock_account(employee_id: str) -> dict:
    """对外主入口：按工号解锁域账户。"""
    missing = _check_config()
    if missing:
        logger.error("AD 配置缺失: %s", missing)
        return _result(AD_NOT_CONFIGURED, "服务端 AD 连接未配置，请联系 IT 管理员")

    try:
        conn = _connect()
    except LDAPBindError:
        logger.exception("管理员账号绑定失败（账号/密码错误或权限不足）")
        return _result(AD_BIND_FAILED, "管理员认证失败，请联系 IT 管理员")
    except (LDAPSocketOpenError, LDAPSocketReceiveError, LDAPResponseTimeoutError):
        logger.exception("连接域控失败")
        return _result(AD_CONNECT_FAILED, "无法连接域控服务器，请稍后重试或联系 IT 管理员")
    except LDAPException:
        logger.exception("绑定域控发生未知异常")
        return _result(AD_ERROR, "域控服务异常，请联系 IT 管理员")

    try:
        entry, err = _search_user(conn, employee_id)
        if err == MULTIPLE_MATCH:
            logger.warning("工号对应多个账户: %s", employee_id)
            return _result(MULTIPLE_MATCH, "该工号对应多个账户，请联系 IT 管理员处理")
        if entry is None:
            logger.info("未找到账户: %s", employee_id)
            return _result(NOT_FOUND, "未找到该工号对应的域账户，请核对工号后重试")

        dn = entry.entry_dn
        uac = _to_int(_attr(entry, "userAccountControl"))
        if uac & _UF_ACCOUNTDISABLE:
            logger.warning("账户已禁用，拒绝解锁: %s", dn)
            return _result(DISABLED, "该账户已被禁用，无法自助解锁，请联系 IT 服务台",
                           details=_details(entry))

        # 已锁定 -> 解锁
        if _is_locked(entry):
            ok, _ = _clear_lock(conn, dn)
            if not ok:
                return _result(AD_ERROR, "解锁操作执行失败，请联系 IT 管理员",
                               details=_details(entry))
            logger.info("解锁成功 employee_id=%s dn=%s", employee_id, dn)
            return _result(UNLOCKED, "解锁成功，请使用正确的密码重新登录",
                           details=_details(entry))

        # 未锁定：若存在过期残留的锁定标记则顺带清理
        cleaned = False
        if _lockout_dt(entry) is not None:
            cleaned, _ = _clear_lock(conn, dn)
            if cleaned:
                logger.info("已清理过期锁定标记 dn=%s", dn)
        msg = "该账户当前未被锁定，无需解锁"
        if cleaned:
            msg += "（已顺带清理残留的锁定标记）"
        return _result(NOT_LOCKED, msg, details=_details(entry))
    finally:
        try:
            conn.unbind()
        except Exception:
            pass
