#!/usr/bin/env python3
"""
Lantern Watch — alerts.py
Background alert checks, scheduled summaries, and notification logging.
"""

import sqlite3
import urllib.request
import json
import time
from datetime import datetime
from urllib.parse import quote

DB_PATH     = "/root/lantern-watch/lanternwatch.db"
CONFIG_PATH = "/root/lantern-watch/lanternwatch_config.json"
CHECK_INTERVAL = 60  # seconds


# ── Config ────────────────────────────────────────────────────────────────────

def _read_dhcp_leases():
    """Return {ip: hostname} from /tmp/dhcp.leases (best-effort)."""
    result = {}
    try:
        with open("/tmp/dhcp.leases") as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) >= 4 and parts[3] != "*":
                    result[parts[2]] = parts[3]
    except Exception:
        pass
    return result


def _pretty_hostname(raw):
    """Pixel-7-Pro.lan → Pixel 7 Pro"""
    if not raw:
        return ""
    name = raw.strip()
    for suffix in (".lan", ".local", ".home", ".internal"):
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)]
    name = name.replace("-", " ").replace("_", " ")
    return " ".join(w.capitalize() for w in name.split())


import re as _re
from pages import _demo  # demo-mode device-name override (no circular import: pages never imports alerts)
_IS_IP = _re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def label(name, config=None):
    if config:
        devices = config.get("devices", {})
        if name in devices:
            stored = devices[name].get("label", name)
            # If the stored label is still just an IP, fall through to DHCP lookup
            if not _IS_IP.match(stored):
                return stored
    # Resolve bare IPs via DHCP lease file
    if _IS_IP.match(name):
        hostname = _read_dhcp_leases().get(name, "")
        if hostname:
            return _pretty_hostname(hostname)
    return name


_VENDOR_DROP = {"co", "ltd", "inc", "llc", "corp", "corporation", "company",
                "pte", "gmbh", "ag", "sa", "bv", "limited", "technology",
                "technologies", "electronics", "electronic", "intl",
                "international", "communications", "communication"}

def _short_vendor(v):
    """Trim a long IEEE maker name to something readable: drop legal-suffix words
    (CO., LTD., PTE., Inc...) and keep the first few meaningful words."""
    words = [w for w in _re.sub(r"[,.]", " ", v).split()
             if w.lower().strip(".") not in _VENDOR_DROP]
    return " ".join(words[:3]) or v


def _friendly(name, config=None):
    """Best human-readable name for a client, so alerts/summaries never show a
    bare IP (which Telegram turns into a dead link). Order: saved label → DHCP
    hostname → MAC maker → device-kind guess ('Smart TV'), then the raw name."""
    lbl = label(name, config)
    if not lbl or _IS_IP.match(lbl):
        try:
            from classify import device_identity, device_kind
            idn = device_identity(name)
            if idn.get("hostname") and idn["hostname"] != name:
                lbl = idn["hostname"]
            elif idn.get("vendor"):
                lbl = f'{_short_vendor(idn["vendor"])} device'
            else:
                k = device_kind(name, "", idn, None)
                if k:
                    lbl = k[0].upper() + k[1:]   # "Smart TV", "Video doorbell"
        except Exception:
            pass
    if config and config.get("demo_mode"):
        return _demo(name, lbl, config)
    return lbl


def load_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except Exception:
        default = {
            "ntfy_topic": "",
            "alerts": {
                "adult_content": True,
                "new_device": True,
                "high_block_rate": True,
                "high_block_threshold": 50,
            },
            "summary": {"daily": True, "daily_hour": 21, "weekly": True, "weekly_day": 0},
            "known_devices": [],
        }
        save_config(default)
        return default


def save_config(config):
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)


# ── Notification logging ──────────────────────────────────────────────────────

def _init_notifications_table():
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


def _log_notification(title, message, topic):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT INTO notifications (ts, title, message, topic) VALUES (?, ?, ?, ?)",
            (datetime.now().isoformat(), title, message, topic),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Notification log error: {e}")


# ── Send ──────────────────────────────────────────────────────────────────────

