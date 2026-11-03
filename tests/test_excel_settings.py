import base64
from io import BytesIO
import tempfile
import unittest

from openpyxl import load_workbook

from app import create_app
from daily_shifts import sample_daily_book


class ExcelSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = {"TESTING": True, "PUBLIC": False, "DATA_DIR": self.temp.name}
        self.client = create_app(self.config).test_client()
        self.post("/api/roster", {"kitchen": [], "hall": ["ホール 仮名"]})
        self.post("/api/submit", {"key": "local-demo-only", "name": "ホール 仮名", "month": "2026-11", "days": [{"date": "2026-11-20", "shift": "17:00-21:00"}]})
        book = load_workbook(BytesIO(sample_daily_book("2026-11-20")))
        book.active["L34"] = None
        out = BytesIO(); book.save(out); book.close()
        self.source = out.getvalue()

    def tearDown(self):
        self.temp.cleanup()

    def post(self, path, data):
        return self.client.post(path, json=data, headers={"Origin": "http://localhost"})

    def data(self):
        candidates = self.client.get("/api/daily-candidates?day=2026-11-20").json
        number = candidates["rows"][0]["id"]
        return {"file": base64.b64encode(self.source).decode(), "sheet": "日別ベータ", "day": "2026-11-20", "date_confirmed": True, "version": candidates["version"], "selected_ids": [number], "breaks": {str(number): {"mode": "none"}}}

    def test_default_off_and_navigation_exists(self):
        self.assertEqual(self.client.get("/api/excel-settings").json["settings"], {"repair_blank_formulas": False})
        for path in ["/", "/daily"]:
            with self.client.get(path) as response:
                self.assertIn(b'href="/settings"', response.data)
        with self.client.get("/settings") as response:
            self.assertEqual(response.status_code, 200)
            self.assertIn(b'role="switch"', response.data)
        self.assertEqual(self.post("/api/daily-preview", self.data()).status_code, 400)

    def test_setting_restored_after_restart_without_losing_names_or_submissions(self):
        self.assertEqual(self.post("/api/excel-settings", {"repair_blank_formulas": True}).status_code, 200)
        self.client = create_app(self.config).test_client()
        self.assertEqual(self.client.get("/api/excel-settings").json["settings"]["repair_blank_formulas"], True)
        self.assertEqual(self.client.get("/api/roster").json["roster"]["hall"], ["ホール 仮名"])
        self.assertEqual(len(self.client.get("/api/submissions?month=2026-11").json["people"]), 1)
        self.post("/api/excel-settings", {"repair_blank_formulas": False})
        self.client = create_app(self.config).test_client()
        self.assertEqual(self.client.get("/api/excel-settings").json["settings"]["repair_blank_formulas"], False)

    def test_saved_policy_overrides_old_per_file_flags_and_confirmation_still_required(self):
        data = self.data(); data["repair_blank_formulas"] = True
        self.assertEqual(self.post("/api/daily-preview", data).status_code, 400)
        self.post("/api/excel-settings", {"repair_blank_formulas": True})
        data = self.data(); data["repair_blank_formulas"] = False
        preview = self.post("/api/daily-preview", data)
        self.assertEqual(preview.status_code, 200, preview.json)
        self.assertEqual([p["cell"] for p in preview.json["formula_repairs"]], ["L34"])
        export = {**data, "plan_token": preview.json["plan_token"]}
        self.assertEqual(self.post("/api/daily-export", export).status_code, 400)
        output = self.post("/api/daily-export", {**export, "formula_repairs_confirmed": True})
        self.assertEqual(output.status_code, 200)
        book = load_workbook(BytesIO(output.data))
        self.assertEqual(book.active["L34"].data_type, "f")
        book.close()

    def test_setting_change_invalidates_candidates_and_confirmed_preview(self):
        old = self.data()
        self.post("/api/excel-settings", {"repair_blank_formulas": True})
        self.assertIn("読み込み直", self.post("/api/daily-preview", old).json["error"])
        data = self.data(); preview = self.post("/api/daily-preview", data).json
        self.post("/api/excel-settings", {"repair_blank_formulas": False})
        result = self.post("/api/daily-export", {**data, "plan_token": preview["plan_token"], "formula_repairs_confirmed": True})
        self.assertEqual(result.status_code, 400)
        self.assertIn("読み込み直", result.json["error"])

    def test_invalid_values_and_cross_site_requests_do_not_change_setting(self):
        self.post("/api/excel-settings", {"repair_blank_formulas": True})
        for value in ["ON", "false", 1, None]:
            self.assertEqual(self.post("/api/excel-settings", {"repair_blank_formulas": value}).status_code, 400)
        result = self.client.post("/api/excel-settings", json={"repair_blank_formulas": False}, headers={"Origin": "https://other.example"})
        self.assertEqual(result.status_code, 403)
        self.assertEqual(self.client.get("/api/excel-settings").json["settings"]["repair_blank_formulas"], True)


if __name__ == "__main__":
    unittest.main()
