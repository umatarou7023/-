import base64
from io import BytesIO
import tempfile
import unittest

from openpyxl import load_workbook

from app import create_app
from break_rules import classify_day
from daily_shifts import sample_daily_book


class BreakRuleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = {"TESTING": True, "PUBLIC": False, "DATA_DIR": self.temp.name}
        self.app = create_app(self.config)
        self.client = self.app.test_client()
        self.post("/api/roster", {"kitchen": ["山田 太郎"], "hall": []})
        self.rules = {"mode": "auto", "weekday": {"mode": "time", "start": "15:00", "end": "17:00"}, "weekend": {"mode": "time", "start": "16:00", "end": "17:00"}}

    def tearDown(self):
        self.temp.cleanup()

    def post(self, url, data):
        return self.client.post(url, json=data, headers={"Origin": "http://localhost"})

    def submit(self, day="2026-11-20", shift="09:00-21:00", name="山田 太郎"):
        r = self.post("/api/submit", {"key": self.app.config["INVITE_CODE"], "name": name, "month": day[:7], "days": [{"date": day, "shift": shift}]})
        self.assertEqual(r.status_code, 200, r.json)

    def data(self, day="2026-11-20"):
        candidates = self.client.get(f"/api/daily-candidates?day={day}").json
        ids = [p["id"] for p in candidates["rows"]]
        return {"file": base64.b64encode(sample_daily_book(day)).decode(), "sheet": "日別ベータ", "day": day, "selected_ids": ids, "date_confirmed": True, "version": candidates["version"], "breaks": {str(i): {"mode": "none"} for i in ids}}

    def test_manual_default_and_no_automatic_break_without_confirmation(self):
        self.assertEqual(self.client.get("/api/break-settings").json["settings"]["mode"], "manual")
        self.submit()
        data = self.data(); data["breaks"] = {}
        self.assertIn("休憩を確認", self.post("/api/daily-preview", data).json["error"])

    def test_mode_rules_roster_and_submissions_survive_restart(self):
        self.submit()
        self.assertEqual(self.post("/api/break-settings", self.rules).status_code, 200)
        self.client = create_app(self.config).test_client()
        self.assertEqual(self.client.get("/api/break-settings").json["settings"], self.rules)
        self.assertEqual(self.client.get("/api/roster").json["roster"]["kitchen"], ["山田 太郎"])
        rows = self.client.get("/api/daily-candidates?day=2026-11-20").json["rows"]
        self.assertEqual(rows[0]["automatic_break"], self.rules["weekday"])

    def test_weekday_weekend_holiday_and_substitute_holiday(self):
        self.post("/api/break-settings", self.rules)
        for day, category in [("2026-11-20", "weekday"), ("2026-11-21", "weekend"), ("2026-11-22", "weekend"), ("2026-11-03", "weekend"), ("2026-05-06", "weekend"), ("2026-09-22", "weekend")]:
            with self.subTest(day=day):
                self.submit(day)
                result = self.client.get(f"/api/daily-candidates?day={day}").json
                self.assertEqual(result["day_type"]["category"], category)
                self.assertEqual(result["rows"][0]["automatic_break"], self.rules[category])
        self.assertEqual(classify_day("2026-11-03")["holiday"], "文化の日")

    def test_saved_rules_override_client_break_and_write_excel_times(self):
        self.post("/api/break-settings", self.rules); self.submit()
        data = self.data()  # 客先の送信は「休憩なし」だが、保存済み設定を適用する。
        plan = self.post("/api/daily-preview", data)
        self.assertEqual(plan.status_code, 200, plan.json)
        self.assertEqual(plan.json["placements"][0]["break"], "15:00-17:00")
        output = self.post("/api/daily-export", {**data, "plan_token": plan.json["plan_token"]})
        self.assertEqual(output.status_code, 200, output.json if output.is_json else "")
        book = load_workbook(BytesIO(output.data))
        self.assertEqual(str(book.active["H15"].value), "15:00:00")
        self.assertEqual(str(book.active["I15"].value), "17:00:00")
        book.close()

    def test_nonoverlap_has_no_break_partial_overlap_and_whole_shift_block(self):
        self.post("/api/break-settings", self.rules)
        for shift in ["09:00-14:00", "17:00-21:00"]:
            self.submit(shift=shift)
            result = self.post("/api/daily-preview", self.data())
            self.assertEqual(result.status_code, 200, result.json)
            self.assertIn("なし", result.json["placements"][0]["break"])
        for shift in ["09:00-16:00", "16:00-21:00", "15:00-17:00"]:
            self.submit(shift=shift)
            result = self.post("/api/daily-preview", self.data())
            self.assertEqual(result.status_code, 400)
            self.assertIn("手動モード", result.json["error"])

    def test_invalid_rules_not_saved_and_settings_change_requires_reload(self):
        self.submit()
        for bad in [{"mode": "auto"}, {**self.rules, "weekday": {"mode": "time", "start": "15:01", "end": "17:00"}}, {**self.rules, "weekend": {"mode": "time", "start": "17:00", "end": "16:00"}}]:
            self.assertEqual(self.post("/api/break-settings", bad).status_code, 400)
            self.assertEqual(self.client.get("/api/break-settings").json["settings"]["mode"], "manual")
        data = self.data()
        self.post("/api/break-settings", self.rules)
        self.assertIn("読み込み直", self.post("/api/daily-preview", data).json["error"])
        data = self.data(); preview = self.post("/api/daily-preview", data).json
        changed = {**self.rules, "weekday": {"mode": "none"}}
        self.post("/api/break-settings", changed)
        self.assertEqual(self.post("/api/daily-export", {**data, "plan_token": preview["plan_token"]}).status_code, 400)

    def test_manual_mode_can_override_saved_fixed_rule(self):
        self.post("/api/break-settings", {**self.rules, "mode": "manual"}); self.submit()
        data = self.data()
        for key in data["breaks"]: data["breaks"][key] = {"mode": "time", "start": "12:00", "end": "13:00"}
        result = self.post("/api/daily-preview", data)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json["placements"][0]["break"], "12:00-13:00")

    def test_automatic_mode_does_not_bypass_capacity_limit(self):
        names = [f"仮名{i}" for i in range(9)]
        self.post("/api/roster", {"kitchen": names, "hall": []})
        self.post("/api/break-settings", self.rules)
        for name in names: self.submit(name=name)
        result = self.post("/api/daily-preview", self.data())
        self.assertEqual(result.status_code, 400)
        self.assertIn("キッチンは9人", result.json["error"])


if __name__ == "__main__":
    unittest.main()
