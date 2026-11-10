"""提出の対象日を基準に、半月単位で履歴を削除する。"""
import calendar
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import hashlib
import hmac
import json
import sqlite3
import threading

from shifts import InputError, month_parts, normalize_name

JST = timezone(timedelta(hours=9))
DEFAULT_SETTINGS = {"retention_days": 60, "auto_months": []}


def parse_retention_settings(data):
    days = data.get("retention_days", 60)
    months = data.get("auto_months", [])
    if type(days) is not int or not 1 <= days <= 3650:
        raise InputError("保存日数は1〜3650日の整数で指定してください。")
    if not isinstance(months, list) or len(months) > 240:
        raise InputError("自動削除する月を選んでください（240か月以内）。")
    checked = []
    for month in months:
        if not isinstance(month, str):
            raise InputError("対象月を年月の形式で指定してください。")
        year, number = month_parts(month)
        expiry_date(date(year, number, 16), days)
        checked.append(f"{year:04}-{number:02}")
    return {"retention_days": days, "auto_months": sorted(set(checked))}


def period_end(day):
    return day.replace(day=15 if day.day <= 15 else calendar.monthrange(day.year, day.month)[1])


def expiry_date(day, days):
    try:
        return period_end(day) + timedelta(days=days)
    except OverflowError:
        raise InputError("その年月と保存日数では削除日を計算できません。対象月を確認してください。")


def read_setting(db, name, default):
    row = db.execute("SELECT value FROM settings WHERE name=?", (name,)).fetchone()
    return json.loads(row[0]) if row else default


def write_setting(db, name, value):
    db.execute("INSERT INTO settings(name,value) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",
               (name, json.dumps(value, ensure_ascii=False)))


