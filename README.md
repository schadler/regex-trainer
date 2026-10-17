# Regex-Trainer

Lern- und Testplattform für **reguläre Ausdrücke (Python `re`)** – als Container für **k3s**.

**Für Schüler**
- 10 Lektionen (Literale → Lookarounds → Praxis: IPv4, MAC, Logs, E-Mail) mit 30 Übungen und sofortigem Feedback
- Spielwiese mit Live-Markierung, Gruppentabelle und Python-Code
- Spickzettel
- Lernfortschritt wird im Browser gespeichert

**Für die Lehrkraft** (`/admin`)
- Test anlegen: Aufgaben aus dem Pool wählen, Dauer festlegen → Code (z.B. `HTL42`) an die Klasse geben
- Schüler melden sich mit Name + Klasse + Code an, die Zeit läuft pro Schüler ab dem Start
- Antworten werden laufend gespeichert; nach Ablauf der Zeit wird automatisch abgegeben
- Bewertung **automatisch** gegen sichtbare **und versteckte** Prüffälle
- Live-Übersicht mit Punkten pro Aufgabe, Prozent, Note, Notenverteilung
- Detailansicht pro Schüler (jeder Prüffall mit erwartet/erhalten)
- +5 Minuten für einzelne Schüler, Versuch löschen (Neustart), Test beenden
- CSV-Export (Excel-tauglich, `;`-getrennt)

## Bewertung

- Pro Aufgabe: `Punkte × bestandene Fälle / alle Fälle`, auf halbe Punkte abgerundet
  (oder `all_or_nothing: true` in der Aufgabe)
- Note aus Prozent, Standard `89 / 76 / 63 / 50` → 1 / 2 / 3 / 4, darunter 5
  (änderbar über `GRADE_THRESHOLDS` im Deployment)
- Gefährliche Muster (katastrophales Backtracking) werden nach 0,5 s abgebrochen

## Projektstruktur

```
app/
  main.py                       Flask-App (Routen, Test-Logik, Lehrerbereich)
  engine.py                     Regex-Auswertung mit Timeout
  content/lessons.yaml          Lektionen + Übungen
  content/test_tasks.example.yaml  Beispiel-Aufgabenpool für Tests
  templates/, static/
k8s/                            Manifeste für k3s
tests/                          pytest (prüft u.a. alle Musterlösungen)
.github/workflows/build.yml     baut amd64 + arm64 Image nach ghcr.io
```

## 1. Image bauen (GitHub Container Registry)

1. Neues GitHub-Repository anlegen (am besten **privat**) und den Ordner pushen:
   ```bash
   cd Regex-Trainer
   git init && git add . && git commit -m "Regex-Trainer"
   git branch -M main
   git remote add origin https://github.com/<OWNER>/regex-trainer.git
   git push -u origin main
   ```
2. Die Action **Build & Push Image** führt die Tests aus und pusht
   `ghcr.io/<OWNER>/regex-trainer:latest` (für amd64 **und** arm64 / Raspberry Pi).
3. Image-Sichtbarkeit: Unter *GitHub → Packages → regex-trainer → Package settings* entweder
   auf **public** stellen, oder ein Pull-Secret anlegen (siehe unten).

## 2. In k3s deployen

```bash
# OWNER im Image-Namen ersetzen
sed -i '' 's/OWNER/<dein-github-name>/g' k8s/deployment.yaml k8s/kustomization.yaml   # macOS
# (Linux: sed -i 's/OWNER/.../g' ...)

kubectl apply -f k8s/namespace.yaml

# Passwort + Schlüssel
cp k8s/secret.example.yaml k8s/secret.yaml   # Werte anpassen!
kubectl apply -f k8s/secret.yaml

# Hostname in k8s/ingress.yaml anpassen, dann:
kubectl apply -k k8s
kubectl -n regex-trainer get pods
```

Aufruf: `http://regex.schule.local/` (bzw. dein Hostname) – Lehrerbereich unter `/admin`.

Ohne DNS zum schnellen Testen:
```bash
kubectl -n regex-trainer port-forward svc/regex-trainer 8080:80
```

