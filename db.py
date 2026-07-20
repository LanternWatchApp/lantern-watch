#!/usr/bin/env python3
"""
Lantern Watch — db.py
SQLite query helpers and data-access functions.
"""

import sqlite3
from datetime import datetime

DB_PATH     = "/root/lantern-watch/lanternwatch.db"
SESSION_GAP = 300  # seconds of silence before a new "session" starts


def get_or_create_install_id():
    """Return a random, anonymous per-install ID — generated once and stored
    locally in the app's own database.

    It is deliberately NOT derived from the MAC address or any hardware
    identifier. A random, resettable ID is the privacy-respecting approach the
    major platforms moved to (Apple removed MAC/UDID access; Google made its ID
    resettable): it lets us count installs without carrying a device
    fingerprint. A factory reset simply mints a new ID and counts as a fresh
    install — which is the honest thing anyway."""
    import uuid
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    row = conn.execute("SELECT value FROM meta WHERE key='install_id'").fetchone()
    if row:
        install_id = row[0]
    else:
        install_id = str(uuid.uuid4())
        conn.execute("INSERT INTO meta (key, value) VALUES ('install_id', ?)", (install_id,))
        conn.commit()
    conn.close()
    return install_id


def init_notifications_table():
    """Create the notifications log table if it doesn't exist."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            ts      TEXT,
            title   TEXT,
            message TEXT,
            topic   TEXT
        )
    """)
    conn.commit()
    conn.close()


def log_notification(title, message, topic=""):
    """Write a sent notification to the DB log."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT INTO notifications (ts, title, message, topic) VALUES (?, ?, ?, ?)",
            (datetime.now().isoformat(), title, message, topic),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"log_notification error: {e}")


def get_notifications(limit=100):
    """Return the most recent notifications, newest first."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, ts, title, message, topic FROM notifications ORDER BY ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        return rows
    except Exception:
        return []


def clear_notifications():
    """Delete all notification log entries."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("DELETE FROM notifications")
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"clear_notifications error: {e}")


def purge_old_notifications(days=30):
    """Delete notifications older than the given number of days."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "DELETE FROM notifications WHERE ts < datetime('now', ?)",
            (f"-{days} days",),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"purge_old_notifications error: {e}")


# ── Small utilities ───────────────────────────────────────────────────────────

def is_noise(domain):
    """Return True for internal / infrastructure domains that clutter the UI."""
    if not domain:             return True
    if domain.endswith(".arpa"):   return True
    if domain.endswith(".lan"):    return True
    if domain.endswith(".local"):  return True
    if "_ldap"  in domain:     return True
    if "_msdcs" in domain:     return True
    return False


def fmt_time(secs):
    """Format a duration in seconds as a human-readable string."""
    if secs < 60:   return f"{secs}s"
    if secs < 3600: return f"{secs // 60}m"
    return f"{secs // 3600}h {(secs % 3600) // 60}m"


def parse_ts(ts):
    """Parse an AdGuard timestamp string into a datetime (best-effort)."""
    if not ts:
        return datetime.now()
    ts = ts.replace("Z", "")
    if "." in ts:
        base, frac = ts.split(".", 1)
        ts = base + "." + frac[:6].ljust(6, "0")
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return datetime.now()


# ── Day boundary ─────────────────────────────────────────────────────────────

def today_start():
    """
    Return the SQLite datetime string for the start of 'today',
    defined as 2:00 AM local time.
    If it's currently before 2 AM, 'today' started at 2 AM yesterday.
    """
    now = datetime.now()
    if now.hour < 2:
        # Before 2AM — today started at 2AM yesterday
        from datetime import timedelta
        base = (now - timedelta(days=1)).replace(hour=2, minute=0, second=0, microsecond=0)
    else:
        base = now.replace(hour=2, minute=0, second=0, microsecond=0)
    return base.strftime("%Y-%m-%d %H:%M:%S")


# ── Screen time ───────────────────────────────────────────────────────────────

def _sum_screen_time(rows):
    """Sum session durations from an ordered list of ts rows."""
    if len(rows) < 2:
        return 0
    total = 0
    prev  = parse_ts(rows[0]["ts"])
    for row in rows[1:]:
        curr = parse_ts(row["ts"])
        diff = (curr - prev).total_seconds()
        if diff < SESSION_GAP:
            total += diff
        prev = curr
    return int(total)


