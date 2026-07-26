"""看板 Basic Auth 挂载行为(api/__init__.py + api/auth.py + config)。

选用不碰数据库的端点(GET /api/attachments/sync/status)验证认证层,
避免 sqlite 内存库在 TestClient 多线程下各连接看到不同库的问题。
"""
import base64
import os
import unittest

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["OPENAI_API_KEY"] = ""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import api_router
from config import settings

PROTECTED_URL = "/api/attachments/sync/status"


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(api_router, prefix="/api")
    return TestClient(app)


def _basic(user: str, password: str) -> dict:
    raw = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {raw}"}


class DashboardAuthTests(unittest.TestCase):
    def setUp(self):
        self._orig = (
            settings.DASHBOARD_USERNAME,
            settings.DASHBOARD_PASSWORD,
            settings.SNOV_WEBHOOK_TOKEN,
        )
        settings.DASHBOARD_USERNAME = "ops"
        settings.DASHBOARD_PASSWORD = "secret-pass"
        settings.SNOV_WEBHOOK_TOKEN = "test-webhook-token-123456"
        self.client = _client()

    def tearDown(self):
        (
            settings.DASHBOARD_USERNAME,
            settings.DASHBOARD_PASSWORD,
            settings.SNOV_WEBHOOK_TOKEN,
        ) = self._orig

    def test_no_credentials_rejected(self):
        resp = self.client.get(PROTECTED_URL)
        self.assertEqual(resp.status_code, 401)
        self.assertIn("Basic", resp.headers.get("WWW-Authenticate", ""))

    def test_wrong_credentials_rejected(self):
        resp = self.client.get(PROTECTED_URL, headers=_basic("ops", "wrong"))
        self.assertEqual(resp.status_code, 401)

    def test_correct_credentials_accepted(self):
        resp = self.client.get(PROTECTED_URL, headers=_basic("ops", "secret-pass"))
        self.assertEqual(resp.status_code, 200)

    def test_placeholder_credentials_fail_closed(self):
        settings.DASHBOARD_USERNAME = "replace-with-a-private-username"
        settings.DASHBOARD_PASSWORD = "replace-with-a-strong-password"
        resp = self.client.get(PROTECTED_URL, headers=_basic("ops", "secret-pass"))
        self.assertEqual(resp.status_code, 503)

    def test_empty_credentials_fail_closed(self):
        settings.DASHBOARD_USERNAME = ""
        settings.DASHBOARD_PASSWORD = ""
        resp = self.client.get(PROTECTED_URL, headers=_basic("ops", "secret-pass"))
        self.assertEqual(resp.status_code, 503)

    def test_webhook_exempt_from_basic_auth(self):
        # webhook 走 Snov 专用 token；错 token 得到它自己的 401，
        # 而非 Basic Auth 的质询（无 WWW-Authenticate 头）。
        resp = self.client.post("/api/webhook/snov?token=bad-token", json={})
        self.assertEqual(resp.status_code, 401)
        self.assertNotIn("WWW-Authenticate", resp.headers)
        self.assertIn("webhook token", resp.json().get("detail", ""))


if __name__ == "__main__":
    unittest.main()