def send_alert(topic, message, title="Lantern Watch", priority="default", tags="bell", click_url=""):
    """Send a push notification via ntfy and log it to the DB."""
    if not topic:
        print(f"[alerts] ntfy_topic is not configured — skipping alert: {title}")
        return
    from config import load_config
    if not load_config().get("ntfy_enabled", True):
        _log_notification(title, message, topic)  # ntfy off: keep the in-app log, skip the push
        return
    try:
        headers = {
            "Content-Type": "text/plain; charset=utf-8",
            "Title":    title.encode("utf-8").decode("latin-1", errors="ignore"),
            "Priority": priority,
            "Tags":     tags,
        }
        if click_url:
            headers["Click"] = click_url
        req = urllib.request.Request(
            f"https://ntfy.sh/{topic}",
            data=message.encode("utf-8"),
            headers=headers,
        )
        urllib.request.urlopen(req, timeout=10)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Alert sent (ntfy): {title}")
    except Exception as e:
        print(f"Alert error (ntfy): {e}")

    # Always log — even if send fails, we want a record of the attempt
    _log_notification(title, message, topic)


def send_telegram(config, message, title="Lantern Watch"):
    """Send a message to a Telegram chat via Bot API."""
    tg = config.get("telegram", {})
    if not tg.get("enabled", True):
        return
    token   = tg.get("bot_token", "").strip()
    chat_id = tg.get("chat_id", "").strip()
    if not token or not chat_id:
        return
    try:
        text    = f"*{title}*\n{message}"
        # Telegram auto-linkifies bare IPs into dead http:// links. Render any IP
        # in the body as monospace (code spans are never linkified), skipping the
        # Dashboard:/View activity: URL lines, which become real links just below.
        _ip = _re.compile(r"(?<![\w.])(\d{1,3}(?:\.\d{1,3}){3})(?![\w.])")
        text = "\n".join(
            ln if ln.lstrip().startswith(("Dashboard:", "View activity:"))
            else _ip.sub(r"`\1`", ln)
            for ln in text.split("\n")
        )
        # Telegram won't linkify a bare single-label host like "lanternwatch", so
        # render the dashboard / device lines as clean labeled Markdown links
        # (the URL target is the LAN IP, which is always reachable + linkable).
        text    = _re.sub(r"(?m)^Dashboard:\s*(\S+)\s*$",     r"[Open Dashboard](\1)", text)
        text    = _re.sub(r"(?m)^View activity:\s*(\S+)\s*$", r"[View activity](\1)", text)
        payload = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Alert sent (Telegram): {title}")
    except Exception as e:
        print(f"Alert error (Telegram): {e}")


def _email_html(message):
    """Build an HTML email body from a plain alert message, turning the
    'Dashboard:' / 'View activity:' lines into tappable labeled links."""
    import html as _html
    safe = _html.escape(message)
    safe = _re.sub(r"(?m)^Dashboard:\s*(\S+)\s*$",     r'<a href="\1">Open Dashboard</a>', safe)
    safe = _re.sub(r"(?m)^View activity:\s*(\S+)\s*$", r'<a href="\1">View activity</a>', safe)
    return ('<div style="font-family:sans-serif;font-size:14px;line-height:1.5">'
            + safe.replace("\n", "<br>") + "</div>")


def send_email(config, message, title="Lantern Watch"):
    """Send an alert email via SMTP (multipart: plain text + HTML with links)."""
    import smtplib, ssl
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    em = config.get("email", {})
    if not em.get("enabled", True):
        return
    host = em.get("smtp_host", "").strip()
    port = int(em.get("smtp_port", 587))
    user = em.get("smtp_user", "").strip()
    pwd  = em.get("smtp_password", "").strip()
    to   = em.get("to_address", "").strip()
    name = em.get("from_name", "Lantern Watch")
    if not (host and user and pwd and to):
        return
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[Lantern Watch] {title}"
        msg["From"]    = f"{name} <{user}>"
        msg["To"]      = to
        msg.attach(MIMEText(message, "plain", "utf-8"))
        msg.attach(MIMEText(_email_html(message), "html", "utf-8"))
        ctx = ssl.create_default_context()
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ctx)
            smtp.login(user, pwd)
            smtp.sendmail(user, to, msg.as_string())
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Alert sent (email): {title}")
    except Exception as e:
        print(f"Alert error (email): {e}")


# ── Day boundary (matches db.py) ──────────────────────────────────────────────

