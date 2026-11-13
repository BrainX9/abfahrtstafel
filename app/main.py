"""Abfahrtstafel auf Basis der DB OpenBoards API (POST /departures)."""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

log = logging.getLogger("abfahrtstafel")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

BASE_DIR = Path(__file__).parent
API_URL = "https://apis.deutschebahn.com/db-api-marketplace/apis/open-boards/v1/departures"


def _csv(name: str) -> list[str]:
    return [v.strip() for v in os.getenv(name, "").split(",") if v.strip()]


# --- Konfiguration (Umgebungsvariablen) ------------------------------------
CLIENT_ID = os.getenv("DB_CLIENT_ID", "")
API_KEY = os.getenv("DB_API_KEY", "")
EVA_NUMBERS = _csv("EVA_NUMBERS") or ["8000105"]
STATION_TITLE = os.getenv("STATION_TITLE", "")
TRANSPORT_TYPES = _csv("TRANSPORT_TYPES")
WINDOW_MINUTES = min(int(os.getenv("WINDOW_MINUTES", "60")), 480)  # API erlaubt max. 8 h
MAX_ROWS = int(os.getenv("MAX_ROWS", "15"))
REFRESH_SECONDS = max(int(os.getenv("REFRESH_SECONDS", "30")), 10)
TZ = ZoneInfo(os.getenv("TZ", "Europe/Berlin"))
DEMO_FILE = os.getenv("DEMO_FILE", "")  # gespeicherte Response statt Live-Abruf

app = FastAPI(title="Abfahrtstafel", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

_cache: dict = {"board": None, "fetched": 0.0, "error": None}
_lock = asyncio.Lock()


# --- Abruf -----------------------------------------------------------------
def build_request_body(now: datetime) -> dict:
    body = {
        "stopPlaceOrStation": {"evaNumbers": EVA_NUMBERS},
        "timeWindow": {
            "timeStart": now.isoformat(timespec="seconds"),
            "timeEnd": (now + timedelta(minutes=WINDOW_MINUTES)).isoformat(timespec="seconds"),
        },
        "languages": ["deu"],
        "sortBy": "TIME",
        "maxViaStops": 3,
    }
    if TRANSPORT_TYPES:
        body["transportTypes"] = TRANSPORT_TYPES
    return body


async def fetch_raw() -> dict:
    if DEMO_FILE:
        return json.loads(Path(DEMO_FILE).read_text(encoding="utf-8"))
    if not CLIENT_ID or not API_KEY:
        raise RuntimeError("DB_CLIENT_ID und DB_API_KEY sind nicht gesetzt")
    headers = {
        "DB-Client-ID": CLIENT_ID,
        "DB-Api-Key": API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/vnd.de.db.ris+json",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(API_URL, headers=headers, json=build_request_body(datetime.now(TZ)))
    if resp.status_code != 200:
        raise RuntimeError(f"DB-API antwortet mit HTTP {resp.status_code}: {resp.text[:200]}")
    return resp.json()


# --- Aufbereitung ----------------------------------------------------------
def _german(entries: list[dict], key: str) -> str | None:
    """Deutschen Text aus einer Liste lokalisierter Einträge holen."""
    for e in entries or []:
        if e.get("language") in ("deu", "de") and e.get(key):
            return e[key]
    for e in entries or []:
        if e.get(key):
            return e[key]
    return None


def _clock(iso: str) -> tuple[datetime, str]:
    dt = datetime.fromisoformat(iso).astimezone(TZ)
    return dt, dt.strftime("%H:%M")


def transform(raw: dict) -> dict:
    """Rohdaten der API in flache Tafelzeilen umwandeln."""
    names = {
        s["evaNumber"]: _german(s.get("localizedNames"), "name") or s["evaNumber"]
        for s in raw.get("stopPlaces", [])
    }
    msgs = raw.get("messages", {})
    # Nur Meldungen, die auf eine Tafelzeile passen (Baustellentexte sind zu lang).
    hint_texts: dict[int, str] = {}
    for group, key in (("risCauseCodes", "text"), ("risQualityDeviations", "text"), ("notes", "textShort")):
        for m in msgs.get(group, []):
            text = _german(m.get("descriptions"), key) or _german(m.get("descriptions"), "text")
            if text:
                hint_texts[m["messageID"]] = text

    rows = []
    for dep in raw.get("departures", []):
        transport = dep.get("transport", {})
        sched_dt, sched = _clock(dep["timeSchedule"])
        real_dt, real = _clock(dep.get("time", dep["timeSchedule"]))
        delay = round((real_dt - sched_dt).total_seconds() / 60)

        # 'direction' kann leer sein; dann steht das Ziel nur in journeyInfo.
        dest_evas = [p["evaNumber"] for p in transport.get("direction", {}).get("stopPlaces", [])]
        if not dest_evas:
            dest = dep.get("journeyInfo", {}).get("destination", {}).get("evaNumber")
            dest_evas = [dest] if dest else []
        via = sorted(transport.get("via", []), key=lambda v: v.get("displayPriority", 99))

        platform = dep.get("platform", {}).get("name", "")
        platform_plan = dep.get("platformSchedule", {}).get("name", "")
        cancelled = bool(dep.get("cancelled"))
        hints = [hint_texts[i] for i in dep.get("messages", []) if i in hint_texts]
        if cancelled:
            hints.insert(0, "Fällt aus")

        rows.append(
            {
                "time": sched,
                "time_real": real,
                "delay": delay,
                "line": transport.get("journeyDescription") or transport.get("line", {}).get("name", ""),
                "type": transport.get("type", ""),
                "destination": " / ".join(names.get(e, e) for e in dest_evas) or "–",
                "via": [names.get(v["evaNumber"], v["evaNumber"]) for v in via if not v.get("cancelled")],
                "platform": platform or platform_plan,
                "platform_changed": bool(platform and platform_plan and platform != platform_plan),
                "cancelled": cancelled,
                "hints": hints,
            }
        )

    title = STATION_TITLE
    if not title:
        stations = raw.get("stations", [])
        title = (_german(stations[0].get("localizedNames"), "name") if stations else None) or names.get(
            EVA_NUMBERS[0], "Abfahrt"
        )
    return {"title": title, "rows": rows[:MAX_ROWS]}


async def get_board() -> dict:
    """Tafel aus dem Cache liefern; höchstens ein API-Aufruf pro Intervall."""
    async with _lock:
        if _cache["board"] is None or time.monotonic() - _cache["fetched"] >= REFRESH_SECONDS:
            try:
                _cache["board"] = transform(await fetch_raw())
                _cache["error"] = None
                _cache["updated"] = datetime.now(TZ).strftime("%H:%M:%S")
            except Exception as exc:  # letzte gute Tafel weiter anzeigen
                log.warning("Abruf fehlgeschlagen: %s", exc)
                _cache["error"] = str(exc)
            _cache["fetched"] = time.monotonic()
    board = _cache["board"] or {"title": STATION_TITLE or "Abfahrt", "rows": []}
    return {**board, "error": _cache["error"], "updated": _cache.get("updated")}


# --- Routen ----------------------------------------------------------------
@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse(
        request, "index.html", {"board": await get_board(), "refresh": REFRESH_SECONDS}
    )


@app.get("/board")
async def board_fragment(request: Request):
    return templates.TemplateResponse(request, "_board.html", {"board": await get_board()})


@app.get("/api/board")
async def board_json():
    return JSONResponse(await get_board())


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
