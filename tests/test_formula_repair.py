import base64
from io import BytesIO
import tempfile
import unittest
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from openpyxl import load_workbook
from openpyxl.styles import PatternFill
from openpyxl.formatting.rule import CellIsRule
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from app import create_app
from daily_shifts import MAIN_NS, sample_daily_book


class FormulaRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app({"TESTING": True, "PUBLIC": False, "DATA_DIR": self.temp.name})
        self.client = self.app.test_client()
        self.post("/api/roster", {"kitchen": ["厨房 仮名"], "hall": ["ホール 仮名"]})
        for name in ["ホール 仮名", "厨房 仮名"]:
            self.post("/api/submit", {"key": self.app.config["INVITE_CODE"], "name": name, "month": "2026-11", "days": [{"date": "2026-11-20", "shift": "09:00-21:00"}]})
        self.source = sample_daily_book("2026-11-20")

    def tearDown(self):
        self.temp.cleanup()

    def post(self, path, data):
        return self.client.post(path, json=data, headers={"Origin": "http://localhost"})

    def change(self, edits=None, callback=None):
        book = load_workbook(BytesIO(self.source))
        for address, value in (edits or {}).items():
            book.active[address] = value
        if callback:
            callback(book.active)
        out = BytesIO(); book.save(out); book.close()
        self.source = out.getvalue()

    def data(self, enabled=True):
        result = self.client.get("/api/daily-candidates?day=2026-11-20").json
        ids = [p["id"] for p in result["rows"]]
        return {"file": base64.b64encode(self.source).decode(), "sheet": "日別ベータ", "day": "2026-11-20", "date_confirmed": True, "version": result["version"], "selected_ids": ids, "breaks": {str(i): {"mode": "none"} for i in ids}, "repair_blank_formulas": enabled}

    def preview(self, data):
        result = self.post("/api/daily-preview", data)
        self.assertEqual(result.status_code, 200, result.json)
        return result.json

    def export(self, data, preview, confirmed=True):
        return self.post("/api/daily-export", {**data, "plan_token": preview["plan_token"], "formula_repairs_confirmed": confirmed})

    def test_specific_cells_use_their_own_row_and_column_and_require_confirmation(self):
        self.change({"L34": None, "M34": None, "BW15": None})
        denied = self.post("/api/daily-preview", self.data(False))
        self.assertEqual(denied.status_code, 400)
        self.assertIn("補完", denied.json["error"])
        data = self.data(); plan = self.preview(data)
        self.assertEqual([p["cell"] for p in plan["formula_repairs"]], ["BW15", "L34", "M34"])
        self.assertEqual(self.export(data, plan, False).status_code, 400)
        result = self.export(data, plan)
        self.assertEqual(result.status_code, 200, result.json if result.is_json else "")
        after = load_workbook(BytesIO(result.data)); before = load_workbook(BytesIO(self.source))
        self.assertEqual(after.active["L34"].value, '=IF(AND($J34<=L$56,$K34>L$56),"・・",IF(AND($F34<=L$56,$G34>L$56),"・",""))')
        self.assertEqual(after.active["M34"].value, '=IF(AND($J34<=M$56,$K34>M$56),"・・",IF(AND($F34<=M$56,$G34>M$56),"・",""))')
        self.assertEqual(after.active["BW15"].value, '=IF(AND($J15<=BW$56,$K15>BW$56),"・・",IF(AND($F15<=BW$56,$G15>BW$56),"・",""))')
        for row in before.active:
            for cell in row:
                if cell.data_type == "f":
                    self.assertEqual(after.active[cell.coordinate].value, cell.value)
        self.assertIsNone(before.active["L34"].value)
        self.assertIsNone(after.active["L35"].value)  # 従業員枠の下半分は対象外。
        after.close(); before.close()

    def test_whole_blank_band_restored_without_filling_name_or_time_inputs_with_formulas(self):
        self.change({f"{get_column_letter(col)}34": None for col in range(12, 76)})
        data = self.data(); plan = self.preview(data)
        self.assertEqual(len(plan["formula_repairs"]), 64)
        result = self.export(data, plan)
        self.assertEqual(result.status_code, 200)
        book = load_workbook(BytesIO(result.data))
        for col in range(12, 76):
            self.assertEqual(book.active.cell(34, col).data_type, "f")
        self.assertEqual(book.active["C34"].value, "ホール 仮名")
        for address in ["C34", "D34", "E34", "H34", "I34"]:
            self.assertNotEqual(book.active[address].data_type, "f")
        book.close()

    def test_nonblank_different_formula_or_value_is_never_overwritten(self):
        original = self.source
        for bad in ["=1+1", "・", " "]:
            self.source = original
            self.change({"L34": None, "M34": bad})
            result = self.post("/api/daily-preview", self.data())
            self.assertEqual(result.status_code, 400)
            self.assertIn("M34", result.json["error"])

    def test_axis_wrong_header_step_unsupported_formula_and_cycle_block(self):
        original = self.source
        for address, bad in [("L14", 10/24), ("L33", 8/24), ("M56", 10/24), ("L56", "=SUM(L5,1)"), ("L56", "=L56")]:
            self.source = original
            self.change({"L34": None, address: bad})
            result = self.post("/api/daily-preview", self.data())
            self.assertEqual(result.status_code, 400, address)
            self.assertIn(address, result.json["error"])

    def test_missing_total_or_helper_formula_is_not_repaired(self):
        original = self.source
        for address in ["BX34", "L54", "L31", "F34"]:
            self.source = original
            self.change({"L34": None, address: None})
            result = self.post("/api/daily-preview", self.data())
            self.assertEqual(result.status_code, 400)
            self.assertIn(address, result.json["error"])

    def test_valid_shifted_axis_accepts_excel_decimal_rounding(self):
        def shift_axis(sheet):
            sheet["L5"] = 0.3958333333  # 09:30。Excelの時刻シリアルの丸め誤差。
            sheet["L5"].number_format = "General"
            for column in range(12, 76):
                start = 9.5 * 3600 + (column - 12) * 900
                for row in (14, 33):
                    sheet.cell(row, column).value = start / 86400
                sheet.cell(56, column).value = (start + 1) / 86400
        self.change({"L34": None}, shift_axis)
        for name in ["厨房 仮名", "ホール 仮名"]:
            self.post("/api/submit", {"key": self.app.config["INVITE_CODE"], "name": name, "month": "2026-11", "days": [{"date": "2026-11-20", "shift": "10:00-21:00"}]})
        self.assertEqual([item["cell"] for item in self.preview(self.data())["formula_repairs"]], ["L34"])

    def test_missing_or_different_coloring_and_merged_band_block(self):
        original = self.source
        for mode in ["missing", "red", "merge", "number_format"]:
            self.source = original
            def modify(sheet):
                if mode == "missing":
                    sheet.conditional_formatting._cf_rules.clear()
                elif mode == "red":
                    for region in sheet.conditional_formatting:
                        for rule in sheet.conditional_formatting[region]:
                            rule.dxf.fill = PatternFill("solid", fgColor="FFFF0000")
                elif mode == "merge":
                    sheet.merge_cells("L34:M34")
                else:
                    sheet["L34"].number_format = "General"
            self.change({"L34": None}, modify)
            self.assertEqual(self.post("/api/daily-preview", self.data()).status_code, 400, mode)

    def test_other_zip_parts_formatting_and_validation_remain_unchanged(self):
        def add_validation(sheet):
            validation = DataValidation(type="list", formula1='"厨房 仮名,ホール 仮名"')
            sheet.add_data_validation(validation); validation.add("C15")
            sheet.conditional_formatting.add("A1", CellIsRule(operator="equal", formula=['"・"'], fill=PatternFill("solid", fgColor="FFFF0000")))
            other = sheet.parent.create_sheet("別の表")
            other["A1"] = "=1+1"
        self.change({"L34": None}, add_validation)
        data = self.data(); result = self.export(data, self.preview(data))
        self.assertEqual(result.status_code, 200)
        with ZipFile(BytesIO(self.source)) as before, ZipFile(BytesIO(result.data)) as after:
            self.assertEqual(before.namelist(), after.namelist())
            for name in before.namelist():
                if name not in {"xl/workbook.xml", "xl/worksheets/sheet1.xml"}:
                    self.assertEqual(before.read(name), after.read(name), name)
            old, new = [ET.fromstring(z.read("xl/worksheets/sheet1.xml")) for z in (before, after)]
            for tag in ["conditionalFormatting", "mergeCells", "dataValidations"]:
                self.assertEqual([ET.tostring(n) for n in old.iter(f"{{{MAIN_NS}}}{tag}")], [ET.tostring(n) for n in new.iter(f"{{{MAIN_NS}}}{tag}")])

    def test_repair_repeat_does_not_change_existing_formulas_again(self):
        self.change({"L34": None})
        data = self.data(); result = self.export(data, self.preview(data))
        self.source = result.data
        data = self.data(False); plan = self.preview(data)
        self.assertEqual(plan["formula_repairs"], [])
        self.assertEqual(self.export(data, plan, False).status_code, 200)


if __name__ == "__main__":
    unittest.main()