def today_start():
    """2:00 AM today, or 2:00 AM yesterday if it's before 2 AM."""
    from datetime import timedelta
    now = datetime.now()
    if now.hour < 2:
        base = (now - timedelta(days=1)).replace(hour=2, minute=0, second=0, microsecond=0)
    else:
        base = now.replace(hour=2, minute=0, second=0, microsecond=0)
    return base.strftime("%Y-%m-%d %H:%M:%S")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _dash_url(config):
    return config.get("dashboard_url", "http://192.168.8.1:8081")


def _append_url(message, config):
    # Include the full URL (with scheme) so it renders as a tappable link:
    # send_telegram turns it into a Markdown link and email clients auto-link it.
    # ntfy also has click_url for tap-to-open.
    return message + f"\n\nDashboard: {_dash_url(config)}"


# ── Alert checks ──────────────────────────────────────────────────────────────

def check_adult_content(config):
    if not config["alerts"].get("adult_content"):
        return
    last_alerted = config.get("last_adult_alert", "2000-01-01T00:00:00Z")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT client_name, domain, MAX(ts) as latest, COUNT(*) as hits
        FROM querylog
        WHERE reason LIKE '%Parental%' AND ts > ?
        GROUP BY client_name, domain ORDER BY hits DESC
    """, (last_alerted,)).fetchall()
    conn.close()
    if not rows:
        return
    newest_ts = max(r["latest"] for r in rows)
    base_url  = _dash_url(config)
    for row in rows:
        device = label(row["client_name"], config)
        domain = row["domain"]
        send_alert(
            config["ntfy_topic"],
            _append_url(f"{device} tried to access: {domain}", config),
            title="Blocked Content",
            priority="high",
            tags="warning",
            click_url=base_url,
        )
    # Single combined message for Telegram / Email
    if len(rows) == 1:
        combined = f"{label(rows[0]['client_name'], config)} tried to access: {rows[0]['domain']}"
    else:
        lines    = [f"• {label(r['client_name'], config)}: {r['domain']}" for r in rows]
        combined = f"{len(rows)} sites blocked:\n" + "\n".join(lines)
    combined = _append_url(combined, config)
    send_telegram(config, combined, "Blocked Content")
    send_email(config, combined, "Blocked Content")
    config["last_adult_alert"] = newest_ts
    save_config(config)


def check_new_devices(config):
    if not config["alerts"].get("new_device"):
        return
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT DISTINCT client_name FROM querylog
        WHERE ts > datetime('now', '-2 minutes')
    """).fetchall()
    conn.close()
    known = set(config.get("known_devices", []))
    for row in rows:
        name = row["client_name"]
        if name not in known:
            known.add(name)
            config["known_devices"] = list(known)
            save_config(config)
            friendly = _friendly(name, config)
            ip_suffix = f" ({name})" if (_IS_IP.match(name) and friendly != name) else ""
            msg = _append_url(f"New device joined: {friendly}{ip_suffix}", config)
            send_alert(config["ntfy_topic"], msg, title="New Device Detected", priority="default", tags="bell",
                       click_url=_dash_url(config))
            send_telegram(config, msg, "New Device Detected")
            send_email(config, msg, "New Device Detected")