def get_screen_time(conn, name):
    """Estimate online time for a device since 2AM today, queried by client_name."""
    rows = conn.execute(
        "SELECT ts FROM querylog WHERE client_name=? AND ts > ? ORDER BY ts ASC",
        (name, today_start()),
    ).fetchall()
    return _sum_screen_time(rows)


def get_screen_time_by_ip(conn, ip):
    """Estimate online time for a device since 2AM today, queried by client_ip."""
    rows = conn.execute(
        "SELECT ts FROM querylog WHERE client_ip=? AND ts > ? ORDER BY ts ASC",
        (ip, today_start()),
    ).fetchall()
    return _sum_screen_time(rows)


# ── Main dashboard query ──────────────────────────────────────────────────────

def get_stats(config):
    """Return all data needed to render the main dashboard page."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    since = today_start()

    devices = conn.execute("""
        SELECT client_name, client_ip, COUNT(*) as total, SUM(blocked) as blocked,
               MAX(ts) as last_seen
        FROM querylog WHERE ts > ?
        GROUP BY client_name ORDER BY total DESC
    """, (since,)).fetchall()

    totals = conn.execute("""
        SELECT COUNT(*) as total, SUM(blocked) as blocked
        FROM querylog WHERE ts > ?
    """, (since,)).fetchone()

    top_blocked = conn.execute("""
        SELECT domain, COUNT(*) as hits FROM querylog
        WHERE blocked=1 AND ts > ?
        GROUP BY domain ORDER BY hits DESC LIMIT 10
    """, (since,)).fetchall()

    top_domains = conn.execute("""
        SELECT domain, COUNT(*) as hits FROM querylog
        WHERE blocked=0 AND ts > ?
        GROUP BY domain ORDER BY hits DESC LIMIT 20
    """, (since,)).fetchall()

    adult_domains = conn.execute("""
        SELECT domain, COUNT(*) as hits, MAX(ts) as last_seen FROM querylog
        WHERE reason='FilteredParental' AND ts > ?
        GROUP BY domain ORDER BY hits DESC
    """, (since,)).fetchall()

    screen_times = {d["client_name"]: get_screen_time(conn, d["client_name"]) for d in devices}

    conn.close()

    clean = [r for r in top_domains if not is_noise(r["domain"])][:10]
    return devices, totals, top_blocked, clean, screen_times, adult_domains


# ── Device detail ─────────────────────────────────────────────────────────────

def get_device_detail(client_name):
    """Return all data needed for the per-device detail page."""
    import subprocess

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    since = today_start()

    totals = conn.execute(
        "SELECT COUNT(*) as total, SUM(blocked) as blocked FROM querylog "
        "WHERE client_name=? AND ts > ?",
        (client_name, since),
    ).fetchone()

    all_time = conn.execute(
        "SELECT COUNT(*) as total, SUM(blocked) as blocked, "
        "MIN(ts) as first_seen, MAX(ts) as last_seen FROM querylog WHERE client_name=?",
        (client_name,),
    ).fetchone()

    sites = conn.execute(
        "SELECT domain, COUNT(*) as hits FROM querylog "
        "WHERE client_name=? AND blocked=0 AND ts > ? "
        "GROUP BY domain ORDER BY hits DESC LIMIT 30",
        (client_name, since),
    ).fetchall()

    blocked_sites = conn.execute(
        "SELECT domain, COUNT(*) as hits FROM querylog "
        "WHERE client_name=? AND blocked=1 AND ts > ? "
        "GROUP BY domain ORDER BY hits DESC LIMIT 15",
        (client_name, since),
    ).fetchall()

    hourly = conn.execute(
        "SELECT strftime('%H', ts) as hour, COUNT(*) as hits FROM querylog "
        "WHERE client_name=? AND ts > ? "
        "GROUP BY hour ORDER BY hour ASC",
        (client_name, since),
    ).fetchall()

    peak_hour = conn.execute(
        "SELECT strftime('%H', ts) as hour, COUNT(*) as hits FROM querylog "
        "WHERE client_name=? AND ts > datetime('now', '-7 days') "
        "GROUP BY hour ORDER BY hits DESC LIMIT 1",
        (client_name,),
    ).fetchone()

    top_category = conn.execute(
        "SELECT reason, COUNT(*) as hits FROM querylog "
        "WHERE client_name=? AND blocked=1 AND ts > datetime('now', '-7 days') "
        "GROUP BY reason ORDER BY hits DESC LIMIT 1",
        (client_name,),
    ).fetchone()

    secs = get_screen_time(conn, client_name)
    conn.close()

    clean_sites = [r for r in sites if not is_noise(r["domain"])][:15]

    # Resolve IP address from hostname via nslookup
    ip_address = ""
    hostname   = client_name
    if client_name and client_name[0].isdigit():
        ip_address = client_name
    else:
        try:
            result = subprocess.run(
                ["nslookup", client_name, "127.0.0.1"],
                capture_output=True, text=True, timeout=3,
            )
            for line in result.stdout.split("\n"):
                if "Address:" in line and "127.0.0.1" not in line:
                    ip_address = line.split("Address:")[-1].strip()
                    break
        except Exception:
            ip_address = ""

    return totals, clean_sites, blocked_sites, hourly, secs, all_time, peak_hour, top_category, ip_address, hostname


def _parse_fids(s):
    """Parse a GROUP_CONCAT of filter_id values into a list of ints."""
    out = []
    for x in (s or "").split(","):
        x = x.strip()
        if x and x.lower() != "none":
            try:
                out.append(int(x))
            except ValueError:
                pass
    return out


def get_notable_blocks(explicit_domains, is_notable_service=None, is_family_list=None, limit=10):
    """Recent 'notable' blocked domains for the dashboard Blocked Content section:
    adult content, hits on a Family & Content blocklist (adult/gambling/dating),
    admin-chosen blocks (custom + category packs), and blocked services whose
    CATEGORY the parent chose to be notified about — NOT the ambient ad/tracker
    noise or chatty gaming/streaming telemetry.
    `explicit_domains` is the custom-block + pack set. `is_notable_service(domain)`
    flags a notify-enabled blocked SERVICE; `is_family_list(filter_ids)` flags a
    hit on a Family & Content blocklist (see adguard.filter_id_category_map)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    since = today_start()
    rows = conn.execute("""
        SELECT domain, COUNT(*) as hits, MAX(ts) as last_seen,
               GROUP_CONCAT(DISTINCT reason) as reasons,
               GROUP_CONCAT(DISTINCT filter_id) as filter_ids
        FROM querylog
        WHERE blocked=1 AND ts > ?
          AND (reason LIKE '%Parental%' OR reason = 'FilteredBlockedService'
               OR reason = 'FilteredBlackList')
        GROUP BY domain ORDER BY MAX(ts) DESC
    """, (since,)).fetchall()
    conn.close()
    exp = set(explicit_domains or [])
    out = []
    for r in rows:
        reasons = r["reasons"] or ""
        dom = (r["domain"] or "").lower()
        notable = "Parental" in reasons
        if not notable and "FilteredBlackList" in reasons:
            notable = any(dom == e or dom.endswith("." + e) for e in exp)
            if not notable and is_family_list:
                notable = is_family_list(_parse_fids(r["filter_ids"]))
        if not notable and "FilteredBlockedService" in reasons:
            notable = bool(is_notable_service and is_notable_service(dom))
        if notable:
            out.append(r)
            if len(out) >= limit:
                break
    return out


