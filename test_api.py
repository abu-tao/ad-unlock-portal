# -*- coding: utf-8 -*-
"""
ad-unlock-portal 自动化测试（不依赖真实 AD，用假连接模拟 LDAP 行为）。

运行：python -m unittest test_api -v   （或直接 python test_api.py）
"""

import json
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import config
import ad_unlock
from app import app

FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


def filetime(dt):
    return int((dt - FILETIME_EPOCH).total_seconds() * 10_000_000)


class FakeEntry:
    def __init__(self, dn, **attrs):
        self.entry_dn = dn
        for k, v in attrs.items():
            setattr(self, k, v)


class FakeConnection:
    """模拟 ldap3 Connection：search / modify / unbind。"""

    def __init__(self, user_map):
        # user_map: {(属性, 值): [FakeEntry, ...]}
        self.user_map = user_map
        self.entries = []
        self.modify_calls = []
        self.unbound = False

    def search(self, base, filt, **kwargs):
        pairs = re.findall(r"\((\w+)=([^)]+)\)", filt)
        key = None
        for attr, val in pairs:
            if attr.lower() != "objectclass":
                key = (attr, val)
                break
        self.entries = list(self.user_map.get(key, []))
        return True

    def modify(self, dn, changes):
        self.modify_calls.append((dn, changes))
        return True

    def unbind(self):
        self.unbound = True


class UnlockApiTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.saved = {
            "AD_SERVER": config.AD_SERVER,
            "AD_BIND_USER": config.AD_BIND_USER,
            "AD_ADMIN_PASSWORD": config.AD_ADMIN_PASSWORD,
            "AD_SEARCH_BASE": config.AD_SEARCH_BASE,
            "AD_USER_ID_ATTR": config.AD_USER_ID_ATTR,
            "AD_LOCKOUT_WINDOW_MINUTES": config.AD_LOCKOUT_WINDOW_MINUTES,
            "RATE_LIMIT_MAX": config.RATE_LIMIT_MAX,
            "ADMIN_PASSWORD": config.ADMIN_PASSWORD,
            "APP_SECRET_KEY": config.APP_SECRET_KEY,
            "AUDIT_LOG_DIR": config.AUDIT_LOG_DIR,
        }

    def setUp(self):
        # 有效的最小配置
        config.AD_SERVER = "ldap://fake-dc.local"
        config.AD_BIND_USER = "TEST\\svc_adunlock"
        config.AD_ADMIN_PASSWORD = "fake-password"
        config.AD_SEARCH_BASE = "DC=test,DC=local"
        config.AD_USER_ID_ATTR = "employeeID"
        config.AD_LOCKOUT_WINDOW_MINUTES = 60
        config.RATE_LIMIT_MAX = 100
        # 管理页面配置
        config.ADMIN_PASSWORD = "admin123"
        config.APP_SECRET_KEY = "test-secret"
        app.secret_key = "test-secret"
        # 审计日志写入独立临时目录，避免污染项目 logs/
        config.AUDIT_LOG_DIR = tempfile.mkdtemp()
        from app import _hits
        _hits.clear()
        self.client = app.test_client()

    @classmethod
    def tearDownClass(cls):
        for k, v in cls.saved.items():
            setattr(config, k, v)

    # ---------------- 工具 ----------------
    def _build_users(self):
        now = datetime.now(timezone.utc)
        locked_dn = "CN=zhangsan,OU=Users,DC=test,DC=local"
        unlocked_dn = "CN=lisi,OU=Users,DC=test,DC=local"
        stale_dn = "CN=wangwu,OU=Users,DC=test,DC=local"
        disabled_dn = "CN=zhaoliu,OU=Users,DC=test,DC=local"
        zhang = FakeEntry(locked_dn,
            sAMAccountName="zhangsan", employeeID="1001", displayName="张三",
            lockoutTime=filetime(now - timedelta(minutes=5)), badPwdCount=5,
            userAccountControl=512)
        li = FakeEntry(unlocked_dn,
            sAMAccountName="lisi", employeeID="1002", displayName="李四",
            lockoutTime=0, badPwdCount=0, userAccountControl=512)
        wang = FakeEntry(stale_dn,
            sAMAccountName="wangwu", employeeID="1003", displayName="王五",
            lockoutTime=filetime(now - timedelta(hours=2)), badPwdCount=3,
            userAccountControl=512)
        zhao = FakeEntry(disabled_dn,
            sAMAccountName="zhaoliu", employeeID="1004", displayName="赵六",
            lockoutTime=filetime(now - timedelta(minutes=5)), badPwdCount=5,
            userAccountControl=514)  # 512 | 0x2
        dup1 = FakeEntry("CN=dup1,OU=Users,DC=test,DC=local", sAMAccountName="dup1",
                         employeeID="2000", displayName="重复1", lockoutTime=0,
                         badPwdCount=0, userAccountControl=512)
        dup2 = FakeEntry("CN=dup2,OU=Users,DC=test,DC=local", sAMAccountName="dup2",
                         employeeID="2000", displayName="重复2", lockoutTime=0,
                         badPwdCount=0, userAccountControl=512)
        m = {
            ("employeeID", "1001"): [zhang],
            ("employeeID", "1002"): [li],
            ("employeeID", "1003"): [wang],
            ("employeeID", "1004"): [zhao],
            ("employeeID", "2000"): [dup1, dup2],
        }
        # 别名：sAMAccountName 兜底查找
        for entry in (zhang, li, wang, zhao, dup1, dup2):
            m.setdefault(("sAMAccountName", entry.sAMAccountName), []).append(entry)
        return m

    def _post(self, employee_id, with_header=True):
        headers = {"X-Requested-With": "XMLHttpRequest"} if with_header else {}
        return self.client.post("/api/unlock",
                                data=json.dumps({"employee_id": employee_id}),
                                content_type="application/json",
                                headers=headers)

    def _login(self, password):
        return self.client.post("/admin/login",
                                data=json.dumps({"password": password}),
                                content_type="application/json",
                                headers={"X-Requested-With": "XMLHttpRequest"})

    def _records(self, **params):
        qs = "&".join("{}={}".format(k, v) for k, v in params.items() if v != "")
        url = "/api/admin/records" + (("?" + qs) if qs else "")
        return self.client.get(url, headers={"X-Requested-With": "XMLHttpRequest"})

    def _seed_audit(self, lines):
        path = os.path.join(config.AUDIT_LOG_DIR, "audit.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")

    # ---------------- 用例 ----------------
    def test_index_page(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("AD \u57df\u8d26\u6237\u81ea\u52a9\u89e3\u9501".encode(), resp.data)

    def test_healthz(self):
        resp = self.client.get("/healthz")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["status"], "ok")

    def test_unlock_locked(self):
        conn = FakeConnection(self._build_users())
        with patch.object(ad_unlock, "_connect", return_value=conn):
            resp = self._post("1001")
        data = resp.get_json()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(data["code"], "UNLOCKED")
        self.assertTrue(data["success"])
        self.assertEqual(data["details"]["sAMAccountName"], "zhangsan")
        # 确认发出了解锁修改：仅 lockoutTime 置 0（badPwdCount 为操作属性，
        # 域控在解锁时自动归零，不允许也不应写入）
        self.assertEqual(len(conn.modify_calls), 1)
        dn, changes = conn.modify_calls[0]
        self.assertEqual(dn, "CN=zhangsan,OU=Users,DC=test,DC=local")
        self.assertIn("lockoutTime", changes)
        self.assertNotIn("badPwdCount", changes)
        self.assertTrue(conn.unbound)

    def test_not_locked(self):
        conn = FakeConnection(self._build_users())
        with patch.object(ad_unlock, "_connect", return_value=conn):
            resp = self._post("1002")
        data = resp.get_json()
        self.assertEqual(data["code"], "NOT_LOCKED")
        self.assertFalse(data["success"])
        self.assertEqual(conn.modify_calls, [])  # 未锁定不应发起写操作

    def test_stale_lock_cleaned(self):
        conn = FakeConnection(self._build_users())
        with patch.object(ad_unlock, "_connect", return_value=conn):
            resp = self._post("1003")
        data = resp.get_json()
        self.assertEqual(data["code"], "NOT_LOCKED")
        self.assertEqual(len(conn.modify_calls), 1)  # 顺带清理过期残留

    def test_not_found(self):
        conn = FakeConnection(self._build_users())
        with patch.object(ad_unlock, "_connect", return_value=conn):
            resp = self._post("9999")
        data = resp.get_json()
        self.assertEqual(data["code"], "NOT_FOUND")
        self.assertFalse(data["success"])

    def test_sam_account_name_fallback(self):
        # employeeID 无匹配时，用 sAMAccountName 兜底
        conn = FakeConnection(self._build_users())
        with patch.object(ad_unlock, "_connect", return_value=conn):
            resp = self._post("zhangsan")
        data = resp.get_json()
        self.assertEqual(data["code"], "UNLOCKED")

    def test_multiple_match(self):
        conn = FakeConnection(self._build_users())
        with patch.object(ad_unlock, "_connect", return_value=conn):
            resp = self._post("2000")
        data = resp.get_json()
        self.assertEqual(data["code"], "MULTIPLE_MATCH")
        self.assertEqual(conn.modify_calls, [])  # 不执行任何修改

    def test_disabled(self):
        conn = FakeConnection(self._build_users())
        with patch.object(ad_unlock, "_connect", return_value=conn):
            resp = self._post("1004")
        data = resp.get_json()
        self.assertEqual(data["code"], "DISABLED")
        self.assertEqual(conn.modify_calls, [])

    def test_bad_input(self):
        resp = self._post("ab!@#")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["code"], "BAD_INPUT")

    def test_empty_input(self):
        resp = self._post("   ")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["code"], "BAD_INPUT")

    def test_missing_header(self):
        resp = self._post("1001", with_header=False)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.get_json()["code"], "FORBIDDEN")

    def test_rate_limit(self):
        config.RATE_LIMIT_MAX = 3
        from app import _hits
        _hits.clear()
        for _ in range(3):
            resp = self._post("1001", with_header=False)  # 头缺失仍计入限流
        resp = self._post("1001")
        self.assertEqual(resp.status_code, 429)
        self.assertEqual(resp.get_json()["code"], "RATE_LIMITED")

    def test_ad_not_configured(self):
        config.AD_SERVER = ""
        resp = self._post("1001")
        self.assertEqual(resp.get_json()["code"], "AD_NOT_CONFIGURED")

    def test_ad_bind_failed(self):
        from ldap3.core.exceptions import LDAPBindError

        def _boom():
            raise LDAPBindError("bind failed")

        with patch.object(ad_unlock, "_connect", side_effect=_boom):
            resp = self._post("1001")
        self.assertEqual(resp.get_json()["code"], "AD_BIND_FAILED")

    # ---------------- 绑定方式（NTLM / SIMPLE DN） ----------------
    def test_connect_ntlm_when_domain_user(self):
        config.AD_BIND_USER = "AMS\\svc_adunlock"
        captured = {}

        def fake_connection(*args, **kwargs):
            captured["authentication"] = kwargs.get("authentication")
            return FakeConnection({})

        with patch.object(ad_unlock, "Connection", side_effect=fake_connection):
            ad_unlock._connect()
        self.assertEqual(captured["authentication"], "NTLM")

    def test_connect_simple_when_full_dn(self):
        config.AD_BIND_USER = "CN=S_LdapAuth,CN=Users,DC=ams,DC=com"
        captured = {}

        def fake_connection(*args, **kwargs):
            captured["authentication"] = kwargs.get("authentication")
            return FakeConnection({})

        with patch.object(ad_unlock, "Connection", side_effect=fake_connection):
            ad_unlock._connect()
        self.assertEqual(captured["authentication"], "SIMPLE")

    def test_unlock_with_dn_bind_config(self):
        # 完整 DN 绑定 + sAMAccountName 作为工号属性 的完整解锁流程
        config.AD_BIND_USER = "CN=S_LdapAuth,CN=Users,DC=ams,DC=com"
        config.AD_USER_ID_ATTR = "sAMAccountName"
        config.AD_SEARCH_BASE = "DC=ams,DC=com"
        conn = FakeConnection(self._build_users())
        with patch.object(ad_unlock, "_connect", return_value=conn):
            resp = self._post("zhangsan")
        data = resp.get_json()
        self.assertEqual(data["code"], "UNLOCKED")
        self.assertTrue(data["success"])

    # ---------------- 管理页面（解锁记录查询） ----------------
    def test_admin_login_page(self):
        resp = self.client.get("/admin/login")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("管理登录".encode(), resp.data)

    def test_admin_requires_login(self):
        resp = self.client.get("/admin")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/admin/login", resp.headers["Location"])

    def test_admin_records_requires_auth(self):
        resp = self._records()
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/admin/login", resp.headers["Location"])

    def test_admin_disabled(self):
        config.ADMIN_PASSWORD = ""
        resp = self._login("any-password")
        self.assertEqual(resp.status_code, 403)
        self.assertIn("未启用", resp.get_json()["message"])

    def test_admin_wrong_password(self):
        resp = self._login("wrong-password")
        self.assertEqual(resp.status_code, 401)
        self.assertFalse(resp.get_json()["success"])

    def test_admin_login_missing_header(self):
        resp = self.client.post("/admin/login",
                                data=json.dumps({"password": "admin123"}),
                                content_type="application/json")
        self.assertEqual(resp.status_code, 403)

    def test_admin_login_and_query_records(self):
        self._seed_audit([
            '{"ts": "2026-09-08 09:00:00", "ip": "10.0.0.1", "employee_id": "1001", "code": "UNLOCKED", "success": true, "message": "解锁成功", "cost_ms": 12}',
            '{"ts": "2026-09-08 10:00:00", "ip": "10.0.0.2", "employee_id": "1002", "code": "NOT_LOCKED", "success": false, "message": "未锁定", "cost_ms": 3}',
            '{"ts": "2026-09-08 11:00:00", "ip": "10.0.0.1", "employee_id": "1001", "code": "NOT_FOUND", "success": false, "message": "未找到", "cost_ms": 1}',
        ])
        # 登录
        resp = self._login("admin123")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["success"])
        # 管理页可访问
        page = self.client.get("/admin")
        self.assertEqual(page.status_code, 200)
        self.assertIn("解锁记录查询".encode(), page.data)
        # 查询全部（按时间倒序：11:00 的记录在最前）
        data = self._records().get_json()
        self.assertEqual(data["total"], 3)
        self.assertEqual(data["records"][0]["employee_id"], "1001")
        self.assertEqual(data["records"][0]["code"], "NOT_FOUND")
        # 按工号模糊筛选
        data = self._records(employee_id="1002").get_json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["records"][0]["code"], "NOT_LOCKED")
        # 按结果码筛选
        data = self._records(code="UNLOCKED").get_json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["records"][0]["employee_id"], "1001")
        # 按日期筛选（9月9日起无记录）
        data = self._records(date_from="2026-09-09").get_json()
        self.assertEqual(data["total"], 0)
        data = self._records(date_from="2026-09-08", date_to="2026-09-08").get_json()
        self.assertEqual(data["total"], 3)

    def test_admin_records_pagination(self):
        lines = []
        for i in range(5):
            lines.append('{"ts": "2026-09-0%d 09:00:00", "ip": "10.0.0.1", "employee_id": "%d", "code": "UNLOCKED", "success": true, "message": "解锁成功", "cost_ms": 1}' % (i + 1, 1000 + i))
        self._seed_audit(lines)
        self._login("admin123")
        data = self._records(page="1", page_size="2").get_json()
        self.assertEqual(data["total"], 5)
        self.assertEqual(data["pages"], 3)
        self.assertEqual(len(data["records"]), 2)
        # 第二页
        data = self._records(page="2", page_size="2").get_json()
        self.assertEqual(len(data["records"]), 2)
        # 最后一页
        data = self._records(page="3", page_size="2").get_json()
        self.assertEqual(len(data["records"]), 1)

    def test_admin_records_bad_date(self):
        self._login("admin123")
        resp = self._records(date_from="2026/09/08")
        self.assertEqual(resp.status_code, 400)

    def test_admin_logout(self):
        self._login("admin123")
        resp = self.client.get("/admin/logout")
        self.assertEqual(resp.status_code, 302)
        # 登出后无法再访问
        resp = self.client.get("/admin")
        self.assertEqual(resp.status_code, 302)


if __name__ == "__main__":
    sys.exit(unittest.main(verbosity=2))
