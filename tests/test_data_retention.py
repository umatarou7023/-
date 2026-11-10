from datetime import date
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from app import create_app
from data_retention import expiry_date


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.today = date(2027, 1, 13)
        self.config = {"TESTING": True, "PUBLIC": False, "DATA_DIR": self.temp.name,
                       "RETENTION_TODAY": lambda: self.today, "SECRET_KEY": "retention-test-key"}
        self.app = create_app(self.config)
        self.client = self.app.test_client()
        self.roster = {"kitchen": ["仮名 A", "仮名B"], "hall": ["仮名C"]}
        self.post("/api/roster", self.roster)
        self.post("/api/break-settings", {"mode": "auto", "weekday": {"mode": "none"}, "weekend": {"mode": "none"}})
        self.post("/api/excel-settings", {"repair_blank_formulas": True})

    def tearDown(self):
        self.temp.cleanup()

    def post(self, path, data):
        return self.client.post(path, json=data, headers={"Origin": "http://localhost"})

    def submit(self, days, name="仮名 A", month="2026-11"):
        result = self.post("/api/submit", {"key": "local-demo-only", "name": name, "month": month,
                                           "days": [{"date": f"{month}-{day:02}", "shift": shift} for day, shift in days.items()]})
        self.assertEqual(result.status_code, 200, result.json)
        return result.json["receipt"]

    def day(self, number):
        return self.client.get(f"/api/daily-candidates?day=2026-11-{number:02}").json

    def settings_data(self, months=None, days=60):
        return {"auto_months": ["2026-11"] if months is None else months, "retention_days": days}

    def save(self, settings=None, confirmed=False):
        settings = settings or self.settings_data()
        preview = self.post("/api/retention-preview", settings)
        self.assertEqual(preview.status_code, 200, preview.json)
        return self.post("/api/retention-settings", {**settings, "preview_token": preview.json["preview_token"], "confirmed": confirmed})

    def delete(self, target, confirmed=True):
        preview = self.post("/api/data-deletion-preview", target)
        self.assertEqual(preview.status_code, 200, preview.json)
        return self.post("/api/data-delete", {**target, "preview_token": preview.json["preview_token"], "confirmed": confirmed})

    def records(self):
        with sqlite3.connect(Path(self.temp.name) / "shifts.sqlite3") as db:
            return db.execute("SELECT id,name,rows_json FROM submissions ORDER BY id").fetchall()

    def test_default_all_months_off_and_existing_data_is_not_deleted(self):
        self.submit({1: "17:00-21:00"})
        self.today = date(2028, 1, 1)
        self.app.extensions["data_retention"].run_due()
        info = self.client.get("/api/retention-settings").json
        self.assertEqual(info["settings"], {"auto_months": [], "retention_days": 60})
        self.assertFalse(info["months"][0]["enabled"])
        self.assertEqual(len(self.day(1)["rows"]), 1)

    def test_half_month_deadlines_year_boundary_and_leap_year(self):
        self.assertEqual(expiry_date(date(2026, 11, 1), 60), date(2027, 1, 14))
        self.assertEqual(expiry_date(date(2026, 11, 15), 60), date(2027, 1, 14))
        self.assertEqual(expiry_date(date(2026, 11, 16), 60), date(2027, 1, 29))
        self.assertEqual(expiry_date(date(2026, 11, 30), 60), date(2027, 1, 29))
        self.assertEqual(expiry_date(date(2028, 2, 16), 60), date(2028, 4, 29))

    def test_auto_purge_removes_all_first_half_history_but_retains_second_half(self):
        self.submit({1: "17:00-21:00", 15: "17:00-21:00", 16: "09:00-15:00", 30: "09:00-15:00"})
        self.submit({1: "18:00-22:00"})
        self.assertEqual(self.save().status_code, 200)
        self.assertEqual(len(self.day(1)["rows"]), 1)
        self.today = date(2027, 1, 14)
        self.app.extensions["data_retention"].run_due()
        self.assertEqual(self.day(1)["rows"], [])
        self.assertEqual(self.day(15)["rows"], [])
        self.assertEqual(len(self.day(16)["rows"]), 1)
        for _, _, content in self.records():
            self.assertTrue(all(row["date"] > "2026-11-15" for row in json.loads(content)))
        self.client = create_app(self.config).test_client()
        self.assertEqual(self.day(1)["rows"], [])
        self.assertEqual(len(self.day(30)["rows"]), 1)
        self.assertEqual(self.client.get("/api/retention-settings").json["last_run"]["deleted"]["history_entries"], 3)

    def test_month_switches_are_independent_and_off_stops_future_cleanup(self):
        self.submit({1: "17:00-21:00", 20: "17:00-21:00"})
        self.submit({1: "17:00-21:00"}, month="2026-12")
        self.assertEqual(self.save().status_code, 200)
        self.today = date(2027, 1, 14)
        self.app.extensions["data_retention"].run_due()
        self.assertEqual(self.day(1)["rows"], [])
        self.assertEqual(len(self.client.get("/api/submissions?month=2026-12").json["people"]), 1)
        self.assertEqual(self.save(self.settings_data([])).status_code, 200)
        self.today = date(2028, 1, 1)
        self.app.extensions["data_retention"].run_due()
        self.assertEqual(len(self.records()), 2)
        self.assertEqual(len(self.day(20)["rows"]), 1)

    def test_enabling_with_expired_data_requires_preview_and_confirmation(self):
        self.submit({1: "17:00-21:00", 20: "09:00-15:00"})
        self.today = date(2027, 1, 14)
        self.assertEqual(self.post("/api/retention-settings", self.settings_data()).status_code, 400)
        self.assertEqual(self.save().status_code, 400)
        self.assertEqual(len(self.day(1)["rows"]), 1)
        self.assertEqual(self.client.get("/api/retention-settings").json["settings"]["auto_months"], [])
        self.assertEqual(self.save(confirmed=True).status_code, 200)
        self.assertEqual(self.day(1)["rows"], [])
        self.assertEqual(len(self.day(20)["rows"]), 1)

    def test_stale_retention_preview_cannot_delete_unreviewed_new_history(self):
        self.submit({1: "17:00-21:00"})
        self.today = date(2027, 1, 14)
        preview = self.post("/api/retention-preview", self.settings_data()).json
        self.submit({2: "09:00-15:00"})
        result = self.post("/api/retention-settings", {**self.settings_data(), "preview_token": preview["preview_token"], "confirmed": True})
        self.assertEqual(result.status_code, 400)
        self.assertEqual(len(self.day(1)["rows"]), 1)
        self.assertEqual(len(self.day(2)["rows"]), 1)

    def test_restart_cleans_missed_deadlines_and_expired_dates_cannot_be_resubmitted(self):
        self.submit({1: "17:00-21:00", 20: "休み"})
        self.assertEqual(self.save().status_code, 200)
        self.today = date(2027, 1, 30)
        self.client = create_app(self.config).test_client()
        self.assertEqual(self.records(), [])
        result = self.post("/api/submit", {"key": "local-demo-only", "name": "仮名 A", "month": "2026-11",
                                           "days": [{"date": "2026-11-01", "shift": "17:00-21:00"}]})
        self.assertEqual(result.status_code, 400)
        self.assertIn("期限", result.json["error"])
        with sqlite3.connect(Path(self.temp.name) / "shifts.sqlite3") as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM submission_priorities").fetchone()[0], 0)

    def test_manual_first_half_deletes_old_versions_only_and_leaves_excel_and_settings(self):
        self.submit({1: "17:00-21:00", 20: "09:00-15:00"})
        self.submit({1: "18:00-22:00"})
        sentinel = Path(self.temp.name) / "company.xlsx"
        sentinel.write_bytes(b"unchanged company file")
        target = {"kind": "submissions", "month": "2026-11", "part": "first"}
        preview = self.post("/api/data-deletion-preview", target).json
        self.assertEqual(preview["summary"]["person_days"], 1)
        self.assertEqual(preview["summary"]["history_entries"], 2)
        self.assertEqual(self.delete(target, confirmed=False).status_code, 400)
        self.assertEqual(self.delete(target).status_code, 200)
        self.assertEqual(self.day(1)["rows"], [])
        self.assertEqual(len(self.day(20)["rows"]), 1)
        self.assertEqual(sentinel.read_bytes(), b"unchanged company file")
        self.assertEqual(self.client.get("/api/roster").json["roster"], self.roster)
        self.assertTrue(self.client.get("/api/excel-settings").json["settings"]["repair_blank_formulas"])
        self.assertEqual(self.client.get("/api/break-settings").json["settings"]["mode"], "auto")

    def test_manual_full_month_removes_priorities_and_history_but_preserves_other_month(self):
        self.submit({1: "休み", 20: "17:00-21:00"})
        self.submit({1: "17:00-21:00"}, month="2026-12")
        self.assertEqual(self.delete({"kind": "submissions", "month": "2026-11", "part": "all"}).status_code, 200)
        with sqlite3.connect(Path(self.temp.name) / "shifts.sqlite3") as db:
            self.assertEqual(db.execute("SELECT DISTINCT month FROM submissions").fetchall(), [("2026-12",)])
            self.assertEqual(db.execute("SELECT DISTINCT month FROM submission_priorities").fetchall(), [("2026-12",)])

    def test_first_monthly_priority_survives_deletion_of_first_submission(self):
        first = self.submit({1: "17:00-21:00"})
        self.submit({20: "09:00-15:00"}, name="仮名B")
        self.submit({20: "17:00-21:00"})
        self.delete({"kind": "submissions", "month": "2026-11", "part": "first"})
        self.assertNotIn(first, [row[0] for row in self.records()])
        self.assertEqual([row["name"] for row in self.day(20)["rows"]], ["仮名 A", "仮名B"])
        self.assertEqual(self.day(20)["rows"][0]["submission_order"], first)

    def test_roster_deletion_is_separate_from_submission_and_other_settings(self):
        self.submit({1: "17:00-21:00"})
        self.assertEqual(self.delete({"kind": "roster", "names": ["仮名A", "仮名C"]}).status_code, 200)
        self.assertEqual(self.client.get("/api/roster").json["roster"], {"kitchen": ["仮名B"], "hall": []})
        self.assertEqual(len(self.records()), 1)
        self.assertIn("未登録", self.day(1)["rows"][0]["issue"])
        self.assertTrue(self.client.get("/api/excel-settings").json["settings"]["repair_blank_formulas"])

    def test_stale_roster_or_submission_preview_is_rejected(self):
        target = {"kind": "roster", "names": ["仮名 A"]}
        preview = self.post("/api/data-deletion-preview", target).json
        self.post("/api/roster", {"kitchen": ["仮名 A", "追加者"], "hall": []})
        self.assertEqual(self.post("/api/data-delete", {**target, "preview_token": preview["preview_token"], "confirmed": True}).status_code, 400)
        self.submit({1: "17:00-21:00"})
        target = {"kind": "submissions", "month": "2026-11", "part": "first"}
        preview = self.post("/api/data-deletion-preview", target).json
        self.submit({2: "17:00-21:00"})
        self.assertEqual(self.post("/api/data-delete", {**target, "preview_token": preview["preview_token"], "confirmed": True}).status_code, 400)
        self.assertEqual(len(self.records()), 2)

    def test_invalid_settings_targets_and_cross_origin_do_not_delete(self):
        self.submit({1: "17:00-21:00"})
        for payload in [self.settings_data(days=0), self.settings_data(days=True), self.settings_data(days="60"),
                        {"retention_days": 60, "auto_months": [None]}, self.settings_data(["2026-13"]), self.settings_data(["9999-12"])]:
            self.assertEqual(self.post("/api/retention-preview", payload).status_code, 400, payload)
        for target in [{"kind": "other"}, {"kind": "roster", "names": []}, {"kind": "roster", "names": ["未登録"]},
                       {"kind": "submissions", "month": "2026-11", "part": []}]:
            self.assertEqual(self.post("/api/data-deletion-preview", target).status_code, 400)
        target = {"kind": "submissions", "month": "2026-11", "part": "all"}
        preview = self.post("/api/data-deletion-preview", target).json
        result = self.client.post("/api/data-delete", json={**target, "preview_token": preview["preview_token"], "confirmed": True}, headers={"Origin": "https://other.invalid"})
        self.assertEqual(result.status_code, 403)
        self.assertEqual(len(self.records()), 1)

    def test_public_routes_require_admin_login(self):
        client = create_app({**self.config, "PUBLIC": True, "ADMIN_PASSWORD": "long-test-admin-password", "INVITE_CODE": "long-test-invite-code"}).test_client()
        self.assertEqual(client.get("/api/retention-settings").status_code, 401)
        for route in ["/api/retention-preview", "/api/retention-settings", "/api/data-deletion-preview", "/api/data-delete"]:
            self.assertEqual(client.post(route, json={}, headers={"Origin": "http://localhost"}).status_code, 401)

    def test_extreme_month_does_not_block_settings_or_manual_deletion(self):
        self.submit({1: "17:00-21:00"}, month="9999-12")
        result = self.client.get("/api/retention-settings")
        self.assertEqual(result.status_code, 200)
        self.assertIsNone(result.json["months"][0]["second_delete_on"])
        self.assertEqual(self.delete({"kind": "submissions", "month": "9999-12", "part": "all"}).status_code, 200)
        self.assertEqual(self.records(), [])


if __name__ == "__main__":
    unittest.main()
