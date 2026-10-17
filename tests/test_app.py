import importlib
import os
import re
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ADMIN_PASSWORD", "geheim")
    monkeypatch.setenv("TEST_TASKS_FILE", "/nonexistent.yaml")
    import app.main as m
    m = importlib.reload(m)
    m.app.config["TESTING"] = True
    return m, m.app.test_client()


def test_all_lesson_solutions_pass(client):
    m, _ = client
    for ex in m.EXERCISES.values():
        ev = m.engine.evaluate(ex, ex["solution"])
        assert ev["error"] is None, (ex["id"], ev["error"])
        assert ev["passed"] == ev["total"], (ex["id"], [r for r in ev["results"] if not r["ok"]])
        # Ergebnis muss mit dem echten Python-re identisch sein
        if ex["type"] == "match":
            for s in ex["match"]:
                assert re.fullmatch(ex["solution"], s), (ex["id"], s)
            for s in ex["nomatch"]:
                assert not re.fullmatch(ex["solution"], s), (ex["id"], s)


def test_all_test_solutions_pass(client):
    m, _ = client
    for t in m.test_tasks().values():
        ev = m.engine.evaluate(t, t["solution"], include_hidden=True)
        assert ev["error"] is None, t["id"]
        assert ev["passed"] == ev["total"], (t["id"], [r for r in ev["results"] if not r["ok"]])
        assert m.engine.score(t, ev) == t["points"]


def test_unique_ids(client):
    m, _ = client
    ids = [e["id"] for l in m.LESSONS for e in l["exercises"]]
    assert len(ids) == len(set(ids))


def test_naive_answers_lose_points(client):
    m, _ = client
    t = m.test_tasks()["t02"]
    ev = m.engine.evaluate(t, r"\d\d:\d\d", include_hidden=True)
    assert 0 < m.engine.score(t, ev) < t["points"]


def test_timeout_redos(client):
    m, _ = client
    ex = {"type": "match", "match": ["a" * 40 + "!"], "nomatch": []}
    t0 = time.time()
    ev = m.engine.evaluate(ex, r"(a|aa)+$")
    assert time.time() - t0 < 3
    assert ev["results"][0]["note"] == "Zeitüberschreitung"


def test_pages(client):
    m, c = client
    for url in ["/", "/lernen/l01", "/lernen/l10", "/spielwiese", "/spickzettel", "/test", "/healthz"]:
        assert c.get(url).status_code == 200, url
    assert c.get("/lernen/xyz").status_code == 404
    r = c.post("/api/match", json={"pattern": r"(?P<j>\d{4})", "text": "a 2026 b 1999"})
    assert r.json["ok"] and len(r.json["matches"]) == 2 and r.json["matches"][0]["groups"][0]["name"] == "j"
    r = c.post("/api/match", json={"pattern": "(", "text": "x"})
    assert not r.json["ok"]
    r = c.post("/api/check", json={"id": "l04e1", "pattern": r"[1-9]\d{3}"})
    assert r.json["passed"] == r.json["total"]


