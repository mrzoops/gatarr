import os
import json
import sqlite3
import urllib.parse
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from apscheduler.schedulers.background import BackgroundScheduler
from app.arr_client import ArrClient
from app.providers import fetch_trakt_watchlist, evaluate_streaming
from app.notifier import send_approval_notification

CONFIG_PATH = "/app/config/config.json"
DB_PATH = "/app/config/sync.db"

app = FastAPI(title="Gatarr")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

scheduler = BackgroundScheduler()

def load_config():
    if not os.path.exists(CONFIG_PATH):
        return {
            "trakt_user": "",
            "trakt_client_id": "",
            "tmdb_api_key": "",
            "mode": "B",
            "auto_approve": False,
            "sync_interval_hours": 4,
            "subscribed": ["Netflix", "Max", "Disney Plus", "Amazon Prime Video", "Apple TV Plus"],
            "radarr": {
                "url": "http://192.168.4.165:7878",
                "api_key": "",
                "profile": "",
                "root": ""
            },
            "sonarr": {
                "url": "http://192.168.4.165:8989",
                "api_key": "",
                "profile": "",
                "root": ""
            },
            "pushover": {
                "token": "",
                "user_key": "",
                "public_base_url": "http://192.168.4.165:5055"
            }
        }
    with open(CONFIG_PATH, "r") as f:
        cfg = json.load(f)
        if "auto_approve" not in cfg:
            cfg["auto_approve"] = False
        if "sync_interval_hours" not in cfg:
            cfg["sync_interval_hours"] = 4
        if "sonarr" not in cfg:
            cfg["sonarr"] = {"url": "http://192.168.4.165:8989", "api_key": "", "profile": "", "root": ""}
        return cfg

def save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trakt_id INT UNIQUE,
                    tmdb_id INT,
                    title TEXT,
                    year INT,
                    media_type TEXT,
                    reason TEXT,
                    status TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )""")
    conn.commit()
    conn.close()

init_db()

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT trakt_id, tmdb_id, title, year, media_type, reason, status FROM queue WHERE status LIKE '%Waiting%' ORDER BY id DESC")
    pending_rows = c.fetchall()

    c.execute("SELECT trakt_id, tmdb_id, title, year, media_type, reason, status FROM queue WHERE status NOT LIKE '%Waiting%' ORDER BY id DESC LIMIT 50")
    approved_rows = c.fetchall()
    conn.close()

    format_items = lambda rows: [
        {"trakt_id": r[0], "tmdb_id": r[1], "title": r[2], "year": r[3], "media_type": r[4], "reason": r[5], "status": r[6]}
        for r in rows
    ]

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "config": load_config(),
            "pending": format_items(pending_rows),
            "approved": format_items(approved_rows)
        }
    )

@app.get("/api/arr/fetch-options")
def fetch_arr_options(service: str, url: str, api_key: str):
    client = ArrClient(url, api_key)
    if not client.test_connection():
        return JSONResponse({"success": False, "error": f"Connection to {service.upper()} failed"}, status_code=400)
    return {
        "success": True,
        "profiles": client.get_quality_profiles(),
        "roots": client.get_root_folders()
    }

@app.post("/api/save-config")
async def update_settings(req: Request):
    cfg = await req.json()
    save_config(cfg)

    hours = float(cfg.get("sync_interval_hours", 4))
    if scheduler.get_job("trakt_sync_job"):
        scheduler.reschedule_job("trakt_sync_job", trigger="interval", minutes=int(hours * 60))

    return {"status": "saved"}

@app.post("/api/approve-item")
async def api_approve_item(req: Request):
    data = await req.json()
    trakt_id = data.get("trakt_id")
    tmdb_id = data.get("tmdb_id")
    media_type = data.get("media_type")
    title = data.get("title")
    year = data.get("year")

    cfg = load_config()
    success, new_status = False, "Unknown Error"

    if media_type == "movie":
        radarr = ArrClient(cfg["radarr"]["url"], cfg["radarr"]["api_key"])
        success, new_status = radarr.add_movie(
            tmdb_id=tmdb_id,
            title=title,
            year=year,
            profile_id=int(cfg["radarr"]["profile"]),
            root_path=cfg["radarr"]["root"]
        )
    elif media_type == "show":
        sonarr = ArrClient(cfg["sonarr"]["url"], cfg["sonarr"]["api_key"])
        success, new_status = sonarr.add_series(
            tvdb_id=tmdb_id,
            title=title,
            year=year,
            profile_id=int(cfg["sonarr"]["profile"]),
            root_path=cfg["sonarr"]["root"]
        )

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE queue SET status = ? WHERE trakt_id = ?", (new_status, trakt_id))
    conn.commit()
    conn.close()

    return {"success": success, "status": new_status}

@app.post("/api/reject-item")
async def api_reject_item(req: Request):
    data = await req.json()
    trakt_id = data.get("trakt_id")

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE queue SET status = ? WHERE trakt_id = ?", ("Rejected / Ignored", trakt_id))
    conn.commit()
    conn.close()

    return {"success": True, "status": "Rejected / Ignored"}

@app.get("/approve")
def approve_download(trakt_id: int, tmdb_id: int, media_type: str, title: str, year: int):
    cfg = load_config()
    success, new_status = False, "Failed"

    if media_type == "movie":
        radarr = ArrClient(cfg["radarr"]["url"], cfg["radarr"]["api_key"])
        success, new_status = radarr.add_movie(
            tmdb_id=tmdb_id,
            title=title,
            year=year,
            profile_id=int(cfg["radarr"]["profile"]),
            root_path=cfg["radarr"]["root"]
        )
    elif media_type == "show":
        sonarr = ArrClient(cfg["sonarr"]["url"], cfg["sonarr"]["api_key"])
        success, new_status = sonarr.add_series(
            tvdb_id=tmdb_id,
            title=title,
            year=year,
            profile_id=int(cfg["sonarr"]["profile"]),
            root_path=cfg["sonarr"]["root"]
        )

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE queue SET status = ? WHERE trakt_id = ?", (new_status, trakt_id))
    conn.commit()
    conn.close()

    status_color = "#c026d3" if success else "#ef4444"
    return HTMLResponse(f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>Gatarr</title>
        </head>
        <body style="background:#0b0a10;color:#f8fafc;font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;padding:16px;box-sizing:border-box;">
            <div style="background:#171520;padding:2rem;border-radius:12px;text-align:center;box-shadow:0 10px 25px rgba(0,0,0,0.5);border:1px solid #2e2640;max-width:400px;width:100%;">
                <h2 style="color:#c026d3;margin-top:0;">Gatarr</h2>
                <div style="font-size:1.15rem;margin:16px 0;font-weight:600;">{title} ({year})</div>
                <div style="background:{status_color}22;color:{status_color};padding:10px 14px;border-radius:8px;font-weight:500;">
                    {new_status}
                </div>
            </div>
        </body>
        </html>
    """)

