"""タブの並び順や古い西暦を使わず、日別シフトと対象日を確認する。"""
import calendar
from datetime import date
import re
import unicodedata

from shifts import InputError, month_parts, normalize_numeric

WEEKDAYS = "月火水木金土日"
KANJI_NUMBERS = {c: i for i, c in enumerate("一二三四五六七八九十", 1)}
TITLE_PATTERN = re.compile(r"第?([0-9]+|[一二三四五六七八九十]+)週(?:目)?(?:の)?[（(]?([月火水木金土日])(?:曜日|曜)?[）)]?")


def normalized(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or "")))


def parse_title(title):
    text = normalized(title)
    matches = list(TITLE_PATTERN.finditer(text))
    if len(matches) > 1:
        return {"occurrence": None, "weekday": None, "issue": "タブ名に複数の週・曜日があり、日付を特定できません。"}
    if matches:
        count_text, weekday = matches[0].groups()
        count = int(count_text) if count_text.isdecimal() else KANJI_NUMBERS.get(count_text)
        return {"occurrence": count, "weekday": WEEKDAYS.index(weekday),
                "issue": "タブ名の週は1〜5回目で指定してください。" if count is None or not 1 <= count <= 5 else ""}
    weekdays = set(re.findall(r"([月火水木金土日])(?:曜日|曜)", text))
    weekdays.update(re.findall(r"[（(]([月火水木金土日])[）)]", text))
    return {"occurrence": None, "weekday": WEEKDAYS.index(next(iter(weekdays))) if len(weekdays) == 1 else None,
            "issue": "タブ名に複数の曜日があります。" if len(weekdays) > 1 else ""}


def nth_weekday(year, month, occurrence, weekday):
    if occurrence is None or not 1 <= occurrence <= 5:
        return None
    day = 1 + (weekday - date(year, month, 1).weekday()) % 7 + 7 * (occurrence - 1)
    return date(year, month, day) if day <= calendar.monthrange(year, month)[1] else None


def component(cell, label, maximum):
    value = cell.value
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, f"{cell.coordinate}の{label}が空欄です。", False
    if cell.data_type == "f":
        return None, f"{cell.coordinate}の{label}は数式のため、古い年の計算結果を使わず手動確認します。", False
    text = normalize_numeric(str(value)).strip()
    if isinstance(value, float) and value.is_integer():
        text = str(int(value))
    match = re.fullmatch(rf"([0-9]{{1,2}})(?:{label})?", text)
    if match and 1 <= int(match[1]) <= maximum:
        return int(match[1]), "", False
    return None, f"{cell.coordinate}の{label}を正しい数字で設定してください。", True


def daily_shape(sheet):
    if sheet.sheet_state != "visible":
        return False, "非表示のシートです。"
    if sheet.protection.sheet:
        return False, "保護されているシートです。"
    expected = {"C": "名前", "D": "開始時間", "E": "終了時間", "H": "休憩開始", "I": "休憩終了"}
    if sheet["B15"].value != "キッチン" or sheet["B34"].value not in {"フロア", "ホール"}:
        return False, "Book3型の所属欄を確認できません。スタッフ一覧・集計表などは候補に含めません。"
    if any(sheet[f"{col}{row}"].value != label for row in (14, 33) for col, label in expected.items()):
        return False, "Book3型の名前・勤務・休憩の見出し配置と異なります。"
    return True, ""


