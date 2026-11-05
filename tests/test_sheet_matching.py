import base64
from copy import deepcopy
from io import BytesIO
import tempfile
import unittest
from zipfile import ZipFile

from openpyxl import load_workbook

from app import create_app
from daily_shifts import sample_daily_book
from sheet_matching import inspect_sheets


class SheetMatchingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.client = create_app({"TESTING": True, "PUBLIC": False, "DATA_DIR": self.temp.name}).test_client()
        self.book = load_workbook(BytesIO(sample_daily_book("2026-11-10")))
        self.sheet = self.book.active
        self.sheet.title = "第２週火曜日"
        self.sheet["C4"] = 2023
        self.post("/api/roster", {"kitchen": ["仮名"], "hall": []})
        self.post("/api/submit", {"key": "local-demo-only", "name": "仮名", "month": "2026-11", "days": [{"date": day, "shift": "17:00-21:00"} for day in ["2026-11-10", "2026-11-24"]]})

    def tearDown(self):
        self.book.close(); self.temp.cleanup()

    def post(self, path, data):
        return self.client.post(path, json=data, headers={"Origin": "http://localhost"})

    def encoded(self):
        out = BytesIO(); self.book.save(out)
        return base64.b64encode(out.getvalue()).decode()

    def data(self, day="2026-11-10", confirmed=False):
        candidates = self.client.get(f"/api/daily-candidates?day={day}").json
        ids = [p["id"] for p in candidates["rows"]]
        return {"file": self.encoded(), "context_month": day[:7], "sheet": self.sheet.title, "day": day, "date_confirmed": True, "sheet_review_confirmed": confirmed, "version": candidates["version"], "selected_ids": ids, "breaks": {str(i): {"mode": "none"} for i in ids}}

    def report(self, context="2026-11"):
        return next(row for row in inspect_sheets(self.book, context)["candidates"] if row["sheet"] == self.sheet.title)

    def copy_sheet(self, name, day, weekday):
        sheet = self.book.copy_worksheet(self.sheet)
        sheet.conditional_formatting = deepcopy(self.sheet.conditional_formatting)
        sheet.title = name; sheet["B9"] = day; sheet["E9"] = weekday
        return sheet

    def test_unordered_workbook_filters_by_content_and_keeps_dates_in_order(self):
        staff = self.book.create_sheet("スタッフ一覧"); staff["A1"] = "氏名"
        unknown = self.book.create_sheet("よく分からない表"); unknown["A1"] = "売上"
        thursday = self.copy_sheet("第2週木曜日", 12, "木")
        monday = self.copy_sheet("第一週月曜日", 2, "月")
        tuesday = self.copy_sheet("１週（火）", 3, "火")
        self.book._sheets = [staff, thursday, unknown, tuesday, self.sheet, monday]
        response = self.post("/api/daily-inspect", {"file": self.encoded(), "context_month": "2026-11"})
        self.assertEqual(response.status_code, 200)
        result = response.json
        self.assertEqual([r["date"] for r in result["candidates"]], ["2026-11-02", "2026-11-03", "2026-11-10", "2026-11-12"])
        self.assertTrue(all(r["automatic"] for r in result["candidates"]))
        self.assertEqual({r["sheet"] for r in result["excluded"]}, {"スタッフ一覧", "よく分からない表"})

    def test_nth_weekday_definition_and_fullwidth_kanji_labels(self):
        self.sheet["B9"] = None
        for name in ["第２週火曜日", "2週（火）", "第二週火曜日", "第2週 火曜", "２週目の火曜"]:
            self.sheet.title = name
            report = self.report("2022-11")  # 1日が火曜の例。第2火曜は8日。
            self.assertEqual(report["date"], "2022-11-08", name)
            self.assertFalse(report["automatic"])
        self.sheet.title = "第一週月曜日"; self.sheet["E9"] = "月"
        self.assertEqual(self.report("2022-11")["date"], "2022-11-07")

    def test_e9_weekday_conflict_requires_explicit_review(self):
        self.sheet["E9"] = "水"
        report = self.report()
        self.assertEqual(report["date"], "2026-11-10")
        self.assertFalse(report["automatic"])
        self.assertTrue(any("E9" in issue for issue in report["issues"]))
        self.assertEqual(self.post("/api/daily-preview", self.data()).status_code, 400)
        self.assertEqual(self.post("/api/daily-preview", self.data(confirmed=True)).status_code, 200)

    def test_missing_weekday_requires_review_instead_of_skipping_check(self):
        self.sheet["E9"] = None
        self.assertFalse(self.report()["automatic"])
        self.assertEqual(self.post("/api/daily-preview", self.data()).status_code, 400)
        self.assertEqual(self.post("/api/daily-preview", self.data(confirmed=True)).status_code, 200)

    def test_title_week_and_weekday_conflict_does_not_change_literal_date(self):
        self.sheet.title = "第2週水曜日"
        report = self.report()
        self.assertEqual(report["date"], "2026-11-10")
        self.assertFalse(report["automatic"])
        self.assertTrue(any("2026-11-11" in issue for issue in report["issues"]))
        self.assertEqual(self.post("/api/daily-preview", self.data()).status_code, 400)

    def test_duplicate_dates_require_choice_and_do_not_reorder_or_edit_other_tabs(self):
        other = self.copy_sheet("2週（火）コピー", 10, "火")
        staff = self.book.create_sheet("スタッフ一覧"); staff["A1"] = "原本の仮名"
        self.book._sheets = [staff, other, self.sheet]
        report = self.report()
        self.assertIn(other.title, report["duplicates"])
        self.assertFalse(report["automatic"])
        self.assertEqual(self.post("/api/daily-preview", self.data()).status_code, 400)
        data = self.data(confirmed=True)
        preview = self.post("/api/daily-preview", data)
        self.assertEqual(preview.status_code, 200, preview.json)
        output = self.post("/api/daily-export", {**data, "plan_token": preview.json["plan_token"]})
        self.assertEqual(output.status_code, 200)
        before = base64.b64decode(data["file"])
        with ZipFile(BytesIO(before)) as old, ZipFile(BytesIO(output.data)) as new:
            self.assertEqual(old.namelist(), new.namelist())
            # 選択した表は3枚目。スタッフ一覧と別のシフト表はそのまま残る。
            self.assertEqual(old.read("xl/worksheets/sheet1.xml"), new.read("xl/worksheets/sheet1.xml"))
            self.assertEqual(old.read("xl/worksheets/sheet2.xml"), new.read("xl/worksheets/sheet2.xml"))
        after = load_workbook(BytesIO(output.data))
        self.assertEqual(after.sheetnames, self.book.sheetnames)
        self.assertEqual(after[self.sheet.title]["C15"].value, "仮名")
        self.assertEqual(after[self.sheet.title]["C4"].value, 2023)
        after.close()

    def test_missing_day_infers_title_date_but_requires_review(self):
        self.sheet["B9"] = None
        report = self.report()
        self.assertEqual(report["date"], "2026-11-10")
        self.assertFalse(report["automatic"])
        self.assertEqual(self.post("/api/daily-preview", self.data()).status_code, 400)
        self.assertEqual(self.post("/api/daily-preview", self.data(confirmed=True)).status_code, 200)

    def test_missing_month_and_unknown_title_can_use_manual_date_after_review(self):
        self.sheet["B5"] = None; self.sheet["B9"] = None; self.sheet.title = "日別入力"
        self.assertIsNone(self.report()["date"])
        self.assertEqual(self.post("/api/daily-preview", self.data()).status_code, 400)
        self.assertEqual(self.post("/api/daily-preview", self.data(confirmed=True)).status_code, 200)

    def test_nonexistent_calendar_day_blocks_even_with_confirmation(self):
        self.sheet["B5"] = 2; self.sheet["B9"] = 30
        report = self.report("2026-02")
        self.assertTrue(report["blocked"])
        self.assertIsNone(report["date"])
        data = self.data(confirmed=True); data["context_month"] = "2026-02"; data["day"] = "2026-02-20"
        data["version"] = self.client.get("/api/daily-candidates?day=2026-02-20").json["version"]
        result = self.post("/api/daily-preview", data)
        self.assertEqual(result.status_code, 400)
        self.assertIn("存在しません", result.json["error"])

    def test_missing_fifth_weekday_does_not_roll_into_next_month(self):
        self.sheet.title = "第5週火曜日"; self.sheet["B9"] = None
        report = self.report()
        self.assertIsNone(report["date"])
        self.assertTrue(any("ありません" in issue for issue in report["issues"]))
        self.assertFalse(report["automatic"])
        self.assertEqual(self.post("/api/daily-preview", self.data("2026-11-24")).status_code, 400)
        self.assertEqual(self.post("/api/daily-preview", self.data("2026-11-24", confirmed=True)).status_code, 200)

    def test_unknown_layout_hidden_and_protected_sheets_are_not_candidates(self):
        unknown = self.copy_sheet("第一週月曜日", 2, "月"); unknown["C14"] = "別の配置"
        hidden = self.copy_sheet("非表示シフト", 10, "火"); hidden.sheet_state = "hidden"
        protected = self.copy_sheet("保護シフト", 10, "火"); protected.protection.sheet = True
        result = inspect_sheets(self.book, "2026-11")
        self.assertEqual([r["sheet"] for r in result["candidates"]], [self.sheet.title])
        self.assertEqual(len(result["excluded"]), 3)

    def test_literal_month_day_and_e9_accept_fullwidth_text(self):
        self.sheet["B5"] = "１１月"; self.sheet["B9"] = "１０日"; self.sheet["E9"] = "（火）"
        self.assertTrue(self.report()["automatic"])
        self.assertEqual(self.post("/api/daily-preview", self.data()).status_code, 200)

    def test_same_title_interpretation_with_different_dates_is_flagged(self):
        other = self.copy_sheet("2週（火）別紙", 17, "火")
        report = self.report()
        self.assertIn(other.title, report["duplicates"])
        self.assertFalse(report["automatic"])

    def test_old_year_weekday_formula_requires_review_not_year_matching(self):
        self.sheet["E9"] = '=TEXT(E5,"aaa")'
        report = self.report()
        self.assertEqual(report["date"], "2026-11-10")
        self.assertFalse(report["automatic"])
        self.assertEqual(self.post("/api/daily-preview", self.data(confirmed=True)).status_code, 200)

    def test_review_does_not_override_known_month_day_or_context_mismatch(self):
        data = self.data(confirmed=True); data["day"] = "2026-11-24"
        result = self.client.get("/api/daily-candidates?day=2026-11-24").json
        data["version"] = result["version"]
        self.assertEqual(self.post("/api/daily-preview", data).status_code, 400)
        data = self.data(confirmed=True); data["context_month"] = "2025-11"
        self.assertEqual(self.post("/api/daily-preview", data).status_code, 400)


if __name__ == "__main__":
    unittest.main()
