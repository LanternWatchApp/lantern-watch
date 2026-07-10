#!/usr/bin/env python3
import json
import sqlite3
import time
import urllib.request
import base64
from datetime import datetime

DB_PATH = "/root/lantern-watch/lanternwatch.db"
CONFIG_PATH = "/root/lantern-watch/lanternwatch_config.json"
POLL_INTERVAL = 30

# AdGuard returns at most PAGE_LIMIT entries per request (newest first). On a
# busy network more than that can accumulate between polls, so we page backward
# with older_than until we either get a short page or reach data we already have.
# MAX_PAGES caps a single poll so a huge backlog (e.g. first run) can't run away.
PAGE_LIMIT = 500
MAX_PAGES  = 40   # up to PAGE_LIMIT * MAX_PAGES = 20,000 entries per poll

def load_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except:
        return {
            "adguard": {
                "url": "http://127.0.0.1:3000",
                "username": "",
                "password": ""
            }
        }

def get_label(client_ip, client_name, config):
    devices = config.get("devices", {})
    if client_name and client_name in devices:
        return devices[client_name].get("label", client_name)
    if client_ip and client_ip in devices:
        return devices[client_ip].get("label", client_ip)
    return client_name or client_ip

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS querylog (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT,
        client_ip TEXT,
        client_name TEXT,
        domain TEXT,
        qtype TEXT,
        blocked INTEGER,
        reason TEXT,
        elapsed_ms REAL,
        filter_id INTEGER,
        UNIQUE(ts, client_ip, domain, qtype)
    )
    """)
    # Migration for DBs created before filter_id existed (which blocklist caught a
    # block — needed to notify on adult/gambling/dating list hits).
    try:
        conn.execute("ALTER TABLE querylog ADD COLUMN filter_id INTEGER")
    except Exception:
        pass
    conn.commit()
    conn.close()

def get_last_ts():
    """Return the newest ts already stored, or None if the table is empty."""
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT MAX(ts) FROM querylog").fetchone()
    conn.close()
    return row[0] if row and row[0] else None


def _ts_key(ts):
    """Best-effort parse of an AGH RFC3339(Nano) timestamp into a comparable
    datetime. Returns None if it can't be parsed (caller then skips the
    timestamp-based stop and relies on short-page / MAX_PAGES instead)."""
    if not ts:
        return None
    s = ts.strip().replace("Z", "+00:00")
    # Python 3.9's fromisoformat can't handle 9-digit (nano) fractions; truncate
    # the fractional part to 6 digits while preserving any trailing tz offset.
    if "." in s:
        head, rest = s.split(".", 1)
        frac, tz = "", ""
        for i, ch in enumerate(rest):
            if ch.isdigit():
                frac += ch
            else:
                tz = rest[i:]
                break
        s = head + "." + frac[:6] + tz
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def fetch_querylog(config, older_than=None):
    ag = config.get("adguard", {})
    url = ag.get("url", "http://127.0.0.1:3000")
    user = ag.get("username", "")
    pwd = ag.get("password", "")
    api_url = f"{url}/control/querylog?limit={PAGE_LIMIT}"
    if older_than:
        from urllib.parse import quote
        api_url += f"&older_than={quote(older_than)}"
    req = urllib.request.Request(api_url)
    auth = base64.b64encode(f"{user}:{pwd}".encode()).decode()
    req.add_header("Authorization", f"Basic {auth}")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())

def store_entries(entries, config):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    saved = 0
    for item in entries:
        if not isinstance(item, dict):
            continue
        try:
            ts = item.get("time")
            client_ip = item.get("client", "")
            ci = item.get("client_info")
            raw_name = (ci.get("name", "") if isinstance(ci, dict) else "") or client_ip
            client_name = raw_name
            question = item.get("question")
            if not isinstance(question, dict):
                question = {}
            domain = question.get("name", "")
            qtype = question.get("type", "")
            blocked = 1 if item.get("reason", "").startswith("Filtered") else 0
            reason = item.get("reason", "")
            elapsed = float(item.get("elapsedMs", 0))
            filter_id = None
            for _rule in (item.get("rules") or []):
                if isinstance(_rule, dict) and _rule.get("filter_list_id") is not None:
                    filter_id = _rule.get("filter_list_id")
                    break
            result = conn.execute("""
            INSERT OR IGNORE INTO querylog
            (ts, client_ip, client_name, domain, qtype, blocked, reason, elapsed_ms, filter_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (ts, client_ip, client_name, domain, qtype, blocked, reason, elapsed, filter_id))
            if result.rowcount > 0:
                saved += 1
        except Exception as e:
            print(f"Insert error: {e}")
    conn.commit()
    conn.close()
    return saved