def describe_sheet(sheet, year, context_month):
    month, month_issue, bad_month = component(sheet["B5"], "月", 12)
    day_number, day_issue, bad_day = component(sheet["B9"], "日", 31)
    title = parse_title(sheet.title)
    issues = [message for message in (month_issue, day_issue, title["issue"]) if message]
    notes = ["ExcelのC4に書かれた年は照合していません。"]
    blocked = bad_month or bad_day
    resolved_month = month or context_month
    if month is not None and month != context_month:
        issues.append(f"B5は{month}月ですが、画面の対象年月は{context_month}月です。対象年月を合わせてください。")
    literal_date = None
    if month is not None and day_number is not None:
        try:
            literal_date = date(year, month, day_number)
        except ValueError:
            blocked = True
            issues.append(f"{year}年{month}月{day_number}日は存在しません。コピーの月・日を修正してください。")
    inferred = None
    if title["occurrence"] is not None and title["weekday"] is not None and not title["issue"]:
        inferred = nth_weekday(year, resolved_month, title["occurrence"], title["weekday"])
        if inferred is None:
            issues.append(f"{year}年{resolved_month}月には第{title['occurrence']}回の{WEEKDAYS[title['weekday']]}曜日がありません。翌月には進めません。")
    suggested = literal_date
    source = "B5・B9" if suggested else "手動確認"
    if suggested is None and not blocked:
        if day_number is not None:
            try:
                suggested = date(year, resolved_month, day_number)
                source = "対象年月・B9"
            except ValueError:
                blocked = True
                issues.append("画面の対象年月に、その日は存在しません。")
        elif inferred:
            suggested, source = inferred, "タブ名（その月の第N曜日）"
    raw_weekday = sheet["E9"].value
    weekday = None
    if raw_weekday is None or not str(raw_weekday).strip():
        issues.append("E9の曜日が空欄です。対象日の曜日を確認してください。")
    else:
        text = normalized(raw_weekday)
        match = re.fullmatch(r"[（(]?([月火水木金土日])(?:曜日|曜)?[）)]?", text)
        if sheet["E9"].data_type == "f":
            issues.append("E9の曜日は数式です。古い年による曜日のずれがあり得るため確認してください。")
        elif match:
            weekday = WEEKDAYS.index(match[1])
        else:
            issues.append("E9の曜日を読み取れません。対象日の曜日を確認してください。")
    if suggested:
        if weekday is not None and weekday != suggested.weekday():
            issues.append(f"E9は{WEEKDAYS[weekday]}曜日ですが、候補日{suggested.isoformat()}は{WEEKDAYS[suggested.weekday()]}曜日です。")
        if title["weekday"] is not None and title["weekday"] != suggested.weekday():
            issues.append("候補日の曜日とタブ名の曜日が一致しません。")
        if literal_date and inferred and literal_date != inferred:
            issues.append(f"B5・B9は{literal_date.isoformat()}、タブ名は{inferred.isoformat()}を表しています。")
    if suggested is None:
        issues.append("対象日を自動設定できません。日付を手入力し、シートとの対応を確認してください。")
    elif not issues:
        notes.append(f"{suggested.isoformat()}（{WEEKDAYS[suggested.weekday()]}）を対象日に設定できます。")
    return {"sheet": sheet.title, "date": suggested.isoformat() if suggested else None,
            "source": source, "month": month, "day": day_number, "weekday": weekday,
            "title_weekday": title["weekday"], "occurrence": title["occurrence"],
            "issues": list(dict.fromkeys(issues)), "notes": notes, "blocked": blocked,
            "date_missing": literal_date is None, "duplicates": []}


def inspect_sheets(book, context):
    year, month = month_parts(context)
    candidates, excluded = [], []
    for sheet in book.worksheets:
        accepted, reason = daily_shape(sheet)
        if accepted:
            candidates.append(describe_sheet(sheet, year, month))
        else:
            excluded.append({"sheet": sheet.title, "reason": reason})
    for candidate in candidates:
        for other in candidates:
            if candidate is other:
                continue
            same_date = candidate["date"] is not None and candidate["date"] == other["date"]
            same_label = (candidate["occurrence"] is not None and candidate["occurrence"] == other["occurrence"]
                          and candidate["title_weekday"] == other["title_weekday"]
                          and (candidate["month"] or month) == (other["month"] or month))
            if same_date or same_label:
                candidate["duplicates"].append(other["sheet"])
        if candidate["duplicates"]:
            candidate["issues"].append("同じ日付または同じ週・曜日の候補が複数あります：" + "、".join(candidate["duplicates"]))
        candidate["needs_review"] = bool(candidate["issues"])
        candidate["automatic"] = bool(candidate["date"] and not candidate["needs_review"] and not candidate["blocked"])
    candidates.sort(key=lambda item: (item["date"] or "9999-99-99", item["sheet"]))
    return {"context_month": f"{year:04}-{month:02}", "candidates": candidates, "excluded": excluded}


def validate_sheet_selection(book, data, selected_day):
    context = data.get("context_month", selected_day.strftime("%Y-%m"))
    year, month = month_parts(context)
    if (year, month) != (selected_day.year, selected_day.month):
        raise InputError("対象年月と対象日が違います。先に対象年月を合わせてください。")
    inspection = inspect_sheets(book, context)
    report = next((item for item in inspection["candidates"] if item["sheet"] == data.get("sheet")), None)
    if report is None:
        raise InputError("このシートは対応する日別シフト表として確認できません。シフト表の候補から選んでください。")
    if report["blocked"]:
        raise InputError("シートの日付を修正してください：" + "\n".join(report["issues"]))
    if report["month"] is not None and report["month"] != selected_day.month:
        raise InputError("Excelの月日と対象日が違います。B5の月または対象日を確認してください。")
    if report["day"] is not None and report["day"] != selected_day.day:
        raise InputError("Excelの月日と対象日が違います。B9の日または対象日を確認してください。")
    issues = list(report["issues"])
    if report["date"] and report["date"] != selected_day.isoformat():
        issues.append(f"候補日は{report['date']}ですが、手入力の対象日は{selected_day.isoformat()}です。")
    if report["weekday"] is not None and report["weekday"] != selected_day.weekday():
        issues.append("手入力の対象日とE9の曜日が一致しません。")
    if report["title_weekday"] is not None and report["title_weekday"] != selected_day.weekday():
        issues.append("手入力の対象日とタブ名の曜日が一致しません。")
    if issues and data.get("sheet_review_confirmed") is not True:
        raise InputError("シートと対象日の確認が必要です：" + "\n".join(dict.fromkeys(issues)))
    return {**report, "issues": list(dict.fromkeys(issues)), "needs_review": bool(issues), "selected_date": selected_day.isoformat()}