def test_full_test_flow(client):
    m, c = client
    assert c.get("/admin").status_code == 302
    c.post("/admin/login", data={"password": "falsch"})
    assert c.get("/admin").status_code == 302
    c.post("/admin/login", data={"password": "geheim"})
    assert c.get("/admin").status_code == 200
    assert c.get("/admin/aufgaben").status_code == 200
    r = c.post("/admin/session/new", data={"name": "Regex 3AHIT", "duration": "30", "code": "abc123",
                                            "active": "on", "tasks": ["t01", "t02", "t07"]})
    assert r.status_code == 302
    sid = int(r.headers["Location"].rsplit("/", 1)[1])

    s = m.app.test_client()
    r = s.post("/test", data={"name": "Max Muster", "klasse": "3ahit", "code": "ABC123"})
    assert r.status_code == 302
    page = s.get("/test/run")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "2CHIT" not in html  # versteckte Fälle nicht im HTML
    assert "[1-5][A-E]H" not in html  # keine Lösung im HTML
    # Probieren nur gegen sichtbare Fälle
    r = s.post("/api/test/try", json={"id": "t02", "pattern": r"\d\d:\d\d"})
    assert all(not x["hidden"] for x in r.json["results"])
    assert s.post("/api/test/save", json={"answers": {"t01": "[1-5][A-E]H(IT|ET|WI)", "evil": "x"}}).json["ok"]
    s.post("/test/submit", data={"a_t07": r"@([\w.-]+)"})
    done = s.get("/test/fertig")
    assert done.status_code == 200

    # zweiter Schüler mit gleichem Namen wird abgewiesen
    s2 = m.app.test_client()
    r = s2.post("/test", data={"name": "Max  Muster", "klasse": "3AHIT", "code": "ABC123"})
    assert "bereits einen Versuch" in r.get_data(as_text=True)

    con = m.sqlite3.connect(m.DB_PATH)
    score, mx = con.execute("SELECT score, max_score FROM attempts").fetchone()
    assert mx == 7 and score >= 2.5  # t01 voll, t02 Teilpunkte, t07 ganz/teilweise

    page = c.get(f"/admin/session/{sid}")
    assert "Max Muster" in page.get_data(as_text=True)
    csv = c.get(f"/admin/session/{sid}/csv").get_data(as_text=True)
    assert "Max Muster" in csv and ";" in csv
    aid = con.execute("SELECT id FROM attempts").fetchone()[0]
    assert c.get(f"/admin/attempt/{aid}").status_code == 200
    c.post(f"/admin/attempt/{aid}/reset")
    assert con.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 0


def test_time_limit(client):
    m, c = client
    c.post("/admin/login", data={"password": "geheim"})
    c.post("/admin/session/new", data={"name": "T", "duration": "1", "code": "ZEIT", "active": "on", "tasks": ["t01"]})
    s = m.app.test_client()
    s.post("/test", data={"name": "A", "klasse": "1A", "code": "ZEIT"})
    con = m.sqlite3.connect(m.DB_PATH)
    con.execute("UPDATE attempts SET started_at = started_at - 600")
    con.commit()
    r = s.post("/api/test/save", json={"answers": {"t01": "x"}})
    assert r.status_code == 403 and r.json["done"]
    assert con.execute("SELECT submitted_at FROM attempts").fetchone()[0] is not None


def test_inactive_code(client):
    m, c = client
    c.post("/admin/login", data={"password": "geheim"})
    c.post("/admin/session/new", data={"name": "T", "duration": "10", "code": "ZU", "tasks": ["t01"]})
    s = m.app.test_client()
    r = s.post("/test", data={"name": "A", "klasse": "1A", "code": "ZU"})
    assert "nicht freigeschaltet" in r.get_data(as_text=True)


def test_admin_idle_logout(client):
    m, c = client
    r = c.post("/admin/login", data={"password": "geheim"})
    assert r.status_code == 302
    assert c.get("/admin").status_code == 200          # frisch angemeldet
    # Inaktivität simulieren: letzte Aktivität liegt länger als ADMIN_IDLE_MINUTES zurück
    with c.session_transaction() as s:
        s["admin_seen"] = int(time.time()) - m.ADMIN_IDLE_MINUTES * 60 - 5
    r = c.get("/admin")
    assert r.status_code == 302 and "/admin/login" in r.headers["Location"]
    with c.session_transaction() as s:
        assert "is_admin" not in s


def test_admin_activity_extends_session(client):
    m, c = client
    c.post("/admin/login", data={"password": "geheim"})
    with c.session_transaction() as s:
        s["admin_seen"] = int(time.time()) - m.ADMIN_IDLE_MINUTES * 60 + 60   # knapp vor Ablauf
    assert c.get("/admin").status_code == 200
    with c.session_transaction() as s:
        assert s["admin_seen"] >= int(time.time()) - 5               # Zeitstempel erneuert