def check_high_block_rate(config):
    if not config["alerts"].get("high_block_rate"):
        return
    threshold = config["alerts"].get("high_block_threshold", 50)
    cooldowns = config.get("high_block_cooldowns", {})
    now = datetime.now()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT client_name, COUNT(*) as total, SUM(blocked) as blocked
        FROM querylog WHERE ts > datetime('now', '-10 minutes')
        GROUP BY client_name HAVING total > 10
    """).fetchall()
    conn.close()
    changed = False
    for row in rows:
        if row["total"] > 0:
            pct = round(row["blocked"] / row["total"] * 100)
            if pct >= threshold:
                name    = row["client_name"]
                last    = cooldowns.get(name, "2000-01-01T00:00:00")
                last_dt = datetime.fromisoformat(last)
                if (now - last_dt).total_seconds() > 3600:
                    device = _friendly(name, config)
                    msg    = _append_url(
                        f"{device} has {pct}% block rate in last 10 min ({row['blocked']} of {row['total']} blocked)",
                        config,
                    )
                    send_alert(config["ntfy_topic"], msg, title="High Block Rate", priority="high", tags="warning",
                               click_url=_dash_url(config))
                    send_telegram(config, msg, "High Block Rate")
                    send_email(config, msg, "High Block Rate")
                    cooldowns[name] = now.isoformat()
                    changed = True
    if changed:
        config["high_block_cooldowns"] = cooldowns
        save_config(config)


def check_vpn_suspected(config):
    if not config.get("alerts", {}).get("vpn_detection", True):
        return
    if datetime.now().hour < 6:
        return
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    normally_active = conn.execute("""
        SELECT client_name, COUNT(*) as recent FROM querylog
        WHERE ts > datetime('now', '-1 hour')
        GROUP BY client_name HAVING recent > 50
    """).fetchall()
    whitelist = config.get("vpn_whitelist", [])
    from config import effective_type
    for device in normally_active:
        name = device["client_name"]
        if name in whitelist:
            continue
        # Work devices are expected to sit on a corporate VPN, which makes their
        # DNS go quiet from our view — that's normal, not suspicious. Auto-exempt
        # them so a work laptop never trips the "activity drop / possible VPN" alert.
        if effective_type(name, config) == "work_device":
            continue
        friendly = _friendly(name, config)
        recent = conn.execute("""
            SELECT COUNT(*) as cnt FROM querylog
            WHERE client_name=? AND ts > datetime('now', '-20 minutes')
        """, (name,)).fetchone()
        if recent["cnt"] < 5:
            cooldowns = config.get("vpn_cooldowns", {})
            last      = cooldowns.get(name, "2000-01-01T00:00:00")
            last_dt   = datetime.fromisoformat(last)
            if (datetime.now() - last_dt).total_seconds() > 7200:
                base_url    = _dash_url(config)
                device_url  = f"{base_url}/device?name={quote(name)}"
                msg = (
                    f"{friendly} was active but has gone quiet. "
                    f"This can happen when a VPN app is used.\n\n"
                    f"View activity: {device_url}\n"
                    f"Dashboard: {base_url}"
                )
                send_alert(
                    config["ntfy_topic"], msg,
                    title="Activity Drop Detected", priority="default",
                    tags="magnifying_glass", click_url=device_url,
                )
                send_telegram(config, msg, "Activity Drop Detected")
                send_email(config, msg, "Activity Drop Detected")
                cooldowns[name] = datetime.now().isoformat()
                config["vpn_cooldowns"] = cooldowns
                save_config(config)
    conn.close()


# ── Summaries ─────────────────────────────────────────────────────────────────

def _build_daily_narrative(config):
    """Turn today's query log into a short, parent-friendly recap — the
    'Timmy's iPad blocked 12 scam sites' moment — using templated sentences
    chosen from the data. Deterministic; no AI required."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    since   = today_start()
    totals  = conn.execute("SELECT COUNT(*) t, SUM(blocked) b FROM querylog WHERE ts > ?", (since,)).fetchone()
    catrows = conn.execute(
        "SELECT client_name, reason, COUNT(*) c FROM querylog "
        "WHERE blocked=1 AND ts > ? GROUP BY client_name, reason", (since,)).fetchall()
    seen    = conn.execute("SELECT DISTINCT client_name FROM querylog WHERE ts > ?", (since,)).fetchall()
    devrows = conn.execute(
        "SELECT client_name, COUNT(*) t, SUM(blocked) b FROM querylog "
        "WHERE ts > ? GROUP BY client_name ORDER BY t DESC", (since,)).fetchall()
    conn.close()

    cat    = {"phishing": 0, "adult": 0, "services": 0, "ads": 0}
    by_dev = {}
    for r in catrows:
        reason, c, dev = (r["reason"] or ""), r["c"], r["client_name"]
        if   reason == "FilteredSafeBrowsing":   k = "phishing"
        elif reason == "FilteredParental":       k = "adult"
        elif reason == "FilteredBlockedService": k = "services"
        else:                                    k = "ads"
        cat[k] += c
        if k in ("phishing", "adult"):
            by_dev.setdefault(dev, {"phishing": 0, "adult": 0})[k] += c

    total_b   = totals["b"] or 0
    devs_cfg  = config.get("devices", {})
    active    = [r["client_name"] for r in seen if r["client_name"]]
    unlabeled = [d for d in active if d not in devs_cfg]

    def nm(d):
        return _friendly(d, config)

    def top_for(key):
        best, best_c = None, 0
        for d, v in by_dev.items():
            if v.get(key, 0) > best_c:
                best, best_c = d, v[key]
        return best, best_c

    opener = ("🌙 Calm night — nothing concerning came up."
              if cat["phishing"] == 0 and cat["adult"] == 0
              else "🛡️ A couple of things worth knowing about.")

    lines = [f"Lantern Watch — {datetime.now().strftime('%A night, %b %d')}", "", opener, ""]

    if total_b:
        lines.append(f"Blocked {total_b:,} things across {len(active)} device(s):")
        if cat["phishing"]:
            d, c = top_for("phishing")
            extra = f' ({c} aimed at "{nm(d)}")' if d and c else ""
            lines.append(f"  • {cat['phishing']:,} scam / phishing / malware sites{extra}")
        if cat["adult"]:
            d, c = top_for("adult")
            extra = f' (mostly on "{nm(d)}")' if d and c else ""
            lines.append(f"  • {cat['adult']:,} content-filtered sites{extra}")
        if cat["services"]:
            lines.append(f"  • {cat['services']:,} blocked apps/services (social, games, etc.)")
        if cat["ads"]:
            lines.append(f"  • {cat['ads']:,} ads & trackers")
    else:
        lines.append("Nothing needed blocking — a quiet night.")

    if unlabeled:
        more = f" (+{len(unlabeled) - 1} more)" if len(unlabeled) > 1 else ""
        lines += ["", "👀 Worth a look:",
                  f'  • New device on the network: "{nm(unlabeled[0])}"{more} — tap to name it.']

    # Per-device breakdown — itemized queries + block rate, busiest first.
    rows = [r for r in devrows if (r["t"] or 0) > 0]
    if rows:
        lines += ["", "📊 By device:"]
        for r in rows:
            t    = r["t"] or 0
            dpct = round((r["b"] or 0) / t * 100) if t else 0
            lines.append(f"  {nm(r['client_name'])}: {t:,} queries, {dpct}% blocked")

    return "\n".join(lines)


