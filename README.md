# Abfahrtstafel

Kleine Web-Abfahrtstafel auf Basis der DB-API **OpenBoards** (`POST /departures`).
FastAPI holt die Daten, bereitet sie auf und rendert eine HTML-Seite, die sich per HTMX selbst aktualisiert.

## Start

```bash
cp .env.example .env      # DB_CLIENT_ID, DB_API_KEY und EVA_NUMBERS eintragen
docker compose up -d --build
```

Die Tafel läuft dann unter `http://<host>:8080/`.

## Einstellungen (`.env`)

| Variable | Standard | Bedeutung |
|---|---|---|
| `DB_CLIENT_ID`, `DB_API_KEY` | – | Zugangsdaten aus dem DB API Marketplace |
| `EVA_NUMBERS` | `8000105` | EVA-Nummern der Haltestelle(n), kommagetrennt |
| `STATION_TITLE` | Name aus der API | Überschrift der Tafel |
| `TRANSPORT_TYPES` | leer (nur Schiene) | Filter auf Produktklassen |
| `WINDOW_MINUTES` | `60` | Zeitfenster ab jetzt, maximal 480 |
| `MAX_ROWS` | `15` | Zeilen auf der Tafel |
| `REFRESH_SECONDS` | `30` | Abstand der API-Abrufe und der Browser-Aktualisierung |
| `DEMO_FILE` | leer | Pfad zu einer gespeicherten Response; dann kein Live-Abruf |

## Endpunkte

- `/` – die Tafel
- `/board` – nur die Tabelle (wird von HTMX nachgeladen)
- `/api/board` – aufbereitete Daten als JSON
- `/healthz` – Healthcheck

## Verhalten

- Unabhängig von der Zahl der Bildschirme gibt es höchstens einen API-Aufruf pro `REFRESH_SECONDS`.
- Schlägt ein Abruf fehl, bleibt die letzte Tafel stehen und oben erscheint ein Hinweis.
- Unter dem Ziel stehen Hinweise (Ausfall, Verspätungsgrund, fehlende Wagen), sonst die Via-Halte.
- Geänderte Gleise und Hinweise sind gelb, die erwartete Zeit steht invertiert unter der Planzeit.

## Aufbau

```
app/main.py              Abruf, Cache, Aufbereitung, Routen
app/templates/           index.html (Seite), _board.html (Tabelle)
app/static/              style.css, htmx.min.js (lokal, kein CDN nötig)
```