def get_storage_info():
    """Returns (used_mb, free_mb, total_mb, usb_path)"""
    import os
    stat = os.statvfs("/root")
    total = stat.f_blocks * stat.f_frsize / 1024 / 1024
    free = stat.f_bavail * stat.f_frsize / 1024 / 1024
    used = total - free

    # Check for USB drive
    usb_path = None
    for mount in ["/mnt/sda1", "/mnt/usb", "/mnt/sda"]:
        if os.path.ismount(mount):
            usb_path = mount
            break

    return round(used), round(free), round(total), usb_path

def trim_querylog(config):
    """Delete querylog rows older than the configured retention window."""
    import os
    used, free, total, usb_path = get_storage_info()
    db_size = os.path.getsize(DB_PATH) / 1024 / 1024
    pct_used = round(used / total * 100) if total > 0 else 0

    print(f"[{time.strftime('%H:%M:%S')}] Storage: {free}MB free of {total}MB ({pct_used}%) | DB: {db_size:.1f}MB | USB: {usb_path or 'none'}")

    days = int(config.get("retention_days", 60))
    # Storage pressure override: >80% used → force 7-day window
    if pct_used > 80:
        days = min(days, 7)
        print(f"[{time.strftime('%H:%M:%S')}] Storage >80% — overriding retention to 7 days")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM querylog WHERE ts < datetime('now', ?)", (f"-{days} days",))
    deleted = conn.execute("SELECT changes()").fetchone()[0]
    conn.commit()
    conn.close()
    if deleted:
        print(f"[{time.strftime('%H:%M:%S')}] Trimmed {deleted:,} records older than {days} days")

def collect_once(config):
    """Fetch the query log, paging backward from newest until we reach data we
    already have (or run out). Returns (total_fetched, total_saved).

    Paging is necessary because AGH caps each response at PAGE_LIMIT entries;
    without it, anything beyond the newest PAGE_LIMIT queries since the last
    poll is silently lost on busy networks. INSERT OR IGNORE still guards
    against double-counting overlap, but is no longer the only thing keeping
    the log complete."""
    last_key = _ts_key(get_last_ts())
    older_than = None
    total_fetched = 0
    total_saved   = 0

    for _ in range(MAX_PAGES):
        data    = fetch_querylog(config, older_than=older_than)
        entries = data.get("data", [])
        if not entries:
            break

        total_fetched += len(entries)
        total_saved   += store_entries(entries, config)

        # A short page means there is nothing older behind it.
        if len(entries) < PAGE_LIMIT:
            break

        oldest = data.get("oldest") or entries[-1].get("time", "")
        if not oldest:
            break

        # Stop once this page has reached entries at/older than what we've
        # already stored — everything newer than last_stored is now captured.
        oldest_key = _ts_key(oldest)
        if last_key and oldest_key and oldest_key <= last_key:
            break

        print(f"[collector] Paginating: fetched {len(entries)} entries, continuing from {oldest}")
        older_than = oldest

    return total_fetched, total_saved


def main():
    init_db()
    print("Lantern Watch collector started")
    config = load_config()
    storage_counter = 0

    while True:
        try:
            config = load_config()
            fetched, saved = collect_once(config)
            if fetched:
                print(f"[{time.strftime('%H:%M:%S')}] Polled {fetched} entries, {saved} new")
            else:
                print(f"[{time.strftime('%H:%M:%S')}] No entries")

        except Exception as e:
            print(f"Poll error: {e}")
        # Trim querylog once per day (2880 polls × 30s = 24h)
        storage_counter += 1
        if storage_counter >= 2880:
            trim_querylog(config)
            storage_counter = 0

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
