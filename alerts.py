#!/usr/bin/env python3
"""
Lantern Watch — alerts.py
Background alert checks, scheduled summaries, and notification logging.
"""

import sqlite3
import urllib.request
import json
import time
from datetime import datetime, timedelta
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


def _drop_dns_suffix(s):
    """Trim a router-local DNS suffix from a display name (Galaxy-S21.lan -> Galaxy-S21).
    Local suffixes are noise in alerts — every device on the LAN has one."""
    for suffix in (".lan", ".local", ".home", ".internal"):
        if s and s.lower().endswith(suffix):
            return s[: -len(suffix)]
    return s


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
    lbl = _drop_dns_suffix(lbl)
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


def notify(config, title, message, priority="default", tags="bell", click_url=""):
    """Record the alert in the in-app activity log (once), THEN push it to every
    channel that's configured (ntfy / Telegram / email). Logging is independent of
    push, so the notification log is a true activity history even when no channel
    is set up. (send_alert/telegram/email are forward-referenced — resolved at
    call time.)"""
    _log_notification(title, message, config.get("ntfy_topic", ""))
    topic = config.get("ntfy_topic", "")
    if topic:
        send_alert(topic, message, title=title, priority=priority, tags=tags, click_url=click_url)
    send_telegram(config, message, title)
    send_email(config, message, title)


# ── Send ──────────────────────────────────────────────────────────────────────

def send_alert(topic, message, title="Lantern Watch", priority="default", tags="bell", click_url=""):
    """Send a push notification via ntfy and log it to the DB."""
    if not topic:
        print(f"[alerts] ntfy_topic is not configured — skipping alert: {title}")
        return
    from config import load_config
    if not load_config().get("ntfy_enabled", True):
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
            ln if ln.lstrip().startswith(("Dashboard:", "View activity:", "Find help:"))
            else _ip.sub(r"`\1`", ln)
            for ln in text.split("\n")
        )
        # Telegram won't linkify a bare single-label host like "lanternwatch", so
        # render the dashboard / device / help lines as clean labeled Markdown links
        # (the URL target is the LAN IP, which is always reachable + linkable).
        text    = _re.sub(r"(?m)^Dashboard:\s*(\S+)\s*$",     r"[Open Dashboard](\1)", text)
        text    = _re.sub(r"(?m)^View activity:\s*(\S+)\s*$", r"[View activity](\1)", text)
        text    = _re.sub(r"(?m)^Find help:\s*(\S+)\s*$",     r"[Find help](\1)", text)
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
    safe = _re.sub(r"(?m)^Find help:\s*(\S+)\s*$",     r'<a href="\1">Find help</a>', safe)
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
    # Resolves to this router's real LAN IP when dashboard_url still holds the
    # stock 192.168.8.1 but the LAN is actually elsewhere (repeater mode), so
    # notification links never point at a different router.
    from config import dashboard_url
    return dashboard_url(config)


def _append_url(message, config):
    # Include the full URL (with scheme) so it renders as a tappable link:
    # send_telegram turns it into a Markdown link and email clients auto-link it.
    # ntfy also has click_url for tap-to-open.
    return message + f"\n\nDashboard: {_dash_url(config)}"


# ── Alert checks ──────────────────────────────────────────────────────────────

def _explicit_block_domains(config):
    """Domains the admin has explicitly chosen to block (custom blocks + category
    packs). Lets us tell a parent-chosen block from ambient ad/tracker noise —
    both land in the querylog as FilteredBlackList."""
    try:
        from adguard import get_custom_blocks, get_blocked_pack_domains
        return set(get_custom_blocks(config)) | set(get_blocked_pack_domains(config))
    except Exception:
        return set()


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


def _is_notable_block(domain, reasons, explicit, config, filter_ids=None):
    """True if a block is worth notifying about: adult content (AdGuard Parental),
    a hit on a Family & Content blocklist (adult / gambling / dating), a
    FilteredBlackList hit on a domain the admin explicitly blocked, or a blocked
    SERVICE whose category the parent chose to be notified about. Excludes the
    high-volume ad/tracker/security blocklist noise."""
    reasons = reasons or ""
    d = (domain or "").lower()
    if "Parental" in reasons:
        return True
    if "FilteredBlackList" in reasons:
        if any(d == e or d.endswith("." + e) for e in explicit):
            return True
        if filter_ids:
            from adguard import filter_id_category_map
            cats = filter_id_category_map(config)
            if any(cats.get(fid) == "Family & Content" for fid in filter_ids):
                return True
    if "FilteredBlockedService" in reasons:
        from adguard import service_category_for_domain, service_notify_enabled
        if service_notify_enabled(service_category_for_domain(domain, config), config):
            return True
    return False


