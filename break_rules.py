"""保存した手動/自動モードと、平日・土日祝の固定休憩。"""
import jpholiday

from daily_shifts import minutes, target_day
from shifts import InputError


def parse_break_settings(value):
    if not isinstance(value, dict) or value.get("mode") not in {"manual", "auto"}:
        raise InputError("管理者が指定／固定休憩を自動入力、のどちらかを選んでください。")
    result = {"mode": value["mode"]}
    for key, label in (("weekday", "平日"), ("weekend", "土日祝")):
        rule = value.get(key, {"mode": "unset"})
        if not isinstance(rule, dict) or rule.get("mode") not in {"unset", "none", "time"}:
            raise InputError(f"{label}の休憩設定を確認してください。")
        mode = rule["mode"]
        if mode == "time":
            start, end = rule.get("start"), rule.get("end")
            if minutes(start) >= minutes(end):
                raise InputError(f"{label}の休憩終了は開始より後にしてください。")
            result[key] = {"mode": "time", "start": start, "end": end}
        else:
            if mode == "unset" and value["mode"] == "auto":
                raise InputError(f"自動モードを使うには{label}の休憩を設定してください。")
            result[key] = {"mode": mode}
    return result


def default_break_settings():
    return {"mode": "manual", "weekday": {"mode": "unset"}, "weekend": {"mode": "unset"}}


def classify_day(value):
    day = target_day(value)
    holiday = jpholiday.is_holiday_name(day)
    weekend = day.weekday() >= 5
    return {"category": "weekend" if weekend or holiday else "weekday",
            "label": "土日祝" if weekend or holiday else "平日",
            "holiday": holiday or "", "date": value}


def automatic_break(person, rule):
    if rule["mode"] == "none":
        return {"mode": "none"}, "固定設定：休憩なし", ""
    try:
        start, end = map(minutes, person["shift"].split("-"))
        rs, re = minutes(rule["start"]), minutes(rule["end"])
    except (InputError, KeyError, ValueError):
        return {}, "", "勤務時刻と固定休憩を確認してください。"
    if end <= rs or start >= re:
        return {"mode": "none"}, "固定休憩の時間帯と重ならないため休憩なし", ""
    if start <= rs < re <= end and (rs - start) + (end - re) > 0:
        return {"mode": "time", "start": rule["start"], "end": rule["end"]}, "保存した固定休憩を適用", ""
    return {}, "", "勤務と固定休憩が一部だけ重なるか、勤務全体が休憩になります。手動モードで休憩を確認してください。"


def apply_break_rules(people, settings, day):
    info = classify_day(day)
    rows = []
    for person in people:
        rest, note, issue = {}, "", ""
        if settings["mode"] == "auto" and not person["issue"]:
            rest, note, issue = automatic_break(person, settings[info["category"]])
        rows.append({**person, "automatic_break": rest, "break_note": note, "break_issue": issue})
    return rows, info
