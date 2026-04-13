import os
import asyncio
import sqlite3
import json
import re
import hashlib
import time
from pathlib import Path
from datetime import datetime

from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import aiohttp
from torf import Torrent

app = FastAPI(title="EchelonHD Scanner")
templates = Jinja2Templates(directory="/app/templates")

# Config from environment
CONFIG = {
    "tracker_url": os.getenv("TRACKER_URL", "https://echelonhd.me"),
    "tracker_passkey": os.getenv("TRACKER_PASSKEY", ""),
    "tracker_api_endpoint": os.getenv("TRACKER_API_ENDPOINT", ""),
    "tracker_api_password": os.getenv("TRACKER_API_PASSWORD", ""),
    "tmdb_api_key": os.getenv("TMDB_API_KEY", "93889655f8028098b115e8aa5e3f4414"),
    "qbit_url": os.getenv("QBIT_URL", "http://localhost:6891"),
    "qbit_user": os.getenv("QBIT_USER", "admin"),
    "qbit_pass": os.getenv("QBIT_PASS", "adminadmin"),
    "db_path": os.getenv("DB_PATH", "/data/scanner.db"),
    "scan_interval": int(os.getenv("SCAN_INTERVAL", "0")),
}

MEDIA_DIRS = {
    "movies": "/media/Movies",
    "shows": "/media/Shows",
    "anime": "/media/Anime",
}

# Database
def get_db():
    db = sqlite3.connect(CONFIG["db_path"])
    db.row_factory = sqlite3.Row
    return db

def init_db():
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS scanned_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT UNIQUE,
            name TEXT,
            title TEXT,
            year TEXT,
            category TEXT,
            resolution TEXT,
            size INTEGER,
            season INTEGER,
            episode INTEGER,
            season_pack INTEGER DEFAULT 0,
            tmdb_id INTEGER,
            tmdb_type TEXT,
            status TEXT DEFAULT 'scanned',
            torrent_hash TEXT,
            error TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            uploaded_at TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS scan_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message TEXT,
            level TEXT DEFAULT 'info',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS stats (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    db.close()

init_db()

# ── Helpers ──

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".m4v", ".ts", ".wmv", ".flv", ".mov"}

