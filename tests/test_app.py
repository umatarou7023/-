import base64
from io import BytesIO
from pathlib import Path
import tempfile
import unittest

from openpyxl import load_workbook
from app import create_app


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = {"TESTING": True, "PUBLIC": True, "ADMIN_PASSWORD": "test-admin-password-long", "INVITE_CODE": "test-invite-code-long", "SECRET_KEY": "test-session-key", "DATA_DIR": self.temp.name}
        self.app = create_app(self.config)
        self.client = self.app.test_client()
        self.base = "https://example.test"

    def tearDown(self):
        self.temp.cleanup()

    def post(self, path, data):
        return self.client.post(path, json=data, base_url=self.base, headers={"Origin": self.base})

    def get(self, path):
        return self.client.get(path, base_url=self.base)

    def login(self):
        self.assertEqual(self.post("/api/login", {"password": self.config["ADMIN_PASSWORD"]}).status_code, 200)

    def submission(self):
        return {"key": self.config["INVITE_CODE"], "name": "山田 太郎", "month": "2026-10", "days": [{"date": "2026-10-20", "shift": "17:00-22:00"}]}

    def test_staff_to_admin_to_excel_round_trip(self):
        with self.get("/submit") as page:
            self.assertEqual(page.status_code, 200)
        self.assertEqual(self.post("/api/submit", self.submission()).json["count"], 1)
        self.assertEqual(self.get("/api/submissions?month=2026-10").status_code, 401)
        self.login()
        submitted = self.get("/api/submissions?month=2026-10").json
        self.assertEqual(submitted["people"][0]["name"], "山田 太郎")
        sample = self.get("/api/sample?month=2026-10").data
        data = {"file": base64.b64encode(sample).decode(), "sheet": "シフト表", "month": "2026-10", "text": submitted["text"]}
        self.assertEqual(self.post("/api/inspect", data).json["sheets"], ["シフト表"])
        self.assertEqual(self.post("/api/preview", data).json["rows"][0]["cell"], "U2")
        result = self.post("/api/export", data)
        self.assertEqual(result.status_code, 200)
        book = load_workbook(BytesIO(result.data))
        self.assertEqual(book["シフト表"]["U2"].value, "17:00-22:00")
        book.close()

    def test_submission_persists_after_app_restart(self):
        self.assertEqual(self.post("/api/submit", self.submission()).status_code, 200)
        self.client = create_app(self.config).test_client()
        self.login()
        self.assertEqual(len(self.get("/api/submissions?month=2026-10").json["people"]), 1)

    def test_latest_month_submission_replaces_previous_whole_month(self):
        self.post("/api/submit", self.submission())
        data = self.submission()
        data["name"] = "山田太郎"
        data["days"] = [{"date": "2026-10-21", "shift": "休み"}]
        self.post("/api/submit", data)
        self.login()
        result = self.get("/api/submissions?month=2026-10").json
        self.assertEqual(len(result["people"]), 1)
        self.assertNotIn("2026-10-20", result["text"])
        self.assertIn("2026-10-21 休み", result["text"])

    def test_invalid_invite_and_bad_dates_are_not_stored(self):
        data = self.submission()
        data["key"] = "wrong"
        self.assertEqual(self.post("/api/submit", data).status_code, 403)
        data = self.submission()
        data["days"] = [{"date": "2026-11-01", "shift": "休み"}]
        self.assertEqual(self.post("/api/submit", data).status_code, 400)
        self.login()
        self.assertEqual(self.get("/api/submissions?month=2026-10").json["people"], [])

    def test_admin_password_origin_logout_and_settings_protection(self):
        self.assertEqual(self.get("/api/settings").status_code, 401)
        for path in ["/api/roster", "/api/daily-candidates?day=2026-11-20", "/api/daily-sample?day=2026-11-20"]:
            self.assertEqual(self.get(path).status_code, 401)
        self.assertEqual(self.get("/daily").status_code, 302)
        for path in ["/api/daily-preview", "/api/daily-export", "/api/roster"]:
            self.assertEqual(self.post(path, {}).status_code, 401)
        self.assertEqual(self.post("/api/login", {"password": "wrong"}).status_code, 401)
        self.assertEqual(self.client.post("/api/submit", json=self.submission(), base_url=self.base, headers={"Origin": "https://other.test"}).status_code, 403)
        self.login()
        settings = self.get("/api/settings").json
        self.assertIn(self.config["INVITE_CODE"], settings["invite_url"])
        self.assertEqual(self.post("/api/logout", {}).status_code, 200)
        self.assertEqual(self.get("/api/settings").status_code, 401)

    def test_public_start_requires_credentials(self):
        with self.assertRaises(RuntimeError):
            create_app({**self.config, "ADMIN_PASSWORD": ""})


if __name__ == "__main__":
    unittest.main()