def check_blocked_content(config):
    """Notify when a device hits a site the admin actually blocks — adult content,
    blocked services, custom blocks, and category packs — NOT the thousands of
    ambient ad/tracker blocks. De-duplicated per device+site so a repeatedly-hit
    site (or a chatty background app) notifies at most once per cooldown window."""
    if not config["alerts"].get("adult_content"):
        return
    last_alerted = config.get("last_adult_alert", "2000-01-01T00:00:00Z")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT client_name, domain, MAX(ts) as latest, COUNT(*) as hits,
               GROUP_CONCAT(DISTINCT reason) as reasons,
               GROUP_CONCAT(DISTINCT filter_id) as filter_ids
        FROM querylog
        WHERE blocked=1 AND ts > ?
          AND (reason LIKE '%Parental%' OR reason = 'FilteredBlockedService'
               OR reason = 'FilteredBlackList')
        GROUP BY client_name, domain ORDER BY MAX(ts) DESC
    """, (last_alerted,)).fetchall()
    conn.close()
    if not rows:
        return
    newest_ts = max(r["latest"] for r in rows)
    explicit  = _explicit_block_domains(config)

    now        = datetime.now()
    cooldown_h = int(config.get("blocked_notify_cooldown_h", 6))
    cooldowns  = config.get("blocked_content_cooldowns", {})
    fresh = []
    for r in rows:
        if not _is_notable_block(r["domain"], r["reasons"], explicit, config,
                                 _parse_fids(r["filter_ids"])):
            continue
        key  = f"{r['client_name']}|{r['domain']}"
        prev = cooldowns.get(key, "2000-01-01T00:00:00")
        try:
            if (now - datetime.fromisoformat(prev)).total_seconds() < cooldown_h * 3600:
                continue                                   # still within cooldown — skip
        except Exception:
            pass
        cooldowns[key] = now.isoformat()
        fresh.append((r["client_name"], r["domain"]))

    # Advance the watermark and prune expired cooldowns every run.
    config["last_adult_alert"] = newest_ts
    cutoff = now - timedelta(hours=cooldown_h)
    kept = {}
    for k, v in cooldowns.items():
        try:
            if datetime.fromisoformat(v) > cutoff:
                kept[k] = v
        except Exception:
            pass
    config["blocked_content_cooldowns"] = kept
    save_config(config)

    if not fresh:
        return

    base_url  = _dash_url(config)
    help_url  = base_url.rstrip("/") + "/findhelp"
    help_line = ("\n\nIf you or someone at home is struggling, you're not alone.\n"
                 f"Find help: {help_url}")
    if len(fresh) == 1:
        c, d = fresh[0]
        body = f"{_friendly(c, config)} tried to reach a blocked site:\n{d}"
    else:
        lines = [f"• {_friendly(c, config)}: {d}" for c, d in fresh]
        body  = f"{len(fresh)} blocked-site attempts:\n" + "\n".join(lines)
    msg = _append_url(body + help_line, config)
    notify(config, "Blocked Content", msg, priority="high", tags="warning", click_url=help_url)


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
            notify(config, "New Device Detected", msg, tags="bell", click_url=_dash_url(config))


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
                    notify(config, "High Block Rate", msg, priority="high", tags="warning", click_url=_dash_url(config))
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
                notify(config, "Activity Drop Detected", msg, tags="magnifying_glass", click_url=device_url)
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


# ── Update availability ───────────────────────────────────────────────────────

def fetch_latest_version():
    """Newest release version on GitHub (tag list), or '' on any failure. Sends
    NO data — an anonymous read of the public tag list, like any web visitor."""
    from config import UPDATE_CHECK_URL, is_newer_version
    try:
        req = urllib.request.Request(
            UPDATE_CHECK_URL,
            headers={"User-Agent": "LanternWatch",
                     "Accept": "application/vnd.github+json"})
        tags   = json.loads(urllib.request.urlopen(req, timeout=8).read().decode())
        latest = ""
        for t in tags:
            n = (t.get("name") or "").lstrip("v")
            if n and (not latest or is_newer_version(n, latest)):
                latest = n
        return latest
    except Exception as e:
        print(f"[Update] check failed: {e}")
        return ""


def _update_summary_line(config):
    """A one-line 'update available' notice for the daily/weekly summary, using
    the last-seen version (no network call here). '' when up to date or when the
    update alert is switched off (the summary line follows that toggle)."""
    from config import VERSION, is_newer_version
    if not config.get("alerts", {}).get("update_available", True):
        return ""
    latest = config.get("latest_known_version", "")
    if latest and is_newer_version(latest, VERSION):
        return f"\U0001F514 Update available: Lantern Watch {latest} — open Settings to update."
    return ""


def check_for_updates(config):
    """Daily: read the latest GitHub release, remember it (for the dashboard badge
    + summary line), and push ONE notification per new version (deduped) if the
    'update_available' alert is on. No-op if already current."""
    from config import VERSION, is_newer_version
    latest = fetch_latest_version()
    if not latest:
        return
    # Remember the newest version for the dashboard badge + summary line.
    if config.get("latest_known_version") != latest:
        config["latest_known_version"] = latest
        save_config(config)
    if not is_newer_version(latest, VERSION):
        return
    if not config.get("alerts", {}).get("update_available", True):
        return
    # Dedup: notify only once per new version, not every day.
    if config.get("update_notified_version") == latest:
        return
    notify(
        config,
        "Update Available",
        f"Lantern Watch {latest} is available (you're on {VERSION}).\n"
        f"Open Settings → Software and tap Update Now — your device names "
        f"and settings are kept.",
        priority="default", tags="arrow_up",
        click_url=_dash_url(config) + "/admin",
    )
    config["update_notified_version"] = latest
    save_config(config)


def send_daily_summary(config):
    message = _append_url(_build_daily_narrative(config), config)
    upd = _update_summary_line(config)
    if upd:
        message = upd + "\n\n" + message
    topics  = [config.get("ntfy_topic", "")]
    extras  = config.get("extra_topics", "")
    if extras:
        topics += [t.strip() for t in extras.split(",") if t.strip()]
    _log_notification("Lantern Watch Daily Summary", message, config.get("ntfy_topic", ""))
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
    skip        = {cfg.get("label", name) for name, cfg in cfg_devices.items() if cfg.get("type") == "infrastructure"}
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
        lines.append(f"  {_friendly(d['client_name'], config)}: {d['total']:,} queries, {dpct}% blocked")
    if top_blocked:
        lines += ["", "Top blocked domains:"]
        for r in top_blocked:
            lines.append(f"  {r['domain']}: {r['hits']} times")

    upd = _update_summary_line(config)
    if upd:
        lines = [upd, ""] + lines
    message = _append_url("\n".join(lines), config)
    topics  = [config.get("ntfy_topic", "")]
    extras  = config.get("extra_topics", "")
    if extras:
        topics += [t.strip() for t in extras.split(",") if t.strip()]
    _log_notification("Lantern Watch Weekly Summary", message, config.get("ntfy_topic", ""))
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
    # Hardware / profile facts. These ride the BASE ping (same category as
    # router_model), so the Lite-vs-Full split reflects the WHOLE fleet, not just
    # opted-in users — which is the only way to sanity-check the 600 MB threshold
    # against real hardware. No personal data.
    ram_mb = 0
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    ram_mb = int(int(line.split()[1]) / 1024)
                    break
    except Exception:
        pass
    prot_profile, dns_tier = "", ""
    try:
        from adguard import protection_profile, lite_dns_tier, is_lite
        prot_profile = protection_profile(config)
        # The DNS tier is a user CHOICE, so it stays opt-in only (send_install_ping
        # never includes it) — unlike RAM/profile, which are hardware facts.
        dns_tier     = lite_dns_tier(config) if is_lite(config) else ""
    except Exception:
        pass
    schedules      = config.get("schedules", {})
    social_profile = config.get("social_profile", "open")
    tg, em = config.get("telegram", {}), config.get("email", {})
    return {
        "install_id":       install_id,
        "version":          VERSION,
        "router_model":     router_model,
        "openwrt_version":  openwrt_version,
        "ram_mb":           ram_mb,
        "protection_profile": prot_profile,
        "adguard_connected": adguard_connected,
        "device_count":     len(config.get("devices", {})),
        "social_profile":   social_profile,
        "lite_dns_tier":    dns_tier,
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
    """POST anonymous USAGE stats (the opt-in ping) — base fields PLUS feature
    flags, device count, and opted_in=TRUE. No-op unless the user has turned on
    'Share anonymous usage stats' in Settings. Returns True on confirmed send."""
    if not config.get("telemetry_enabled"):
        return False
    try:
        from config import TELEMETRY_URL
        if not TELEMETRY_URL:
            return False
        payload = _telemetry_payload(config)
        payload["event"] = "ping"
        data = json.dumps(payload).encode()
        req  = urllib.request.Request(TELEMETRY_URL, data=data,
                                      headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req, timeout=10)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Anonymous stats sent (opt-in)")
        return 200 <= getattr(resp, "status", 200) < 300
    except Exception as e:
        print(f"Telemetry error: {e}")
        return False


def send_install_ping(config):
    """One-time anonymous install record — fires regardless of the opt-in toggle,
    so installs can be counted. Retried until it succeeds (see main), so a
    boot-time network hiccup can't silently lose the count. Minimal payload: a
    random anonymous ID, version, router model, OpenWrt version. No usage,
    device, or personal data. Disclosed in the installer + README.

    Returns True only when the endpoint confirms receipt, False otherwise."""
    try:
        from config import TELEMETRY_URL
        if not TELEMETRY_URL:
            return False
        p = _telemetry_payload(config)
        ping = {
            "event":              "install",
            "install_id":         p["install_id"],
            "version":            p["version"],
            "router_model":       p["router_model"],
            "openwrt_version":    p["openwrt_version"],
            "ram_mb":             p.get("ram_mb", 0),
            "protection_profile": p.get("protection_profile", ""),
        }
        data = json.dumps(ping).encode()
        req  = urllib.request.Request(TELEMETRY_URL, data=data,
                                      headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req, timeout=10)
        ok = 200 <= getattr(resp, "status", 200) < 300
        if ok:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Anonymous install recorded")
        return ok
    except Exception as e:
        print(f"Install ping error (will retry): {e}")
        return False


def main():
    _init_notifications_table()
    print("Lantern Watch alert system started")
    _ensure_oui_db()
    _ensure_doh_blocking()

    # The one-time install record is attempted inside the loop below (after the
    # network has had time to come up) and retried until it actually lands, so a
    # boot-time hiccup can't lose the count.

    print("Waiting 5 minutes before alerting to allow devices to reconnect...")
    time.sleep(300)

    last_daily     = None
    last_weekly    = None
    last_purge     = None
    last_telemetry = None
    last_update    = None
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

            # Telemetry heartbeat. EVERY router reports a small anonymous base
            # record (random ID, version, model, OpenWrt); opted-in routers report
            # usage too. Sent once per calendar day at THIS install's jittered slot
            # (spreads a whole fleet across 24h — no herd), PLUS immediately on
            # boot and whenever the version changes. Because it repeats daily and
            # is never gated off after "already sent", the fleet view self-heals:
            # a router whose row was lost simply reappears at its next slot.
            from config import VERSION as _V
            opted           = bool(config.get("telemetry_enabled"))
            version_changed = config.get("install_ping_version") != _V
            slot_due        = (last_telemetry is not None
                               and last_telemetry.date() < now.date()
                               and (now.hour * 60 + now.minute) >= _tslot)
            if last_telemetry is None or version_changed or slot_due:
                sent = send_telemetry(config) if opted else send_install_ping(config)
                if sent:
                    last_telemetry = now
                    if version_changed:
                        config["install_ping_version"] = _V
                        save_config(config)

            check_blocked_content(config)
            check_new_devices(config)
            check_high_block_rate(config)
            check_vpn_suspected(config)

            # Once a day: anonymous version check → dashboard badge + one push per
            # new release. Sends no data (reads GitHub's public tag list).
            if last_update is None or last_update.date() < now.date():
                check_for_updates(config)
                last_update = now

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