# ── Domain detail ─────────────────────────────────────────────────────────────

def get_domain_detail(domain):
    """Return all data needed for the per-domain detail page."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    since = today_start()

    entries = conn.execute(
        "SELECT ts, client_name, reason, elapsed_ms, qtype FROM querylog "
        "WHERE domain=? AND ts > ? ORDER BY ts DESC LIMIT 100",
        (domain, since),
    ).fetchall()

    summary = conn.execute(
        "SELECT client_name, COUNT(*) as attempts, MAX(ts) as last_seen FROM querylog "
        "WHERE domain=? AND ts > ? "
        "GROUP BY client_name ORDER BY attempts DESC",
        (domain, since),
    ).fetchall()

    total = conn.execute(
        "SELECT COUNT(*) as cnt, MIN(ts) as first, MAX(ts) as last FROM querylog "
        "WHERE domain=? AND ts > ?",
        (domain, since),
    ).fetchone()

    conn.close()
    return entries, summary, total


def clear_domain(domain):
    """Delete every query-log row for one domain, leaving the rest of the log
    intact. Returns the number of rows removed."""
    domain = (domain or "").strip()
    if not domain:
        return 0
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute("DELETE FROM querylog WHERE domain = ?", (domain,))
    conn.commit()
    deleted = cur.rowcount
    conn.close()
    return deleted


# ── Device management ─────────────────────────────────────────────────────────

def _dhcp_devices():
    devices = {}
    try:
        with open('/tmp/dhcp.leases') as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) >= 4:
                    ip = parts[2]
                    hostname = parts[3] if parts[3] != '*' else ''
                    devices[ip] = hostname
    except Exception:
        pass
    return devices

def get_all_known_devices(active_hours=168, include_idle=True):
    """Return devices that did DNS in the last `active_hours` (default 7 days).

    `include_idle` also appends currently-leased DHCP devices that haven't done
    any DNS in the window (handy for the pause-all sweep, noisy for the manage
    page). The window is applied in SQLite's own clock so it can't skew vs the
    stored timestamps."""
    win  = f"-{int(active_hours)} hours"
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT q.client_name, q.client_ip, counts.total, counts.last_seen
        FROM querylog q
        INNER JOIN (
            SELECT client_name, COUNT(*) as total, MAX(ts) as last_seen
            FROM querylog WHERE ts > datetime('now', ?)
            GROUP BY client_name
        ) counts ON q.client_name = counts.client_name AND q.ts = counts.last_seen
        GROUP BY q.client_name
        ORDER BY counts.total DESC
    """, (win,)).fetchall()
    conn.close()
    result = [dict(r) for r in rows]
    if include_idle:
        seen_ips = {r['client_ip'] for r in result}
        for ip, hostname in _dhcp_devices().items():
            if ip not in seen_ips:
                result.append({'client_name': hostname or ip, 'client_ip': ip, 'total': 0, 'last_seen': None})
    return result


