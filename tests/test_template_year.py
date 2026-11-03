import base64
from io import BytesIO
import tempfile
import unittest

from openpyxl import load_workbook

from app import create_app
from daily_shifts import sample_daily_book


class TemplateYearTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.client = create_app({"TESTING": True, "PUBLIC": False, "DATA_DIR": self.temp.name}).test_client()
        self.post("/api/roster", {"kitchen": ["テスト 太郎"], "hall": []})
        for year, shift in [(2023, "09:00-15:00"), (2026, "17:00-21:00")]:
            self.post("/api/submit", {"key": "local-demo-only", "name": "テスト 太郎", "month": f"{year}-11", "days": [{"date": f"{year}-11-20", "shift": shift}]})
        self.book = load_workbook(BytesIO(sample_daily_book("2026-11-20")))
        self.book.active.title = "2週（火）"
        self.book.active["C4"] = 2023

    def tearDown(self):
        self.book.close()
        self.temp.cleanup()

    def post(self, path, data):
        return self.client.post(path, json=data, headers={"Origin": "http://localhost"})

    def data(self):
        output = BytesIO(); self.book.save(output)
        result = self.client.get("/api/daily-candidates?day=2026-11-20").json
        ids = [p["id"] for p in result["rows"]]
        return {"file": base64.b64encode(output.getvalue()).decode(), "sheet": self.book.active.title, "day": "2026-11-20", "date_confirmed": True, "version": result["version"], "selected_ids": ids, "breaks": {str(i): {"mode": "none"} for i in ids}}

    def test_old_year_template_accepts_selected_year_and_preserves_source_year(self):
        data = self.data(); preview = self.post("/api/daily-preview", data)
        self.assertEqual(preview.status_code, 200, preview.json)
        self.assertEqual(preview.json["placements"][0]["date"], "2026-11-20")
        self.assertEqual(preview.json["placements"][0]["shift"], "17:00-21:00")
        self.assertFalse(preview.json["date_missing"])
        result = self.post("/api/daily-export", {**data, "plan_token": preview.json["plan_token"]})
        self.assertEqual(result.status_code, 200)
        after = load_workbook(BytesIO(result.data))
        self.assertEqual(after.active["C4"].value, 2023)
        self.assertEqual(after.active["E5"].value, "=DATE(C4,B5,B9)")
        self.assertEqual(str(after.active["D15"].value), "17:00:00")
        after.close()

    def test_month_or_day_mismatch_still_blocks(self):
        for address, bad in [("B5", 12), ("B9", 21)]:
            previous = self.book.active[address].value
            self.book.active[address] = bad
            result = self.post("/api/daily-preview", self.data())
            self.assertEqual(result.status_code, 400)
            self.assertIn("Excelの月日", result.json["error"])
            self.book.active[address] = previous

    def test_blank_year_does_not_make_known_month_day_unknown(self):
        self.book.active["C4"] = None
        data = self.data(); result = self.post("/api/daily-preview", data)
        self.assertEqual(result.status_code, 200)
        self.assertFalse(result.json["date_missing"])
        data["date_confirmed"] = False
        self.assertEqual(self.post("/api/daily-preview", data).status_code, 400)

    def test_unknown_month_day_still_requires_confirmation(self):
        self.book.active["B5"] = None; self.book.active["B9"] = None
        data = self.data(); result = self.post("/api/daily-preview", data)
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json["date_missing"])
        data["date_confirmed"] = False
        self.assertEqual(self.post("/api/daily-preview", data).status_code, 400)


if __name__ == "__main__":
    unittest.main()