class DataRetention:
    def __init__(self, db_file, secret, today=None):
        self.db_file = db_file
        self.secret = secret.encode() if isinstance(secret, str) else secret
        self.today = today or (lambda: datetime.now(JST).date())

    @contextmanager
    def connection(self, write=False):
        db = sqlite3.connect(self.db_file, timeout=30)
        try:
            db.execute("PRAGMA secure_delete=ON")
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def settings(self, db):
        return parse_retention_settings(read_setting(db, "retention_settings", DEFAULT_SETTINGS))

    def records(self, db, months):
        if not months:
            return []
        marks = ",".join("?" for _ in months)
        return db.execute(f"SELECT id,name,name_key,month,rows_json,submitted_at FROM submissions WHERE month IN ({marks}) ORDER BY id", months).fetchall()

    def rows(self, record):
        try:
            rows = json.loads(record[4])
            if not isinstance(rows, list):
                raise ValueError()
            for row in rows:
                date.fromisoformat(row["date"])
            return rows
        except (ValueError, TypeError, KeyError):
            raise InputError("提出履歴を読み取れません。データを変更せずに処理を止めました。")

    def summarize(self, records, selected):
        people, days, history, receipts = set(), set(), 0, 0
        for record in records:
            matches = [row for row in self.rows(record) if selected(date.fromisoformat(row["date"]))]
            if matches:
                people.add(record[2])
                receipts += 1
            history += len(matches)
            days.update((record[2], row["date"]) for row in matches)
        return {"people": len(people), "person_days": len(days), "history_entries": history, "receipts": receipts}

    def token(self, action, payload, snapshot):
        content = json.dumps([action, payload, snapshot, self.today().isoformat()], ensure_ascii=False,
                             sort_keys=True, separators=(",", ":")).encode()
        return hmac.new(self.secret, content, hashlib.sha256).hexdigest()

    def check_token(self, provided, expected):
        if not isinstance(provided, str) or not hmac.compare_digest(provided, expected):
            raise InputError("対象データや設定が変わりました。削除内容をもう一度確認してください。")

    def purge(self, db, records, selected):
        summary = self.summarize(records, selected)
        for record in records:
            rows = self.rows(record)
            remaining = [row for row in rows if not selected(date.fromisoformat(row["date"]))]
            if len(remaining) == len(rows):
                continue
            if remaining:
                db.execute("UPDATE submissions SET rows_json=? WHERE id=?",
                           (json.dumps(remaining, ensure_ascii=False), record[0]))
            else:
                db.execute("DELETE FROM submissions WHERE id=?", (record[0],))
        # 残りの日の並び順を守る補助記録も、その人の当月の希望がなくなったら消す。
        db.execute("DELETE FROM submission_priorities WHERE NOT EXISTS (SELECT 1 FROM submissions s WHERE s.month=submission_priorities.month AND s.name_key=submission_priorities.name_key)")
        return summary

    def expired(self, settings):
        today = self.today()
        return lambda day: f"{day.year:04}-{day.month:02}" in settings["auto_months"] and expiry_date(day, settings["retention_days"]) <= today

    def info(self):
        with self.connection() as db:
            settings = self.settings(db)
            months = {row[0] for row in db.execute("SELECT DISTINCT month FROM submissions")}
            months.update(settings["auto_months"])
            listing = []
            for month in sorted(months, reverse=True):
                year, number = month_parts(month)
                try:
                    first = expiry_date(date(year, number, 1), settings["retention_days"]).isoformat()
                    second = expiry_date(date(year, number, 16), settings["retention_days"]).isoformat()
                except InputError:
                    # 年9999などの誤入力があっても、他の月の設定や手動削除を使えるようにする。
                    first = second = None
                listing.append({"month": month, "enabled": month in settings["auto_months"],
                                "first_delete_on": first, "second_delete_on": second})
            return {"settings": settings, "months": listing, "today": self.today().isoformat(),
                    "last_run": read_setting(db, "retention_status", None)}

    def preview_settings(self, data):
        proposed = parse_retention_settings(data)
        with self.connection() as db:
            old = self.settings(db)
            records = self.records(db, proposed["auto_months"])
            return {"settings": proposed, "summary": self.summarize(records, self.expired(proposed)),
                    "preview_token": self.token("retention", proposed, [old, records]), "today": self.today().isoformat(),
                    "schedule": [{"month": month,
                                  "first": expiry_date(date(*month_parts(month), 1), proposed["retention_days"]).isoformat(),
                                  "second": expiry_date(date(*month_parts(month), 16), proposed["retention_days"]).isoformat()}
                                 for month in proposed["auto_months"]]}

    def save_settings(self, data):
        proposed = parse_retention_settings(data)
        with self.connection(write=True) as db:
            old = self.settings(db)
            records = self.records(db, proposed["auto_months"])
            self.check_token(data.get("preview_token"), self.token("retention", proposed, [old, records]))
            selected = self.expired(proposed)
            summary = self.summarize(records, selected)
            if summary["history_entries"] and data.get("confirmed") is not True:
                raise InputError("保存すると今すぐ削除する履歴があります。対象と件数を確認してチェックしてください。")
            write_setting(db, "retention_settings", proposed)
            self.purge(db, records, selected)
            self.record_status(db, summary)
        return {**self.info(), "deleted": summary}

    def record_status(self, db, summary):
        write_setting(db, "retention_status", {"checked_date": self.today().isoformat(),
                                               "checked_at": datetime.now(JST).isoformat(), "deleted": summary})

    def run_due(self):
        with self.connection(write=True) as db:
            settings = self.settings(db)
            if not settings["auto_months"]:
                return
            status = read_setting(db, "retention_status", {})
            if status.get("checked_date") == self.today().isoformat():
                return
            summary = self.purge(db, self.records(db, settings["auto_months"]), self.expired(settings))
            self.record_status(db, summary)

    def start(self, logger):
        def worker():
            while True:
                threading.Event().wait(3600)
                try:
                    self.run_due()
                except Exception:
                    logger.exception("Automatic retention check failed")
        threading.Thread(target=worker, name="shift-retention", daemon=True).start()

    def validate_submission(self, db, rows):
        settings = self.settings(db)
        selected = self.expired(settings)
        if any(selected(date.fromisoformat(row["date"])) for row in rows):
            raise InputError("自動削除の期限を過ぎた日が含まれています。その日を除いて提出してください。")

    def target(self, data):
        kind = data.get("kind")
        if kind == "submissions":
            month = data.get("month", "")
            if not isinstance(month, str):
                raise InputError("削除する対象月を選んでください。")
            year, number = month_parts(month)
            part = data.get("part", "all")
            if not isinstance(part, str) or part not in {"all", "first", "second"}:
                raise InputError("全月・前半・後半のいずれかを選んでください。")
            first = 16 if part == "second" else 1
            last = 15 if part == "first" else calendar.monthrange(year, number)[1]
            return {"kind": kind, "month": f"{year:04}-{number:02}", "part": part,
                    "start": date(year, number, first).isoformat(), "end": date(year, number, last).isoformat()}
        if kind == "roster":
            names = data.get("names")
            if not isinstance(names, list) or not names or len(names) > 600 or any(not isinstance(n, str) or not n.strip() for n in names):
                raise InputError("スタッフ一覧から削除する人を選んでください。")
            return {"kind": kind, "names": sorted({normalize_name(n) for n in names})}
        raise InputError("削除する情報の種類を選んでください。")

    def deletion_plan(self, db, target):
        if target["kind"] == "submissions":
            records = self.records(db, [target["month"]])
            selected = lambda day: target["start"] <= day.isoformat() <= target["end"]
            return self.summarize(records, selected), records
        roster = read_setting(db, "roster", {"kitchen": [], "hall": []})
        available = {normalize_name(n) for names in roster.values() for n in names}
        if not set(target["names"]).issubset(available):
            raise InputError("スタッフ一覧が変わりました。画面を読み込み直してください。")
        return {"people": len(target["names"])}, roster

    def preview_deletion(self, data):
        target = self.target(data)
        with self.connection() as db:
            summary, snapshot = self.deletion_plan(db, target)
            return {"target": target, "summary": summary, "preview_token": self.token("delete", target, snapshot)}

    def delete(self, data):
        target = self.target(data)
        with self.connection(write=True) as db:
            summary, snapshot = self.deletion_plan(db, target)
            self.check_token(data.get("preview_token"), self.token("delete", target, snapshot))
            if data.get("confirmed") is not True:
                raise InputError("削除する対象と件数を確認して、確認欄にチェックしてください。")
            if target["kind"] == "submissions":
                self.purge(db, snapshot, lambda day: target["start"] <= day.isoformat() <= target["end"])
            else:
                remaining = {role: [name for name in names if normalize_name(name) not in target["names"]]
                             for role, names in snapshot.items()}
                write_setting(db, "roster", remaining)
        return {"deleted": summary, "target": target}