# ── Storage info ──────────────────────────────────────────────────────────────

def get_storage_info():
    """Return a dict with flash storage and DB size information."""
    import os
    try:
        stat  = os.statvfs("/root")
        total = stat.f_blocks * stat.f_frsize / 1024 / 1024
        free  = stat.f_bavail * stat.f_frsize / 1024 / 1024
        used  = total - free
        db_size = os.path.getsize(DB_PATH) / 1024 / 1024

        usb_path = None
        for mount in ["/mnt/sda1", "/mnt/usb", "/mnt/sda"]:
            if os.path.ismount(mount):
                usb_path = mount
                break

        pct_used = round((used / total * 100) if total > 0 else 0)
        return {
            "free_mb":  round(free),
            "used_mb":  round(used),
            "total_mb": round(total),
            "db_mb":    round(db_size, 1),
            "pct_used": pct_used,
            "usb":      usb_path,
        }
    except Exception:
        return {}


def _dns_chain_status():
    """Check that AdGuard is on :3053 and dnsmasq (not AdGuard) owns :53."""
    import socket as _s, os as _os
    out = {"adguard_ok": False, "port_53_owner": "unknown"}
    try:
        c = _s.create_connection(("127.0.0.1", 3053), timeout=1)
        c.close()
        out["adguard_ok"] = True
    except Exception:
        pass
    inodes = set()
    for path in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(path) as f:
                next(f)
                for line in f:
                    cols = line.split()
                    if len(cols) >= 10 and cols[3] == "0A" and int(cols[1].split(":")[1], 16) == 53:
                        inodes.add(cols[9])
        except Exception:
            pass
    for pid in _os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            for fd in _os.listdir(f"/proc/{pid}/fd"):
                try:
                    lnk = _os.readlink(f"/proc/{pid}/fd/{fd}")
                    if lnk.startswith("socket:[") and lnk[8:-1] in inodes:
                        with open(f"/proc/{pid}/cmdline", "rb") as cf:
                            cmd = cf.read().decode("utf-8", errors="replace")
                        out["port_53_owner"] = "dnsmasq" if "dnsmasq" in cmd else ("adguard" if "AdGuardHome" in cmd else "other")
                        return out
                except Exception:
                    pass
        except Exception:
            pass
    return out


