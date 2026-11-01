"""ローカル起動: python app.py / 公開設定は DEPLOY.md を参照。"""
import argparse
from functools import wraps
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
from datetime import datetime, timezone
from urllib.parse import urlsplit, quote
from io import BytesIO

from flask import Flask, request, jsonify, session, send_file, redirect
from waitress import serve
from werkzeug.middleware.proxy_fix import ProxyFix

from shifts import InputError, read_book, make_plan, export_book, sample_book, parse_lines, normalize_name, month_parts
from daily_shifts import (parse_roster, target_day, daily_candidates, make_daily_plan,
                          patch_daily_excel, fingerprint, sample_daily_book)
from break_rules import parse_break_settings, default_break_settings, apply_break_rules

ROOT = Path(__file__).parent


def create_app(config=None):
    public = os.environ.get("SHIFT_HOST", "127.0.0.1") != "127.0.0.1"
    app = Flask(__name__, static_folder=None)
    app.config.update(
        MAX_CONTENT_LENGTH=12 * 1024 * 1024,
        SECRET_KEY=os.environ.get("SHIFT_SESSION_SECRET") or secrets.token_hex(32),
        ADMIN_PASSWORD=os.environ.get("SHIFT_ADMIN_PASSWORD", ""),
        INVITE_CODE=os.environ.get("SHIFT_INVITE_CODE", "local-demo-only"),
        DATA_DIR=os.environ.get("SHIFT_DATA_DIR", str(ROOT / "data")),
        PUBLIC=public,
        PUBLIC_URL=(os.environ.get("SHIFT_PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL", "")).rstrip("/"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=public,
    )
    if config:
        app.config.update(config)
    if app.config["PUBLIC"]:
        if len(app.config["ADMIN_PASSWORD"]) < 16 or len(app.config["INVITE_CODE"]) < 16:
            raise RuntimeError("公開時はSHIFT_ADMIN_PASSWORDとSHIFT_INVITE_CODEをそれぞれ16文字以上で設定してください。")
        app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1)
    data_dir = Path(app.config["DATA_DIR"])
    data_dir.mkdir(parents=True, exist_ok=True)
    db_file = data_dir / "shifts.sqlite3"
    with sqlite3.connect(db_file) as db:
        db.execute("CREATE TABLE IF NOT EXISTS submissions (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, name_key TEXT NOT NULL, month TEXT NOT NULL, rows_json TEXT NOT NULL, submitted_at TEXT NOT NULL)")
        db.execute("CREATE INDEX IF NOT EXISTS submissions_month ON submissions(month)")
        db.execute("CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT NOT NULL)")

    def admin_required(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if app.config["PUBLIC"] and not session.get("admin"):
                if request.path.startswith("/api/"):
                    return jsonify(error="管理画面にログインしてください。"), 401
                return redirect("/login")
            return fn(*args, **kwargs)
        return wrapped

    @app.before_request
    def protect_origin():
        if not app.config["PUBLIC"] and request.host.split(":")[0] not in {"127.0.0.1", "localhost"}:
            return jsonify(error="このPCのブラウザから開いてください。"), 403
        if request.method == "POST":
            origin = urlsplit(request.headers.get("Origin", ""))
            if origin.scheme not in {"http", "https"} or origin.netloc != request.host:
                return jsonify(error="同じサイトの画面から操作してください。"), 403

    @app.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; form-action 'self'"
        return response

    def static(name):
        return send_file(ROOT / "static" / name)

    @app.get("/")
    @admin_required
    def index():
        return static("index.html")

    @app.get("/daily")
    @admin_required
    def daily_page():
        return static("daily.html")

    @app.get("/submit")
    def submit_page():
        return static("submit.html")

    @app.get("/login")
    def login_page():
        return static("login.html")

    @app.get("/health")
    def health():
        return jsonify(status="ok")

    @app.get("/<name>")
    def asset(name):
        if name not in {"app.js", "style.css", "submit.js", "login.js", "daily.js", "input-normalize.js"}:
            return jsonify(error="ページがありません。"), 404
        return static(name)

    def json_data():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise InputError("送信形式が正しくありません。")
        return data

    @app.post("/api/login")
    def login():
        password = json_data().get("password", "")
        if not isinstance(password, str) or not hmac.compare_digest(password.encode(), app.config["ADMIN_PASSWORD"].encode()) or not app.config["ADMIN_PASSWORD"]:
            return jsonify(error="パスワードが違います。"), 401
        session.clear()
        session["admin"] = True
        return jsonify(ok=True)

    @app.post("/api/logout")
    def logout():
        session.clear()
        return jsonify(ok=True)

    @app.get("/api/settings")
    @admin_required
    def settings():
        base = app.config["PUBLIC_URL"] or request.host_url.rstrip("/")
        return jsonify(public=app.config["PUBLIC"], invite_url=f"{base}/submit?key={quote(app.config['INVITE_CODE'])}")

    @app.post("/api/submit")
    def submit():
        data = json_data()
        code = data.get("key", "")
        if not isinstance(code, str) or not hmac.compare_digest(code.encode(), app.config["INVITE_CODE"].encode()):
            return jsonify(error="提出用リンクが無効です。管理者に新しいリンクを確認してください。"), 403
        name = data.get("name", "")
        if not isinstance(name, str) or not name.strip() or len(name) > 60 or any(c in name for c in "\n\r\t"):
            raise InputError("氏名を60文字以内で入力してください。")
        year, month_number = month_parts(data.get("month", ""))
        month = f"{year:04}-{month_number:02}"
        days = data.get("days")
        if not isinstance(days, list) or not 1 <= len(days) <= 31:
            raise InputError("1日以上のシフトを選んでください。")
        lines = []
        for item in days:
            if not isinstance(item, dict) or not isinstance(item.get("date"), str) or not isinstance(item.get("shift"), str):
                raise InputError("シフトの形式が正しくありません。")
            if "\n" in item["date"] + item["shift"] or "\r" in item["date"] + item["shift"]:
                raise InputError("日付・時刻に改行は使えません。")
            lines.append(f"{name.strip()} {item['date']} {item['shift']}")
        rows = parse_lines("\n".join(lines), month)
        stamp = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(db_file, timeout=15) as db:
            cursor = db.execute("INSERT INTO submissions(name,name_key,month,rows_json,submitted_at) VALUES(?,?,?,?,?)", (name.strip(), normalize_name(name), month, json.dumps(rows, ensure_ascii=False), stamp))
            number = cursor.lastrowid
        return jsonify(ok=True, receipt=number, count=len(rows))

    @app.get("/api/submissions")
    @admin_required
    def submissions():
        year, month_number = month_parts(request.args.get("month", ""))
        month = f"{year:04}-{month_number:02}"
        people, lines = [], []
        for record in latest_records(month):
            rows = record["rows"]
            people.append({"id": record["id"], "name": record["name"], "count": len(rows), "submitted_at": record["submitted_at"]})
            lines.extend(f"{r['name']} {r['date']} {r['shift']}" for r in rows)
        return jsonify(people=people, text="\n".join(lines))

    def latest_records(month):
        with sqlite3.connect(db_file) as db:
            records = db.execute("""
                SELECT s.id,s.name,s.rows_json,s.submitted_at,x.first_id,f.submitted_at
                FROM submissions s
                JOIN (SELECT name_key,MAX(id) AS latest,MIN(id) AS first_id
                      FROM submissions WHERE month=? GROUP BY name_key) x ON s.id=x.latest
                JOIN submissions f ON f.id=x.first_id
                ORDER BY x.first_id
            """, (month,)).fetchall()
        return [{"id": number, "name": name, "rows": json.loads(content), "submitted_at": stamp,
                 "first_id": first_id, "first_submitted_at": first_stamp}
                for number, name, content, stamp, first_id, first_stamp in records]

    def get_roster():
        with sqlite3.connect(db_file) as db:
            row = db.execute("SELECT value FROM settings WHERE name='roster'").fetchone()
        return parse_roster(json.loads(row[0]) if row else {"kitchen": [], "hall": []})

    def get_break_settings():
        with sqlite3.connect(db_file) as db:
            row = db.execute("SELECT value FROM settings WHERE name='break_settings'").fetchone()
        return parse_break_settings(json.loads(row[0])) if row else default_break_settings()

    @app.route("/api/break-settings", methods=["GET", "POST"])
    @admin_required
    def break_settings():
        if request.method == "POST":
            value = parse_break_settings(json_data())
            with sqlite3.connect(db_file) as db:
                db.execute("INSERT INTO settings(name,value) VALUES('break_settings',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value", (json.dumps(value, ensure_ascii=False),))
        return jsonify(settings=get_break_settings())

    @app.route("/api/roster", methods=["GET", "POST"])
    @admin_required
    def roster():
        if request.method == "POST":
            value = parse_roster(json_data())
            with sqlite3.connect(db_file) as db:
                db.execute("INSERT INTO settings(name,value) VALUES('roster',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value", (json.dumps(value, ensure_ascii=False),))
        return jsonify(roster=get_roster())

    def daily_context(day):
        day = target_day(day).isoformat()
        rows, version = daily_candidates(latest_records(day[:7]), get_roster(), day)
        settings = get_break_settings()
        rows, info = apply_break_rules(rows, settings, day)
        return rows, fingerprint({"submissions": version, "settings": settings}), settings, info

    @app.get("/api/daily-candidates")
    @admin_required
    def candidates():
        day = request.args.get("day", "")
        rows, version, settings, info = daily_context(day)
        return jsonify(rows=rows, version=version, settings=settings, day_type=info)

    def excel_response(content):
        return send_file(BytesIO(content), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", as_attachment=True, download_name="shift-result.xlsx")

    @app.get("/api/sample")
    @admin_required
    def sample():
        return excel_response(sample_book(request.args.get("month", "")))

    @app.get("/api/daily-sample")
    @admin_required
    def daily_sample():
        return excel_response(sample_daily_book(request.args.get("day", "")))

    @app.post("/api/<action>")
    @admin_required
    def excel_action(action):
        if action not in {"inspect", "preview", "export", "daily-preview", "daily-export"}:
            return jsonify(error="操作がありません。"), 404
        data = json_data()
        book = read_book(data.get("file", ""))
        try:
            if action == "inspect":
                return jsonify(sheets=book.sheetnames)
            if action.startswith("daily-"):
                people, version, settings, info = daily_context(data.get("day"))
                if data.get("version") != version:
                    raise InputError("提出内容・スタッフ一覧・休憩設定が変わりました。候補を読み込み直して、もう一度確認してください。")
                if settings["mode"] == "auto":
                    ids = data.get("selected_ids", [])
                    if not isinstance(ids, list):
                        raise InputError("反映する人を選んでください。")
                    for person in people:
                        if person["id"] in ids and person["break_issue"]:
                            raise InputError(f"{person['name']}：{person['break_issue']}")
                    # 自動モードの休憩は保存済みルールから計算し直す。画面側の時刻は信用しない。
                    data = {**data, "breaks": {str(p["id"]): p["automatic_break"] for p in people}}
                plan = make_daily_plan(book, data, people)
                plan["mode"] = settings["mode"]
                plan["day_type"] = info
                if settings["mode"] == "auto":
                    plan["warnings"][1] = f"{info['label']}の固定休憩を適用しました。勤務と重ならない場合は休憩なしです。職場のルールへの適合は管理者が確認してください。"
                    for placement in plan["placements"]:
                        if placement["break"] == "なし（管理者確認）":
                            placement["break"] = "なし（固定ルールによる）"
                token = fingerprint({"version": version, "plan": plan, "sheet": data.get("sheet"), "day": data.get("day"), "file": data.get("file")})
                if action == "daily-preview":
                    return jsonify(**plan, plan_token=token)
                if data.get("plan_token") != token:
                    raise InputError("確認内容が変わりました。反映内容をもう一度プレビューしてください。")
                if any(item["conflict"] for item in plan["updates"]) and data.get("overwrite") is not True:
                    raise InputError("変更・消去する既存の値があります。確認表を見て、上書き・消去の許可にチェックしてください。")
                return excel_response(patch_daily_excel(data["file"], data["sheet"], plan))
            if action == "preview":
                _, plan = make_plan(book, data)
                return jsonify(rows=plan)
            return excel_response(export_book(book, data))
        finally:
            book.close()

    @app.errorhandler(InputError)
    def input_error(exc):
        return jsonify(error=str(exc)), 400

    @app.errorhandler(413)
    def too_large(exc):
        return jsonify(error="送信データが大きすぎます。Excelは8MB以下にしてください。"), 413

    @app.errorhandler(500)
    def internal_error(exc):
        return jsonify(error="処理できませんでした。設定とExcelの形式を確認してください。"), 500

    return app


def main():
    parser = argparse.ArgumentParser(description="LINEグループ向けシフト提出・Excel転記")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    args = parser.parse_args()
    app = create_app()
    host = os.environ.get("SHIFT_HOST", "127.0.0.1")
    print(f"起動しました（{host}:{args.port}）。終了は Ctrl+C。", flush=True)
    if not app.config["PUBLIC"]:
        print(f"同じPCのブラウザで http://127.0.0.1:{args.port} を開いてください。", flush=True)
    serve(app, host=host, port=args.port, threads=4, max_request_body_size=12 * 1024 * 1024)


if __name__ == "__main__":
    main()