def send_daily_summary(config):
    message = _append_url(_build_daily_narrative(config), config)
    topics  = [config.get("ntfy_topic", "")]
    extras  = config.get("extra_topics", "")
    if extras:
        topics += [t.strip() for t in extras.split(",") if t.strip()]
    for topic in [t for t in topics if t]:
        send_alert(topic, message, title="Lantern Watch Daily Summary", priority="default", tags="chart",
                   click_url=_dash_url(config))
    send_telegram(config, message, "Lantern Watch Daily Summary")
    send_email(config, message, "Lantern Watch Daily Summary")


def send_weekly_summary(config):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    devices = conn.execute("""
        SELECT client_name, COUNT(*) as total, SUM(blocked) as blocked
        FROM querylog WHERE ts > datetime('now', '-7 days')
        GROUP BY client_name ORDER BY total DESC
    """).fetchall()
    totals = conn.execute("""
        SELECT COUNT(*) as total, SUM(blocked) as blocked
        FROM querylog WHERE ts > datetime('now', '-7 days')
    """).fetchone()
    top_blocked = conn.execute("""
        SELECT domain, COUNT(*) as hits FROM querylog
        WHERE blocked=1 AND ts > datetime('now', '-7 days')
        GROUP BY domain ORDER BY hits DESC LIMIT 5
    """).fetchall()
    adult_count = conn.execute("""
        SELECT COUNT(*) as cnt FROM querylog
        WHERE reason='FilteredParental' AND ts > datetime('now', '-7 days')
    """).fetchone()
    conn.close()

    cfg_devices = config.get("devices", {})
    skip        = {cfg.get("label", name) for name, cfg in cfg_devices.items() if cfg.get("type") in ("infrastructure", "guest")}
    total_q = totals["total"] or 0
    total_b = totals["blocked"] or 0
    pct     = round((total_b / total_q * 100) if total_q > 0 else 0, 1)
    adult   = adult_count["cnt"] if adult_count else 0
    lines   = [
        f"Weekly Summary — {datetime.now().strftime('%b %d, %Y')}",
        f"Total queries: {total_q:,}",
        f"Total blocked: {total_b:,} ({pct}%)",
        f"Content blocked: {adult}",
        "",
        "Device breakdown:",
    ]
    for d in devices:
        if label(d["client_name"], config) in skip:
            continue
        dpct = round(d["blocked"] / d["total"] * 100) if d["total"] > 0 else 0
        lines.append(f"  {_demo(d['client_name'], label(d['client_name'], config), config)}: {d['total']:,} queries, {dpct}% blocked")
    if top_blocked:
        lines += ["", "Top blocked domains:"]
        for r in top_blocked:
            lines.append(f"  {r['domain']}: {r['hits']} times")

    message = _append_url("\n".join(lines), config)
    topics  = [config.get("ntfy_topic", "")]
    extras  = config.get("extra_topics", "")
    if extras:
        topics += [t.strip() for t in extras.split(",") if t.strip()]
    for topic in [t for t in topics if t]:
        send_alert(topic, message, title="Lantern Watch Weekly Summary", priority="default", tags="bar_chart",
                   click_url=_dash_url(config))
    send_telegram(config, message, "Lantern Watch Weekly Summary")
    send_email(config, message, "Lantern Watch Weekly Summary")


