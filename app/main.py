"""Regex-Trainer – Lern- und Testplattform für reguläre Ausdrücke (Python re)."""
import csv
import io
import json
import os
import secrets
import sqlite3
import time
from datetime import datetime
from functools import wraps

import markdown as md
import yaml
from flask import (Flask, Response, abort, flash, g, jsonify, redirect,
                   render_template, request, session, url_for)
from markupsafe import Markup

from . import engine

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(BASE, "..", "data"))
DB_PATH = os.path.join(DATA_DIR, "regex.db")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin")
GRACE_SECONDS = 30
GRADE_THRESHOLDS = [float(x) for x in os.environ.get("GRADE_THRESHOLDS", "89,76,63,50").split(",")]
GRADE_NAMES = {1: "Sehr gut", 2: "Gut", 3: "Befriedigend", 4: "Genügend", 5: "Nicht genügend"}

def _secret_key():
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    # ohne Env-Variable: Schlüssel einmalig erzeugen und im Datenverzeichnis ablegen,
    # damit alle Gunicorn-Worker und Neustarts denselben verwenden
    os.makedirs(DATA_DIR, exist_ok=True)
    p = os.path.join(DATA_DIR, "secret_key")
    try:
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_hex(32))
    except FileExistsError:
        pass
    with open(p) as f:
        return f.read().strip()


app = Flask(__name__)
app.secret_key = _secret_key()
app.config.update(SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_HTTPONLY=True,
                  JSON_AS_ASCII=False, MAX_CONTENT_LENGTH=256 * 1024)


# ------------------------------------------------------------------ Inhalte

