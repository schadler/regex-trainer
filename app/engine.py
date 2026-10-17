"""Sichere Auswertung von regulären Ausdrücken (Python-re-kompatibel).

Verwendet das Paket `regex` im VERSION0-Modus (verhält sich wie Pythons `re`),
weil es einen Timeout unterstützt. So kann ein Schüler mit einem
katastrophalen Backtracking-Muster (z.B. (a+)+$) den Server nicht blockieren.
"""
import math

import regex

TIMEOUT = 0.5          # Sekunden pro Prüfung
MAX_PATTERN = 500      # max. Länge eines Musters
MAX_TEXT = 20000       # max. Länge eines Testtexts

FLAG_MAP = {
    "I": regex.IGNORECASE,
    "M": regex.MULTILINE,
    "S": regex.DOTALL,
    "X": regex.VERBOSE,
}


class PatternError(Exception):
    pass


def compile_pattern(pattern, flags=()):
    if pattern is None:
        pattern = ""
    if len(pattern) > MAX_PATTERN:
        raise PatternError(f"Muster zu lang (max. {MAX_PATTERN} Zeichen).")
    f = regex.VERSION0
    for fl in flags or ():
        f |= FLAG_MAP.get(fl, 0)
    try:
        return regex.compile(pattern, f)
    except regex.error as e:
        raise PatternError(f"Syntaxfehler: {e}") from None
    except Exception as e:  # pragma: no cover
        raise PatternError(f"Fehler: {e}") from None


def find_matches(pattern, text, flags=(), limit=500):
    """Für die Spielwiese: alle Treffer inkl. Gruppen."""
    if len(text) > MAX_TEXT:
        raise PatternError(f"Text zu lang (max. {MAX_TEXT} Zeichen).")
    p = compile_pattern(pattern, flags)
    names = {v: k for k, v in p.groupindex.items()}
    out = []
    try:
        for m in p.finditer(text, timeout=TIMEOUT):
            groups = []
            for i in range(1, p.groups + 1):
                s, e = m.span(i)
                groups.append({
                    "nr": i,
                    "name": names.get(i),
                    "start": s,
                    "end": e,
                    "text": m.group(i),
                })
            out.append({"start": m.start(), "end": m.end(), "text": m.group(0), "groups": groups})
            if len(out) >= limit:
                break
    except TimeoutError:
        raise PatternError("Zeitüberschreitung – das Muster ist zu aufwändig (katastrophales Backtracking?).") from None
    return out


# ---------------------------------------------------------------- Aufgaben

def build_cases(task, include_hidden):
    """Erzeugt die Prüffälle einer Aufgabe.

    type: match   -> ganzer String muss passen (fullmatch) bzw. darf nicht passen
    type: findall -> alle Treffer (bzw. Gruppe `group`) im Text müssen exakt der Liste entsprechen
    """
    cases = []
    if task["type"] == "match":
        for s in task.get("match", []):
            cases.append({"kind": "match", "text": s, "expect": True, "hidden": False})
        for s in task.get("nomatch", []):
            cases.append({"kind": "match", "text": s, "expect": False, "hidden": False})
        if include_hidden:
            for s in task.get("hidden_match", []):
                cases.append({"kind": "match", "text": s, "expect": True, "hidden": True})
            for s in task.get("hidden_nomatch", []):
                cases.append({"kind": "match", "text": s, "expect": False, "hidden": True})
    elif task["type"] == "findall":
        if "text" in task:
            cases.append({"kind": "findall", "text": task["text"], "expected": list(task["expected"]), "hidden": False})
        if include_hidden:
            for h in task.get("hidden", []):
                cases.append({"kind": "findall", "text": h["text"], "expected": list(h["expected"]), "hidden": True})
    else:
        raise ValueError(f"Unbekannter Aufgabentyp: {task['type']}")
    return cases


def _extract(m, group):
    if group is None:
        return m.group(0)
    return m.group(group)


def evaluate(task, pattern, include_hidden=False):
    cases = build_cases(task, include_hidden)
    result = {"error": None, "results": [], "passed": 0, "total": len(cases)}
    pattern = (pattern or "")
    if not pattern.strip():
        result["error"] = "Kein Muster eingegeben."
        return result
    try:
        p = compile_pattern(pattern, task.get("flags", []))
    except PatternError as e:
        result["error"] = str(e)
        return result

    group = task.get("group")
    for c in cases:
        r = {"kind": c["kind"], "text": c["text"], "hidden": c["hidden"], "ok": False, "note": None}
        try:
            if c["kind"] == "match":
                got = p.fullmatch(c["text"], timeout=TIMEOUT) is not None
                r["expect"] = c["expect"]
                r["got"] = got
                r["ok"] = got == c["expect"]
            else:
                try:
                    got = [_extract(m, group) for m in p.finditer(c["text"], timeout=TIMEOUT)]
                except IndexError:
                    got = None
                    r["note"] = f"Dein Muster hat keine Gruppe „{group}“."
                r["expected"] = c["expected"]
                r["got"] = got
                r["ok"] = got == c["expected"]
        except TimeoutError:
            r["note"] = "Zeitüberschreitung"
            r["got"] = None
        if r["ok"]:
            result["passed"] += 1
        result["results"].append(r)
    return result


def score(task, ev):
    pts = float(task.get("points", 1))
    if ev["total"] == 0 or ev["error"]:
        return 0.0
    if task.get("all_or_nothing"):
        return pts if ev["passed"] == ev["total"] else 0.0
    raw = pts * ev["passed"] / ev["total"]
    return math.floor(raw * 2 + 1e-9) / 2   # auf halbe Punkte abrunden


def public_view(result):
    """Entfernt versteckte Fälle aus einem Ergebnis (für Schüler)."""
    vis = [r for r in result["results"] if not r["hidden"]]
    return {"error": result["error"], "results": vis,
            "passed": sum(1 for r in vis if r["ok"]), "total": len(vis)}