# ── Main loop ─────────────────────────────────────────────────────────────────

def _ensure_oui_db():
    """Download the device-maker (OUI) database if missing or older than 30 days.
    Public list only — no device data leaves the network."""
    try:
        import os
        from classify import refresh_oui_db, _OUI_DB_PATH
        if (not os.path.exists(_OUI_DB_PATH)
                or time.time() - os.path.getmtime(_OUI_DB_PATH) > 30 * 86400):
            refresh_oui_db()
    except Exception as e:
        print(f"[OUI] ensure failed: {e}")


def _ensure_doh_blocking():
    """Re-apply the DoH/DoT bypass-blocking firewall rules on startup if the user
    enabled them — the iptables rules don't survive a reboot (the AGH domain rules
    do). Keeps the protection whole after a power cycle."""
    try:
        config = load_config()
        if config.get("doh_blocking"):
            from adguard import apply_doh_iptables
            apply_doh_iptables(True)
            print("[DoH] re-applied bypass-blocking firewall rules on startup")
    except Exception as e:
        print(f"[DoH] startup re-apply failed: {e}")


def _telemetry_payload(config):
    """Anonymous install stats — a random ID, version, router model, feature
    on/off flags and a device COUNT. Never names, domains, IPs, or browsing."""
    from config import VERSION
    install_id = ""
    try:
        from db import get_or_create_install_id
        install_id = get_or_create_install_id()
    except Exception:
        pass
    router_model = "unknown"
    try:
        with open("/tmp/sysinfo/model") as f:
            router_model = f.read().strip() or "unknown"
    except Exception:
        pass
    openwrt_version = "unknown"
    try:
        with open("/etc/openwrt_release") as f:
            for line in f:
                if line.startswith("DISTRIB_RELEASE="):
                    openwrt_version = line.split("=", 1)[1].strip().strip('"\'')
                    break
    except Exception:
        pass
    try:
        from adguard import get_adguard_setup_status
        adguard_connected = bool(get_adguard_setup_status(config).get("connected"))
    except Exception:
        adguard_connected = False
    schedules      = config.get("schedules", {})
    social_profile = config.get("social_profile", "open")
    tg, em = config.get("telegram", {}), config.get("email", {})
    return {
        "install_id":       install_id,
        "version":          VERSION,
        "router_model":     router_model,
        "openwrt_version":  openwrt_version,
        "adguard_connected": adguard_connected,
        "device_count":     len(config.get("devices", {})),
        "social_profile":   social_profile,
        "features": {
            "screen_time":        any(s.get("screen_time", {}).get("enabled") for s in schedules.values()),
            "social_blocking":    social_profile != "open",
            "bedtime_enabled":    any(s.get("enabled") for s in schedules.values()),
            "focus_times_enabled": any(ft.get("enabled") for s in schedules.values() for ft in s.get("focus_times", [])),
            "notifications": {
                "ntfy":     bool(config.get("ntfy_topic")),
                "telegram": bool(tg.get("bot_token") or config.get("telegram_token")),
                "email":    bool(em.get("smtp_host") or config.get("email_address")),
            },
        },
    }