def _load_yaml(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or []


def load_lessons():
    lessons = _load_yaml(os.path.join(BASE, "content", "lessons.yaml"))
    for l in lessons:
        l["html"] = Markup(md.markdown(l.get("text", ""), extensions=["fenced_code", "tables"]))
    return lessons


def test_tasks_path():
    p = os.environ.get("TEST_TASKS_FILE", "/config/test_tasks.yaml")
    if os.path.exists(p):
        return p
    return os.path.join(BASE, "content", "test_tasks.example.yaml")


def load_test_tasks():
    tasks = _load_yaml(test_tasks_path())
    return {t["id"]: t for t in tasks}


LESSONS = load_lessons()
EXERCISES = {e["id"]: e for l in LESSONS for e in l.get("exercises", [])}


def test_tasks():
    # bei jedem Aufruf neu laden -> geänderte ConfigMap wirkt ohne Neustart
    return load_test_tasks()


def render_desc(text):
    return Markup(md.markdown(text or ""))


app.jinja_env.filters["md"] = render_desc


# ------------------------------------------------------------------ Datenbank

def db():
    if "db" not in g:
        os.makedirs(DATA_DIR, exist_ok=True)
        g.db = sqlite3.connect(DB_PATH, timeout=10)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    d = g.pop("db", None)
    if d is not None:
        d.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    code TEXT NOT NULL UNIQUE,
    task_ids TEXT NOT NULL,
    duration_min INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    show_results INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    student_name TEXT NOT NULL,
    klasse TEXT NOT NULL,
    started_at REAL NOT NULL,
    submitted_at REAL,
    answers TEXT NOT NULL DEFAULT '{}',
    score REAL,
    max_score REAL,
    details TEXT,
    ip TEXT,
    UNIQUE(session_id, student_name, klasse)
);
"""


def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    con.commit()
    con.close()


init_db()


# ------------------------------------------------------------------ Hilfen

def grade_for(pct):
    for i, t in enumerate(GRADE_THRESHOLDS, start=1):
        if pct >= t:
            return i
    return 5


def fmt_time(ts):
    if not ts:
        return "–"
    return datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M")


app.jinja_env.filters["time"] = fmt_time


def fmt_pts(x):
    if x is None:
        return "–"
    return f"{x:g}".replace(".", ",")


app.jinja_env.filters["pts"] = fmt_pts


def session_tasks(sess_row):
    pool = test_tasks()
    ids = json.loads(sess_row["task_ids"])
    return [pool[i] for i in ids if i in pool]


def deadline(attempt, sess_row):
    return attempt["started_at"] + sess_row["duration_min"] * 60


def finalize(attempt_id):
    """Bewertet einen Versuch endgültig (mit versteckten Prüffällen)."""
    con = db()
    a = con.execute("SELECT * FROM attempts WHERE id=?", (attempt_id,)).fetchone()
    if a is None or a["submitted_at"]:
        return
    s = con.execute("SELECT * FROM sessions WHERE id=?", (a["session_id"],)).fetchone()
    answers = json.loads(a["answers"] or "{}")
    total, max_total, details = 0.0, 0.0, {}
    for t in session_tasks(s):
        ev = engine.evaluate(t, answers.get(t["id"], ""), include_hidden=True)
        pts = engine.score(t, ev)
        details[t["id"]] = {"points": pts, "max": float(t.get("points", 1)),
                            "passed": ev["passed"], "total": ev["total"], "error": ev["error"]}
        total += pts
        max_total += float(t.get("points", 1))
    now = time.time()
    sub = min(now, deadline(a, s) + GRACE_SECONDS)
    con.execute("UPDATE attempts SET submitted_at=?, score=?, max_score=?, details=? WHERE id=?",
                (sub, total, max_total, json.dumps(details), attempt_id))
    con.commit()


def finalize_expired(session_id):
    con = db()
    s = con.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
    for a in con.execute("SELECT * FROM attempts WHERE session_id=? AND submitted_at IS NULL", (session_id,)):
        if time.time() > deadline(a, s) + GRACE_SECONDS:
            finalize(a["id"])


def current_attempt():
    aid = session.get("attempt_id")
    if not aid:
        return None, None
    con = db()
    a = con.execute("SELECT * FROM attempts WHERE id=?", (aid,)).fetchone()
    if a is None:
        session.pop("attempt_id", None)
        return None, None
    s = con.execute("SELECT * FROM sessions WHERE id=?", (a["session_id"],)).fetchone()
    return a, s


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("admin_login", next=request.path))
        return f(*args, **kwargs)
    return wrapper


# ------------------------------------------------------------------ Lernbereich

@app.get("/")
def index():
    return render_template("index.html", lessons=LESSONS)


@app.get("/lernen/<lid>")
def lesson(lid):
    idx = next((i for i, l in enumerate(LESSONS) if l["id"] == lid), None)
    if idx is None:
        abort(404)
    prev_l = LESSONS[idx - 1] if idx > 0 else None
    next_l = LESSONS[idx + 1] if idx + 1 < len(LESSONS) else None
    return render_template("lesson.html", lesson=LESSONS[idx], prev=prev_l, next=next_l,
                           nr=idx + 1, lessons=LESSONS)


@app.get("/spielwiese")
def playground():
    return render_template("playground.html")


@app.get("/spickzettel")
def cheatsheet():
    return render_template("cheatsheet.html")


@app.post("/api/match")
def api_match():
    d = request.get_json(force=True, silent=True) or {}
    try:
        matches = engine.find_matches(d.get("pattern", ""), d.get("text", ""), d.get("flags", []))
    except engine.PatternError as e:
        return jsonify(ok=False, error=str(e))
    return jsonify(ok=True, matches=matches)


@app.post("/api/check")
def api_check():
    d = request.get_json(force=True, silent=True) or {}
    ex = EXERCISES.get(d.get("id"))
    if ex is None:
        abort(404)
    return jsonify(engine.evaluate(ex, d.get("pattern", "")))


@app.get("/api/solution/<eid>")
def api_solution(eid):
    ex = EXERCISES.get(eid)
    if ex is None:
        abort(404)
    return jsonify(solution=ex.get("solution", ""))


@app.get("/healthz")
def healthz():
    db().execute("SELECT 1")
    return "ok"


# ------------------------------------------------------------------ Testbereich (Schüler)

@app.route("/test", methods=["GET", "POST"])
def test_join():
    a, s = current_attempt()
    if a is not None and request.method == "GET":
        return redirect(url_for("test_run"))
    if request.method == "POST":
        name = " ".join(request.form.get("name", "").split())[:60]
        klasse = request.form.get("klasse", "").strip().upper()[:10]
        code = request.form.get("code", "").strip().upper()
        if not name or not klasse or not code:
            flash("Bitte alle Felder ausfüllen.", "error")
            return render_template("test_join.html", form=request.form)
        con = db()
        s = con.execute("SELECT * FROM sessions WHERE code=?", (code,)).fetchone()
        if s is None or not s["active"]:
            flash("Dieser Test-Code ist ungültig oder der Test ist nicht freigeschaltet.", "error")
            return render_template("test_join.html", form=request.form)
        existing = con.execute("SELECT * FROM attempts WHERE session_id=? AND student_name=? AND klasse=?",
                               (s["id"], name, klasse)).fetchone()
        if existing is not None:
            flash("Für diesen Namen gibt es bereits einen Versuch. Bitte wende dich an deine Lehrkraft.", "error")
            return render_template("test_join.html", form=request.form)
        cur = con.execute("INSERT INTO attempts (session_id, student_name, klasse, started_at, ip) VALUES (?,?,?,?,?)",
                          (s["id"], name, klasse, time.time(), request.headers.get("X-Forwarded-For", request.remote_addr)))
        con.commit()
        session["attempt_id"] = cur.lastrowid
        session.permanent = True
        return redirect(url_for("test_run"))
    return render_template("test_join.html", form={})


@app.get("/test/run")
def test_run():
    a, s = current_attempt()
    if a is None:
        return redirect(url_for("test_join"))
    if not a["submitted_at"] and time.time() > deadline(a, s) + GRACE_SECONDS:
        finalize(a["id"])
        a, s = current_attempt()
    if a["submitted_at"]:
        return redirect(url_for("test_done"))
    tasks = session_tasks(s)
    answers = json.loads(a["answers"] or "{}")
    public = [{k: t[k] for k in ("id", "title", "description", "type", "points", "match", "nomatch",
                                  "text", "expected", "group") if k in t} for t in tasks]
    return render_template("test_run.html", attempt=a, sess=s, tasks=public, answers=answers,
                           remaining=int(deadline(a, s) - time.time()))


def _open_attempt_or_error():
    a, s = current_attempt()
    if a is None:
        return None, None, (jsonify(ok=False, error="Kein aktiver Test."), 403)
    if a["submitted_at"]:
        return None, None, (jsonify(ok=False, error="Test bereits abgegeben.", done=True), 403)
    if time.time() > deadline(a, s) + GRACE_SECONDS:
        finalize(a["id"])
        return None, None, (jsonify(ok=False, error="Die Zeit ist abgelaufen.", done=True), 403)
    return a, s, None


@app.post("/api/test/try")
def api_test_try():
    a, s, err = _open_attempt_or_error()
    if err:
        return err
    d = request.get_json(force=True, silent=True) or {}
    tasks = {t["id"]: t for t in session_tasks(s)}
    t = tasks.get(d.get("id"))
    if t is None:
        abort(404)
    _save_answer(a, d.get("id"), d.get("pattern", ""))
    return jsonify(engine.public_view(engine.evaluate(t, d.get("pattern", ""), include_hidden=False)))


def _save_answer(a, tid, pattern):
    con = db()
    answers = json.loads(con.execute("SELECT answers FROM attempts WHERE id=?", (a["id"],)).fetchone()[0] or "{}")
    answers[tid] = (pattern or "")[:engine.MAX_PATTERN]
    con.execute("UPDATE attempts SET answers=? WHERE id=?", (json.dumps(answers), a["id"]))
    con.commit()


@app.post("/api/test/save")
def api_test_save():
    a, s, err = _open_attempt_or_error()
    if err:
        return err
    d = request.get_json(force=True, silent=True) or {}
    valid = {t["id"] for t in session_tasks(s)}
    answers = {k: str(v)[:engine.MAX_PATTERN] for k, v in (d.get("answers") or {}).items() if k in valid}
    con = db()
    old = json.loads(a["answers"] or "{}")
    old.update(answers)
    con.execute("UPDATE attempts SET answers=? WHERE id=?", (json.dumps(old), a["id"]))
    con.commit()
    return jsonify(ok=True, saved=time.strftime("%H:%M:%S"))


@app.post("/test/submit")
def test_submit():
    a, s = current_attempt()
    if a is None:
        return redirect(url_for("test_join"))
    if not a["submitted_at"]:
        if time.time() <= deadline(a, s) + GRACE_SECONDS:
            valid = {t["id"] for t in session_tasks(s)}
            answers = json.loads(a["answers"] or "{}")
            for k in valid:
                if f"a_{k}" in request.form:
                    answers[k] = request.form[f"a_{k}"][:engine.MAX_PATTERN]
            db().execute("UPDATE attempts SET answers=? WHERE id=?", (json.dumps(answers), a["id"]))
            db().commit()
        finalize(a["id"])
    return redirect(url_for("test_done"))


@app.get("/test/fertig")
def test_done():
    a, s = current_attempt()
    if a is None:
        return redirect(url_for("test_join"))
    if not a["submitted_at"]:
        return redirect(url_for("test_run"))
    details = json.loads(a["details"] or "{}")
    tasks = session_tasks(s)
    pct = (a["score"] / a["max_score"] * 100) if a["max_score"] else 0
    return render_template("test_done.html", attempt=a, sess=s, tasks=tasks, details=details,
                           pct=pct, grade=grade_for(pct), grade_names=GRADE_NAMES)


@app.post("/test/abmelden")
def test_logout():
    session.pop("attempt_id", None)
    return redirect(url_for("index"))


# ------------------------------------------------------------------ Lehrerbereich

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if secrets.compare_digest(request.form.get("password", ""), ADMIN_PASSWORD):
            session["is_admin"] = True
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/admin") else url_for("admin"))
        time.sleep(1)
        flash("Falsches Passwort.", "error")
    return render_template("admin_login.html")


@app.post("/admin/logout")
def admin_logout():
    session.pop("is_admin", None)
    return redirect(url_for("index"))


@app.get("/admin")
@admin_required
def admin():
    con = db()
    rows = con.execute("""SELECT s.*, COUNT(a.id) AS n,
                          SUM(CASE WHEN a.submitted_at IS NOT NULL THEN 1 ELSE 0 END) AS n_done
                          FROM sessions s LEFT JOIN attempts a ON a.session_id = s.id
                          GROUP BY s.id ORDER BY s.created_at DESC""").fetchall()
    return render_template("admin.html", sessions=rows, pool=list(test_tasks().values()),
                           pool_file=test_tasks_path(),
                           using_example=test_tasks_path().endswith("example.yaml"))


def _new_code():
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(6))


@app.post("/admin/session/new")
@admin_required
def admin_session_new():
    name = request.form.get("name", "").strip() or "Test"
    ids = [i for i in request.form.getlist("tasks") if i in test_tasks()]
    if not ids:
        flash("Bitte mindestens eine Aufgabe auswählen.", "error")
        return redirect(url_for("admin"))
    try:
        dur = max(1, min(600, int(request.form.get("duration", "45"))))
    except ValueError:
        dur = 45
    code = request.form.get("code", "").strip().upper() or _new_code()
    con = db()
    try:
        cur = con.execute("INSERT INTO sessions (name, code, task_ids, duration_min, active, show_results, created_at)"
                          " VALUES (?,?,?,?,?,?,?)",
                          (name, code, json.dumps(ids), dur, 1 if request.form.get("active") else 0,
                           1 if request.form.get("show_results") else 0, time.time()))
        con.commit()
    except sqlite3.IntegrityError:
        flash("Dieser Code ist bereits vergeben.", "error")
        return redirect(url_for("admin"))
    return redirect(url_for("admin_session", sid=cur.lastrowid))


def _get_session(sid):
    s = db().execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
    if s is None:
        abort(404)
    return s


@app.get("/admin/session/<int:sid>")
@admin_required
def admin_session(sid):
    s = _get_session(sid)
    finalize_expired(sid)
    tasks = session_tasks(s)
    rows = []
    for a in db().execute("SELECT * FROM attempts WHERE session_id=? ORDER BY klasse, student_name", (sid,)):
        det = json.loads(a["details"] or "{}")
        pct = (a["score"] / a["max_score"] * 100) if a["submitted_at"] and a["max_score"] else None
        remaining = None if a["submitted_at"] else int(deadline(a, s) - time.time())
        answered = len([v for v in json.loads(a["answers"] or "{}").values() if v])
        rows.append({"a": a, "det": det, "pct": pct, "grade": grade_for(pct) if pct is not None else None,
                     "remaining": remaining, "answered": answered})
    done = [r for r in rows if r["pct"] is not None]
    stats = None
    if done:
        stats = {
            "avg": sum(r["pct"] for r in done) / len(done),
            "grades": {n: sum(1 for r in done if r["grade"] == n) for n in range(1, 6)},
            "task_avg": {t["id"]: sum(r["det"].get(t["id"], {}).get("points", 0) for r in done) / len(done)
                         for t in tasks},
        }
    return render_template("admin_session.html", sess=s, tasks=tasks, rows=rows, stats=stats,
                           grade_names=GRADE_NAMES, join_url=request.host_url.rstrip("/") + url_for("test_join"))


@app.post("/admin/session/<int:sid>/toggle")
@admin_required
def admin_session_toggle(sid):
    s = _get_session(sid)
    field = request.form.get("field")
    if field not in ("active", "show_results"):
        abort(400)
    db().execute(f"UPDATE sessions SET {field}=? WHERE id=?", (0 if s[field] else 1, sid))
    db().commit()
    return redirect(url_for("admin_session", sid=sid))


@app.post("/admin/session/<int:sid>/close")
@admin_required
def admin_session_close(sid):
    _get_session(sid)
    for a in db().execute("SELECT id FROM attempts WHERE session_id=? AND submitted_at IS NULL", (sid,)).fetchall():
        finalize(a["id"])
    db().execute("UPDATE sessions SET active=0 WHERE id=?", (sid,))
    db().commit()
    flash("Alle offenen Versuche wurden abgegeben und der Test gesperrt.", "ok")
    return redirect(url_for("admin_session", sid=sid))


@app.post("/admin/session/<int:sid>/delete")
@admin_required
def admin_session_delete(sid):
    _get_session(sid)
    db().execute("DELETE FROM attempts WHERE session_id=?", (sid,))
    db().execute("DELETE FROM sessions WHERE id=?", (sid,))
    db().commit()
    flash("Test gelöscht.", "ok")
    return redirect(url_for("admin"))


@app.get("/admin/session/<int:sid>/csv")
@admin_required
def admin_session_csv(sid):
    s = _get_session(sid)
    finalize_expired(sid)
    tasks = session_tasks(s)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Klasse", "Name", "Beginn", "Abgabe"] + [f"{t['id']} {t['title']} (max {t.get('points', 1)})" for t in tasks]
               + ["Punkte", "Max", "Prozent", "Note"])
    for a in db().execute("SELECT * FROM attempts WHERE session_id=? ORDER BY klasse, student_name", (sid,)):
        det = json.loads(a["details"] or "{}")
        if a["submitted_at"]:
            pct = a["score"] / a["max_score"] * 100 if a["max_score"] else 0
            tail = [fmt_pts(a["score"]), fmt_pts(a["max_score"]), f"{pct:.1f}".replace(".", ","),
                    f"{grade_for(pct)} ({GRADE_NAMES[grade_for(pct)]})"]
        else:
            tail = ["", "", "", "läuft noch"]
        w.writerow([a["klasse"], a["student_name"], fmt_time(a["started_at"]), fmt_time(a["submitted_at"])]
                   + [fmt_pts(det.get(t["id"], {}).get("points")) if a["submitted_at"] else "" for t in tasks] + tail)
    data = "﻿" + buf.getvalue()   # BOM -> Excel erkennt UTF-8
    fname = f"regex-test-{s['code']}.csv"
    return Response(data, mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename={fname}"})


@app.get("/admin/attempt/<int:aid>")
@admin_required
def admin_attempt(aid):
    a = db().execute("SELECT * FROM attempts WHERE id=?", (aid,)).fetchone()
    if a is None:
        abort(404)
    s = _get_session(a["session_id"])
    answers = json.loads(a["answers"] or "{}")
    items = []
    for t in session_tasks(s):
        ev = engine.evaluate(t, answers.get(t["id"], ""), include_hidden=True)
        items.append({"t": t, "answer": answers.get(t["id"], ""), "ev": ev, "pts": engine.score(t, ev)})
    pct = (a["score"] / a["max_score"] * 100) if a["submitted_at"] and a["max_score"] else None
    return render_template("admin_attempt.html", a=a, sess=s, items=items, pct=pct,
                           grade=grade_for(pct) if pct is not None else None, grade_names=GRADE_NAMES)


@app.post("/admin/attempt/<int:aid>/reset")
@admin_required
def admin_attempt_reset(aid):
    a = db().execute("SELECT * FROM attempts WHERE id=?", (aid,)).fetchone()
    if a is None:
        abort(404)
    db().execute("DELETE FROM attempts WHERE id=?", (aid,))
    db().commit()
    flash(f"Versuch von {a['student_name']} gelöscht – der Schüler kann sich neu anmelden.", "ok")
    return redirect(url_for("admin_session", sid=a["session_id"]))


@app.post("/admin/attempt/<int:aid>/extend")
@admin_required
def admin_attempt_extend(aid):
    """Gibt einem Schüler zusätzliche Minuten (verschiebt den Startzeitpunkt)."""
    a = db().execute("SELECT * FROM attempts WHERE id=?", (aid,)).fetchone()
    if a is None:
        abort(404)
    try:
        mins = int(request.form.get("minutes", "5"))
    except ValueError:
        mins = 5
    if a["submitted_at"]:
        flash("Der Versuch ist bereits abgegeben – Verlängern geht nur bei laufenden Tests.", "error")
    else:
        db().execute("UPDATE attempts SET started_at = started_at + ? WHERE id=?", (mins * 60, aid))
        db().commit()
        flash(f"{a['student_name']} hat {mins} Minuten mehr Zeit.", "ok")
    return redirect(url_for("admin_session", sid=a["session_id"]))


@app.get("/admin/aufgaben")
@admin_required
def admin_tasks():
    pool = list(test_tasks().values())
    checks = {t["id"]: engine.evaluate(t, t.get("solution", ""), include_hidden=True) for t in pool}
    return render_template("admin_tasks.html", pool=pool, checks=checks, pool_file=test_tasks_path())


if __name__ == "__main__":
    app.run(debug=True, port=8000)