def get_router_health():
    """Read router system stats from /proc for the health card."""
    result = {
        "ram_used_mb": 0, "ram_total_mb": 0, "ram_pct": 0,
        "load_1": 0.0, "uptime_str": "unknown",
    }
    try:
        with open("/proc/meminfo") as f:
            mem = {}
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    mem[parts[0].rstrip(":")] = int(parts[1])
        total_kb = mem.get("MemTotal", 0)
        avail_kb = mem.get("MemAvailable", mem.get("MemFree", 0))
        used_kb  = total_kb - avail_kb
        result["ram_total_mb"] = round(total_kb / 1024)
        result["ram_used_mb"]  = round(used_kb  / 1024)
        result["ram_pct"]      = round(used_kb / total_kb * 100) if total_kb else 0
    except Exception:
        pass
    try:
        with open("/proc/loadavg") as f:
            result["load_1"] = float(f.read().split()[0])
    except Exception:
        pass
    try:
        with open("/proc/uptime") as f:
            secs = int(float(f.read().split()[0]))
        days  = secs // 86400
        hours = (secs % 86400) // 3600
        mins  = (secs % 3600) // 60
        if days:
            result["uptime_str"] = f"{days}d {hours}h {mins}m"
        elif hours:
            result["uptime_str"] = f"{hours}h {mins}m"
        else:
            result["uptime_str"] = f"{mins}m"
    except Exception:
        pass
    result.update(get_storage_info())
    result.update(_dns_chain_status())
    return result