**Privates Image?** Pull-Secret anlegen (GitHub Token mit `read:packages`):
```bash
kubectl -n regex-trainer create secret docker-registry ghcr \
  --docker-server=ghcr.io --docker-username=<OWNER> --docker-password=<TOKEN>
```
und im Deployment unter `spec.template.spec` ergänzen:
```yaml
imagePullSecrets:
  - name: ghcr
```

**Update** nach neuem Push:
```bash
kubectl -n regex-trainer rollout restart deploy/regex-trainer
```

## 3. Eigene Testaufgaben (wichtig!)

Die Beispielaufgaben liegen im Repository – Schüler mit Zugriff auf das Repo könnten sie lesen.
Deshalb die **echten** Testaufgaben nur als ConfigMap in den Cluster laden:

```bash
cp app/content/test_tasks.example.yaml ~/meine_test_tasks.yaml   # bearbeiten
kubectl -n regex-trainer create configmap regex-test-tasks \
  --from-file=test_tasks.yaml=$HOME/meine_test_tasks.yaml \
  --dry-run=client -o yaml | kubectl apply -f -
```

Änderungen werden nach ca. 1 Minute ohne Neustart übernommen.
Unter **Lehrerbereich → Aufgabenpool prüfen** siehst du sofort, ob jede Musterlösung
alle Prüffälle besteht (fängt Tippfehler in den Aufgaben ab).

Aufgabenformat (Kurzfassung, Details in der Beispieldatei):

```yaml
- id: t02
  title: "Uhrzeit 24h"
  points: 3
  type: match                       # ganzer String muss passen
  description: "Uhrzeit **hh:mm** von 00:00 bis 23:59."
  match: ["00:00", "23:59"]         # sichtbar
  nomatch: ["24:00", "9:30"]        # sichtbar
  hidden_match: ["19:05"]           # nur Bewertung
  hidden_nomatch: ["12:60", "1230"] # nur Bewertung
  solution: '([01]\d|2[0-3]):[0-5]\d'

- id: t09
  type: findall                     # alle Treffer im Text
  group: port                       # optional: nur diese Gruppe vergleichen
  text: "port=80\nport=443"
  expected: ["80", "443"]
  hidden:
    - text: "port=22\n  port=23"
      expected: ["22"]
  ...
```

Lektionen/Übungen werden in `app/content/lessons.yaml` gepflegt (Markdown im `text`).

## Ablauf im Unterricht

1. `/admin` → Test anlegen (Häkchen „sofort freischalten“ ggf. erst kurz vor Beginn)
2. Code an die Tafel schreiben, Schüler öffnen `/test`
3. Übersicht offen lassen (aktualisiert sich alle 30 s)
4. Am Ende **Test beenden** → alle offenen Versuche werden abgegeben und der Code gesperrt
5. CSV exportieren; optional „Ergebnis für Schüler“ einschalten

Hinweis: Ein Schüler, der den Browser wechselt, kann sich mit gleichem Namen nicht erneut anmelden
(Schutz gegen „Mitspielen“ unter fremdem Namen). In dem Fall im Lehrerbereich den Versuch löschen –
gespeicherte Antworten gehen dabei verloren.

## Lokal entwickeln

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest
pytest -q tests
ADMIN_PASSWORD=test flask --app app.main run --debug --port 8000
```

oder mit Docker:
```bash
docker build -t regex-trainer .
docker run -p 8000:8000 -e ADMIN_PASSWORD=test -v regexdata:/data regex-trainer
```

## Technik

- Python 3.12, Flask, Gunicorn (2 Worker × 8 Threads), SQLite auf PVC (`/data`)
- Regex-Auswertung serverseitig mit dem Paket `regex` im `VERSION0`-Modus (= Verhalten von `re`)
  wegen Timeout-Unterstützung
- Läuft als Non-Root (UID 10001); **genau 1 Replica** (SQLite)
- Konfiguration über Umgebungsvariablen: `ADMIN_PASSWORD`, `SECRET_KEY`, `TEST_TASKS_FILE`,
  `GRADE_THRESHOLDS`, `DATA_DIR`