def parse_media_name(path: str):
    """Extract title, year, resolution, season, episode from file/folder name."""
    name = Path(path).stem if Path(path).is_file() else Path(path).name
    clean = name.replace(".", " ").replace("_", " ").replace("-", " ")

    year = None
    year_match = re.search(r"\b((?:19|20)\d{2})\b", clean)
    if year_match:
        year = year_match.group(1)

    resolution = "1080p"
    if re.search(r"2160p|4[kK]|UHD", clean):
        resolution = "2160p"
    elif re.search(r"720p", clean):
        resolution = "720p"
    elif re.search(r"480p", clean):
        resolution = "480p"

    # Detect season/episode (S01E01, Season 1, etc.)
    season = None
    episode = None
    se_match = re.search(r"S(\d{1,2})E(\d{1,3})", clean, re.IGNORECASE)
    if se_match:
        season = int(se_match.group(1))
        episode = int(se_match.group(2))
    else:
        s_match = re.search(r"Season\s*(\d{1,2})", clean, re.IGNORECASE)
        if s_match:
            season = int(s_match.group(1))
        e_match = re.search(r"(?:Episode|E|Ep)\s*(\d{1,3})", clean, re.IGNORECASE)
        if e_match:
            episode = int(e_match.group(1))

    title = re.sub(r"\b(S\d{1,2}E\d{1,3}|Season\s*\d+|Episode\s*\d+)\b", "", clean, flags=re.IGNORECASE)
    title = re.sub(r"\b(2160p|1080p|720p|480p|BluRay|WEB[ -]?DL|WEBRip|HDTV|REMUX|x264|x265|H[ .]?264|H[ .]?265|HEVC|AVC|AAC|DDP|DTS|FLAC|10bit|HDR|AMZN|NF|DSNP|REMASTERED|REPACK|PROPER|INTERNAL)\b.*", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*\(?\d{4}\)?\s*$", "", title)
    title = re.sub(r"\s+", " ", title).strip()

    return title, year, resolution, season, episode

def scan_directory(base_path: str, category: str):
    """Scan a media directory and return found items.

    Handles:
    - Movies: /Movies/Movie Name (2024)/movie.mkv → single torrent per movie
    - TV Shows: /Shows/Show Name/Season 01/ → one torrent per season pack
    - TV Shows: /Shows/Show Name/Show.S01E01.mkv → individual episodes
    - Anime: Same as TV Shows or Movies depending on structure
    """
    items = []
    base = Path(base_path)
    if not base.exists():
        return items

    for entry in sorted(base.iterdir()):
        if entry.name.startswith("."):
            continue

        if entry.is_dir():
            # Check for TV-style structure (has Season subdirs)
            season_dirs = [d for d in entry.iterdir() if d.is_dir() and re.match(r"(?:Season|Series|S)\s*\d+", d.name, re.IGNORECASE)]

            if season_dirs and category in ("shows", "anime"):
                # TV Show with season folders
                show_title, show_year, _, _, _ = parse_media_name(entry.name)

                for season_dir in sorted(season_dirs):
                    videos = sorted([f for f in season_dir.rglob("*") if f.suffix.lower() in VIDEO_EXTENSIONS])
                    if not videos:
                        continue

                    total_size = sum(f.stat().st_size for f in videos)
                    s_match = re.search(r"(\d+)", season_dir.name)
                    season_num = int(s_match.group(1)) if s_match else 1
                    _, _, resolution, _, _ = parse_media_name(videos[0].name)

                    # 1. Season Pack — whole season as one torrent
                    pack_name = f"{show_title} S{season_num:02d}"
                    if show_year:
                        pack_name = f"{show_title} ({show_year}) S{season_num:02d}"

                    items.append({
                        "path": str(season_dir),
                        "name": season_dir.name,
                        "title": show_title,
                        "year": show_year,
                        "category": category,
                        "resolution": resolution,
                        "size": total_size,
                        "file_count": len(videos),
                        "season": season_num,
                        "episode": None,
                        "season_pack": 1,
                        "upload_name": f"{pack_name} {resolution} Season Pack",
                    })

                    # 2. Individual Episodes — each episode as its own torrent
                    for video_file in videos:
                        ep_title, ep_year, ep_res, ep_season, ep_episode = parse_media_name(video_file.name)
                        if not ep_episode:
                            # Try to extract episode number from filename
                            ep_match = re.search(r"[Ee](\d{1,3})", video_file.name)
                            if ep_match:
                                ep_episode = int(ep_match.group(1))
                            else:
                                # Number-based (01, 02, etc.)
                                num_match = re.search(r"(?:^|\D)(\d{1,3})(?:\D|$)", video_file.stem)
                                if num_match:
                                    ep_episode = int(num_match.group(1))

                        ep_name = f"{show_title} S{season_num:02d}E{ep_episode:02d}" if ep_episode else f"{show_title} S{season_num:02d} - {video_file.stem}"
                        if show_year:
                            ep_name = f"{show_title} ({show_year}) S{season_num:02d}E{ep_episode:02d}" if ep_episode else ep_name

                        items.append({
                            "path": str(video_file),
                            "name": video_file.name,
                            "title": show_title,
                            "year": show_year,
                            "category": category,
                            "resolution": ep_res,
                            "size": video_file.stat().st_size,
                            "file_count": 1,
                            "season": season_num,
                            "episode": ep_episode,
                            "season_pack": 0,
                            "upload_name": f"{ep_name} {ep_res}",
                        })
            else:
                # Movie or single-folder content
                videos = [f for f in entry.rglob("*") if f.suffix.lower() in VIDEO_EXTENSIONS]
                if videos:
                    total_size = sum(f.stat().st_size for f in videos)
                    title, year, resolution, season, episode = parse_media_name(entry.name)

                    upload_name = title
                    if year:
                        upload_name = f"{title} {year}"
                    if season and episode:
                        upload_name = f"{title} S{season:02d}E{episode:02d}"
                    elif season:
                        upload_name = f"{title} S{season:02d}"

                    items.append({
                        "path": str(entry),
                        "name": entry.name,
                        "title": title,
                        "year": year,
                        "category": category,
                        "resolution": resolution,
                        "size": total_size,
                        "file_count": len(videos),
                        "season": season,
                        "episode": episode,
                        "season_pack": 1 if (season and not episode) else 0,
                        "upload_name": upload_name,
                    })
        elif entry.suffix.lower() in VIDEO_EXTENSIONS:
            title, year, resolution, season, episode = parse_media_name(entry.name)

            upload_name = title
            if year:
                upload_name = f"{title} {year}"
            if season and episode:
                upload_name = f"{title} S{season:02d}E{episode:02d}"

            items.append({
                "path": str(entry),
                "name": entry.name,
                "title": title,
                "year": year,
                "category": category,
                "resolution": resolution,
                "size": entry.stat().st_size,
                "file_count": 1,
                "season": season,
                "episode": episode,
                "season_pack": 0,
                "upload_name": upload_name,
            })

    return items

async def search_tmdb(title: str, year: str = None, media_type: str = "movie"):
    """Search TMDB for metadata."""
    try:
        query = title.replace(" ", "+")
        url = f"https://api.themoviedb.org/3/search/{media_type}?api_key={CONFIG['tmdb_api_key']}&query={query}&language=en-US"
        if year:
            url += f"&year={year}" if media_type == "movie" else f"&first_air_date_year={year}"

        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
                if data.get("results"):
                    result = data["results"][0]
                    return {
                        "id": result["id"],
                        "title": result.get("title") or result.get("name"),
                        "year": (result.get("release_date") or result.get("first_air_date", ""))[:4],
                        "overview": result.get("overview", ""),
                        "poster": result.get("poster_path"),
                        "backdrop": result.get("backdrop_path"),
                        "vote_average": result.get("vote_average", 0),
                    }
    except Exception:
        pass
    return None

def create_torrent(path: str, announce_url: str, output_path: str):
    """Create a .torrent file using torf."""
    try:
        t = Torrent(path=path, trackers=[announce_url], private=True, comment="Downloaded from EchelonHD", piece_size_max=16 * 1024 * 1024)
        t.generate()
        t.write(output_path, overwrite=True)
        return t.infohash
    except Exception as e:
        return None

async def add_to_qbit(torrent_path: str, save_path: str):
    """Add torrent to qBittorrent."""
    try:
        import qbittorrentapi
        client = qbittorrentapi.Client(host=CONFIG["qbit_url"], username=CONFIG["qbit_user"], password=CONFIG["qbit_pass"])
        client.auth_log_in()
        client.torrents_add(torrent_files=torrent_path, save_path=save_path, is_skip_checking=True)
        return True
    except Exception:
        return False

async def register_on_tracker(item: dict, torrent_hash: str, torrent_path: str, tmdb_data: dict = None):
    """Register torrent on EchelonHD via upload-manager API."""
    import base64
    try:
        endpoint = CONFIG["tracker_api_endpoint"]
        if not endpoint:
            return {"success": False, "error": "No API endpoint configured"}

        # Read torrent file and base64 encode it
        with open(torrent_path, "rb") as f:
            torrent_data = base64.b64encode(f.read()).decode("ascii")

        # Map category names
        cat_map = {"movies": "Movies", "shows": "TV", "anime": "Anime"}
        category = cat_map.get(item["category"], "Movies")

        upload_name = item.get("upload_name", f"{item['title']} {item.get('year', '')}".strip())

        payload = {
            "password": CONFIG["tracker_api_password"],
            "name": upload_name,
            "category": category,
            "resolution": item["resolution"],
            "torrent_data": torrent_data,
            "size": item["size"],
            "tmdb_id": tmdb_data["id"] if tmdb_data else None,
            "tmdb_type": "tv" if item["category"] in ("shows",) else "movie",
            "season": item.get("season"),
            "episode": item.get("episode"),
            "season_pack": item.get("season_pack", 0),
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(endpoint, json=payload, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                result = await resp.json()
                if result.get("success"):
                    return {"success": True, "id": result.get("id"), "name": result.get("name")}
                else:
                    return {"success": False, "error": result.get("error", "Unknown error")}
    except Exception as e:
        return {"success": False, "error": str(e)}

# ── Background Scanner ──

scan_state = {"running": False, "progress": 0, "total": 0, "current": "", "last_scan": None}

async def run_scan():
    """Full scan of all media directories."""
    global scan_state
    if scan_state["running"]:
        return

    scan_state = {"running": True, "progress": 0, "total": 0, "current": "Initializing...", "last_scan": None}
    db = get_db()

    try:
        # Phase 1: Scan directories
        all_items = []
        for category, path in MEDIA_DIRS.items():
            scan_state["current"] = f"Scanning {category}..."
            items = scan_directory(path, category)
            all_items.extend(items)
            db.execute("INSERT OR IGNORE INTO scan_logs (message, level) VALUES (?, ?)",
                       (f"Found {len(items)} items in {category}", "info"))

        scan_state["total"] = len(all_items)
        db.execute("REPLACE INTO stats (key, value) VALUES ('total_scanned', ?)", (str(len(all_items)),))

        # Phase 2: Process each item
        announce_url = f"{CONFIG['tracker_url']}/announce/{CONFIG['tracker_passkey']}"
        uploaded = 0
        skipped = 0

        for i, item in enumerate(all_items):
            scan_state["progress"] = i + 1
            scan_state["current"] = item["title"]

            # Check if already processed
            existing = db.execute("SELECT status FROM scanned_items WHERE path = ?", (item["path"],)).fetchone()
            if existing and existing["status"] in ("uploaded", "skipped"):
                skipped += 1
                continue

            # Save to DB
            db.execute("""INSERT OR REPLACE INTO scanned_items (path, name, title, year, category, resolution, size, season, episode, season_pack, status)
                          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'processing')""",
                       (item["path"], item["name"], item["title"], item["year"], item["category"], item["resolution"], item["size"],
                        item.get("season"), item.get("episode"), item.get("season_pack", 0)))
            db.commit()

            # TMDB lookup
            tmdb_type = "tv" if item["category"] == "shows" else "movie"
            tmdb_data = await search_tmdb(item["title"], item["year"], tmdb_type)
            if tmdb_data:
                db.execute("UPDATE scanned_items SET tmdb_id = ?, tmdb_type = ? WHERE path = ?",
                           (tmdb_data["id"], tmdb_type, item["path"]))

            # Create .torrent
            upload_name = item.get("upload_name", f"{item['title']} {item.get('year', '')}".strip())
            torrent_filename = re.sub(r'[^\w\s-]', '', upload_name).replace(" ", "_") + ".torrent"
            torrent_path = f"/torrents/{torrent_filename}"
            infohash = create_torrent(item["path"], announce_url, torrent_path)

            if not infohash:
                db.execute("UPDATE scanned_items SET status = 'error', error = 'Failed to create torrent' WHERE path = ?", (item["path"],))
                db.commit()
                continue

            db.execute("UPDATE scanned_items SET torrent_hash = ? WHERE path = ?", (infohash, item["path"]))

            # Register on tracker
            reg_result = await register_on_tracker(item, infohash, torrent_path, tmdb_data)

            if not reg_result.get("success"):
                error_msg = reg_result.get("error", "Registration failed")
                if error_msg in ("Duplicate", "Duplicate hash"):
                    db.execute("UPDATE scanned_items SET status = 'skipped', error = ? WHERE path = ?", (error_msg, item["path"]))
                    db.commit()
                    skipped += 1
                    continue
                else:
                    db.execute("UPDATE scanned_items SET status = 'error', error = ? WHERE path = ?", (f"Register: {error_msg}", item["path"]))
                    db.commit()
                    continue

            # Add to qBit
            parent_path = str(Path(item["path"]).parent) if Path(item["path"]).is_file() else str(Path(item["path"]).parent)
            qbit_added = await add_to_qbit(torrent_path, parent_path)

            if qbit_added:
                db.execute("UPDATE scanned_items SET status = 'uploaded', uploaded_at = ? WHERE path = ?",
                           (datetime.now().isoformat(), item["path"]))
                uploaded += 1
            else:
                db.execute("UPDATE scanned_items SET status = 'error', error = 'Failed to add to qBit' WHERE path = ?", (item["path"],))

            db.commit()

            # Small delay to not overwhelm APIs
            await asyncio.sleep(0.5)

        # Save stats
        db.execute("REPLACE INTO stats (key, value) VALUES ('last_scan', ?)", (datetime.now().isoformat(),))
        db.execute("REPLACE INTO stats (key, value) VALUES ('last_uploaded', ?)", (str(uploaded),))
        db.execute("REPLACE INTO stats (key, value) VALUES ('last_skipped', ?)", (str(skipped),))
        db.execute("INSERT INTO scan_logs (message, level) VALUES (?, ?)",
                   (f"Scan complete: {uploaded} uploaded, {skipped} skipped", "success"))
        db.commit()

    except Exception as e:
        db.execute("INSERT INTO scan_logs (message, level) VALUES (?, ?)", (f"Scan error: {str(e)}", "error"))
        db.commit()
    finally:
        scan_state["running"] = False
        scan_state["current"] = "Complete"
        scan_state["last_scan"] = datetime.now().isoformat()
        db.close()

# ── Routes ──

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    db = get_db()

    # Stats
    total_items = db.execute("SELECT COUNT(*) as c FROM scanned_items").fetchone()["c"]
    uploaded = db.execute("SELECT COUNT(*) as c FROM scanned_items WHERE status = 'uploaded'").fetchone()["c"]
    errors = db.execute("SELECT COUNT(*) as c FROM scanned_items WHERE status = 'error'").fetchone()["c"]
    pending = db.execute("SELECT COUNT(*) as c FROM scanned_items WHERE status IN ('scanned', 'processing')").fetchone()["c"]

    total_size = db.execute("SELECT COALESCE(SUM(size), 0) as s FROM scanned_items").fetchone()["s"]

    # Category breakdown
    categories = db.execute("SELECT category, COUNT(*) as c, SUM(size) as s FROM scanned_items GROUP BY category").fetchall()

    # Recent activity
    recent = db.execute("SELECT * FROM scanned_items ORDER BY created_at DESC LIMIT 20").fetchall()

    # Logs
    logs = db.execute("SELECT * FROM scan_logs ORDER BY created_at DESC LIMIT 10").fetchall()

    # Library stats (quick scan without DB)
    library_stats = {}
    for cat, path in MEDIA_DIRS.items():
        p = Path(path)
        if p.exists():
            count = sum(1 for _ in p.iterdir() if not _.name.startswith("."))
            library_stats[cat] = count
        else:
            library_stats[cat] = 0

    last_scan = db.execute("SELECT value FROM stats WHERE key = 'last_scan'").fetchone()

    db.close()

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "total_items": total_items,
        "uploaded": uploaded,
        "errors": errors,
        "pending": pending,
        "total_size": total_size,
        "categories": categories,
        "recent": recent,
        "logs": logs,
        "library_stats": library_stats,
        "scan_state": scan_state,
        "last_scan": last_scan["value"] if last_scan else None,
    })

@app.post("/api/scan")
async def start_scan(background_tasks: BackgroundTasks):
    if scan_state["running"]:
        return JSONResponse({"error": "Scan already running"}, status_code=409)
    background_tasks.add_task(run_scan)
    return {"status": "started"}

@app.get("/api/scan/status")
async def scan_status():
    return scan_state

@app.get("/api/items")
async def list_items(status: str = None, category: str = None, limit: int = 50, offset: int = 0):
    db = get_db()
    query = "SELECT * FROM scanned_items WHERE 1=1"
    params = []
    if status:
        query += " AND status = ?"
        params.append(status)
    if category:
        query += " AND category = ?"
        params.append(category)
    query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    items = db.execute(query, params).fetchall()
    db.close()
    return [dict(item) for item in items]

@app.get("/api/stats")
async def get_stats():
    db = get_db()
    stats = {row["key"]: row["value"] for row in db.execute("SELECT * FROM stats").fetchall()}

    # qBit status
    qbit_status = {"online": False, "torrents": 0, "dl_speed": 0, "up_speed": 0}
    try:
        import qbittorrentapi
        client = qbittorrentapi.Client(host=CONFIG["qbit_url"], username=CONFIG["qbit_user"], password=CONFIG["qbit_pass"])
        client.auth_log_in()
        info = client.transfer_info()
        qbit_status = {
            "online": True,
            "torrents": len(client.torrents_info()),
            "dl_speed": info.get("dl_info_speed", 0),
            "up_speed": info.get("up_info_speed", 0),
        }
    except Exception:
        pass

    db.close()
    return {"stats": stats, "qbit": qbit_status, "scan": scan_state}

@app.delete("/api/items/{item_id}")
async def delete_item(item_id: int):
    db = get_db()
    db.execute("DELETE FROM scanned_items WHERE id = ?", (item_id,))
    db.commit()
    db.close()
    return {"status": "deleted"}

@app.post("/api/items/{item_id}/retry")
async def retry_item(item_id: int):
    db = get_db()
    db.execute("UPDATE scanned_items SET status = 'scanned', error = NULL WHERE id = ?", (item_id,))
    db.commit()
    db.close()
    return {"status": "queued"}

@app.post("/api/reset")
async def reset_all():
    """Clear all scanned items and stats — fresh start."""
    db = get_db()
    db.execute("DELETE FROM scanned_items")
    db.execute("DELETE FROM stats")
    db.execute("DELETE FROM scan_logs")
    db.commit()
    db.close()
    return {"status": "reset"}