def get_querylog_entries(device=None, blocked_only=False, window="1h", offset=0, limit=200, q=None):
    window_map = {"1h": "-1 hours", "6h": "-6 hours", "24h": "-24 hours", "7d": "-7 days",
                  "30d": "-30 days", "60d": "-60 days", "90d": "-90 days"}
    since_expr = window_map.get(window, "-1 hours")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    where  = ["ts > datetime('now', ?)"]
    params = [since_expr]
    if device:
        where.append("(client_name=? OR client_ip=?)")
        params.extend([device, device])
    if blocked_only:
        where.append("blocked=1")
    if q:
        where.append("domain LIKE ?")
        params.append(f"%{q}%")
    where_sql = " AND ".join(where)
    total = conn.execute(
        f"SELECT COUNT(*) FROM querylog WHERE {where_sql}", params
    ).fetchone()[0]
    rows = conn.execute(
        f"SELECT ts, client_name, client_ip, domain, qtype, blocked, reason, elapsed_ms "
        f"FROM querylog WHERE {where_sql} ORDER BY ts DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    conn.close()
    return rows, total


_safety_cache = {"t": 0.0, "data": None}

def compute_safety_score(config, ttl=90):
    """Home Safety Score (0-100) from AdGuard protection toggles, blocklists,
    DoH-bypass blocking, and device hygiene — plus a 7-day "blocked" tally as
    proof it's working. All inputs are real (no device scanning). Cached for
    `ttl` seconds so the dashboard's 60s refresh doesn't hammer the AGH API."""
    import time as _t
    now = _t.time()
    if _safety_cache["data"] is not None and now - _safety_cache["t"] < ttl:
        return _safety_cache["data"]

    connected = sb = par = ss = has_lists = doh = False
    try:
        from adguard import get_adguard_setup_status, get_doh_blocking_status
        st = get_adguard_setup_status(config)
        connected = st.get("connected", False)
        sb        = st.get("safe_browsing", False)
        par       = st.get("parental", False)
        ss        = st.get("safe_search", False)
        has_lists = len(st.get("existing_urls", set())) > 0
        if connected:
            try:
                doh = get_doh_blocking_status(config)
            except Exception:
                doh = bool(config.get("doh_blocking"))
    except Exception:
        pass

    # Active devices (24h) + how many aren't labeled yet.
    device_count = unknown = 0
    try:
        conn = sqlite3.connect(DB_PATH)
        seen = [n for (n,) in conn.execute("SELECT DISTINCT client_name FROM querylog "
                "WHERE ts > datetime('now','-24 hours')") if n]
        conn.close()
        device_count = len(seen)
        devs = config.get("devices", {})
        unknown = sum(1 for n in seen if n not in devs)
    except Exception:
        pass

    core_on    = connected and has_lists
    content_on = par or ss

    def _chk(lbl, ok, url, fix, detail=""):
        return {"label": lbl, "ok": ok, "fix_url": url, "fix_label": fix, "detail": detail}

    # CORE = protections an ordinary parent cares about (these drive the score).
    core = [
        _chk("Blocking ads, trackers &amp; malware",  core_on,    "/setup/adguard", "Finish setup"),
        _chk("Scam &amp; phishing protection",         sb,         "/setup/adguard", "Turn on"),
        _chk("Adult-site &amp; Safe Search filtering", content_on, "/setup/adguard", "Turn on"),
    ]
    # ADVANCED = optional hardening for power users — shown, but never tanks the score.
    advanced = [
        _chk("Encrypted-DNS bypass blocking", doh, "/admin#doh-setting", "Enable below",
             "blocks tech-savvy filter bypass; can affect some apps &amp; smart TVs"),
        _chk("Devices all labeled", unknown == 0, "/admin/devices",
             ("Name %d" % unknown), ("%d still show as IP only" % unknown if unknown else "")),
    ]
    score = (40 if core[0]["ok"] else 0) + (30 if core[1]["ok"] else 0) + (30 if core[2]["ok"] else 0)
    level = ("Fully protected" if score >= 90 else
             "Protected — one gap" if score >= 60 else "Needs setup")
    protected = core_on and sb   # reliable, reassuring "the core shield is up"

    # 7-day blocked tally by category (proof it's working).
    stats = {"phishing": 0, "adult": 0, "ads": 0, "services": 0}
    try:
        conn = sqlite3.connect(DB_PATH)
        for reason, cnt in conn.execute(
            "SELECT reason, COUNT(*) FROM querylog WHERE blocked=1 "
            "AND ts > datetime('now','-7 days') GROUP BY reason"):
            r = reason or ""
            if   r == "FilteredSafeBrowsing":    stats["phishing"] += cnt
            elif r == "FilteredParental":        stats["adult"]    += cnt
            elif r == "FilteredBlockedService":  stats["services"] += cnt
            else:                                stats["ads"]      += cnt
        conn.close()
    except Exception:
        pass

    data = {"protected": protected, "device_count": device_count,
            "score": score, "level": level, "core": core, "advanced": advanced,
            "stats": stats}
    _safety_cache["data"] = data
    _safety_cache["t"] = now
    return data


def get_top_domains_map(window="-7 days", per_device=3):
    """{client_name: [top N most-queried domains]} — a "what does this device
    talk to" hint that helps identify an otherwise-anonymous device."""
    out = {}
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        for r in conn.execute(
            "SELECT client_name, domain, COUNT(*) c FROM querylog "
            "WHERE ts > datetime('now', ?) AND domain != '' "
            "GROUP BY client_name, domain ORDER BY client_name, c DESC", (window,)):
            lst = out.setdefault(r["client_name"], [])
            if len(lst) < per_device:
                lst.append(r["domain"])
        conn.close()
    except Exception:
        pass
    return out


def get_recent_blocks(since=None, limit=50):
    """Most recent blocked DNS queries, newest first.

    Powers the dashboard's near-real-time "blocked activity" feed. `since` is an
    ISO timestamp; when given, only blocks strictly newer than it are returned,
    so the client can poll for *new* events without re-fetching the whole list.
    The collector observes blocks within one poll interval (~30s), which is the
    practical ceiling on "real-time" here — blocking itself happens in AdGuard at
    DNS time; this surfaces it without a custom push channel.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    where, params = ["blocked=1"], []
    if since:
        where.append("ts > ?")
        params.append(since)
    try:
        limit = max(1, min(int(limit), 200))
    except (TypeError, ValueError):
        limit = 50
    rows = conn.execute(
        f"SELECT ts, client_name, client_ip, domain, reason "
        f"FROM querylog WHERE {' AND '.join(where)} ORDER BY ts DESC LIMIT ?",
        params + [limit],
    ).fetchall()
    conn.close()
    return rows


def get_querylog_devices(window="1h"):
    window_map = {"1h": "-1 hours", "6h": "-6 hours", "24h": "-24 hours", "7d": "-7 days",
                  "30d": "-30 days", "60d": "-60 days", "90d": "-90 days"}
    since_expr = window_map.get(window, "-1 hours")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT DISTINCT client_name FROM querylog "
        "WHERE ts > datetime('now', ?) AND client_name != '' ORDER BY client_name",
        (since_expr,),
    ).fetchall()
    conn.close()
    return [r["client_name"] for r in rows if r["client_name"]]


def get_oldest_log_date():
    """Return a human-readable date string for the oldest record in the DB."""
    try:
        conn = sqlite3.connect(DB_PATH)
        row  = conn.execute("SELECT MIN(ts) as oldest FROM querylog").fetchone()
        conn.close()
        if row and row["oldest"]:
            dt = parse_ts(row["oldest"])
            return dt.strftime("%b %d, %Y")
    except Exception:
        pass
    return "90 days"