def send_telemetry(config):
    """POST anonymous USAGE stats to the opt-in endpoint. No-op unless the user
    has turned on 'Share anonymous usage stats' in Settings."""
    if not config.get("telemetry_enabled"):
        return
    try:
        from config import TELEMETRY_URL
        if not TELEMETRY_URL:
            return
        payload = _telemetry_payload(config)
        payload["event"] = "ping"
        data = json.dumps(payload).encode()
        req  = urllib.request.Request(TELEMETRY_URL, data=data,
                                      headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Anonymous stats sent (opt-in)")
    except Exception as e:
        print(f"Telemetry error: {e}")


def send_install_ping(config):
    """One-time anonymous install record — fires ONCE on first boot regardless of
    the opt-in toggle, so installs can be counted. Minimal payload: a random
    hardware-derived ID, version, router model, OpenWrt version. No usage,
    device, or personal data. Disclosed in the installer + README."""
    try:
        from config import TELEMETRY_URL
        if not TELEMETRY_URL:
            return
        p = _telemetry_payload(config)
        ping = {
            "event":           "install",
            "install_id":      p["install_id"],
            "version":         p["version"],
            "router_model":    p["router_model"],
            "openwrt_version": p["openwrt_version"],
        }
        data = json.dumps(ping).encode()
        req  = urllib.request.Request(TELEMETRY_URL, data=data,
                                      headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Anonymous install recorded")
    except Exception as e:
        print(f"Install ping error: {e}")


def main():
    _init_notifications_table()
    print("Lantern Watch alert system started")
    _ensure_oui_db()
    _ensure_doh_blocking()

    # One-time anonymous install record (fires once; ongoing stats stay opt-in).
    _cfg = load_config()
    if not _cfg.get("install_recorded"):
        send_install_ping(_cfg)
        _cfg["install_recorded"] = True
        save_config(_cfg)

    print("Waiting 5 minutes before alerting to allow devices to reconnect...")
    time.sleep(300)

    last_daily     = None
    last_weekly    = None
    last_purge     = None
    last_telemetry = None
    # Per-install minute-of-day for the daily stats ping, derived from the stable
    # install ID. Spreads a whole fleet evenly across 24h instead of every router
    # pinging at once just after midnight (no thundering herd on the endpoint).
    try:
        import hashlib as _hl
        from db import get_or_create_install_id
        _tslot = int(_hl.md5(get_or_create_install_id().encode()).hexdigest(), 16) % 1440
    except Exception:
        _tslot = 0

    while True:
        try:
            config = load_config()
            now    = datetime.now()

            # Opt-in anonymous stats — once a day, at THIS install's jittered slot
            # (no-op unless enabled in Settings).
            if ((last_telemetry is None or last_telemetry.date() < now.date())
                    and (now.hour * 60 + now.minute) >= _tslot):
                send_telemetry(config)
                last_telemetry = now

            check_adult_content(config)
            check_new_devices(config)
            check_high_block_rate(config)
            check_vpn_suspected(config)

            # Daily summary
            if config["summary"].get("daily"):
                target_hour = config["summary"].get("daily_hour", 21)
                if now.hour == target_hour and (last_daily is None or last_daily.date() < now.date()):
                    send_daily_summary(config)
                    last_daily = now

            # Weekly summary
            if config["summary"].get("weekly"):
                weekly_day  = config["summary"].get("weekly_day", 6)
                weekly_hour = config["summary"].get("weekly_hour", config["summary"].get("daily_hour", 21))
                if (now.weekday() == weekly_day and now.hour == weekly_hour and
                        (last_weekly is None or last_weekly.date() < now.date())):
                    send_weekly_summary(config)
                    last_weekly = now

            # Auto-purge old notifications once a day at 3AM
            if now.hour == 3 and (last_purge is None or last_purge.date() < now.date()):
                from db import purge_old_notifications
                purge_old_notifications(days=30)
                print(f"[{now.strftime('%H:%M:%S')}] Purged notifications older than 30 days")
                last_purge = now

        except Exception as e:
            print(f"Alert loop error: {e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
