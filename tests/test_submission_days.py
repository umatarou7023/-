import base64
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from openpyxl import load_workbook

from app import create_app
from daily_shifts import sample_daily_book


class SubmissionDayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = {"TESTING": True, "PUBLIC": False, "DATA_DIR": self.temp.name}
        self.client = create_app(self.config).test_client()
        self.post("/api/roster", {"kitchen": ["仮名A", "仮名B"], "hall": []})

    def tearDown(self):
        self.temp.cleanup()

    def post(self, route, data):
        return self.client.post(route, json=data, headers={"Origin": "http://localhost"})

    def submit(self, days, name="仮名A", month="2026-11"):
        result = self.post("/api/submit", {"key": "local-demo-only", "name": name, "month": month,
                                           "days": [{"date": f"{month}-{day:02}", "shift": shift} for day, shift in days.items()]})
        self.assertEqual(result.status_code, 200, result.json)
        return result.json["receipt"]

    def month(self, month="2026-11"):
        return self.client.get(f"/api/submissions?month={month}").json

    def day(self, number):
        return self.client.get(f"/api/daily-candidates?day=2026-11-{number:02}").json

    def plan_data(self, day=10):
        candidates = self.day(day)
        ids = [row["id"] for row in candidates["rows"]]
        date = f"2026-11-{day:02}"
        return {"file": base64.b64encode(sample_daily_book(date)).decode(), "sheet": "日別ベータ", "day": date,
                "date_confirmed": True, "selected_ids": ids, "version": candidates["version"],
                "breaks": {str(number): {"mode": "none"} for number in ids}}

    def test_first_and_second_half_submissions_form_one_month(self):
        first = self.submit({day: "17:00-21:00" for day in range(1, 16)})
        second = self.submit({day: "09:00-15:00" for day in range(16, 31)})
        result = self.month()
        self.assertEqual(result["people"][0]["count"], 30)
        self.assertEqual(len(result["text"].splitlines()), 30)
        self.assertEqual(self.day(10)["rows"][0]["id"], first)
        self.assertEqual(self.day(20)["rows"][0]["id"], second)
        self.assertEqual(self.day(10)["rows"][0]["shift"], "17:00-21:00")
        self.assertEqual(self.day(20)["rows"][0]["shift"], "09:00-15:00")

    def test_full_month_then_single_day_change_keeps_other_dates(self):
        self.submit({day: "17:00-21:00" for day in range(1, 31)})
        self.submit({20: "18:00-22:00"}, name="仮名 A")
        result = self.month()
        self.assertEqual(len(result["people"]), 1)
        self.assertEqual(result["people"][0]["count"], 30)
        self.assertEqual(result["text"].count("18:00-22:00"), 1)
        self.assertEqual(result["text"].count("17:00-21:00"), 29)
        self.assertEqual(self.day(20)["rows"][0]["name"], "仮名A")

    def test_explicit_day_off_replaces_only_that_date_and_can_be_changed_back(self):
        self.submit({10: "17:00-21:00", 20: "17:00-21:00"})
        original = self.day(10)
        self.submit({10: "休み"})
        self.assertEqual(self.day(10)["rows"], [])
        self.assertNotEqual(self.day(10)["version"], original["version"])
        self.assertIn("2026-11-10 休み", self.month()["text"])
        self.assertEqual(self.day(20)["rows"][0]["shift"], "17:00-21:00")
        self.submit({10: "09:00-15:00"})
        self.assertEqual(self.day(10)["rows"][0]["shift"], "09:00-15:00")
        self.assertEqual(self.month()["people"][0]["count"], 2)

    def test_other_people_and_months_are_not_merged_together(self):
        self.submit({10: "17:00-21:00"})
        self.submit({10: "09:00-15:00"}, name="仮名B")
        self.submit({10: "10:00-16:00"}, month="2026-12")
        self.submit({20: "18:00-22:00"})
        self.assertEqual([p["count"] for p in self.month()["people"]], [2, 1])
        self.assertEqual([p["shift"] for p in self.day(10)["rows"]], ["17:00-21:00", "09:00-15:00"])
        self.assertEqual(self.month("2026-12")["text"], "仮名A 2026-12-10 10:00-16:00")

    def test_existing_history_is_read_per_day_after_restart_without_rewriting(self):
        # 更新前と同じスキーマ・JSONの履歴を直接用意する。
        with sqlite3.connect(Path(self.temp.name) / "shifts.sqlite3") as db:
            for day, shift in [(10, "17:00-21:00"), (20, "09:00-15:00"), (10, "18:00-22:00")]:
                content = json.dumps([{"name": "仮名A", "date": f"2026-11-{day:02}", "shift": shift}])
                db.execute("INSERT INTO submissions(name,name_key,month,rows_json,submitted_at) VALUES(?,?,?,?,?)",
                           ("仮名A", "仮名A", "2026-11", content, "2026-10-25T09:00:00+00:00"))
            before = db.execute("SELECT * FROM submissions ORDER BY id").fetchall()
        self.client = create_app(self.config).test_client()
        self.assertEqual(self.month()["people"][0]["count"], 2)
        self.assertEqual(self.day(10)["rows"][0]["shift"], "18:00-22:00")
        self.assertEqual(self.day(20)["rows"][0]["shift"], "09:00-15:00")
        with sqlite3.connect(Path(self.temp.name) / "shifts.sqlite3") as db:
            self.assertEqual(db.execute("SELECT * FROM submissions ORDER BY id").fetchall(), before)

    def test_second_half_does_not_invalidate_review_of_unchanged_first_half(self):
        first = self.submit({10: "17:00-21:00"})
        data = self.plan_data()
        preview = self.post("/api/daily-preview", data)
        self.assertEqual(preview.status_code, 200, preview.json)
        self.submit({20: "09:00-15:00"})
        self.assertEqual(self.day(10)["rows"][0]["id"], first)
        self.assertEqual(self.day(10)["version"], data["version"])
        output = self.post("/api/daily-export", {**data, "plan_token": preview.json["plan_token"]})
        self.assertEqual(output.status_code, 200, output.json)
        book = load_workbook(BytesIO(output.data))
        self.assertEqual(book.active["C15"].value, "仮名A")
        self.assertEqual(str(book.active["D15"].value), "17:00:00")
        book.close()

    def test_same_day_change_invalidates_its_preview_and_keeps_other_day(self):
        self.submit({10: "17:00-21:00", 20: "09:00-15:00"})
        data = self.plan_data()
        preview = self.post("/api/daily-preview", data)
        self.assertEqual(preview.status_code, 200, preview.json)
        self.submit({10: "18:00-22:00"})
        output = self.post("/api/daily-export", {**data, "plan_token": preview.json["plan_token"]})
        self.assertEqual(output.status_code, 400)
        self.assertIn("読み込み直", output.json["error"])
        self.assertEqual(self.day(20)["rows"][0]["shift"], "09:00-15:00")

    def test_monthly_initial_priority_survives_partial_additions_and_changes(self):
        first = self.submit({1: "17:00-21:00"})
        second = self.submit({20: "09:00-15:00"}, name="仮名B")
        later = self.submit({20: "17:00-21:00"})
        rows = self.day(20)["rows"]
        self.assertEqual([r["name"] for r in rows], ["仮名A", "仮名B"])
        self.assertEqual([r["submission_order"] for r in rows], [first, second])
        self.assertEqual([r["id"] for r in rows], [later, second])

    def test_invalid_partial_submission_does_not_save_any_dates(self):
        self.submit({10: "17:00-21:00"})
        result = self.post("/api/submit", {"key": "local-demo-only", "name": "仮名A", "month": "2026-11",
                                           "days": [{"date": "2026-11-20", "shift": "09:00-15:00"},
                                                    {"date": "2026-12-01", "shift": "休み"}]})
        self.assertEqual(result.status_code, 400)
        self.assertEqual(self.month()["people"][0]["count"], 1)
        self.assertEqual(self.day(20)["rows"], [])


if __name__ == "__main__":
    unittest.main()