@app.post("/api/sync")
def trigger_manual_sync():
    run_sync_cycle()
    return {"status": "sync_triggered"}

def run_sync_cycle():
    cfg = load_config()
    if not cfg.get("trakt_user") or not cfg.get("trakt_client_id") or not cfg.get("tmdb_api_key"):
        return

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    radarr = ArrClient(cfg["radarr"]["url"], cfg["radarr"]["api_key"])
    sonarr = ArrClient(cfg["sonarr"]["url"], cfg["sonarr"]["api_key"])

    # 1. Process Movie Watchlist
    movie_items = fetch_trakt_watchlist(cfg["trakt_user"], cfg["trakt_client_id"], "movies")
    for item in movie_items:
        movie = item.get("movie", {})
        trakt_id = movie.get("ids", {}).get("trakt")
        tmdb_id = movie.get("ids", {}).get("tmdb")
        title = movie.get("title")
        year = movie.get("year", 0)

        if not trakt_id or not tmdb_id:
            continue

        c.execute("SELECT status FROM queue WHERE trakt_id = ?", (trakt_id,))
        if c.fetchone():
            continue

        # Check Radarr first
        if radarr.movie_exists(tmdb_id):
            c.execute("INSERT OR REPLACE INTO queue (trakt_id, tmdb_id, title, year, media_type, reason, status) VALUES (?, ?, ?, ?, 'movie', 'Existing library item', 'Already in Radarr')",
                      (trakt_id, tmdb_id, title, year))
            conn.commit()
            continue

        is_streaming, reason = evaluate_streaming(
            tmdb_id=tmdb_id,
            media_type="movie",
            tmdb_api_key=cfg["tmdb_api_key"],
            mode=cfg.get("mode", "B"),
            subscribed_services=cfg.get("subscribed", [])
        )

        if not is_streaming:
            if cfg.get("auto_approve"):
                success, status_str = radarr.add_movie(
                    tmdb_id=tmdb_id,
                    title=title,
                    year=year,
                    profile_id=int(cfg["radarr"]["profile"]),
                    root_path=cfg["radarr"]["root"]
                )
                c.execute("INSERT OR REPLACE INTO queue (trakt_id, tmdb_id, title, year, media_type, reason, status) VALUES (?, ?, ?, ?, 'movie', ?, ?)",
                          (trakt_id, tmdb_id, title, year, reason, status_str))
                conn.commit()
                continue

            base_cb = cfg["pushover"].get("public_base_url", "http://192.168.4.165:5055").rstrip("/")
            encoded_title = urllib.parse.quote(title)
            approve_url = f"{base_cb}/approve?trakt_id={trakt_id}&tmdb_id={tmdb_id}&media_type=movie&title={encoded_title}&year={year}"

            if cfg["pushover"].get("token") and cfg["pushover"].get("user_key"):
                send_approval_notification(
                    token=cfg["pushover"]["token"],
                    user_key=cfg["pushover"]["user_key"],
                    title=title,
                    year=year,
                    reason=reason,
                    approve_url=approve_url
                )

            c.execute("INSERT OR REPLACE INTO queue (trakt_id, tmdb_id, title, year, media_type, reason, status) VALUES (?, ?, ?, ?, 'movie', ?, 'Waiting Approval')",
                      (trakt_id, tmdb_id, title, year, reason))
            conn.commit()
        else:
            # Note streamable/destined item and move on
            c.execute("INSERT OR REPLACE INTO queue (trakt_id, tmdb_id, title, year, media_type, reason, status) VALUES (?, ?, ?, ?, 'movie', ?, 'Available on Streaming')",
                      (trakt_id, tmdb_id, title, year, reason))
            conn.commit()

    # 2. Process Show Watchlist
    show_items = fetch_trakt_watchlist(cfg["trakt_user"], cfg["trakt_client_id"], "shows")
    for item in show_items:
        show = item.get("show", {})
        trakt_id = show.get("ids", {}).get("trakt")
        tmdb_id = show.get("ids", {}).get("tmdb")
        tvdb_id = show.get("ids", {}).get("tvdb") or tmdb_id
        title = show.get("title")
        year = show.get("year", 0)

        if not trakt_id or not tmdb_id:
            continue

        c.execute("SELECT status FROM queue WHERE trakt_id = ?", (trakt_id,))
        if c.fetchone():
            continue

        # Check Sonarr first
        if sonarr.series_exists(tvdb_id):
            c.execute("INSERT OR REPLACE INTO queue (trakt_id, tmdb_id, title, year, media_type, reason, status) VALUES (?, ?, ?, ?, 'show', 'Existing library item', 'Already in Sonarr')",
                      (trakt_id, tvdb_id, title, year))
            conn.commit()
            continue

        is_streaming, reason = evaluate_streaming(
            tmdb_id=tmdb_id,
            media_type="show",
            tmdb_api_key=cfg["tmdb_api_key"],
            mode=cfg.get("mode", "B"),
            subscribed_services=cfg.get("subscribed", [])
        )

        if not is_streaming:
            if cfg.get("auto_approve"):
                success, status_str = sonarr.add_series(
                    tvdb_id=tvdb_id,
                    title=title,
                    year=year,
                    profile_id=int(cfg["sonarr"]["profile"]),
                    root_path=cfg["sonarr"]["root"]
                )
                c.execute("INSERT OR REPLACE INTO queue (trakt_id, tmdb_id, title, year, media_type, reason, status) VALUES (?, ?, ?, ?, 'show', ?, ?)",
                          (trakt_id, tvdb_id, title, year, reason, status_str))
                conn.commit()
                continue

            base_cb = cfg["pushover"].get("public_base_url", "http://192.168.4.165:5055").rstrip("/")
            encoded_title = urllib.parse.quote(title)
            approve_url = f"{base_cb}/approve?trakt_id={trakt_id}&tmdb_id={tvdb_id}&media_type=show&title={encoded_title}&year={year}"

            if cfg["pushover"].get("token") and cfg["pushover"].get("user_key"):
                send_approval_notification(
                    token=cfg["pushover"]["token"],
                    user_key=cfg["pushover"]["user_key"],
                    title=title,
                    year=year,
                    reason=reason,
                    approve_url=approve_url
                )

            c.execute("INSERT OR REPLACE INTO queue (trakt_id, tmdb_id, title, year, media_type, reason, status) VALUES (?, ?, ?, ?, 'show', ?, 'Waiting Approval')",
                      (trakt_id, tvdb_id, title, year, reason))
            conn.commit()
        else:
            # Streamable or destined for subscription (e.g., Apple TV+, Netflix)
            c.execute("INSERT OR REPLACE INTO queue (trakt_id, tmdb_id, title, year, media_type, reason, status) VALUES (?, ?, ?, ?, 'show', ?, 'Available on Streaming')",
                      (trakt_id, tvdb_id, title, year, reason))
            conn.commit()

    conn.close()

initial_cfg = load_config()
init_interval_minutes = int(float(initial_cfg.get("sync_interval_hours", 4)) * 60)
scheduler.add_job(run_sync_cycle, "interval", minutes=init_interval_minutes, id="trakt_sync_job")
scheduler.start()