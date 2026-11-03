"""既知の日別表で、空白になった勤務帯の式だけを補完する。"""
from datetime import datetime, time, timedelta
import math
import re

from openpyxl.cell.cell import MergedCell
from openpyxl.utils import get_column_letter
from openpyxl.utils.datetime import to_excel

from shifts import InputError


def band_formula(row, column):
    col = get_column_letter(column) if isinstance(column, int) else column
    return f'=IF(AND($J{row}<={col}$56,$K{row}>{col}$56),"・・",IF(AND($F{row}<={col}$56,$G{row}>{col}$56),"・",""))'


def axis_seconds(sheet, address, visiting=None):
    """既知の時刻・参照・時刻加算だけを読む。任意のExcel式は実行しない。"""
    visiting = set() if visiting is None else visiting
    if address in visiting:
        raise InputError(f"時間目盛りの{address}に循環参照があります。")
    visiting = visiting | {address}
    value = sheet[address].value
    if isinstance(value, datetime):
        # 24時以降の時刻が、Excelの基準日付きで読み込まれる場合。
        serial = to_excel(value, epoch=sheet.parent.epoch)
        if 0 <= serial < 2:
            return serial * 86400
    if isinstance(value, time):
        return value.hour * 3600 + value.minute * 60 + value.second
    if isinstance(value, timedelta):
        return value.total_seconds()
    if type(value) in (int, float) and math.isfinite(value) and 0 <= value < 2:
        return value * 86400
    if isinstance(value, str):
        match = re.fullmatch(r'=\$?([A-Z]{1,3})\$?(5|14|33|56)(?:\+"([0-9]{1,2}):([0-9]{1,2})(?::([0-9]{1,2}))?")?', value)
        if match:
            col, row, hours, minutes, seconds = match.groups()
            if not 12 <= sheet[f"{col}{row}"].column <= 75:
                raise InputError(f"{address}が時間目盛り以外を参照しています。")
            result = axis_seconds(sheet, f"{col}{row}", visiting)
            if hours is not None:
                if int(minutes) > 59 or int(seconds or 0) > 59:
                    raise InputError(f"{address}の時間加算が確認できません。")
                result += int(hours) * 3600 + int(minutes) * 60 + int(seconds or 0)
            return result
    raise InputError(f"時間目盛りの{address}が未設定か、未対応の式です。補完を止めました。")


def same_clock(actual, expected):
    # 24時以降の表示は、経過時間と0時に戻る表記の両方を許容。
    return abs(actual - expected) < 0.01 or (expected >= 86400 and abs(actual - expected % 86400) < 0.01)


def verify_axis(sheet):
    start = axis_seconds(sheet, "L5")
    if not 0 <= start < 86400 or abs(start - round(start / 900) * 900) > 0.01:
        raise InputError("L5の表示開始時刻を15分単位で確認できません。")
    for column in range(12, 76):
        col = get_column_letter(column)
        expected = start + (column - 12) * 900
        if not same_clock(axis_seconds(sheet, f"{col}56"), expected + 1):
            raise InputError(f"{col}56の内部時間目盛りが15分刻みと一致しません。補完を止めました。")
        for row in (14, 33):
            cell = sheet[f"{col}{row}"]
            # Book3の見出しは1時間ごとに4セルを結合。サンプルは15分ごと。
            if isinstance(cell, MergedCell):
                continue
            if not same_clock(axis_seconds(sheet, cell.coordinate), expected):
                raise InputError(f"{cell.coordinate}の表示時刻と内部時間目盛りが一致しません。")


def known_work_fill(fill):
    if fill is None:
        return False
    fg, bg = fill.fgColor, fill.bgColor
    if fill.patternType == "solid":
        return ((fg.type == "rgb" and fg.rgb[-6:].upper() in {"000000", "969696"})
                or (fg.type == "indexed" and fg.indexed in {8, 55}))
    return (fill.patternType is None and fg.type == "rgb" and fg.rgb[-6:] == "000000"
            and bg.type == "indexed" and bg.indexed == 55)


def verify_display_and_totals(sheet, rows):
    valid_ranges = []
    for region in sheet.conditional_formatting:
        if not any(area.min_col <= 75 and area.max_col >= 12 and any(area.min_row <= row <= area.max_row for row in rows) for area in region.sqref.ranges):
            continue
        for rule in sheet.conditional_formatting[region]:
            if rule.type == "cellIs" and rule.operator == "equal" and rule.formula == ['"・"']:
                if not rule.dxf or not known_work_fill(rule.dxf.fill):
                    raise InputError("勤務の目印を塗る色設定が確認済みのものと違います。補完を止めました。")
                valid_ranges.append(region.sqref)
    for row in rows:
        expected = f'=COUNTIF(L{row}:BW{row},"・")/4'
        if sheet[f"BX{row}"].value != expected:
            raise InputError(f"BX{row}の勤務時間の集計式が未設定か、確認済みの形式と違います。勤務帯だけの補完は行いません。")
        for column in range(12, 76):
            address = f"{get_column_letter(column)}{row}"
            if sheet[address].number_format != ";;;":
                raise InputError(f"{address}の勤務の目印を隠す書式が確認できません。表示設定を確認してください。")
            if not any(address in region for region in valid_ranges):
                raise InputError(f"{address}を勤務中に塗る表示設定が見つかりません。補完を止めました。")
    for column in range(12, 76):
        col = get_column_letter(column)
        for total_row, first, last in ((31, 15, 30), (54, 34, 53)):
            if sheet[f"{col}{total_row}"].value != f'=COUNTIF({col}{first}:{col}{last},"・")':
                raise InputError(f"{col}{total_row}の人数集計式が未設定か、確認済みの形式と違います。")


def plan_band_repairs(sheet, rows, enabled=False):
    repairs = []
    merged_targets = set()
    for region in sheet.merged_cells.ranges:
        for row in rows:
            if region.min_row <= row <= region.max_row:
                merged_targets.update((row, col) for col in range(max(12, region.min_col), min(75, region.max_col) + 1))
    for row in rows:
        for column in range(12, 76):
            cell = sheet.cell(row, column)
            if isinstance(cell, MergedCell) or (row, column) in merged_targets:
                raise InputError(f"{get_column_letter(column)}{row}の勤務帯が結合されています。補完対象として確認できません。")
            expected = band_formula(row, column)
            if cell.value == expected:
                continue
            if cell.value in (None, ""):
                if not enabled:
                    raise InputError(f"{cell.coordinate}の勤務帯の計算式が空白です。設定ページで「空白の勤務帯数式を自動補完する」をONにすると、配置と目盛りを確認して補完できます。")
                repairs.append({"cell": cell.coordinate, "formula": expected,
                                "role": "キッチン" if row < 31 else "ホール", "name_cell": f"C{row}"})
            else:
                raise InputError(f"{cell.coordinate}の計算式が確認済みの形式と違います。既存の式・値は変更せず停止しました。")
    if repairs:
        verify_axis(sheet)
        verify_display_and_totals(sheet, rows)
    return repairs
