#!/usr/bin/env python3
"""
Lantern Watch — pages.py
All HTML page-builder functions and the shared CSS.
"""

import json
import os
import urllib.request
from datetime import datetime
from urllib.parse import quote

from config import label, is_infrastructure, is_monitored, is_pauseable, effective_type, VERSION, dashboard_url
from adguard import get_adguard_stats, PLATFORM_DOMAINS, PROFILE_SAFE_SEARCH, get_ip_hostname_map, pretty_hostname
from db import (
    get_stats, get_device_detail, get_domain_detail,
    get_all_known_devices, get_oldest_log_date,
    get_notifications, clear_notifications, fmt_time, is_noise,
    get_router_health, compute_safety_score, get_top_domains_map,
)
from scheduler import get_paused_devices


def _local_ts(ts):
    """Convert a stored UTC timestamp (AdGuard RFC3339 with 'Z', or a naive UTC
    string) to a local 'YYYY-MM-DD HH:MM:SS' string for display. Query filtering
    stays in UTC; only presentation is localized. Returns '' for empty input and
    falls back to a trimmed raw value if it can't be parsed."""
    if not ts:
        return ""
    try:
        s = ts.strip()
        if "T" not in s:
            s = s.replace(" ", "T", 1)
        s = s.replace("Z", "+00:00")
        if "." in s:                       # trim sub-second (AGH sends nanoseconds)
            head, rest = s.split(".", 1)
            frac, tz = "", ""
            for i, ch in enumerate(rest):
                if ch.isdigit():
                    frac += ch
                else:
                    tz = rest[i:]
                    break
            s = head + "." + frac[:6] + tz
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is not None:
            dt = dt.astimezone()                              # -> system local tz
        else:
            dt = dt + (datetime.now() - datetime.utcnow())    # assume UTC, shift
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ts[:19].replace("T", " ")


_LOGO_SVG = open(os.path.join(os.path.dirname(__file__), 'lantern_watch_logo.svg')).read()
_LANTERN_SVG = open(os.path.join(os.path.dirname(__file__), 'lantern_logo.svg')).read()

# Favicon variant: the lantern artwork only fills ~37% of the source logo's
# square canvas (lots of padding), so as a browser-tab icon it looks tiny next
# to other sites. This crops the viewBox tight to the artwork (~36u margin) so
# the mark fills the tab. The full-padding logo is left untouched everywhere else.
_FAVICON_SVG = _LANTERN_SVG.replace('viewBox="0 0 1024 1024"', 'viewBox="288 136 448 752"', 1)

# ── Shared CSS ────────────────────────────────────────────────────────────────

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
:root{
  --orange:#e8a000;--orange-dark:#b87d00;
  --ink:#1a1a1a;--body:#3a3a3a;--muted:#6b6b6b;
  --line:#e8e6e0;--bg:#ffffff;--bg-soft:#faf8f3;--bg-card:#ffffff;
  --amber-soft:#fff4dc;--amber-border:#f3e3b8;
  --radius:14px;
  --ok:#16a34a;--danger:#dc2626;--warn:#b45309;
  --shadow:0 1px 3px rgba(26,26,26,.05);--shadow-hover:0 6px 20px rgba(26,26,26,.08);
  --font:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:var(--font);background:var(--bg-soft);color:var(--body);min-height:100vh;line-height:1.6;-webkit-font-smoothing:antialiased}
.page-wrap{max-width:780px;margin:0 auto;width:100%}
a{color:inherit;text-decoration:none}
h1,h2,h3{color:var(--ink);font-weight:700;line-height:1.2;letter-spacing:-0.02em}
.header{background:var(--bg);border-bottom:1px solid var(--line);padding:0 24px;height:52px;display:flex;align-items:center;position:relative}
.header-brand{display:flex;align-items:center;gap:10px;text-decoration:none;color:inherit;flex:1;min-width:0;cursor:pointer;-webkit-tap-highlight-color:rgba(232,160,0,0.15)}.header-brand:active{opacity:0.75}
.header-brand .header-logo{width:60px;flex-shrink:0}.header-brand .header-logo svg{width:100%;height:auto;display:block}
.header-brand h1{font-size:1.1em;color:var(--ink);font-weight:800;letter-spacing:-0.01em;white-space:nowrap}
.header-brand p{font-size:13px;color:var(--muted);font-style:italic;margin:0;white-space:nowrap}
.header-nav{display:flex;align-items:center;gap:4px;flex-shrink:0}
.header-actions{flex:1;display:flex;justify-content:flex-end;align-items:center}
.header-link{color:var(--body);font-size:14px;font-weight:600;transition:color 150ms ease;border-bottom:3px solid transparent;line-height:52px;white-space:nowrap;padding:0}
.header-link:hover{color:var(--orange)}
.header-link.active{color:var(--ink);border-bottom:3px solid var(--orange)}
.nav-group{position:relative}
.nav-link{display:inline-flex;align-items:center;gap:7px;color:var(--body);font-size:14px;font-weight:600;background:none;border:none;cursor:pointer;font-family:inherit;padding:8px 11px;border-radius:8px;white-space:nowrap;text-decoration:none;transition:background .12s ease,color .12s ease}
.nav-link>svg{color:var(--muted);transition:color .12s ease}
.nav-link:hover{background:var(--bg-soft);color:var(--ink)}
.nav-link:hover>svg{color:var(--orange-dark)}
.nav-link .caret{display:inline-flex;color:var(--muted);margin-left:-3px;transition:transform .15s ease}
.nav-link.active{color:var(--orange);background:rgba(232,160,0,.13)}
.nav-link.active>svg{color:var(--orange)}
.nav-group.open>.nav-link{background:var(--bg-soft);color:var(--ink)}
.nav-group.open .caret{transform:rotate(180deg)}
.nav-menu{position:absolute;top:calc(100% + 6px);left:0;min-width:212px;background:#fff;border:1px solid var(--line);border-radius:12px;box-shadow:0 12px 32px rgba(26,26,26,.13);padding:6px;opacity:0;visibility:hidden;transform:translateY(-6px);transition:opacity .13s ease,transform .13s ease;z-index:300}
.nav-group.open .nav-menu{opacity:1;visibility:visible;transform:translateY(0)}
.nav-menu a{display:flex;align-items:center;gap:11px;padding:9px 12px;border-radius:8px;color:var(--body);font-size:13.5px;font-weight:600;white-space:nowrap;text-decoration:none}
.nav-menu a>svg{color:var(--muted);flex-shrink:0}
.nav-menu a:hover{background:var(--bg-soft);color:var(--ink)}
.nav-menu a:hover>svg{color:var(--orange-dark)}
.nav-menu a .ext{display:inline-flex;margin-left:auto;color:var(--muted);opacity:.65}
.mob-group{padding:12px 24px 4px;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;color:var(--muted)}
.header-link.signout{color:var(--danger)}
.header-link.signout:hover{color:#b91c1c}
.hamburger{display:none;background:none;border:none;cursor:pointer;padding:8px;flex-shrink:0;margin-left:4px}
.hamburger span{display:block;width:20px;height:2px;background:var(--ink);margin:4px 0;border-radius:2px}
.mobile-nav{display:none;position:absolute;top:52px;left:0;right:0;background:var(--bg);border-bottom:1px solid var(--line);box-shadow:0 4px 16px rgba(0,0,0,0.10);z-index:200;padding:4px 0}
.mobile-nav.open{display:block}
.mobile-nav a{display:flex;align-items:center;gap:11px;padding:11px 24px;color:var(--body);font-size:15px;font-weight:600}
.mobile-nav a svg{color:var(--muted);flex-shrink:0}
.mobile-nav a:last-child{border-bottom:none}
.mobile-nav a.signout{color:var(--danger)}
@media(max-width:640px){.header-nav{display:none}.header-actions{display:none}.hamburger{display:block}}
.back{display:inline-block;padding:8px 16px;background:var(--amber-soft);border-radius:8px;color:var(--orange-dark);margin:12px 16px;font-size:0.9em;border:1px solid var(--amber-border);font-weight:600;transition:background 150ms ease}.back:hover{background:#ffeccb}.back-wrap{max-width:780px;margin:0 auto;padding:0}
.section{background:var(--bg-card);border-radius:var(--radius);margin:12px 16px;padding:20px;box-shadow:var(--shadow);border:1px solid var(--line)}
.section h2{font-size:14px;font-weight:700;color:var(--ink);margin-bottom:12px;border-left:3px solid var(--orange);padding-left:10px;letter-spacing:-0.01em}
.section h2.alert{color:var(--danger)}
.stats-bar{display:flex;gap:8px;padding:12px 16px;overflow-x:auto;max-width:780px;margin:0 auto}
.stat-card{background:var(--bg-card);border-radius:12px;padding:12px 16px;min-width:90px;flex:1;text-align:center;border:1px solid var(--line);box-shadow:var(--shadow);transition:box-shadow 200ms ease,transform 200ms ease}
.stat-card:hover{box-shadow:var(--shadow-hover);transform:translateY(-2px)}
.stat-card .num{font-size:clamp(16px,5vw,28px);font-weight:700;color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.num.blue{color:var(--ink)}.num.green{color:var(--ok)}.num.red{color:var(--danger)}
.stat-card .label{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:0.08em;margin-top:4px}
.device-card{background:var(--bg-card);border-radius:var(--radius);padding:16px;margin-bottom:10px;border:1px solid var(--line);box-shadow:var(--shadow);transition:border-color 150ms ease,box-shadow 150ms ease}
.device-card:hover{border-color:var(--orange);box-shadow:var(--shadow-hover)}
.device-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
.device-name{font-weight:700;font-size:1em;color:var(--ink)}
.badge{padding:2px 10px;border-radius:99px;font-size:0.75em;font-weight:700}
.badge-green{background:#eaf7ef;color:var(--ok)}.badge-yellow{background:var(--amber-soft);color:var(--orange-dark)}.badge-red{background:#fdeaea;color:var(--danger)}
.bar-wrap{height:6px;background:#ece9e2;border-radius:99px;margin-bottom:8px;overflow:hidden}
.bar-fill{height:100%;background:var(--orange);border-radius:99px;transition:width 0.3s}.bar-fill.danger{background:var(--danger)}
.device-stats{display:flex;flex-wrap:wrap;gap:6px 14px}
.device-stat{font-size:0.78em;color:var(--muted)}.device-stat span{color:var(--ink);font-weight:700}
.domain-list{border-radius:8px;overflow:hidden}
.domain-item{display:flex;justify-content:space-between;align-items:center;padding:10px 14px;border-bottom:1px solid var(--line);font-size:0.85em}
.domain-item:last-child{border-bottom:none}
.domain-name{color:var(--body);font-weight:500;word-break:break-all}
.domain-count{font-weight:700;color:var(--ink);white-space:nowrap;margin-left:8px}.domain-count.red{color:var(--danger)}
.infra-section{opacity:0.65}
.refresh{text-align:center;color:var(--muted);font-size:0.75em;padding:12px;margin-bottom:24px}
.alert-box-red{background:#fdeaea;border:1px solid #f3c2c2;border-radius:10px;padding:10px 14px;color:var(--danger);font-size:0.85em;font-weight:600}
.alert-box-green{background:#eaf7ef;border:1px solid #b5e2c5;border-radius:10px;padding:10px 14px;color:var(--ok);font-size:0.85em;font-weight:600}
.netstats{display:flex;flex-direction:column;gap:0}.netstat-item{display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--line);font-size:0.85em;transition:background 150ms ease}.netstat-item:hover{background:var(--bg-soft);margin:0 -8px;padding:8px 8px}.netstat-item:last-child{border-bottom:none}
.detail-header{background:var(--bg-card);padding:20px;border-bottom:1px solid var(--line);max-width:780px;margin:0 auto}
.detail-name{font-size:1.3em;font-weight:800;color:var(--ink)}
.detail-stats{display:flex;flex-wrap:wrap;gap:6px 20px;margin-top:8px}
.detail-stat{font-size:0.82em;color:var(--muted)}.detail-stat span{color:var(--ink);font-weight:700}
.hour-chart{display:flex;align-items:flex-end;gap:2px;height:48px}
.hour-bar{flex:1;background:var(--orange);border-radius:2px 2px 0 0;min-width:6px;cursor:default}
.hour-labels{display:flex;gap:2px;margin-top:2px}
.hour-label{flex:1;font-size:0.55em;color:var(--muted);text-align:center}
input[type=text],input[type=password],input[type=number],input[type=time],input[type=email],select,textarea{width:100%;padding:10px 12px;border:1.5px solid var(--line);border-radius:10px;font-size:0.95em;color:var(--ink);font-family:var(--font);background:#fff}
input:focus,select:focus,textarea:focus{outline:none;border-color:var(--orange);box-shadow:0 0 0 3px rgba(232,160,0,0.12)}
input[type=checkbox],input[type=radio]{width:18px;height:18px;accent-color:var(--orange)}
.form-card{background:var(--bg-card);border-radius:var(--radius);border:1px solid var(--line);padding:16px;margin-bottom:12px;box-shadow:var(--shadow)}
.form-label{color:var(--muted);font-size:0.8em;display:block;margin-bottom:4px;text-transform:uppercase;letter-spacing:0.08em;font-weight:700}
.check-row{display:flex;align-items:center;gap:10px;padding:10px 0;border-bottom:1px solid var(--line);cursor:pointer;color:var(--body)}
.check-row:last-child{border-bottom:none}
.radio-row{display:flex;align-items:center;gap:10px;padding:8px 0;cursor:pointer;color:var(--body)}
.btn{width:100%;padding:14px;background:var(--orange);border:none;border-radius:20px;color:white;font-size:1em;font-weight:700;cursor:pointer;display:block;text-align:center;margin-bottom:8px;transition:background 150ms ease,transform 120ms ease,box-shadow 150ms ease;box-shadow:0 4px 14px rgba(232,160,0,.28)}
.btn:hover{background:var(--orange);transform:translateY(-1px);box-shadow:0 6px 18px rgba(232,160,0,.36)}
.btn-secondary{background:var(--bg-card);border:1px solid var(--amber-border);color:var(--orange);box-shadow:none}
.btn-secondary:hover{background:var(--amber-soft);border-color:var(--orange)}
.btn-danger{background:var(--bg-card);border:1px solid #f0bcbc;color:var(--danger);box-shadow:none}
.btn-danger:hover{background:#fdeaea}
.success{margin:0 0 12px;padding:14px;background:#eaf7ef;border:1px solid #b5e2c5;border-radius:12px;color:var(--ok);text-align:center;font-weight:700}
.tag{display:inline-block;padding:2px 10px;border-radius:99px;font-size:0.8em;font-weight:600;margin-right:4px}
.tag-gold{background:var(--amber-soft);color:var(--orange-dark)}
.tag-gray{background:#f0eee9;color:var(--muted)}
.tag-red{background:#fdeaea;color:var(--danger)}
/* ── design-system additions (available for the page-by-page markup rollout) ── */
.eyebrow{color:var(--orange-dark);font-weight:700;font-size:13px;letter-spacing:.08em;text-transform:uppercase}
.card{background:var(--bg-card);border:1px solid var(--line);border-radius:var(--radius);padding:20px;box-shadow:var(--shadow);transition:border-color .15s ease,box-shadow .15s ease}
.card:hover{border-color:var(--orange);box-shadow:var(--shadow-hover)}
.badge-pill{display:inline-flex;align-items:center;gap:7px;background:var(--amber-soft);color:var(--orange-dark);font-weight:600;font-size:13px;padding:6px 14px;border-radius:999px;border:1px solid var(--amber-border)}
.btn-primary{display:inline-flex;align-items:center;justify-content:center;gap:8px;width:auto;font-weight:600;font-size:15px;padding:13px 22px;border-radius:10px;border:1.5px solid transparent;cursor:pointer;text-decoration:none;background:var(--orange);color:#fff;box-shadow:0 4px 14px rgba(232,160,0,.28);transition:transform .12s ease,box-shadow .12s ease,background .12s ease}
.btn-primary:hover{background:var(--orange);transform:translateY(-1px);box-shadow:0 6px 18px rgba(232,160,0,.36)}
.btn-ghost{display:inline-flex;align-items:center;justify-content:center;gap:8px;width:auto;font-weight:600;font-size:15px;padding:13px 22px;border-radius:10px;border:1.5px solid var(--line);cursor:pointer;text-decoration:none;background:#fff;color:var(--ink);transition:transform .12s ease,border-color .12s ease,color .12s ease}
.btn-ghost:hover{border-color:var(--orange);color:var(--orange);transform:translateY(-1px)}
a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible{outline:2px solid var(--orange);outline-offset:2px}
@media(prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
"""


# ── Demo mode ─────────────────────────────────────────────────────────────────
# When config["demo_mode"] is on, every device's *displayed* name is replaced
# with a stable fake one (from config["demo_map"], with a deterministic fallback)
# so the dashboard can be shown publicly without revealing real device names.
# Non-destructive: real client_name/labels are untouched; only display changes.
_DEMO_POOL = [
    "Kids Phone 1", "Kids Phone 2", "Teen's Phone", "Mom's Phone", "Dad's Phone",
    "Family Tablet", "Homework Laptop", "Living Room TV", "Game Console",
    "Smart Speaker", "Kitchen Tablet", "Guest Phone", "Robot Vacuum",
    "Smart Thermostat", "Security Camera", "Home Office PC", "eReader",
    "Smart Fridge", "Baby Monitor", "Garage Sensor", "Work Laptop",
    "School Chromebook", "Streaming Stick", "Front Door Cam", "Backyard Cam",
    "Smart Lock", "Smart Doorbell", "Smart Plug", "Smart Bulbs", "Air Purifier",
    "Wireless Printer", "NAS Drive", "Grandma's Tablet", "Smart Watch",
    "Weather Station", "Garage Door Opener",
]


def _demo(key, real, config):
    """Return a stable fake display name for `key` when demo mode is on."""
    if not config.get("demo_mode"):
        return real
    dm = config.get("demo_map", {})
    if key in dm:
        return dm[key]
    import hashlib
    return _DEMO_POOL[int(hashlib.md5(str(key).encode()).hexdigest(), 16) % len(_DEMO_POOL)]


def build_demo_map(config, db_path):
    """Assign a stable fake name to every known device (built when demo mode is
    enabled). Keyed by client_name (which is what every display site keys on)."""
    names = set(config.get("devices", {}).keys())
    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        for row in conn.execute("SELECT DISTINCT client_name FROM querylog "
                                "WHERE client_name IS NOT NULL AND client_name != ''"):
            if row[0]:
                names.add(row[0])
        conn.close()
    except Exception:
        pass
    mp = {}
    for i, n in enumerate(sorted(names)):
        mp[n] = _DEMO_POOL[i] if i < len(_DEMO_POOL) else "Device %d" % (i + 1)
    return mp


# Demo-mode scramblers — stable fake identity so screenshots never leak real data.
_DEMO_VENDORS = ["Acme Electronics", "Globex Devices", "Initech Systems",
                 "Umbrella Tech", "Soylent Devices", "Hooli Hardware",
                 "Vandelay Devices", "Wonka Electronics", "Cyberdyne Systems",
                 "Stark Devices", "Pied Piper Tech", "Gekko Electronics"]
_DEMO_DOMAINS = ["example.com", "cdn.example.net", "media.example.org",
                 "api.example.io", "static.example.co", "updates.example.net",
                 "telemetry.example.com", "stream.example.tv"]

def _demo_hash(key, salt):
    import hashlib
    return int(hashlib.md5((salt + str(key)).encode()).hexdigest(), 16)

def _demo_ip(key):
    h = _demo_hash(key, "ip")
    return f"192.168.{(h >> 8) % 254 + 1}.{h % 254 + 1}"

def _demo_mac(key):
    h = _demo_hash(key, "mac")
    o = [(h >> (8 * i)) & 0xff for i in range(6)]
    o[0] = (o[0] & 0xfe) | 0x02   # valid, locally-administered
    return ":".join(f"{x:02x}" for x in o)

def _demo_vendor(key):
    return _DEMO_VENDORS[_demo_hash(key, "ven") % len(_DEMO_VENDORS)]

def _demo_domain(domain):
    return _DEMO_DOMAINS[_demo_hash(domain, "dom") % len(_DEMO_DOMAINS)]

def demo_ident(name, ident, ip, config):
    """Display-ready identity {ip, mac, vendor, hostname} — real values, or stable
    fakes when demo mode is on. Only fakes fields the real device actually has, so
    a demo card looks just like a real one. The IP is shown as-is even in demo: a
    router-assigned LAN address (192.168.x etc.) isn't personally identifying."""
    mac, vendor, host = ident.get("mac", ""), ident.get("vendor", ""), ident.get("hostname", "")
    if not config.get("demo_mode"):
        return {"ip": ip, "mac": mac, "vendor": vendor, "hostname": host}
    return {
        "ip":       ip,    # LAN IP — fine to display even in demo mode
        "mac":      _demo_mac(name)  if mac    else "",
        "vendor":   _demo_vendor(name) if vendor else "",
        "hostname": _demo(name, host, config) if host else "",
    }


# ── Notification helper ───────────────────────────────────────────────────────

def send_ntfy(topic, message, title="Lantern Watch", priority="default", tags="bell", click_url=""):
    try:
        headers = {
            "Title":        title.encode("utf-8").decode("latin-1", errors="ignore"),
            "Priority":     priority,
            "Tags":         tags,
            "Content-Type": "text/plain; charset=utf-8",
        }
        if click_url:
            headers["Click"] = click_url
        req = urllib.request.Request(
            f"https://ntfy.sh/{topic}",
            data=message.encode("utf-8"),
            headers=headers,
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"ntfy error: {e}")


def send_test_alert(config):
    topics = [config.get("ntfy_topic", "")]
    extras = config.get("extra_topics", "")
    if extras:
        topics += [t.strip() for t in extras.split(",") if t.strip()]
    for topic in [t for t in topics if t]:
        send_ntfy(topic, "Lantern Watch is protecting your network!", "Lantern Watch Test")


def send_test_ntfy(config):
    topic = config.get("ntfy_topic", "").strip()
    if not topic:
        return False, "No ntfy topic configured — enter one in Settings and save first."
    try:
        dash_url    = dashboard_url(config)
        body        = f"Lantern Watch is protecting your network!\n\nDashboard: {dash_url}"
        send_ntfy(topic, body, "Lantern Watch Test", click_url=dash_url)
        return True, ""
    except Exception as e:
        return False, str(e)


def send_test_telegram(config):
    tg      = config.get("telegram", {})
    token   = tg.get("bot_token", "").strip()
    chat_id = tg.get("chat_id",   "").strip()
    if not token or not chat_id:
        return False, "Bot Token and Chat ID are required — enter them in Settings and save first."
    try:
        dash_url    = dashboard_url(config)
        text        = (f"<b>Lantern Watch Test</b>\n"
                       f"Lantern Watch is protecting your network!\n\n"
                       f'<a href="{dash_url}">Open Dashboard</a>')
        payload = json.dumps({
            "chat_id":    chat_id,
            "text":       text,
            "parse_mode": "HTML",
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
        return True, ""
    except Exception as e:
        return False, str(e)


def send_test_email(config):
    import smtplib, ssl
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    em   = config.get("email", {})
    host = em.get("smtp_host",    "").strip()
    port = int(em.get("smtp_port", 587))
    user = em.get("smtp_user",    "").strip()
    pwd  = em.get("smtp_password","").strip()
    to   = em.get("to_address",  "").strip()
    if not (host and user and pwd and to):
        return False, "Email is not fully configured — fill in all fields in Settings and save first."
    try:
        dash_url    = dashboard_url(config)
        plain       = f"Lantern Watch is protecting your network!\n\nDashboard: {dash_url}"
        html_body   = ('<div style="font-family:sans-serif;font-size:14px;line-height:1.5">'
                       'Lantern Watch is protecting your network!<br><br>'
                       f'<a href="{dash_url}">Open Dashboard</a></div>')
        msg             = MIMEMultipart("alternative")
        msg["Subject"]  = "[Lantern Watch] Test"
        msg["From"]     = f"Lantern Watch <{user}>"
        msg["To"]       = to
        msg.attach(MIMEText(plain, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))
        ctx = ssl.create_default_context()
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ctx)
            smtp.login(user, pwd)
            smtp.sendmail(user, to, msg.as_string())
        return True, ""
    except Exception as e:
        return False, str(e)


# ── Shared page chrome ────────────────────────────────────────────────────────

def _page(title, body, *, config=None, refresh=False):
    """Wrap content in a full HTML document with the shared CSS."""
    meta_refresh = '<meta http-equiv="refresh" content="60">' if refresh else ""
    return (
        f'<!DOCTYPE html><html><head><link rel="icon" type="image/svg+xml" href="/favicon.svg">'
        f'<meta charset="UTF-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1.0">'
        f'<title>{title}</title>'
        f'<style>{CSS}</style>'
        f'{meta_refresh}'
        f'</head><body>'
        + build_header(config=config)
        + body
        + '</body></html>'
    )


_NAV_PATHS = {
    "home":    '<path d="M3 9.5L12 3l9 6.5V20a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1z"/>',
    "gear":    '<circle cx="12" cy="12" r="3.2"/><path d="M19.4 13a7.9 7.9 0 0 0 0-2l2-1.5-2-3.4-2.3 1a8 8 0 0 0-1.7-1l-.3-2.6h-4l-.3 2.6a8 8 0 0 0-1.7 1l-2.3-1-2 3.4 2 1.5a7.9 7.9 0 0 0 0 2l-2 1.5 2 3.4 2.3-1a8 8 0 0 0 1.7 1l.3 2.6h4l.3-2.6a8 8 0 0 0 1.7-1l2.3 1 2-3.4z"/>',
    "chart":   '<path d="M3 3v18h18"/><path d="M7 14l3-4 3 2 4-6"/>',
    "grid":    '<rect x="3" y="3" width="7.5" height="7.5" rx="1.5"/><rect x="13.5" y="3" width="7.5" height="7.5" rx="1.5"/><rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.5"/><rect x="3" y="13.5" width="7.5" height="7.5" rx="1.5"/>',
    "caret":   '<path d="M6 9l6 6 6-6"/>',
    "sliders": '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/>',
    "users":   '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>',
    "bell":    '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/>',
    "list":    '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
    "monitor": '<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/>',
    "shield":  '<path d="M12 2l8 3v6c0 5-3.5 8.5-8 10-4.5-1.5-8-5-8-10V5z"/>',
    "wifi":    '<path d="M5 12.5a10 10 0 0 1 14 0M8.5 16a5 5 0 0 1 7 0M2 9a15 15 0 0 1 20 0"/><circle cx="12" cy="19.5" r=".6"/>',
    "help":    '<circle cx="12" cy="12" r="10"/><path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3M12 17h.01"/>',
    "ext":     '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><path d="M15 3h6v6M10 14L21 3"/>',
}


def _ic(name, size=18):
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
            f'stroke="currentColor" stroke-width="1.9" stroke-linecap="round" '
            f'stroke-linejoin="round">{_NAV_PATHS.get(name, "")}</svg>')


_NAV_ACTIVE = {
    "Light for your home network": "dashboard",
    "Settings": "settings", "Social Media Profiles": "settings", "Notifications": "settings",
    "Blocked Services": "settings",
    "Query Log": "traffic", "Device Manager": "traffic",
    "Blocked Site Details": "traffic", "Find Help": "tools",
}


def build_header(subtitle="Light for your home network", config=None):
    logo = f'<div class="header-logo">{_LANTERN_SVG}</div>'
    agh_url = "http://192.168.8.1:3000"
    if config:
        agh_url = config.get("adguard", {}).get("url", agh_url)
    _host = agh_url.split("//")[-1].split(":")[0].split("/")[0] or "192.168.8.1"
    glinet_url = f"http://{_host}/#/login"
    active = _NAV_ACTIVE.get(subtitle, "")
    if subtitle.startswith("Schedule"):
        active = "traffic"
    def _a(grp):
        return " active" if active == grp else ""
    caret = f'<span class="caret">{_ic("caret", 14)}</span>'
    return (
        '<div class="header">'
        f'<a class="header-brand" href="/">{logo}<div><h1>Lantern Watch</h1><p>{subtitle}</p></div></a>'
        '<nav class="header-nav">'
        f'<a class="nav-link{_a("dashboard")}" href="/">{_ic("home")}Dashboard</a>'
        f'<div class="nav-group"><button class="nav-link{_a("traffic")}" onclick="lwDrop(event,this)">{_ic("chart")}Traffic{caret}</button>'
        '<div class="nav-menu">'
        f'<a href="/querylog">{_ic("list",16)}Query Log</a>'
        f'<a href="/admin/devices">{_ic("monitor",16)}Devices</a>'
        '</div></div>'
        f'<div class="nav-group"><button class="nav-link{_a("tools")}" onclick="lwDrop(event,this)">{_ic("grid")}Tools{caret}</button>'
        '<div class="nav-menu">'
        f'<a href="{agh_url}" target="_blank">{_ic("shield",16)}AdGuard<span class="ext">{_ic("ext",13)}</span></a>'
        f'<a href="{glinet_url}" target="_blank">{_ic("wifi",16)}GL.iNet<span class="ext">{_ic("ext",13)}</span></a>'
        f'<a href="/findhelp">{_ic("help",16)}Find Help</a>'
        '</div></div>'
        f'<div class="nav-group"><button class="nav-link{_a("settings")}" onclick="lwDrop(event,this)">{_ic("gear")}Settings{caret}</button>'
        '<div class="nav-menu">'
        f'<a href="/admin">{_ic("sliders",16)}General Settings</a>'
        f'<a href="/notifications">{_ic("bell",16)}Notifications</a>'
        f'<a href="/social">{_ic("users",16)}Social</a>'
        f'<a href="/blocked-services">{_ic("shield",16)}Services</a>'
        '</div></div>'
        '</nav>'
        '<div class="header-actions"><a class="header-link signout" href="/logout">Sign Out</a></div>'
        '<button class="hamburger" onclick="document.getElementById(\'mob-nav\').classList.toggle(\'open\')" aria-label="Menu">'
        '<span></span><span></span><span></span></button>'
        '</div>'
        '<nav class="mobile-nav" id="mob-nav">'
        f'<a href="/">{_ic("home",16)}Dashboard</a>'
        '<div class="mob-group">Traffic</div>'
        f'<a href="/querylog">{_ic("list",16)}Query Log</a>'
        f'<a href="/admin/devices">{_ic("monitor",16)}Devices</a>'
        '<div class="mob-group">Tools</div>'
        f'<a href="{agh_url}" target="_blank">{_ic("shield",16)}AdGuard</a>'
        f'<a href="{glinet_url}" target="_blank">{_ic("wifi",16)}GL.iNet</a>'
        f'<a href="/findhelp">{_ic("help",16)}Find Help</a>'
        '<div class="mob-group">Settings</div>'
        f'<a href="/admin">{_ic("sliders",16)}General Settings</a>'
        f'<a href="/notifications">{_ic("bell",16)}Notifications</a>'
        f'<a href="/social">{_ic("users",16)}Social</a>'
        f'<a href="/blocked-services">{_ic("shield",16)}Services</a>'
        '<a href="/logout" class="signout">Sign Out</a>'
        '</nav>'
        '<script>'
        'function lwDrop(e,b){e.stopPropagation();var g=b.parentNode,o=g.classList.contains("open");'
        'document.querySelectorAll(".nav-group.open").forEach(function(x){x.classList.remove("open")});'
        'if(!o)g.classList.add("open");}'
        'document.addEventListener("click",function(){document.querySelectorAll(".nav-group.open").forEach(function(x){x.classList.remove("open")})});'
        '</script>'
    )


# ── Auth pages ────────────────────────────────────────────────────────────────

def get_welcome_page():
    return ("""<!DOCTYPE html><html><head><link rel="icon" type="image/svg+xml" href="/favicon.svg">
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Welcome to Lantern Watch</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;background:#faf8f3;color:#3a3a3a;-webkit-font-smoothing:antialiased;display:flex;align-items:center;justify-content:center;min-height:100vh}
.box{background:#fff;border-radius:16px;padding:32px;width:100%;max-width:420px;box-shadow:0 8px 30px rgba(26,26,26,0.06);border:1px solid #e8e6e0;margin:16px}
h1{font-size:1.6em;color:#1a1a1a;font-weight:800;letter-spacing:-0.02em;text-align:center;margin-bottom:8px}
.subtitle{color:#6b6b6b;font-size:0.9em;text-align:center;margin-bottom:24px;line-height:1.5}
.welcome-icon{text-align:center;margin-bottom:16px}.welcome-icon svg{width:90px;height:auto;display:block;margin:0 auto}
label{display:block;font-size:0.8em;font-weight:700;color:#6b6b6b;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px;margin-top:16px}
input{width:100%;padding:12px;border:1.5px solid #e8e6e0;border-radius:10px;font-size:1em;color:#1a1a1a;font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif}
input:focus{outline:none;border-color:#e8a000;box-shadow:0 0 0 3px rgba(232,160,0,0.15)}
.btn{width:100%;padding:14px;background:#e8a000;border:none;border-radius:12px;color:white;font-size:1em;font-weight:700;cursor:pointer;margin-top:20px;font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;box-shadow:0 4px 14px rgba(232,160,0,.28);transition:transform .12s ease,box-shadow .12s ease}
.btn:hover{transform:translateY(-1px);box-shadow:0 6px 18px rgba(232,160,0,.36)}
.hint{color:#6b6b6b;font-size:0.75em;text-align:center;margin-top:12px;line-height:1.4}
.error{color:#dc2626;background:#fdeaea;border:1px solid #f3c2c2;border-radius:8px;padding:10px;margin-bottom:12px;font-size:0.85em}
.strength{height:4px;border-radius:99px;margin-top:6px;transition:all 0.3s}
.step-badge{text-align:center;font-size:0.75em;color:#6b6b6b;letter-spacing:0.5px;margin-bottom:12px;margin-top:4px;text-transform:uppercase;font-weight:600}
</style></head><body>
<div class="box">
  <div class="welcome-icon">LANTERN_LOGO_PLACEHOLDER</div>
  <h1>Welcome to Lantern Watch</h1>
  <p class="subtitle">Light for your home network. To secure your family dashboard, please create a unique password.</p>
  <form method="POST" action="/setup/password">
    <label>New Username</label>
    <input type="text" name="username" value="admin" placeholder="Enter username">
    <label>New Password</label>
    <input type="password" name="password" id="pwd" placeholder="Create a strong password" oninput="checkStrength(this.value)">
    <div class="strength" id="strength"></div>
    <label>Confirm Password</label>
    <input type="password" name="confirm" id="confirm" placeholder="Confirm your password">
    <label style="display:flex;align-items:center;gap:8px;text-transform:none;letter-spacing:0;margin-top:10px;cursor:pointer;font-size:0.85em;color:#64748b">
      <input type="checkbox" onclick="togglePasswords()" style="width:16px;height:16px;accent-color:#D97706"> Show passwords
    </label>
    <button type="submit" class="btn">Secure My Dashboard</button>
  </form>
  <p class="hint">This password protects access to your family network dashboard. Store it somewhere safe.</p>
</div>
<script>
function togglePasswords() {
  var p = document.getElementById('pwd');
  var c = document.getElementById('confirm');
  var type = p.type === 'password' ? 'text' : 'password';
  p.type = type; c.type = type;
}
function checkStrength(pwd) {
  var s = document.getElementById('strength');
  if (pwd.length < 6)       { s.style.background='#FCA5A5'; s.style.width='25%'; }
  else if (pwd.length < 10) { s.style.background='#F4B942'; s.style.width='60%'; }
  else                      { s.style.background='#7FB069'; s.style.width='100%'; }
}
</script>
</body></html>""".replace("LANTERN_LOGO_PLACEHOLDER", _LOGO_SVG))


def get_welcome_error_page(error):
    return get_welcome_page().replace(
        '<form method="POST" action="/setup/password">',
        f'<div class="error">{error}</div><form method="POST" action="/setup/password">',
    )


def get_adguard_enable_page():
    return """<!DOCTYPE html><html><head><link rel="icon" type="image/svg+xml" href="/favicon.svg">
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Enable AdGuard Home — Lantern Watch</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;background:#faf8f3;color:#3a3a3a;-webkit-font-smoothing:antialiased;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:16px}
.box{background:#fff;border-radius:16px;padding:32px;width:100%;max-width:420px;box-shadow:0 8px 30px rgba(26,26,26,0.06);border:1px solid #e8e6e0}
.step-badge{text-align:center;font-size:0.75em;color:#6b6b6b;letter-spacing:0.5px;margin-bottom:12px;text-transform:uppercase;font-weight:600}
h1{font-size:1.4em;color:#1a1a1a;font-weight:800;letter-spacing:-0.02em;text-align:center;margin-bottom:8px}
.sub{color:#6b6b6b;font-size:0.9em;text-align:center;margin-bottom:20px;line-height:1.5}
.panel-btn{display:block;width:100%;padding:12px;background:#fff4dc;border:1px solid #f3e3b8;border-radius:10px;text-align:center;color:#b87d00;font-weight:700;font-size:0.95em;text-decoration:none;margin-bottom:20px;transition:background 150ms ease}
.panel-btn:hover{background:#ffeccb}
.steps-list{margin:0 0 16px 0;padding:0;list-style:none}
.steps-list li{display:flex;align-items:flex-start;gap:12px;padding:10px 0;border-bottom:1px solid #e8e6e0;font-size:0.88em;color:#3a3a3a;line-height:1.4}
.steps-list li:last-child{border-bottom:none}
.num{width:22px;height:22px;border-radius:50%;background:#e8a000;color:white;font-weight:800;font-size:0.78em;display:flex;align-items:center;justify-content:center;flex-shrink:0;margin-top:1px}
.warn{background:#fff4dc;border:1px solid #f3e3b8;border-radius:8px;padding:10px 14px;font-size:0.82em;color:#b45309;margin-bottom:18px;line-height:1.5}
.btn{width:100%;padding:14px;background:#e8a000;border:none;border-radius:20px;color:white;font-size:1em;font-weight:700;cursor:pointer;font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;transition:transform .12s ease,box-shadow .12s ease;box-shadow:0 4px 14px rgba(232,160,0,.28);display:flex;align-items:center;justify-content:center;gap:10px}
.btn:hover{transform:translateY(-1px);box-shadow:0 6px 18px rgba(232,160,0,.36)}
.btn:disabled{background:#ccc;cursor:not-allowed;box-shadow:none;transform:none}
.spinner{width:18px;height:18px;border:3px solid rgba(255,255,255,0.4);border-top-color:white;border-radius:50%;animation:spin 0.7s linear infinite;display:none}
@keyframes spin{to{transform:rotate(360deg)}}
.error-msg{display:none;background:#fdeaea;border:1px solid #f3c2c2;border-radius:10px;padding:12px;color:#dc2626;font-size:0.85em;margin-top:14px;line-height:1.5}
.skip{display:block;text-align:center;color:#6b6b6b;font-size:0.82em;margin-top:12px;text-decoration:none}
.skip:hover{color:#e8a000}
</style></head><body>
<div class="box">
  <div style="text-align:center;font-size:2.5em;margin-bottom:12px">&#x1F6E1;&#xFE0F;</div>
  <h1>Enable AdGuard Home</h1>
  <p class="sub">AdGuard Home handles the DNS filtering that powers Lantern Watch. Enable it in the GL.iNet admin panel first.</p>
  <a class="panel-btn" href="http://192.168.8.1" target="_blank">Open GL.iNet Admin Panel &#x2197;</a>
  <ul class="steps-list">
    <li><span class="num">1</span><span>Go to <b>Applications &rarr; AdGuard Home</b></span></li>
    <li><span class="num">2</span><span>Toggle <b>AdGuard Home</b> ON</span></li>
    <li><span class="num">3</span><span>Come back here and tap <b>Check &amp; Continue</b></span></li>
  </ul>
  <div class="warn">Lantern Watch sets up AdGuard&rsquo;s DNS routing for you &mdash; there are no other settings to change.</div>
  <button class="btn" id="checkBtn" onclick="checkAdGuard()">
    <span id="btnText">Check &amp; Continue</span>
    <span class="spinner" id="spinner"></span>
  </button>
  <div class="error-msg" id="errMsg">AdGuard Home not detected yet. Make sure you toggled it ON in the GL.iNet panel, then try again.</div>
  <a href="/setup/password" class="skip">Skip for now &rarr;</a>
</div>
<script>
function checkAdGuard() {
  var btn = document.getElementById('checkBtn');
  btn.disabled = true;
  document.getElementById('btnText').textContent = 'Checking…';
  document.getElementById('spinner').style.display = 'block';
  document.getElementById('errMsg').style.display = 'none';
  fetch('/setup/check-adguard', {method:'POST'})
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (d.connected) {
        window.location.href = '/setup/password';
      } else {
        document.getElementById('errMsg').style.display = 'block';
        btn.disabled = false;
        document.getElementById('btnText').textContent = 'Check & Continue';
        document.getElementById('spinner').style.display = 'none';
      }
    })
    .catch(function() {
      document.getElementById('errMsg').style.display = 'block';
      btn.disabled = false;
      document.getElementById('btnText').textContent = 'Check & Continue';
      document.getElementById('spinner').style.display = 'none';
    });
}
</script>
</body></html>"""


def get_adguard_wizard_page():
    return """<!DOCTYPE html><html><head><link rel="icon" type="image/svg+xml" href="/favicon.svg">
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Set Up Family Protection — Lantern Watch</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;background:#faf8f3;color:#3a3a3a;-webkit-font-smoothing:antialiased;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:16px}
.box{background:#fff;border-radius:16px;padding:32px;width:100%;max-width:420px;box-shadow:0 8px 30px rgba(26,26,26,0.06);border:1px solid #e8e6e0}
.icon{text-align:center;font-size:3em;margin-bottom:12px}
h1{font-size:1.4em;color:#1a1a1a;font-weight:800;letter-spacing:-0.02em;text-align:center;margin-bottom:8px}
.sub{color:#6b6b6b;font-size:0.9em;text-align:center;margin-bottom:24px;line-height:1.5}
.checks{list-style:none;margin-bottom:24px}
.checks li{display:flex;align-items:center;gap:12px;padding:10px 0;border-bottom:1px solid #e8e6e0;font-size:0.93em;color:#3a3a3a;font-weight:600}
.checks li:last-child{border-bottom:none}
.checks li .tick{width:22px;height:22px;border-radius:50%;background:#eaf7ef;border:2px solid #b5e2c5;display:flex;align-items:center;justify-content:center;flex-shrink:0;font-size:13px;color:#16a34a;font-weight:800}
.btn{width:100%;padding:14px;background:#e8a000;border:none;border-radius:20px;color:white;font-size:1em;font-weight:700;cursor:pointer;font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;transition:transform .12s ease,box-shadow .12s ease;box-shadow:0 4px 14px rgba(232,160,0,.28);display:flex;align-items:center;justify-content:center;gap:10px}
.btn:hover{transform:translateY(-1px);box-shadow:0 6px 18px rgba(232,160,0,.36)}
.btn:disabled{background:#ccc;cursor:not-allowed;box-shadow:none;transform:none}
.spinner{width:18px;height:18px;border:3px solid rgba(255,255,255,0.4);border-top-color:white;border-radius:50%;animation:spin 0.7s linear infinite;display:none}
@keyframes spin{to{transform:rotate(360deg)}}
.hint{color:#6b6b6b;font-size:0.78em;text-align:center;margin-top:14px;line-height:1.4}
.step-badge{text-align:center;font-size:0.75em;color:#6b6b6b;letter-spacing:0.5px;margin-bottom:12px;text-transform:uppercase;font-weight:600}
.error-msg{display:none;background:#fdeaea;border:1px solid #f3c2c2;border-radius:10px;padding:14px;color:#dc2626;font-size:0.85em;margin-top:16px;line-height:1.6}
.skip{display:block;text-align:center;color:#6b6b6b;font-size:0.82em;margin-top:10px;cursor:pointer;text-decoration:none}
.skip:hover{color:#e8a000}
.success-wrap{display:none;text-align:center;padding:8px 0}
.success-wrap .check-big{font-size:3.5em;margin-bottom:12px}
.success-wrap p{color:#16a34a;font-weight:700;font-size:1em}
</style></head><body>
<div class="box">
  <div id="mainContent">
    <div class="icon">&#x1F6E1;&#xFE0F;</div>
    <h1>Set up family protection</h1>
    <p class="sub">Lantern Watch will now configure your network to:</p>
    <ul class="checks">
      <li><span class="tick">&#x2713;</span>Block adult and explicit content</li>
      <li><span class="tick">&#x2713;</span>Block malware and phishing sites</li>
      <li><span class="tick">&#x2713;</span>Enable safe search on Google, Bing &amp; image search (YouTube comments stay on)</li>
      <li><span class="tick">&#x2713;</span>Block deceptive ads and trackers</li>
    </ul>
    <p class="sub" style="margin-bottom:20px;font-size:0.82em">This takes about 10 seconds and only happens once.</p>
    <button class="btn" id="applyBtn" onclick="applySetup()">
      <span id="btnText">Protect My Family</span>
      <span class="spinner" id="spinner"></span>
    </button>
    <div class="error-msg" id="errMsg">
      Couldn't connect to AdGuard. You can set this up later in
      <b>Settings &rarr; AdGuard</b>.
      <br><br>
      <a href="/" class="skip">Skip for now &rarr;</a>
    </div>
    <p class="hint">You can adjust these settings anytime in AdGuard.</p>
  </div>
  <div class="success-wrap" id="successWrap">
    <div class="check-big">&#x2705;</div>
    <p>Your family is protected!</p>
  </div>
</div>
<script>
function applySetup() {
  var btn = document.getElementById('applyBtn');
  btn.disabled = true;
  document.getElementById('btnText').textContent = 'Setting up…';
  document.getElementById('spinner').style.display = 'block';
  document.getElementById('errMsg').style.display = 'none';
  fetch('/setup/adguard', {method: 'POST'})
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (d.ok) {
        document.getElementById('mainContent').style.display = 'none';
        document.getElementById('successWrap').style.display = 'block';
        setTimeout(function() { window.location.href = '/setup/notifications'; }, 1500);
      } else {
        document.getElementById('errMsg').style.display = 'block';
        btn.disabled = false;
        document.getElementById('btnText').textContent = 'Try Again';
        document.getElementById('spinner').style.display = 'none';
      }
    })
    .catch(function() {
      document.getElementById('errMsg').style.display = 'block';
      btn.disabled = false;
      document.getElementById('btnText').textContent = 'Try Again';
      document.getElementById('spinner').style.display = 'none';
    });
}
</script>
</body></html>"""


def get_notifications_wizard_page(config):
    return ("""<!DOCTYPE html><html><head><link rel="icon" type="image/svg+xml" href="/favicon.svg">
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Set Up Notifications — Lantern Watch</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;background:#faf8f3;color:#3a3a3a;-webkit-font-smoothing:antialiased;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:16px}
.box{background:#fff;border-radius:16px;padding:32px;width:100%;max-width:480px;box-shadow:0 8px 30px rgba(26,26,26,0.06);border:1px solid #e8e6e0}
.step-badge{text-align:center;font-size:0.75em;color:#6b6b6b;letter-spacing:0.5px;margin-bottom:12px;text-transform:uppercase;font-weight:600}
h1{font-size:1.4em;color:#1a1a1a;font-weight:800;letter-spacing:-0.02em;text-align:center;margin-bottom:8px}
.sub{color:#6b6b6b;font-size:0.88em;text-align:center;margin-bottom:20px;line-height:1.5}
.card{border:1px solid #e8e6e0;border-radius:12px;padding:18px;margin-bottom:14px}
.card-title{font-size:0.92em;font-weight:800;color:#1a1a1a;margin-bottom:4px;display:flex;align-items:center;gap:8px}
.badge{background:#eaf7ef;color:#16a34a;font-size:0.7em;padding:2px 8px;border-radius:99px;font-weight:700;letter-spacing:0}
.card-sub{font-size:0.8em;color:#6b6b6b;margin-bottom:10px;line-height:1.4}
label{display:block;font-size:0.75em;font-weight:700;color:#6b6b6b;text-transform:uppercase;letter-spacing:0.8px;margin-bottom:4px;margin-top:10px}
label:first-of-type{margin-top:0}
input[type=text],input[type=password],input[type=number]{width:100%;padding:10px 12px;border:1.5px solid #e8e6e0;border-radius:10px;font-size:0.92em;color:#1a1a1a;font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif}
input:focus{outline:none;border-color:#e8a000;box-shadow:0 0 0 3px rgba(232,160,0,0.12)}
.btn-submit{width:100%;padding:14px;background:#e8a000;border:none;border-radius:20px;color:white;font-size:1em;font-weight:700;cursor:pointer;font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;margin-top:6px;box-shadow:0 4px 14px rgba(232,160,0,.28);transition:transform .12s ease,box-shadow .12s ease}
.btn-submit:hover{transform:translateY(-1px);box-shadow:0 6px 18px rgba(232,160,0,.36)}
.skip{display:block;text-align:center;color:#6b6b6b;font-size:0.82em;margin-top:14px;text-decoration:none}
.skip:hover{color:#e8a000}
</style></head><body>
<div class="box">
  <div style="text-align:center;font-size:2.5em;margin-bottom:12px">&#x1F389;</div>
  <h1>You&#x2019;re all set!</h1>
  <p class="sub">Notifications (ntfy, Telegram, email), schedules, and social profiles are all waiting in the dashboard whenever you want them.</p>
  <form method="POST" action="/setup/notifications">
    <div class="card" style="border:2px solid #e8d080;background:#fffbf0">
      <div class="card-title">&#x1F4CA; Help keep Lantern Watch free <span class="badge">Optional</span></div>
      <p class="card-sub">Lantern Watch is free for everyone, forever. Knowing how many families actually use it is the one thing that tells us whether to keep building and supporting it &mdash; that's the only reason we ask.</p>
      <p class="card-sub">Every install already sends a tiny <b>anonymous</b> record once a day, just so we can count active routers: a random ID, app version, router model, memory size, and protection profile. Nothing else, and it never identifies you.</p>
      <p class="card-sub">Ticking the box below adds how you actually <i>use</i> it:</p>
      <p class="card-sub" style="margin:0 0 10px 0;line-height:1.9">
        &#x2705; How many devices, and which features you use (schedules, screen time, social blocking)<br>
        &#x2705; Which notification types you&#x2019;ve set up<br>
        &#x274C; <b>Never</b> your name, your devices&#x2019; names, the sites anyone visits, or any IP/MAC address
      </p>
      <label style="display:flex;align-items:flex-start;gap:10px;cursor:pointer">
        <input type="checkbox" name="telemetry_enabled" checked style="margin-top:3px;width:18px;height:18px;accent-color:#e8a000">
        <span><b>Yes, share anonymous usage stats to support the project.</b></span></label>
      <p class="card-sub" style="margin-top:8px;font-size:0.8em">This one is ticked to start with &mdash; untick it if you&#x2019;d rather not, and you can change it anytime in Settings.</p>
    </div>
    <button type="submit" class="btn-submit">Go to Dashboard</button>
  </form>
</div>
</body></html>""")


_LOGO_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAJYAAAEBCAYAAACaFVytAAAAAXNSR0IArs4c6QAAAARnQU1BAACx"
    "jwv8YQUAAAAJcEhZcwAADsMAAA7DAcdvqGQAAGwGSURBVHhe7Z0HmGRlme//G+/e3b27d6/rquua"
    "XROKq2vASBAUF0VEkiSRZAAEJIikISM5h5nuOqFiV3dPJAcFRKKIqIhkmJmuXN1VnSZC931+b53S"
    "4thVHaZ6ZmD6/zz1TE+dr9I53/m+N/zf/yvNYQ5zmMMc5jCHOcxhDnOYwxzmMIc5zGEOc3hVIxfV"
    "P2Qj+nJ/TLsUPO0xmNBXi77+JxfVO8bn6S95hF8zhzn8GZYn9C/z5ukvc1HtORDTaaWoFhR9HZz3"
    "9HUmU9HXYX0RfSXr6fCsp/3zng4tRPXp8PvMYQ5/xHPz9c/5qHbLODo55+n2vCevHNUjpaguK3i6"
    "mJUq6+r0alJONq63FVxdP5jS1aWo1hc83bEirf8dfs85bMEYl/5iuad3lTr1f7IRbZvzlMh56s25"
    "ui0b0elZR/9T9vX+rKOPZzv1sXULtU3W1c45TxcXPMVLUf2mHNVA3lPv8k79e/j957AFY3y+/qbP"
    "13tyri7NueopuLp8oFNvC48DtlV6+kS1Q/+PVazgaPecoygTMuPqsIKrN4ZfM4ctEGxhpah2yDi6"
    "Mefqjpyrg/odvSU8biJgdxVcfaPo20r3o4Kv7+dcnTQ+rr8Ij53DFga2v3JM40NJPVOI6sTw8cnw"
    "gqu/YxvNudon5+q+oqeTC1frH8Pj5rCFga0s4+i5VWktzrj6TPj4VDAc1b+VOvXveVfpvCe35Gq/"
    "jKv3PX2F/ld47Bxe4+jz9bpSTNsVXB000q278q6uCo+ZDnKu3l6M6XM5VwtLvm4sudou52mr8Lg5"
    "vMYx3q2/zbvaJu/qC6WovJyvc/OOPhQeN1VkEvrXoqfP5zxFh9NamfcUYbKFx81hC0DB12eLUf2y"
    "ktC6kqftc1H9W3jMVEEUnkh93tMVhCCyvr4VHjOHLQB5XzvmPe0/mNQdY4s1XvD0rvCY6WC8W3+V"
    "9bRH3tO8rKt8oUPvzkX1yTk7awtDNqIPFH19JOvqu2t79GKpU+8Nj5kOWLFI72C8V1MaK3g6Ph/R"
    "O8fv0l+Hx87hNY6Cr90Lri7NOnqo4Oq/2hEmyDi6blWPXs67+m742By2EJSj2inn6p5KUmuxiZhc"
    "4THTwYsxvamS0G8Kvp7OePo622N4zBy2AJDby7l6bM0iVQu+ri/6es+GbF0wHsZv03h/TI9lHX2/"
    "lJzLHW6RyMzX3+dcdVcTquZc/ark6b83JB2TdRUZTOnxnKcjc1F9MHx8DlsIYDbkPZ1a8jVQ8HQd"
    "kXfSM+FxU8EL8/R3WVdXrezUL7KOflj29ObwmDlsQSh6OnowqaE1vRrKuTqRVSw8ZioghUMMa3WP"
    "xnOuknMshy0cuQXaqhRTpRjTMwVXR0GLCY+ZCqDSQAQcu0FPYGuROwyPmcMWhBev15tynu7Mu7qp"
    "4GlJxtHnsk24WBOhu1t/RcSdYGjB0VlZR1dABJzbCrdwlGP6j7yvw3Kuns55ejjvadfpRMsJKfQR"
    "aPX1/ryrG3KuLip4+mJ43By2MJCMzrnaLu/phpynS17o0Puns42RX4QsmPV0Qd7T8ryj7xSi+nB4"
    "3By2QGBX5T3dSO6wEtNHYSWMTXHVevoK/RP/Gq3Z0715V/vVn5vDFo6Sr49BKyYOVfC1DB4VPPjw"
    "uDAIVxSiejdU5pyjs6DejMT0pvC4OWyhYCvLRY1afEY1qRuIba3o0P8Lj5sIKz1tnXF1fd7RvrAb"
    "xufNPHI/h9cY+jr1EbawYlR79sf1QMHVScuv1b+Ex02ErK//qcR1Vd7TL/KebinH9IHwmDlsoXi8"
    "W38L6Q/O+khaD+c8xXPXTo30RxHFQEK/MhvL11fmAqNzeAWKMb0JO2swqcUY4Tz3gqv/O1nukILW"
    "jCsv7+qnfa6+MNPI/RxeQyDUwL/5Dr2Bf/sc7T3ao6gxQWuUmkMaba2Kq7fXJxqBVeJdOU97FXz9"
    "CrssC+fd0Q4NHzGHLQlU6PDv8g7tVHT00eXX610ZRzsVfJ1QTaoj72qgP2ahg1OKUc0r+Dp3sEt3"
    "lmOKVpPyB1O6jhL7/phcts9qQnmqoHOODiYmNrZUb7hrnv6Oz6hH4Oe0HV7jGPT1OlaZkZQuqya0"
    "MO/rof64zizF9Py6HnVW4jou5+realzfLUR1UCWpY8pxXbKqW48FlTfe2CJdUPD0vaKvh/vjunGw"
    "S3+AflOMaknO1W8Gk7p7MKFv5xx9lUj+s9frQ/a5Uf1boVv/SAoo/L3m8CrDYxfpH4ox/bCS0BWw"
    "F9Yu1iU5V4+WonphOKWeYlTRoqdnBpO6anVaO1MSxnZHWgay3guBMc5z9a1zvFv/yN8ZV7sNJHRg"
    "JaUvDMR1QCGqi6sJ3Zl1tWwwqbOM9oxCTcqCp3EM/bynbw73aCvYEOHvOofNHKRkyAFmXf1kJK3b"
    "yzE9U4xqHaX0piTj6blVXfpxn6PjmUDlSC1E0Ofqv6pJfZNJs4Iqm87WZL3nOvSGRxAU6dR7x27W"
    "HisuqW13hBxIRGPYl3w9MH6jxqHmwK0nJ7kqrflMMjzIzHz9Kykgov31iTuHzQSkUKpp/b+Mr7ey"
    "3RDoxGYaSurO/pgVS3yH1YeJhnE9Ftg7jEXCKPx+7YBte1G9ezipN2Q6tG8hpi9Wk1rcH5OTdXRB"
    "ztUROAdsrXlXTiGqncdunloaaQ6zjEJ3bZtandLbs65OQywt5+oHJIJXRvThgq9v8e/TsT/l7qqJ"
    "qQU+2wEoNY3/Z0Jna+mj3fOujiGoWonr8eGUXsy75lleORjXl++ap78mf8lq2Pj6OWwkmM3i6Mqh"
    "lJ7JexokYp7ztXcuqq/aFjVFOaKNhcfn6W+hPlPEkY3pA3iMQ0l9vehpec5VRzmmWypxLc86urIv"
    "pbeQwwy/xxxmGUVHn+uL6AhCAeWYluV8HYUYWjmm0+FGEWPanBX24HGxXY7dqa/mHB1Z8HRWf0yl"
    "0bTGyzG9kI/o2Lyv09Dgwg4Lv34Os4BiTB/FXhqI1VYoDG2eZ/so+vph3tenWBnwvNbeoA9Xkjo4"
    "H9MXxm7Re5F9LHraq54sLsf0TwVfBwQithfmPV2QdXVALqJPZj0diFAIFTe8js8q+dp3/WLtRgpo"
    "XY8+Vonq28WofpBzdW7R10Wre3T2H66qTWi0s5CYHLtR36Tqmi3biIWutuG4qTJ72qPP0adLUS1a"
    "v0jjBV+pSkLX5V3dnHfkEtbYEE2JOUwDqOYVPD1fTerevKej8aayvrZdu1i3DyZ0W97XD5ERgma8"
    "ZqmOKfi6Iuvpe2sW6SRsMIx78oO8F1smRj81hVlPx+Y8XTfapYtynn6MUZ13dXrB1QmDKR2f6dBn"
    "Cp6OXLtQy7DnVqW1dzWpU3OuriEd1B/TDweT6hpMKkU6CGZqNanfr16o85BJWtOjaH/M6g7Rilic"
    "d3R+KapbC75Oyjm6bP0SPcVKPP9w/U3e04q8p8PznrYO//45zBKyjr413KULc65O4ILkPX0H492C"
    "lUml7HlPPxpN6/GhLuXyrk22Tz1xlV5n8ahJilLZpv4Ys5qvv2FbrXtsaDSY5kMQopg3T39diPyJ"
    "MUoKaCil77Fa4SiUXL2PPGTe0U6DSf0g62jeQFxDa3pscpUH4uo1Q97R0QMxXczEJqFd9m3yzqkD"
    "bi54PLgQKx3950BcF65epJ761rO5AEM95+nbw2l9k+h8ztWePDIRfR1NebZHmhZQps8EDb9+DpsQ"
    "bEVIEy3v0FfDxzYV+D5E/80wd3VY+PgcXgUwGkyqpt+OgR4+vimQS+nteV/X5iM6naLW6ZSZzWEz"
    "AZ4WrNC8q91mKlrbbuSTekPe0YVZx6Lvd2Yj2iM8Zg6bOTIxfRRmASkV/j+V4oiNgZWd2r4/ruf7"
    "YxrOudolfHwOrwLUvT8mVd7Xvo3HSJs0/r8RbFHrFukTlM3jTbL6laP65IpOfTDj6KPBarjNaJe2"
    "mW6KiODtuh49VYhqKBvR68PH57AZA3pwIa7/gjFAIJNuEzlP59ALpz4G+goTaOwOvS7seREQXduj"
    "/UkZFV3twsqSdbT7YFLnIy0JUxRWxOq0dqgzSvl37P7mZL5yTNtkfH2WqmnYD0G/nrkV69UEIvAr"
    "HH3ImikREI3qk0ySUtSIeicWXe0DPWalq23WLdOpkPuIVTFhCKoSxyr6Oqbg6oj6e0LUG+nSVaNd"
    "OoaiCaL0RMsLrs7Oe7q66Kkw1KUH857OpxRsqEvfryb0PaPfuNpzZJEWWVjBs5Vwr+ULtBUFsvX3"
    "n2pV0Bw2AYLo+ZfpI1j0tTc6oHCq8MBWdau34FmLuMXDXbpodbd+gsb7C9fp7WPL9MWgbcmJQ12a"
    "h21W8vU1ApX192aiDiaVWN1tUflzsq6cKrKQnn6bjejYclTf7uvUCUNJzRtN65yCpwXVhG4uR/XU"
    "+E0aH1+mcRLmpI5I02QcI/+tqyR1PgwMmkS98tfMYZOCbQ+WABMI3hWrBwS74S4tIeJejqpY8JUp"
    "eHou7+jkgqczB5P6CbpY2GBw0eFzoXhMdD0XNY5Ux9pFupgcIIHWxs9jy2u0zwa69c9jd2qnelk9"
    "78mDmFXW0QJozWugPMd0cCWp+GBSz+dcZUg2I1M50mUkwPEsMt5zXuLmAyYGWwpJ25yns/K+jiM9"
    "MpTSSQVfXyrHdHAxqn0GYjq53moX2ybTWVOEqTdZWrlA/xH8/415T/dlHB1Kj5xs5E/b1UQgkTx+"
    "q97HBAsfoxCWJDR/Z+brreWoTsWug7I8ktLp5A8LrhYVfePTR21LhQoUVA3NYRPD4lWOfshdzzYI"
    "1QQbhonESkKimSKGyWoEAUb5YFIVtjr6QMNEDY+ZCWBTwL/nb5il9edNW6uWVL+EhHeOimpfp73i"
    "xXPYeBig1W5cHxpM6StBIBSNq4Pw/FZE9e6wpzdVBMzOB6HBkMfD/gmPaTfYyovYhq6uyvm1PCLN"
    "B6aqHzGHNiIoYvjIQFx3ZV3dEJTIb8MFCY+dDiDd0YY34+pSttC8o6+Fx8wGuBFwClCsgeGAnim9"
    "p8Pj5jDLoDCUbSvjqDcf0Y7wpyDRhcdNF7Za+Lq54OjTBDMJU4THzBYyEX3TOrV6OjMocJ10655D"
    "GzDg662N/8eeqiT0RIlG4HG9rR3VxpSCESroi2hH3pNYWHjMbGDc1d/RBhh7K+fqeqSRGrdzjP66"
    "N1p3QuYwTdRbhRDbMSamp29S8dLXqYMzjr7ESSYAWfB056q0SjlPd/R16lPtYDEQ+8p7uhX7amOS"
    "7IIm5gRNT866Vp1dKPrqoUTspV4dTniEcdkOvZ9YGDWM09FK3aJB7gwGQF8tP/eWbKe+UYrq+ExE"
    "J6EPOtKj7ryrxwfimjfSpW7soYG47svVQg17ZeIbzmKg/jDv6556mGBTIO+pc7hLN5vCoKsj1vTK"
    "W92rnv6ozsJJydRaAx9FHrOxyHWmzQ9e8+BiYrjS4SHvKDXSrTuLvgYqcd2a83RmOaab854eWdWt"
    "456bX9sWBxNK0NWULawu9rEhyLk6KudpMVH88LGNBSq0cR5II7EKL3f0tbHbtS2FHsS6Cp5uL/p6"
    "Mu9qecHXPXnX9Cb2YXUPv9cWCWwiytoLrj4znNYZAwldjJx10ddVOU8L8p7xvq+pxtVBpQpFDxjV"
    "vJa8HsZ1xtVNOVe30gcn/P4zgfXB8ZSAR798Az3M6YKqbAz4gbietu2Y3+zqfazez1+tt8GqKLr6"
    "QTWhCwbiGs57eqoUtfOEtnwXxRcEf+tKN1ssHr1Cr7eegb7OKEe1ZKhLD7EN8Mh6Op/JU++gRY0g"
    "idr6XUkMqy+ib+c9PZ/jIkT0yfD7zwRshQVXS5ngk2k3tBvQcii5H0ppDOGSxmNDEb2eim5qDElj"
    "kRUo+tp2MKldA09ycY6iEVfXjHZpN+g9pKPIJEwlMPyaQc7T9tWUbsq6epDAYDaiwzOdOppO8JwI"
    "ItIYp816/lFaZUlngoqu7slE9NnwmJmArbDoaxFbb70SZ2PBTAFHF/fHdXne0wvh4xOBjMJQQv9K"
    "r0Wrh/QUfXmJYqWY7obiU3C0O43Qw697TYGtDxcaWgkaVP1xPTkQ13MFXzeXYvp4PV83FViMKaqv"
    "Zh0tJbkMxz08ZibACaDDBLYMpVvh47MJJkd/TDf0RRRF9qjZTTURWMWI1Js8eELuUJeewdHJ+/ou"
    "REdLjm8mLNq2ouLq/2Yj+nIpqisLnu4goEmhZzmmY4jhYEfUi0inCmJMA3E9SziiLiG0obBeOq5u"
    "RalvYxvwfQv0HuoNyzGtzrk6e6aiICs7tHUmYjyxB4nmE+9DKinv62vtiPVtdmAiDaY0REYfg31D"
    "404km0dSGs55uoX3Cx+fCXJR7ZD1dGPO1ULK6sPHZxM4C9WE0WjWF1wlx8dnFgit06WNPVu7gS8P"
    "ahn3em6+/pnGB+HXvCpBpJjA5kBcL6GkRz4sPGamyDr6WR6PqE0eXMDwfITtlVU2fHy2QFSdm2Mg"
    "rkzG0QMEau+apGJ7MpiWhas96fNDZVDOVcxYtQv0jvDYVx3qXl3W09UvL7Gy8mXcSdPZ9tgq+ZcA"
    "Kqscin14UPDHS1FdPpTW79pmvCPcRkpnI0sgWXDY05V5TzdVEzYBunh+/Eb9C1yvRqrNVMHqRGyP"
    "oOpQWrfnPXVtblXhM0bR0X8iLVSK6p61i3XbdHolE1WG1Uk0ntcFHHZ449f1x3Ulf6/q0a242fX4"
    "1oYCt54oP1z58LHZBBMaSe+cq+z4UtsOf5H1TaMCCvVxBVfzq106arpVQQDHZiBl4YiLSNrDsA2P"
    "edUBT2/9Qo2P36LxlR2t2ZiNsG4RUX2aCcPWWU1oFfmzgqcU3PWRLp1YxHPz1Zv35ZBDC7/HTBBI"
    "TXZkHN1c8rS/NWzq1l/hcVXi2p7Kn/Br8HQHmrSS46ZY16vPE5PDtuH1E22xbOU5R3sTcljVq9JA"
    "XB1UBmUdXUgDqKyj8ytJFbKOTs1HtRu6DuH3aAZWQwx6o2Wn1INUEiEKNFTDY18VwFMrReWtW6Tx"
    "gbhuQaWlrkLcCthkuPpsR32IZjha0B9Trj+uh5F/JFqfQazMV2xFxMRqzyT8EH6fmYDvTPHFcFoj"
    "5ZhW5FzdUY7pW0Tki76eJd0TiHiczQNPK+dpYSmmasHV/ZWE7kMf3pisrpYUPD05foPGxxZrfLRL"
    "f8h7Sk0UH2PL4vygFZ/1dBeFrRAWC652Rr+LpHg2oquKvvqGulQ26aSoDsJeIpSAWmD4PRsxAFMj"
    "og+ggzrSpSQe7/IZrH6bHPC2qXapJNQ/0qOXchHtFR4zEVgdeF2xJvx6ENl96MHU5dXtHgKnuM14"
    "besX616EYmfKGJ0IpI7KMXXlPF02kNDpRhv2rDR+3miXziVtUnD0pZEu2zIvxzhmMlK8YQlxV3fk"
    "Pd3FazCYB+L6XcnX74ZS+jX06VYeLDdSNamnkWWimpvfjGgcv4/vtTqtIzOuLoHagy1Y8HRxNqJt"
    "pxJG4Lz1dWpe1rHsxsmFDr17OrbuJgeTw7q6u7oh2L6eyPvacSp9ZliN4COZuL8jl61ooh+/wtOb"
    "ETurJPUSF7ydisNsh1ysoHnTUeWojs17tUeRv10dxsP+Dp6HhWDpIL/24O81PTo23MYXG6nVSoGR"
    "nvf1tGUjmqzCTLCsp+PJZ5ZilleNlmJ6b3hcGPVG6jnXbogOSuVwgMLjNlsEBQLbBT2SfVx4JlUr"
    "HXOKDczVr20xi6yCZQKpbGS3iV8FEfJ0xtXdEOReK+Q30yb19O2cq2cotG0lBUCl0UhtW/w5lUpT"
    "yT5gK3JTmG6rqxNZFcNjNltgsLNd9Mf1DB5b+PhEIHOfdaw3zelsA63cfdRjqCEs+Lq+WCtOxXZr"
    "21a4OSDQQ52Xc/Tj8LFGIIsJC8QaS3n65mTtVDDkAyHdR3Oe7gsf32xBtBjmZdB54QZWLSLKkwlg"
    "ZOhQWksoX9KqgIHWbRjzeVfnsWqFj79WgBOBR1j0dVre0aHNSHx4wznXOl2ckYU2E9UObLXNGA1U"
    "BOFY0JaFVRHd+fCYzRJk27l7SlHz5hblfZ0yWTKX5Gs1qUtynm7POPoaNkR4DIAyQ3K2HNMCVkRy"
    "aaYXepf+mr/t/2iMSn9RF+Cf6YMtiPdqfI7ttv6cHQ9SI+FxrR72/YLvyHuEjze+b2DjnWFCuL5O"
    "m8hGrSb0zsCE+AFmATE4wjTNTAOaGKAZRoEsdCNkBsK1BJs1sJUGknKLvv6nENU3wscbwcpTTeqm"
    "vKufWHu3JiEJTgh5vEpCt5U6a8YqLUxWpbU/FcuEItg+SlGdyYWAG45n2efqoIKnAxnDA3sk8Dgn"
    "ehxCOgUF5QKrAK1SPH2PRwGDma3a0/cw2EsxnYFGBI2YGsflfB1sGg3B+/3x+VqbEybKmXxHXhP+"
    "3IyrY4q+Tic53x/V8SuxVR3Ny5IUD0RLGs8JkxRvj4h6NaEbcr6ub+UhkrkIVq1OGCbYq81Wt80K"
    "bHs0kez3dftwr/qpJg6PaYRpV3la2OfoW6WYtiOgGB4DIK0FdJYrKgndExQ8MHGOgodE9y3+Lbg6"
    "YFVa32ISmkB/r9423q23clev7a2NC7bp/xpIamvkronqo+e+Nq0P4W3yeRRsZF19o7FtSX1C2t8R"
    "vXNtrw6qxHUQgc1GIxung6aZeLSNNwmpKeygclQHjnTrIPTq0X7nO5J+YQyExuEuHZN3dQ/iJdWU"
    "bjRpcFRsXH2hr4n3x2cNxC1tk+J7U9AbHtOIvKehvKe7g/jY65utcJsNCBeQFCYomIkYS+DsVl1H"
    "zbXnzvW1d/hYHdhsMCItUOgqMdJl7dqOK3i6v+SbyP5ZPIZSOrnkafs/vs7VEcMpPbCuV6vwnEq+"
    "TuiP6QRWG05+JVZTKSa8kXV1CtF8M4aDhHa9iWUd8MimkrNDnrufihtXX+DReGw4pb2fCrIEUInX"
    "9uqKkS5dV/B03eoeHcFKx6qK47MyomPIrUI15rcTYiBi3vh+IEgH7YpyTlCmf06zYle2WYKueV89"
    "RV8v5lxdW4jos5NJlG8WYOmuJM0FvoAf0SweQz8ZgoWwSYu+TmHcREY+nk5ddIOOqA3PW94x0GWn"
    "hcg7TM66QzsTh0LFr5LQN6pJHTHSpVOZGNW49mU1a3x/0ijre/VVgpOssFay7+mbOAjGyqQ/D1ue"
    "o3lE1ev/b/ogW1CzMb8Ki5PnqintbQJunXrbS4u0v8WrXF1bjuqRalKlvKvToBsbEdLX1ypJnYi4"
    "ibEsYtqDrZzofGPDqTosWFyjLf99kBXYLjymDmPnutruuW79c97TrzOOHq9u7tpcfGkmCnrlaEVh"
    "T7SqnEFzynop1/oGXjJZHZ/JDrUg9Fn3Uk+X9Ue1EkO/buzymvFleit1ioMpvSef0DvrwUIAA2PN"
    "Ir2HvoOkOviXR71jBcFHHta2ztXb6/+f7MF7sPrx92BM72XiMwnGFuk95AtLnnYlHAN3nQnVEMB8"
    "+6puvZXX8pm8ttkKVAc3Hqsj+UVsOMI1E+Uk6wj05WPluIYmc642OVhmudtZgYg4F6Pak2KJVsus"
    "tYFz9SQ2RL2/zYYA3QWEzgJxja0mShpvLiA7QUFJ+PmZAFuOiUUxBm2EuYmaUZuNMeLrU3lXy2jS"
    "Sf1i3RnabMGdYxpPbEmONeo+aiJDcnyZ/p4fT3uSfETnUVqO4kp43HSBMT9+s3VSXUEhAdHr8JjN"
    "BdBh1i9RB65/+NhMwPZPNB2bEeckfLwOKwaG/+XqZiZWxtFZE4UyNitgA3DCMhE9RJKU5yZyZzFC"
    "qbTJu9aS5FpCDrmo9tlQ2mwg6diLbQJVZXOWYKQod/wRjeddPR0+NhNgX1nE3tUowiYUqITH1BEI"
    "zF09mtZY0dOx4eObHdj2co56h3vUTzqi2XKMe2tuvqfiukVaiYcUHjMTEBykNa41C/f05c25qTf2"
    "VjWlC+gy1ionOFVYHtXVM7Sny7laSqorPAZYo3RHO+U8LemPaRUdxzKevjjRArDZAEOUQoB1i/Rc"
    "3tVDeIXhMY0ghVOK6Y6puPFTAXlERGRNHZkehL4+FR6zOaJdcaS8p1+u7dVIKyVCGBa5iAnWpdcy"
    "CX0t2tDCllkHLjUcpf649Tg+v1kTR9RhrEze0dU5V7cTV2oHvZgmlxbl9vSrF6/XmzbnE4Y5QJQ+"
    "/PxMYaEKOGCObqaIwvhwLTzDbI3e8xT8emOfzLAqaKPAaBmOjs5EdEaz+BUIYk6dpCrYBgmAtho/"
    "VZTwjCI6MedrKHxsc0W9WGRDwdaH1GUmot9SU4gD1Sy9YwqCjs4a7NKKnGfFrds0i+pvclgcKKY3"
    "9cflDSf1LAL94TGNIHg42q0n8q7unchznA7oBx1IYH88iOXc34wRsLmA8MBIl77NysHKQhI6PGY6"
    "IJBMg/L+uGk/jLei0NgO4elHNEFA/4Kurs3s4U0O7ISsq8srcT1Rium+lU3aztq4Du1MamWoRwO0"
    "sOXOmomdEWh3boXNAKuimtCRWbquunqR9rqlqHGPToC/ZeRAOEue9ir4OoHyc+7qxgfaU/1xHcfx"
    "LErMrr5RPxa05yXwa6khk5WsHTuadBEtfku1lBGJ7le8b5A62hOP2WSHavm/l15epHHaAi/v1K6m"
    "LNMioDwVQHqsJk2TdQWpGmTAw2MA4Qi2SqPPeLqEHSM8ZrOCNS/q1lDW0S/Cx+qwQKqnrTOOZfjX"
    "Wn7L1XbT1XdiQj19hZVr/SjnqRuuedHX0qGkFT1QUdMxmtaKSkLZalIPcNKJgpNLq8SVgehmqjIN"
    "DxKz/XFVaDqQc+19Lms49vBAzJ7P8PqAIcvzTxSjtef7a8eT4fe1cTW+WV/B12De08vFqP6QdfTr"
    "waTuqCS0xKpxPH2PyUU0HH2s8G9uBaL0rHwZR/PHbtQ4pkYreaeVjnYYqNnCN1FQTKI+PGaTw+Sx"
    "aXzk6cDhLr2IIAV2VHhcHeOP6G9MJdhVlkLN8PHJwN1NshaKiWX+fd0YFBj8MQldx1PX6c3l6J+8"
    "pDqHngmO0dr4IHjYyEZlJa0fqz9XByGCiZ6nYib8vmYcozPq67MURIykdRqivIRnaK+yutcai5Pi"
    "2c6aQzn6HAHj6WxPMEWwcbkhVi3Sy4RbwmPqIJDNuVrTq5wRJ33t2K5q8raCdIwxEDxdVvT1MPpO"
    "JH3D4wD8IfJ6UFxKUT2R83VCeMxkwH7CUF27VGtN3tHTqdZV1dO7GvOAryL8BXJD5E1ZJYOV6z0T"
    "TdxWeDGmN1FcQSsV0lvNtOJhnlK4atr4EXXA6giP2WxA1XPeU2F1t0pE1ZsF3FhyEfrHGyx4Klla"
    "pwkPqxmYxGw55ZiuC5RhLBgYuM0Tfu7mjkC/AduHcjJkMU+djt2J8R+0vLu44Fl3ja6JahgB58g+"
    "z9PitYs0iM3HNtrMi9xkYGta7mmrTESHFHzdiaRhswts8ZZaJh7G5zqYmRPRZZqByWPMTLRKXd0A"
    "i7PeJOm1gAJVOp5+C/sAW4v6v/CYiYAXaI0UPF2xdpEqqPCExzTCmKee7lq3SNWsZ87Nt6ejUbZR"
    "QKgB6m0lqXtWdatS9OStbFJ2TmgAzylbk2RcSn4vPKYVSGCXXW3TH9VlOU8XN1vuX40gKo7tBReM"
    "Mn9CJ808u2Zg9emPKwbVGY57s7ADdlzW0/6jXTaJydduFR6zWYAZX0npyaGU9aI5qpk4LFXNNFUK"
    "ag4pld99uhFyKMQlX8dhq4WPvZphNOFxox9dNNytIbIRUGKmmpy3UvpaOOMPhEcoq2vF8KB4Je/J"
    "ZQvl/9PZemcdnAhOiOkOOKbWd85kwT62yqAV3DWmWdAk9dMMGJ4wIyhf2uzsgjaAxPT4PRpHkxRm"
    "61QvOAxYpAco9ye+xurXrNEAHimlZbBXsXv7PO2a8fWl8LhNBjxCJHfyvsVrYqaSEqmxIsNj62DV"
    "CuJEifCxViBUQOED6sD5mvLeLvyfYoTl1+tdOAX9vr5E8ntFjZ78KWsY3qlPGQHR0Zcssx/VDvzN"
    "g+cR4TBXP6od+H8lZqS57RrtRGyd1WntNJLWLhjZKDUPxK1yZqf1C7Uz34Px0KdX9wbv7WgHPpvv"
    "ALvW3Hq/9l1KMX2h7GtHvif9ovk84kmVmI6hvdyaXi0nrvbKM9AattrF7fzQC/EMVJbDY+qggbpJ"
    "BLg6j1iWTUS/eU3nJkFgUD+WdRS3ggRX20ykp1BO681B8HJ+1tELsBDgX4fHNQPhBLxPyqBogRsY"
    "8Qf0R/U9iinYBsrU2TmKVhM28S7lMZTSpcTYrDN9rcTsG4F6HzV5l1RqHS7uyLsqj9+p8YKvS/kN"
    "xmF3tc+aHn2t4OnHq1P6QiWuYwmRQDUpx3T0QFwrBpMayrt6ZlWPboRFuy5de2+S4nw23yHvWk/q"
    "J+07efoFCjRojlYS9prTBpL6VsnXvqWYbkfBJuPoYsrQeB+qd8LnYiIwsYzr5unKbERLmMTYUuFx"
    "AO5cv6O3EJilUgqd0umGN2YdT9P21tXoagJuvi4PH6+DSZTp0GdKUT1ajmrUCij8qQmBsQKy5eIo"
    "2IpBzZ2vfftjOqPg61zSNxaFd7UPeciBmE4hqs8D8bdgIu5DYULQ33AfHty15ZhOtkIJTwvWL9Fv"
    "SO3wmVby5emsgYT2Y3VcMV8fJ1XFzVOJ6zD6QZdiOinj6Ee8vuDr7Iyrw/r+9N6H89nB92DL+bH9"
    "XeuZ8zN0Ktb06hRCLiMpnVKOaUnWM9rP5QVPK1lBWAnryoiTwXrt1HofPlhJ6ZfUHbSaLEPz9a+c"
    "szy/A66/r9dNJyg7q8CQNuEwT90jaUut/LH7+0QwUQ9XsXUL9VI2ogObhSXCgMiGO02iFfEx/m9i"
    "IlH9Qz26zd80GiCA+khD1Ju/eY5jRJ2tlci1+hceVl72p3F/ZhsymYnyB4WlR3GX8zzZBpyHcITd"
    "tuXgvevVMxM9eA9+O3/XbahGW4rtLOOqe6yJVzcRrKCEcjBXj79Mg3OvJjnZDNZP29V8bgrqOtlN"
    "WpkwGxVsTZxs7BuCcnh6zUQ9cGlfcK3+jUrlh+Glh8c0A9qjeDhIRcIQbeZGtxvcxegiBBqhh69s"
    "Q1/EVmCLYstnuy7HlRzo1Nv4rRPJOTUDE4WcYR4P3NeOzSqg+CwqdkiS40RN9SbfqIBnPpjSPa0M"
    "d2wWK5hELSbQIW+VU2xEUEp+mAVGUWLZSC1JMMAJjRiPPqI9Zrvyh1WT1RClwpeWaDjr6qd8bitd"
    "rUaw6rBSkoTGYcC2bJUHZGWFeZHtDEIOUwxtzDpYygObJ2kaCbGJ0wjAtLM8LShFLQ3jGIVlirEo"
    "coB0ly9G9UsmFWyIjXGHWdGtZxJLkOfmbYwu83SLxSmhorzoaRWORCt9sUZg6JOQDlaiJ1sFWJlw"
    "RiVyTST4ErIYG1vfvinMy3O0g530Gn3liGZLL4AbNZSyAop54WPNYHdwrRz/F8MprbG4WVQ7NFsZ"
    "24liQv+JkV309BKBy+mkn2YKYnO5qI5Y02Me5/PkD5EvCI9rBTxmmAs5X19ptrpzDi3MQNW3YznK"
    "IyayMzcJ8CKs/ByOu6+v4GVNtL3VK3Nynp5eldb6nKdflxJ631R+CNsPLj5CuTAiWCE3xmoFAhmA"
    "W1Z3q5pz9dREZe7tBltZEAq5f3WvUY2vblX42wirGURZB/UaTz9G7Yfq9PC4Okz31dXPBhNaYeSB"
    "KQZjNwryCKa62q9UW363riz+c9IeEyHwWI5ZvVgjFtuJ60NTWeLZLvt8HbxuodZUE1o5mYpNOxFw"
    "pG4eX2JlVeXMsubue7sA88CCqZ4uWNVt3t2auic5GbgJkevOOTqTADRbYrPsBLnETE2n4qfD8N9d"
    "nUt8LjxuowNbgAg0CjOFWhWuyfy0ApHqdQs1ziQMH2sGPDPTjqppPcSzHfrvZier3aCkjRULGnHW"
    "VWFjeKMWhqBdnKfjS76xPDPT5fCTA7RehpGJyQDAhHIjVtX084yjLPTqP0zzc2YFAVXjSyY0hjR0"
    "i64HTAQ4PzlXzw3EdHqr5GgjOMHoYwWKL98nVAG9Y6LI/mzAvFhXP+mP60605TeUlz5VWHzO0b55"
    "R6f2RdRhMuBNGCONMNVqR59jZ2ArtYooXztOZI/iabMSkrct+mZDHhMes8lgKZSgy1TdZZ0I7P2Z"
    "mkxh5/IF+pq1MemcXAOTDgrmNrtKB51EKWhoKtXTbnBRTGzD0bNm5G4E4x0QXsAbJMyxZqGVaN09"
    "lcpuM/xrNyEMURSoDzORkBY2KSmdwS49OhBXH1y5Vs7XRgOU4Kxj0tjvm6zXCyuNxaBcxWjDMZVU"
    "BexS5Bhzrm6irS58LFJI4XGzhf64Teo7KnFz/S/cGOEGwIrFZyG4Vk0pjzgb8SYCxeGxEyHj6kTO"
    "9WQpGvRiTbXa1a8qcUtnfWazMOApo7KWH1Mw+oyqwcSawGucCKRsAn67NXoiD2Z9kSdQtpstEOfp"
    "j+uBom/dy/ZopdE1W8hETLmQONrWU+2UaikoX+cGQd19miWyibijFVuJK8853mwIfwVP8cGUfprz"
    "dKcVjTbZKrgT6ITAhWKCkEyezAC3UIav91OBU/Q1SgwM4bTwuNkEnz+Q0K8Jj5gq4CSrcjthYYeg"
    "d0/W1f1IlTdTlW4E56ivUx8xRoejOK9vZRviUA13qW8wqdE+V0dAawqP2ejI+DqM7lSo8llCtcle"
    "zh0TNAr4eGBr7TqZUp3l6TpsYq3M0iXU1dlT3QraCftsTxdDQyH5HT4+WzCRXU//TQ+egbgeRaN9"
    "KvEsixmig4r6c9BoITymDlZ/6D0QJ8dvNVmle1vpa8068DII/1tFMPWBrp5vxQQ1m8HVcbjvdJ8w"
    "3fNJ9nK2zECSqM6t2pOkd3jcbKIvoh2pjKZnIKK100kGtwN4eDhGlYQS0HP6rmq+8jRiRYfebQ4V"
    "TQjQVW2ivENTUc4xLZVH0voDgdXwmI0KViaTyfZ0fn/cgofPlqIT61xhJ6HniacSMAWOnizZyfEi"
    "6jWejsx4OhpJw/6oPtjMVpgNIMcNUQ5OlrEEYKdOMbfZDnAOWKEzjn788mLLOrzAOZysRoCVHruK"
    "2B/lcYRLsr62DY9rRJYGDhHdyd9c14nCExsNGNKUno8RlfZ0TrN0B18Sglw1YT1cFpuEjjd5Y0yi"
    "8kgfWtm6ox3wQNshUjYZ+F0vzNf7jImJXDjhFE8JJL4JAWxMd5ytCuLhYFJjJoPp6tzJGjKxqrJD"
    "GLOhVqN4Y3hMI4w27ekWwiqmWuNo31a05lmFfYEaf3vhuqUayEYmXq3q4E4r+vpFX0TnoSkwFbG1"
    "oE7uloKnOwrolzdxDNoNtvSBhDE8rw361Jy6epHiJvNdq4lsKsPYblCa35/SWzKunqsmVDYpzikY"
    "8IBqKDNRXD2MFsTjTW4IimNxkCyr4ek74eMbFQTwOOkDcaPA3gTfPDymjmAr/AhG/kiXniKKHB4T"
    "Bp5NztdJ1oSoJuBxxFQizxsK2+Ij+nAloetKUXVh11lhBPx6FAsd7U5RKGETHIup5vBmCpMkgOsf"
    "0+f6YxYOiFKbOZmtB5PVWh97ugUqODdLs+9qLYsd/Wh1j9awupkkeROy5kYBOqOU1A8lNdxKR9Tq"
    "3Tr1+ZynB0bSegZ5oFaxLC6uCY0gP0T3BF9DvGayFrXtABXdxlKNWiNPr0BblZg+wHaON4uEuK1k"
    "js5Bq6LV72gXLOwQ0SercT2WdXUDaaZ6m5RmYDLSgInGVkNdur9ZuX0dOGFrerQm5+qevK9jipHm"
    "oiKzBmudEdW7ubOHUpZ9f5oO8OFxjcDtzURUXdlp9W7vbBUPMvJgRB8mQdof19KcrxgR+Mm8yA2F"
    "6XNG9UlKp/Kefp6tNTT/WH11ILtAsUjG0ROBdtYVtCOZLLq9oWAbQ68CG3XVQtvamhasNILJkvV0"
    "J94kskWVJjcBDhHpqjU9+pU1oXJ13HRlpdoGWsfSDznr6IGsq/uqEb2z1QkmgbtmoUWvF4ePhVFa"
    "ov9j7rKjcwYQ/nC0Ex5ieFy7Ybx6OnFBWKxRgE4NBxapmhnttpZs+1mLNlfHtPrd7QDOgtmzro4a"
    "SlsJ2aQsEmBetavbEMQLhH8nlDNAN8ze39GRVj3ktE8bddpgeS56Oj/oMbNf0Lqt6aoFjWOsVjny"
    "CHt4+HgjrDra1dlE9bOullAu9WyLGFm7QFf4fE2T8xT+j00VDviS0qFHDys2MTVrH+LrK+Fx7QRB"
    "UdgcxJuqKS2mZ2OZXkSThD5Y5QaT+nnO0S/4juHjjXgagmBNcbrHilUc7TCbv2lCcIcGzQL8oq/f"
    "c5fzI5tFhVnK4RRlXT1PX8FWXSjYZk2ZjjvM1a2FqH5M67bZNiah71qbOkdX4mpTJtaKhMg5YJIt"
    "v1bvJHgbPt5umI0KzdjVL8tR/QpWyWSxQAtK+1a3OIathYnR2CqvDgh/eOoW7HY0L2CrvKISfKMA"
    "hV1jG0Z00/pFepFuqq2Mw1xKb2f1ybhaRoPI8PFGBBRmyrxISZzMyQxvR+0EToLReT0dnoW9QIFo"
    "p/4d47dZ+giP2DTtPb1r1SKTvt4fNmx4XDuBOAieYRYKOLGpUMu6iRAk/enCRujkCLbGiWwn0lTk"
    "IUkZUfFtOqkbgdD4Z8DYI3QwnFIVPjbiEs0Cnqw8FENmHLnDta7r104mPWRVz56+XIjoQLQQ8Lxm"
    "S6nP2BP0pK51RyUn+Fi49VwYgfoewiSnV1Na1ufoYNJbxJvCY9sFW0GRm/R0/GBK+dIkXLZACvOt"
    "RU/fhA6D/cRWHx7XiFzUOr0+iCHPDbfRVywAr3q0x3jSSbaOZjOcVI5tf75uWd2jX1Odm+v8c63Q"
    "RvQt0Hu4I4uO9qaMvlUOckMRsFrnW7vfqPahPI2td6I7uxEYvNZOF4G0qJ7FS+Tmmq2INRcZYRJj"
    "LKAzQY/HiD7QLDYFuGmC9n0vIAsQ1B1MeJ1gmhR8ddDdNZBC2gs2SnjcrILWtBiGmU5dXfR1wcpI"
    "a5pxNqLTqZHri+ieAbQPWpDlrM9fh7YOekR/Dld/tvJW2G2BfPbRFIgWojoTtiX2xmS8J2vlVutJ"
    "kxhM6k6LyEf0ZWyTeuPOdoJJwUQhYEoYJFPr6rFdK+oRMblAgvz2vCPXZMU7J+bNGfXZ1510t8g6"
    "OpVJO5smyITId5oszxu5C6pJ/bpZ5pzl2GgfrnauJtRd8HXjZPEuEDAZfEQxCAzO1opl9Ymetifo"
    "iNfJXYoMU3hcM1g5m6/rn+swefFrkSwicDndwoepgq5d1vvaV2wwqcdMG76Fh53h/CE9WStIvY3U"
    "VLMVjpsBqYS8o16IBeHjGw12wWn47emcZm6v7fOBOH3W0f3lmG7hhzZjYVpxRlSfps5tKGVjF2MX"
    "zFbi+Um2cFfPISdUihuJ7rPTrQTG0cDOHEqqOpzSk7R8CY9pJ1hlc55eZgeA6oKT08wWQmciG9PH"
    "rWO9p8WsXuExjYD/lXO0GNUctnn4c+Exsw6T/XGtkeO8yS68FUB4+q3pH7g6oNldg3sfiN+mh1J6"
    "HqHXqRRczBSkiKxm8E7jsy+fac0gcper0ioHMtinNrNj2gVrYnmnKf5F2a7QXwiPqcN6Rkd1F6sW"
    "hn+zxk0m5e1ovjkwrjVCYCGYVQGUV8D61iAbjXSRq3u5W8NjwiDYWa7xqX8UPtYIKCmoA1o2PqXH"
    "sVtmK41DPMeagnu6bP1CLSAoGDQQmPDEtwKe72iXuoJg7qHQWmYz7oa9NNKjh0a79CQmSfh4HdYS"
    "BnUfT4fSaa3gKUHMKjwOWFUQWycs3ZhegGoeHjOrMPc8qp1sK6z1hDn5xSYxrOWe3mVyjY4OxePI"
    "ttBrMu7VAr0D+6E/oZdLMS3EO5utJK/ZV7VWJLutIPjo6EjLRzYJ8rYCSeqMo3NwAHKuriFUMlsO"
    "ByAz0Z/QXUMp3U6pFzdkeAzgprTuGtQnuhZvHMA8aRVYLbj63dgNWs+uEYjjzop9OyHwnBAGY7Wi"
    "cqVZnR+TEKJ+3tM1uMjQYMJj6mBimRqdpxXVpO6GMTpVjfOZwERgPUVMzMTXjkGfwxlVqASExwuD"
    "3jo/ncyW2RCw9UGHGUzrvIEuFWBiTMbDz0WsoRQNmR5gQWjVQo7UG/Zb3lXVYmAzWMFnhIDXw4k8"
    "Oe8pRUFkeEwjjLPOFuHq8VZuuHG20Bb1dT1kQEhqs9ld3TRGWWF8nWRFmjUxtxnfnYWodi54Oovt"
    "hsoeU8eZggc8ExAK6evQtwZSOg2BtlbFD6Yr5usn5WgtJMLEbMblslxpVDut7lF/wdX9dfXCjQLY"
    "k9yRFBdAL6FuLTymDtxhktUFX4/QnLHYqY+s6dVuxH8oUkBfnMm0rkefI12BzYOB2R+3leRk7JRC"
    "TF80sn/wCNSFP8djKGWFBm9HmYXoPu9XPxZ+8Dk2Poi54QEVfd1OpBmeFQyCmbIUmECWlPa1LXc5"
    "9Bsrdff11vW92n75AkvFfB7BDX7D6h59sdnFbQYmxDO1DvTbknYilkgGhAA0266pNffoi6y+9d88"
    "wDl1tQseHkXFeU831H//ROA7M0mDtNFt2I6zrWD4CsBDqiT0csHX5a1soGJMH2Vptd7DrpZScFmO"
    "KYqXCB2GyuaCp2UoHPPjg/L52GBaBXje2D+o+iJgC1s1eMTKMS3Lu1pW9LUMhT/eu+DbiSMGY8fq"
    "D+vJ5+pW2nv0x3QDiXM8zWpCp1fiWlFJqUyVMaS4ybzbZqhrkRKxLvl6sK6gRwxvdbdi/XHdRZAS"
    "R4HcXTmmi8m18rt4mNK0o335fQX/zx+VhM7IRHTimh7Tx0e09zA0LMjpYcfyWX0d2nt9j63A1wc3"
    "zM0Dcd2S93VV0dV5qxbqOdPX6ND7kZAM/wZA5VTO1UlFT0vKUY0RzpisCKOtMP56VOs4OZN1R4Vx"
    "WfBUtniXq52HU1ZtUin6Fgn+aSWhxyoJLbXIt6cjh9K6i0lARLxQU5k7rJLQgyVfj+U9PVZNapz6"
    "t/GbND5+e41kWPDNJnh8dbfGKTiwY/XHT23MM6jhQLulPyCrYUDNOWHdTXqGiw0tpVk8aDJgJNuK"
    "FbPmUZcScuB5VpRSVL8YThsd+zeo5lngt0ZtXk2TAGsU4CrPxBv/mcbH76j9rlc8fq7xgbh+X4ob"
    "Pfsy4kxZxxLmN3JOSS+t7dUBxahuW9WjDjTEUPMrxVSk3xA28WDKejTegeR3szbJgUbGN0pR3UBl"
    "NIUk4TGzBmMf+PpKxtHdrFhsKeExgPJ4kqaWOff0UxOq8PSdkm+C9acSSORuHOnSpUNJK3rdveDr"
    "ORipsDQDQ37XkbROrsTl0TGVR87RdVBbqKCpJHQlgmzFmr1HWuayYk2E9o+PQca4OmlVWnsR7qAB"
    "gFF+fJPkvsb6V7doGDkdWJmYp85630Qu4GiPju0j51nToTosqPg5Ere+P64reZCnZLXiuzZ+9/qD"
    "3zsQsxzmWXRkDTToD2YS98f0PJNrJG3JaSrGef4o3q8cNdmn42H6rujQzvxebqrw964Ds8VKxzyd"
    "BeGPjAmcs8nSW22BFVF4urOfKhZPl3BBJxLpIP0ACY4vZXu7q0PumsSVhzA4lLIc2KwKyOKCY6eQ"
    "DM97+n25Dd3DsAe5cUi4Y4faKubqjTPdXieDdap1dcpAUmPEn359WXPvzZq5u4qY1ryr25plPoAF"
    "qB09nvd1j6kzujpitgO+xu/Bnsi7Wv7SEr2MF4T308zoJZE5nNRTTET63oSPbyoEHuH2Zhc5+lbG"
    "06EbGnfi3GDsWv0j4iURfRYJgfHx2QnwApoijN9mEfgfTSa5aR6wp99WExovN4k78h7YwVZ15ekC"
    "akZZdWdqIkwZlF9Z4tXVvWt6NUrLkvCYRlglr69U3lFqMglCI/R7ig7EdcRsq8kwyfFMaShu4RBf"
    "P2SFDY+bLkyFL6p3GwOhFnJo6thsCMzI9vQJvPJiXMsnS3sxybEpM65uRmu0WXyQWGK9SWkZT97V"
    "eZNVXbcFrExQSjKOHly/xEq+rm5WlYtbDCXZKpk99WZ9HdCK4Mc2aHm7G62qeq9Wy/WGgsoaK4bw"
    "dEXRr/HbN/SuZOWmwDb8fLPzsyEIeFX/luswAdyFk1GjCTmwmq7u0W/7Yxo1GnKLmB2xRFZChEIY"
    "1+q6tQ0E0CoJvTiYNK/wB82i1cRVSDjnPD2CKCwnHSpveFwd1uPG03e4S4gUE58Jj2kXyAcGTZrI"
    "+Deth5wOgpzcH1m0wf+/DpvjlSPbB2PmRvR11A7Z0sPHG0FmgcQ1YrmEbJDiDo+pg+s2kDI6c5Kg"
    "aqvAdltAeB+7oRjVdQVf97QSpwdsN5WkNR9/LHysEWYwImbPqhXVBzH4m9lt7QKR/qwjt1mt3XSB"
    "sd4Y9IQFiwfYzGtuB8hJ0lQh6+rcQosEPwFbwgjDKV1TTegpYl3NcqJsmTBHM47OKkR1UH7BxDGv"
    "tqKPnJOri9b02GxeXvL1tYmMXr40hrtV1y7UGGrDFKBOxFTAXsDFJRiK50I4ITxmNoFCzkTfqxHQ"
    "R5o1lNzUKLj65UtLVIJCw/8n2tIDu/jtFK1aj50mqkAgULf5KKkp66ZGc4gWLVPaAlI0BU8vQowj"
    "W269iyfgeFtzTMRVPZ1T8jVCuIHY1UST0KgyNB73tWMloXTR1SkEGsPjZgv0Tp5sYhFKmMkKajbp"
    "LLFJuXnJVgwltWI0bYqDi62xU1pvDo8F3BhB5H8hCfNW9GvrNsJqWwtPJOHZh8e0DSiV4PXABijH"
    "9Azb22SZ75yrR19eZp3qzyTnNFFMxwKpNdmdTw526SHurla2WLuBXddsYvG8lULN0E5iG2y0u9oJ"
    "TBJYI/1R7bwyokuguXAzT3TzApwIVJSzROs9LYYSM9HNEnznQ4mN0XsRlnAzYmZbgM1gnbcc7bQi"
    "Yk0dF4TH1MGPNsqMq8NW36AhZCTDYxpBY3JiMhlHl5JWCB+fDZhQCV3IfJ0QPtYI0iE4ElYZ3MQu"
    "CYM4Fp4tObnJKC0zBTcjW3TetT6RL+c9FZttWUHK6cMWPohrOWGKZkHPoLh4axuPfkVQDT7V3z5t"
    "WKKVUixXsaBTfaKZThRxIlz6gbgWjd0YuK1NvEdAaGJVj9aSy2NLDB+fDWAf5lGy8XR8+FgdkBTx"
    "iKi+MeXmFjwmYJxx32jYcdP0crVzeEy7UXD1G9qxDCY0Ptn3YwskH8kEI/vQii1LRTThpIBj9s1W"
    "RRsbDKO2uLqeNrk0xqZDZ3hMHeQGh1PKVeJ6yVRPWkTeUcob7FKCpR2aTPh4O0GayVZTZB99fZ9w"
    "g92RIaM3qI5G4fnzBG9zUe05WfUxVToku7OeKRXfPxUdsA0B3znv6qbRLpXznoaQ0gyPaYQ1e4Ir"
    "7yrJtWwWn7IwiauDzFkj+u7pwGZb7AYDrnQuqoMG4kqzag23CLBZqsTTHsNderqS0DhdQsNjGkFK"
    "oi9ikpD+ZPGYDQWOAasQJ8/Kzz1dDHU3bPQaw7TWuOCoIEp/YcHVAa28QzzhYi3RfHHB0xXTrfiZ"
    "LrBZc44u64/pDzRaavV51vTJ11X9MV2Bik8rOgw7EYn5UVgZnq5ZOZuCd7APyLJX4lpFkSZpgmbG"
    "ey6ivWgiPtKlsYKvP7T6wYAI+GBS43RhQJQ1fLzdsFZxNf30I4Ku8j9urHQhv2faESgOBw2/OdFU"
    "DRmdZwKZS+sdXRPWiGN/Qt2GHTCbGQSqjEhE0+2eG4RuH+ExdcA3wynKODpyMKVHEbULj2kEY4dS"
    "ejrvyunz9alZKw4hyMaXoZVskYh7rZjiE+FxwKgXjr46mNY6SGfGIkg2r34mR1iKKpeJ6JhcbHa3"
    "D+ygPrSvUGHx9Ew5qofDIQFKobCPWN2I69S1uejgwCo2kW2JDQq7gVXQKo08/QhB31YrXDtgDARP"
    "j1biGkYuPHy8EUyWvK+HAlsWvYqm7F+oNhlHqayv3YcS+tdWzNMNBrYSHh5UGai8E4UP6oBnvX6Z"
    "qpTK48FM5NrWEYiKXWQMR1/b8kPCY9oFArJBTAepnrNNRzWUsrALENeHGvN8TJwieqREo2ml2xAD"
    "opSMm6nP065sN3lXHnTnP77hLMCyFbWehp0FX4uGu7S6GWsBBE2yPmOCwa5+xoRsxkOjmhuThLgX"
    "7NxWck4bBFxNtgaTd8ZT8HVwsyXeVrZaY6XTBrv1BMZleEwjTD+hls45Lu/rAui9GNXhce0ETAta"
    "4ZI/yyL17enM+jEM2iK8cl8/zF37SjsS954JAwmOO7r+PCyOQPP+cIpyTenZ1UGTmQAbgqB8Dar2"
    "QVRC4SywZYXH1UF4gRvF9Pahc3dMvNuAetmYtf/1tBa+Pjdb2w14bA4roqjV4Z2SdXVKuUlYoOSa"
    "PvqueHiBB/Jwq37GGMYYxUES+lC20FZucDvAHUm/GDzctYtUIZleP0axBpOjmTRl3tH5lFzB4aqv"
    "wrb1R0wU7Qx67gRND77ZLFbULiBHwHWxzrWebqzLGbQC9iEcf2xHFKCbeYbm4CDlTQW7Kw97sx2E"
    "yD9DsPQeZ1zrpN7A5Am76HUEQcXFo2m9YJqeTQJ3dViqyLGGmv9DZj18fDbAimuankk9SdEFJ5zV"
    "CI+WlAbfOZzysEBjzYBPwfevR+yNru3qkOFuPV2K6m6S6dxMBBsbX99OoNgTOCEIqCRgODS7HnUQ"
    "uLb+RExGnBdf5xavn5jWg2OT9/SLnKufU4AyK+X2nOyAD033Ltr0toyO29ZW4zo9GT4WBnYay27Q"
    "CmVeodYJouky3Q4EqZpt+LyVHdqOO94abqLUDGMAuZ+g5L7xddhZ1viTqmnkj4KaStOWiurTECAH"
    "UxpkJZhN2g8wpolvmvOH5331sJuEx4TBdXvxer0p6Fz/UPh4I8gBc01Kvh4p+noGB6fttpbVmhFM"
    "9I1xCXf6qlYnzipgHF1XTeg+agXDxxthLWrJQVIGXqtn40K3DPRtKKxPT0L/SRV3411u9hOCvUht"
    "u9qmWe0fXCbOBbaHyQTVtEFPLMd0bzmmu5lYs/0bcEKsdUxE2xZ9y2y0FLOrA+eIrZ/GWnzHVgxR"
    "Ul4vLdK6gbj1wz4v09F6QZkRAloLHSKodYMn3jRCbifa0cFPX693TUW8y/oWO0rlPd3L3Y+7Hx7T"
    "brBNsQJzE9Sfw0uyaHunPo+n2qyS2Wg+CMg5OtQae3r6BN8dEl3W0UNkGVgZwq9rF9iiMaZJRxWj"
    "WjiU0nJymc1uhDqMzuTqu9YkAEmAWsJ/wgS7MVQ9XUZj+PULzVa+qNUknDa4o/GAshHtP9RV64tM"
    "bKeVhwBjoJrSSZRYhY9NBCgcL1DR7Ou2ev/DyTovtAMs7axM1n44oteXWIlqjNdJg7TGAKjJkH8R"
    "7nze069H0noK56aZQdwuoN1QIjpe65XzLA4DaaeJKEyNCJSuDxtO6W48fFonc4OExwEaJxDKqCQ0"
    "9nKt4PiI8JgNQr29G0lVWptRo9fKKDVmJoWpXbqOAtPxKVIuTCnZ0Q5QbCpdZlDPOnPRVkoqdhyd"
    "hVdnmqfUQE5hGzP1O19XmSvu66SCqztKUX0v06n9ZnO1AmgsmAR6TXgtl/Nah3TCWJ3Su3KeMq3C"
    "EyDIl/5+VVorzeOF4dFCqWbaYOkljZNz9WSQD5tQ1hkg5EGGH5VkWA14elP5MgF9dmfybP0pqxru"
    "mE1aL0CJOcgbnm8hj4g+jBvOzWBaDpNQRayrva+P0TgJUiN25VS2/g2BifGyTbm6fv1C081/wJoY"
    "tMjdNoLV1ApaPa3LerpiuIkebLCaf4FSv9G0MSL6WmlxzRjsy0NdypkoBy16m5DjAG1A0LmyCPUk"
    "+34dbLkYpOhoDcQ0bJI7BBnbua83gckY4Y2SqurU9gQPKRpplYaqI6g6vo2uDmyN4ePtBtSYsqev"
    "0wmsFNWLpahuw4Cfag/Foqej4cnBtcp5erEZQ2V8sf6vOTG+Dit6ur3g6fbwmA2CNS7y9Imsq8Rw"
    "WsvxCMNj6mCFWeHpzSsXmK1y4XTzS6ba4ulB8ob9ca3kcycrxGwHAlsDDa+zMeax+cJjwoBDbnEt"
    "SslqkfYzJkpOtxMwTAhWB6X6v8h5ei4b0bEvLtA7Wt3odViGIKb/MPvJ1bVFXy9MduNCf8p0ysl6"
    "Nb0ywjLhMTOC6WG5Oqaa0ONFzyjJZzRr9oOnwlZWSen6TKA32qrMKAxoygEn+5lSVDeTlwvHkmYL"
    "bIPk0ZhYeKaTiZ2YVFGtjJ7upNRNzirdB6xcoP/g4hLa6I9ZMPb3U22K2YhALK9jIK6VpnnRRNKA"
    "nQmdskpCuUpSLxNUbev1gGprKr2ehliF0IEKj6kDblV/SgNZVz8hmbziuomX2mawqLCn3+Y8Lcki"
    "iOHrWyR5J7N3NhSW4/S0v6mxePp8Pqrd6iJz4bGNCOydg1q1fGkX7Dv6Fu97BtWcoq9jmnVNbYag"
    "Cpz3uHGky3aF3mYLBewHPPzBpF4YTKqrmQc5Y/R16iMDcd0/mNTyZuXvJr1dyxN+veRrEBnGmVQB"
    "o/dEfIbXox9AWVNdiS48tp0Iqovp/nUoSjimiFNrnfffjSL9pl1RYwYca00DgvL2ZuelHbCVI6J3"
    "IlZHi928p5uGEjrFqC9NYm3NwA1gTpKvy0d6tZKQRXhMHUwkExRxrK3v8dbIqp35T1zrTES9BABX"
    "ePp8+DhgW8h2aGdSOba6+dqduFCY6zQVBKsHskQ/Goir0OcouTFkC0kqmzyQp5ug8MATo5qF7a7O"
    "q2JrD1gFh8DpIo4Vfp92wggANRIhvPtbA0HhY/t87ciNOxXbKgzrXuvqoZG0CpA3w8cbwY1USVir"
    "vbtZmaFqh8fMGLjgaxdquBTTvRQ9NgsAIl5GUK0/pnLe13f54TMlullbuRqZ/9xK0sTRrjGt0CaC"
    "Fu3CGJqdqOt5uizjmNF6uMVv5utvCNqaAnGNA7U7nPaZ3DjTQWDDsQucl3NtJyA3+LWZfq4VCEe0"
    "IymdalIjBFiLTciVhetrqtc0es85uhmaUXjMBgH3dFWv1iMwX6SBUhPPAFsIVT7rmdyi5HsqsF57"
    "ng4fSilVjutRpJAoVJiKx7ahsEi8owuHu/Tzoq87+2PqZMujUqU/rueQo6R9SN9Vs7c91yWFrGG4"
    "q7Mrcd1SiWtpIBH5hVatj1uB8xpUEj1Q8vVbYnjNHAB+L6k7OHWVhOnYn2Ke6Ybau8Rx7I5x1fXS"
    "Mr1ccHVPeEwd2CechGxEh5veqKOjCT2Ex00V1kfQ1TcGYtqf5Tpo93ZPXde8rXt9A4I2Lag3f3Yg"
    "pu+vX6TxSlzn4KazBSLlGOTbJvSk2gXsGZwJWKslEvOd2n44rQsoRWMibEjjdexJDPNMRA9BEAwf"
    "D4M01+pFWhswSveh/2R4zLRgqiyu9sy6ctYutOjrPROp9wHjMdFbB1KYp+cx9qCfhMdNB0weIs2B"
    "WjK9mu/NuXqCfi+tcpUbioBfRbnXNhSDVuK63Lp6xTVU8tscKAwBW5JiWtMzdSyiT6n7ceRqV7Rg"
    "fU4HUJXoyDbcpcGcq5Il1JsEg60bm6snBhKq5D3dByFzstjXlGBNgVytXdOjlxEoYxUJj6kjoI/8"
    "gIQszIENXjIbENA9LqecDOFV7K/ZSlRbvMjVZ/CIVkZ04GDS+gDtW/S1ss/R+bAIrBfgBqzIzRBI"
    "bH8k7+sYyuatl6KnQ/GK2/V5lgJy5Q8mNVqM6lkYs81kDajqwdCvxPVcDnusHXlQbA22hbyrtSNd"
    "etEM2SY5KSsvimpnAqM0+oEQFx6zoaBCZjStUtHXXegPWKMov9a0cqbtShpR14wnxYN3GKgb79rX"
    "qRNgxZpIhqeLyQtyJzdzYqaDxiITWCSB1Pbp2ED9MfVyUYmUv/JVG4aAHjQvG7HeP15mvt4XHlOH"
    "VSkl9J9ZRxkKZFhRZ+KJvgLGXXflVxLK0oGeSHh4TB2o9cLZGu7Ss2yHK2ZhYrEtUm2MUd8fM52H"
    "3xSjehL1ZuwQVpr65GqWJA+jvqwThxqIG3HvzFJUDwRFp3uYPHVQum7bPQUGjs7E5TcPrZZe2X4m"
    "MTsr7PX1fiZOxtOPR9K6kt7ZUJ+zrjpadZzYUDBZsJ1znm7JLNDnwscbwdaZd1XIOLquiNyAo53C"
    "Y6YFi59As41qnVFmmpDCQGDk77eqW1VKuGkZGx7TLhj7M14TkrWuoTWKLhf6ZGsKReOBhpXVFG3q"
    "/PRu/VUjIQ4jPefrqKKv35ai1hHjOxD4sLGaTRa2JOI5JuldkwZiIjiZiBVQ/PFzCb80vq6e92RC"
    "sdoRUEb9xRogoEQd0zdp/4sc0VQKIzYE2FU0YKBQotk2WId5kp5yA3E9D8EAilF4zLRBWRba65Wk"
    "js04+lr4eCO4w3KObsu5WrJilru612HVwLV816NQdU3e0NMTHCOoiLcKjbouxfMHupfVOPlb1+sk"
    "cQZoY5dzrPnmXrTOC39OI1gN8aws5+br3Lyr2/qjerKS1E3IXLJNshKFL1igof4J7nh2A3MQ0HJ3"
    "dR4THI+vPnam4YSpgms11KVnLKc7SfTeWqF4ilN1RYVPW2SNEJ2n48NKR/+D/lL4eCOskqfGTYLk"
    "tzVxrfCY2QLb5CNsVTUH4mzTZqiVnx+/pkfHs7U9hxSTr205MQEDg/YrRxD0JW3FCjgdh8AKMKL6"
    "MAZ3OaY9SjHrOvF1ngvKrL74zNX2LxTt98BjM96Wr72tS4Wjg3kf87w20o1Yh5XaeXpkqEsJjPnw"
    "8UaYVr+vG03w1tMF4eMzAsWjZV9PvjhfB5BjCh9vBGXpLK1MRtRZZivWU09zsAKMLfpTkHLsQf2T"
    "kRId/QgbZTClRaW49i16OpRu8yMpncZJJEE+nNLZpG1o3tTKeJ0O+iI6jlieNfh2rHchnTcuD1rw"
    "Xbmq27ZaSseQBtqf4o3JSrZmA0G+89BSTL8bX2omzpGtdLwwHaA/98e13Goeom1o6YvrS+eokbRe"
    "nKwRd70/Mplz7ITw8XYCF3kopSuqSV1E9QhNM1++QVcS7wkKIWwbLEd1c9HXLyoJ3ViJ60Tcd5SG"
    "xxbrQwT7mJyjPfoYtJm1PfpI3UZigqL8PBDVh5+/Vu9lFeJcrOvRxxr12xk/doP+E4bAcLpW0IDt"
    "tKJD78bWG0zo29bt3tU+rGDYZeWY6WcdOZjUt8xp8LT1mpTeU03r3Y06Yjcfpf81G8l3vjMredbR"
    "vVlHN5BVgJMWHldHENd7LFNTBMJp2rBrCxU1KK3/76G0fkmMCjpveFwdGLuBfvoFGNTh4xsCLhi2"
    "EqGAlbXo/g/GFtoFO6O/ZlTutqpbn6USu/4a0yDwdHU/AmoJbWuJ3JBTgSE6mDQ76aaRlOJk/+vy"
    "lWiAlaI6YrhH15v95eq+tT3as/E9GI9SDsWd1aTmUWrf+P5hcBHp50Oecf1ibRfIC2xDlzAowDlP"
    "t1rGw9OBa5Zol8ZS/nYBG7Hgq68/rp9DUpxM94vj0NKDDrJ7TmaTTQo8F4vS1hRaFhVcHUWn9/C4"
    "RuCC51w9ExRGtPQ2poMgd0a51arBhB7tj+mEFztqBZhjS7W1hRpC4h7YUv1RncPSDXWX0AJ2YmPs"
    "yPJh8/VWvu/qhSYSchSaDXwedhtJ9LGfaitCDthGvIbytLW92hfaChKXpah+A48sA92Gbheu/ovH"
    "2O1/6rSFzVkKaiwRNKsmtOfYzXp9wdeX6Pm4Jm2a8DBRqW4+dyCul9Ys1H3ULzb8pLaAnG85ppFy"
    "1DhWF0xWqW6BUV8v4nBkY3+utDNtsATCj8pG9D3u2IKvpc0CpICLa3EdV0tN0tmt6XtucEAtgKV3"
    "XB20bqGOKkd1EDmzOnuCH83WaI0BXO0yGNcBHB9N6t/rd5gltiN6Z7NVFw8Tr5G/iay3akm8epHx"
    "xt5FQ8pGD47zY8oz2H83m5CGxdMsvBAke83zTOi+VV06jtVqNK1dV/fogsBL/DrbejmqI0fTzRs0"
    "zBSB7lV/1rU8YQfbdKM2WCN4Hju5L6JbhlIqZD1diGZrs/TPlMEdbt1UPS0pePr9QFwlI741UZph"
    "dbP9uLZkxuDvGOM00Xp7aBdedPRRvMJqQndRaIm9Fx6zqWFduyAKRnQK4RsuNFviaFqfmGrhyUzB"
    "TchWXYpa8fFp5CNb3fRGfnR10HC3fgW50GJYrt7eljwtuSH4R6t7dH/e1e+s/92C1gIRJEtNONbX"
    "pUVfvdSvWeynxY9oF/jRWU/uYK/upkYxfHxTw4LOrpZZrMvVd8PHZxM4H0Vf5xUiOpBStaloZGQ9"
    "zRvp0UsWb0NSs13iIFwoYjKDKd1ejOqXZNrDY8IIRDcowPjdUEqr+FK4uBtjYj1b83gOLHj6HXVz"
    "4eObGpxP4mzYYDgabJEbI+QQ8LBO6o9ZMjlJDA0bMTyuDoLEJnTi6bJsxBqVk2XYqi3B0TpwtblI"
    "nBCkIrFnwmPCsLSHbwzQOxD6Ksd1bHjMbMC82FrpeQ8R+baeiDbBijbQK41Yp9qPbmjyfDIEXH4T"
    "hBvqNerLHsTbuNnDY+uwIHKnpZZQpkmbCMo06henBEhn7MnwoYbTeqDgKT8Vl5PkLOXnlbjurSQs"
    "XRKdzAPZELAiEvBEoxxvzYoiNkKp/nRhZLlO7YIzNJ1I/3RgzscCW2H+Fean9cWB2+VoKaslTRvC"
    "r2kEnXSHu3RWJa6HB1P6g6W/2jmpgJV1+/oKRid9hqEeTyXJXM/eD6VM8vpxYiHkHttCFAvBcnOI"
    "p9VIgX5/TC5Bz/C4zQGkj0q+UWLuIZAaPt4OWEFKxOg/pLhOMglLOtpPEmer47kObT2c1rqRbuuk"
    "izBd+88lNoDlAaPajdLsYlR3lqO6sJU4CGBilSiRIhruybUVy1V6IKbTCDLi9hNT2pBqZ+sRQ3er"
    "oOqaYof+pH6GjddMLnxzQDmmLxLzgyXBbzANss5aVH9Dt0bz/mpkxRisC25mqxF09V34VeHxYcC5"
    "qiR0Xs7Rr0e7dc9sUKBeAWs07qsDRZahlMqUlU9WGWs9Xzy9GW+CdAphiKA7e4TiCKt2mWYpfh1j"
    "9+t/93Xoq0F3+4OxGwYS6hm/W+PFqK6h/w/j6rktJjFR9fD7zDbqkpMUIjx9hf6pr/OVNmohos+W"
    "o/pk1tFlFMsi6ThVdkNmmf6euBI312BK71nba91jHTxiy1H6OoX4GZMXefDw68MImoT+qkoTc1en"
    "9HVqz9kWZ6lJFHk6vhTTfTAYCPSRWmkly10HAUO2QLOBXF3Lj6aNXDmqNNUnLLVT3RbIDeIQDHfr"
    "1qEuPZj3dBcxK9z3dQv18aKvnwymVBmI67aRlLExzxnr0VasBt17zh7jAv7VRAbxYxcZn2kr7L2x"
    "pdrfCk48ncmNmu3Q+7OuTg/SJvv0oRDo6pI6RYndYiI7jFgik5StPxMzu/Lk4W49uCptYioXZWgq"
    "6uhIGn0ysaayKwQB0UvwGgcSKnCdUGRuxktrG/6Yr4PWWks2n4q3SH6Qu3KqCdNAigeH4LtIT5ai"
    "5sFFC64uIT9ZT7lM5IajW0Dzp4KnX5E6otCBO7Qu6m8nu1aR/TA0D1MI9uSt6dWJeV/nVRPqHEnp"
    "muGUjqSM7eYgcs/2w8OCvPzO2hY7YfdVgoRjt5jc4m7jrv6O3061NCtHPXLP6kA6qejr9EqXripG"
    "9QQaD6Wojh9I6EGjH5P2wuOu6VChaXpeJS6/5OuBYlRDZBKe8PU6bEcuOvSc+nfoT+kt5CZH0nZT"
    "kce8NOvYNfk1aS6TV5rmThAUxT5UTWoJeVGSza0kQdsKwgiBIU8K4n7a35L5Dvr6TXkvZgvlTiSH"
    "lnU0r5rQwtEuncOdXIxq3opOEzSzpuDk8l5aYnLdbs7Rxf0JRfO+fjEQV3YkpZPqRjrfy9JKnnYd"
    "TauD5gUjaV2+ZqE6MxH9AGmi/pi6oVtXk7op6+jx4VStqnlNt5XUn1yM6bSBuDUXOIgVpf59mYTE"
    "nJj4fY4OXrdQxwdyTd8nzYHXVE3q1NGktVE5l/bGpajS/XG9MNqt3xV8/aY/pkcqSY0OpvRLKDuE"
    "HNj2eH/LRdYUmX9XTej5Uky35H3dNtylC7GP0Mtg4hMqGE3rnKG0Hh6I65b+mH5NU1IKTWzFc/Vf"
    "Fupx9J9hBmszMGmtw4WrR5GRKvlKTtR9Y9aBIW/pHmJFriUy7yPkT5xj/uH6m+lW6Zr3SKtfcmW+"
    "dqwmdeFgUku4cMNdOmckrUPKMT1NcHa4W0dluDupuWuSKuL78R2wuUbT2oPgYKDt/k8UhwwldWve"
    "0wvVhBZUEzqpP65rRrpM1jpWTcrPO1Zp/O+Q80jB8CB5zfuZenSDXcm4oKvGuzKuTlq3TD18t9Eu"
    "nbu2Vw8NJhUv+joN7jwJ7tWL9SVoOK/8xn8CK0QpavSek/Ke7q4mdJo1FHfkwJvKexoYSul3g2mt"
    "4Dm2ViqbmXRQXyZa5Vvht7Xt76JAFunMYHIfTkV4eOxGA8G9nKuFeddCCWfz5YZ6dUVQuPr6cCuR"
    "6WA0rTcHvYmvW9OrcaqxaUI0skinQ+OdCnMCOwTP0+g/QYErq1M1qY/Bbu1P6FPYIesWadv1vdqe"
    "UvKRtNk7x2D8ciFRDraGmSj++fpWwLz8dv35kq/vDsRMzGw3fu/YY/oH0yXt1NtYMczGCQRD6hKU"
    "U/H60LwyujJtjT1ds3aZvj3Srbs4z30RHVGM6vzp3sBhYPcNxJUsRm3rOykwb3ZFxmAyodxZh3mK"
    "tbzTYvb3obRuNE/C0cetv2ELNsREIKDHCaWzAwnwjKMLA7Xmo6Yag2mFRmYDtlJjwLYvpbeM3aJD"
    "6w0sCbhic7V6mKcZWiUsqj7N390MgQbXAZWkVnB+A1GQZ4bT6jBzwdUbp1sVbSwPEsq+Yv0xLRtK"
    "KYueKQSDoq+vNGM7bFQwsSwO4+lHGKflmG4dTFlbte+yrbViJgIuDGq+/G08dXrYuDrX2svSPSGq"
    "Hcyl7tAbTF1lfPbzjZsjll+rd+JNYnIUfN21qltPVBJ6gOKNupTSVGC0nojFqKhNWGx9dRwtLsJT"
    "m6e/nKmIy6wBz8jowI5+hGIyyiTWgs3VcdzBbEss3Y1uL/EtVowSy6+vDlxdagSR67GiyibiI1sy"
    "uPkGYrqp4Or+SsIqoubDr8+4OqqV6YEIHp4nnKrhLj0B7z9Q8zmR64JH3xY6TLuB280Sy6xf0aGd"
    "TfrG1VIMUcISw726j1UMDhDjGWs2hKfPD6etxS/1iE+RisHtbXte6jUGbKEVndqvP1qzcWHVUhMZ"
    "nlzYkgQ9EbEb6dIIMgFDXVpnjQ+I8j8yeXxrswG5MFtx6GYQ1fVIAq3p1S2olsCV4kcStAskC48d"
    "SGi4EtcfUJVh+xxK6/VzE6s1glq/d+WjOhZiY9HXYwNxPVX29ZNqqiZ3Sayr6GtvbDRYCkVfbsHV"
    "0r6ILoc5uskN9A0BYQSaiQeFrAhcEHW+GNH+SkK7UTIFzcW6f8X0psYg4Byaw8IgNer4VtRKEiRd"
    "06PxasJyuT8L5MXvLfg617Ikji4z4Tp2iU69zQLR0wxNbFbgB1ik3an18CNkUKjFaCIvL9SZ1YS+"
    "U4ppOzw+20pfzXfRJoI14aT8rEsfzHTq5HJMvx1J62fcvEaZcXUbThBxv9mq9dzkML0CT/+Ntmc5"
    "pn40IUjJBEov8Ld2t37E8/X3jZHvOUwMWBGsRKj9rKBczVeeMrKhLj1qnDT0Sl39F7G0jVmZvsnA"
    "BGPppniTrL+1Z3N1AiGGasLoJOStWta7bUmw8rek3sDkCDS0th1brH0Lri6FwYABT1XzaJfGEQQu"
    "+TrOAthtiqe9KhAUPBxI9ryeubdqIFdnkJUn5FCoVYX4QXR/l/sv0f8mXlPv/sCJ3iwCeW1GmJ/G"
    "JDKZpKgJ2h063KWFgwkNYz+VfF1qOVvf6iF3IdG9ulfrhlP6PQ0+WzV4f03CIr9IANWKCnY29ZVa"
    "K1psr2VZV0sqCT073KWnECArxzS6Kq3TMDzrFB2jnPj6Pt7lJkmYthmEa+pVz9w8YzfpPUhtkjjP"
    "ebqEXjZZRz8rxzRAsWl/XL+kasoyEq526a+xRY4bSKprZUT714trtyggQEGhAxl++FyNAv0E6khR"
    "lOPafShpwVJiW4vLUSMIRsoxfQvxNVa8ckynDdX4VnuZHTFf/zoZq7UR3NFBf5q2eUfkBydy48N9"
    "duqfSd7QtKoID8R07XDSuGqnDKX0s6yrp1EvhBiJNFR/TL8dSulKPL6xm7R3xtGh9c4ZwY35xmxE"
    "HdWEemhT0/h5WwTGbtY/EfHN1yg3Z4ePA1SKyetB/oNfT4gCVkHR07KAj7V4qEsXFmsrX/dQUtFs"
    "RJevrZXJJ8iF9cd0RSWhb69fqB3wRusVO2zFKNNQzv7Soom7zRsV29Vulai+AS+p/ljfa/82VdrD"
    "NmxcQblR2O65gaoJ7TO2WAeQpF+d1plrevVjyqqGUrq15KuzHDXpxhUkwLOd+m61S7/n5sl6+o41"
    "/46aOs1f1sv2UZZuLKmDLpP1dNf4zbW+kvXntxiQG8x5OotlfLLG5cDK0ymTonNEVP+wqkufgXtF"
    "+CLn6MeDSd1TSegM+E4j3Xq24FtnjJ+t6dXtqNaNppRClRDyHGmMgbj+MNSln0JnHu3SVwNBtK1Y"
    "VawDWdCXD47UkK/3l329P5/QO/l3bKE+yNhGpgLZhLEl+oitVpfUlPqwjYZTup6mUySO0W0oeCpx"
    "0RHozXta0x/THxAUKfkaqST0FLJLQYMAK4IYu6tG8LPaTl+fnSyPB7cLEbiRLlVhlIaPv+YBy5Io"
    "+0zbhUAp4cJZVY6jL43fpb9DXnptj/ZHD52CjWJUx65OW3uSRDWpJyHvVZI6n+g03tNLizRe8nTW"
    "SEonDKV0UzDRr8c2YXJwIcmhjdVia//Y2M4X5qc1ZorrbS8t1nG8bmyxDoH0NwJh0NUp5bgOWrNU"
    "t1onVE9fNJ3UqHXQqtIMoUwQs8ba2IdIOVwoVnK6TvAZ3EjTpVEb69XV8NgSjW2RK1awNZxdiM1s"
    "YtXRaBs1K/unrHzsJn0I9b3BlA4grDGQ0KVDKV06nNYP7LvQ5cupUXzY5l5aYrTjq9imylEdAtEQ"
    "FeWXlup7KEMH9Om/5fNNI93Vfi8tMl34/cZu0Iexl8aW6hAqnTHATdI8qncMpbXt2oU6p07VaVyt"
    "61LbU+lG2wxGsfG0bPVSVfnu4eOveSA3VPR0KkWUxsCM1mSDwuPaDfjwQxG9vjvEX8r4+tpgQgeu"
    "6zUt0+3Gl+mtE7WTG1+st4dpQNiBrJ7PN6lkwWNrLEYYu1NvmI26SkCMK+dpwfqb9BI2Xfj4FoG8"
    "q2LBUzboALHfVCqrZwsY9aYtsUx/P1O+18a4MVphpattgp7ad492a5BUTnjMax6BaMiTBU8PEHbY"
    "WEo0r2VYowf0FTxdmHX0ZMbTOeExr3mY4gpqJ7VaukPwosJj5jB9BErR6fXLrJHUpKpAr0mgXmwN"
    "yV1dOusFkVsIrKmBq55qykRrd58oPveaBRQa9NaLMe2Vd3Uz7vaGVptsycA+JHxDrM9qAz3dB8eN"
    "GBzneqql+q9KcOfUvak+V18YTmvRqoVaSK2d6S642gXhkNXpiSuP59AcFOtWktoBDjsiu/Qxws5a"
    "3a1z89QzTqPQ4lUH8lhMHpMWcnXGQEJJ6gWtQ4OnOCo0Q0nd99IS/bzer2cqqihbGuqV2PxN9J//"
    "9yf0JbrQ0hyrFNUvTBfDs/N4OztC1tGjiKJMtWnVqwJ1FxxSH5SP/phKo2ndlXW0KGhsSdyI0vCl"
    "RV8PF31d1R/XYH9c5xRqbUuoLdyVXF+gTf4KKXBSHK81bzJI24SDpH8x/rj+diih91nE39VVq9Ja"
    "VPAVg882lNLVg0kVR9N6nEQ9zFEroYvqxwMJZfKuIlZ026F31zUqQu//6oJJFLr6biWh2/nBWUcL"
    "yJtRYVynIMNGoIqYvCFaC4Nd+p+1C00XgqX8U32d+gKJ5IAJce5Li7QX6Y6xm/UfeJb1kiWS1tgb"
    "lvd7lZDbCJTahSaw6WpPYmkkqNcs0peh0VBuj1ZDNamdBhO6i5Up6+pyJtZAQneWovp9oFQDI+L0"
    "1WntXFe0pqBldVoXrFtkrePOoddg0Gpl6xUt2pts9jB14E59PuNoaX9Mj5WjehhNA6pHwmNJq2Bs"
    "NqrVUIGC3jlpj3JUuYJv1T6rB1PKUAm8qlsLCr5SQVvfgyja4HWkT+p/m2oMpWQhyoqNoylTExET"
    "q24mWMojWBGh8vB/Jm/9YccnqMVj4q9bpM82Eu2gBjN5+E6s5KSDqjRyCmSPch36hAmsOPrRyEIl"
    "Cp7JM8H+eHp1t8qVuJ7OOrqzkjTWxqND3XqUOgLrFNGpj3FjveJLBFmBwEk6DjU/ClRLUd1d305f"
    "lQiaZV85lNaKcky/yfvqDY+pI7ydwRIg1wUJEL7VSJe64G+PLtQl8OOrCd05ENdq04KKmuj+4YQv"
    "SEhnIjokG9MHEDJD7wFRjZG0Th7p0jx4TBTPwt2CPcCkZMULp2/gj6/u0WGDSe3PhTf5Jk9fXt1t"
    "fQwPwpO1ZpwJ68JxENs0N0V9ksHbX5W2lXWn8WXa7deXWV3f9wYTpol6WiFqNZf7VBI6AV7ZQEIH"
    "kt6ignwoqYuyjh5atVgP5lxTpVkylNSdsCRKMV26Km3hhNPpNtb4nSfilEGKpCMaf1s3Wlf3VGFW"
    "dEwuwLbZgruVusGRtJ6B1jJZq5RGoLoXLgPD+Ke1iVX8UHjh6OBhLh6NLF0dZA0tXV2PlBBcL/hL"
    "1g7N1RdeukVHDKe0N+Q322I9nW/BRE+d5biuZwumwXefp10bg7WsdPWVNOPqMyYwQqcOVwfUx1hb"
    "lZi+Q6l7ocbbP8piSBEdkfN1xupefWkkbQW8R5oktqMdVnVbw6bvFaO6uJLQFaNdNpk+zpZfjtnW"
    "ttvYz7QLKz6raiWpj1DixefxXdjuJuupGAYNp/KeflWO6m7eczIlxs0WrEIY6DRUKvjWW9m6j85U"
    "Hrse60Ifk38pLCA3xirB1sedj+zQ+sUWeN3aVpS4DhuI6fv9i/90Eq2RlKeTx5ZY7+W7B1M6Hvtv"
    "3VIT20DP4BzK0U0HjPZ4rq7JpvV66MFB22AaaO7F1mbbW1TvKPnWuHvhmkVKWD/CqP4BjhaThW1q"
    "MKmrwgYzN8jaRdpz/ULtbMoynt4FqbEu3FZHvb0I53MmzIegdP6NRU8Lh9JahzqQactPwHJ91cAC"
    "dr7OQB6HrDvqdXg901VHmQwWHGzQeYAHz9Zgq6avT63t0TdXpbUvWhH1MVxoFO+yrs6tJHVOydfl"
    "cKIKnk00trxDTILc12nB3wdAnQmkjPBWaRh1KM4JRjGTZ1Vap7PdTqSBUOrUe8du0H5MWv7Pijx2"
    "i/Z64eogfBDVO1r1DJwp+M1ZRykIgNikbMV1kuKrFkHTpvPQbghIdEeZpGFDi1q7EyewD9oF7CcC"
    "rzwmWi0pYFi/0GJsu9zVBjcc/tdEkpmmnbVU75iqyt5MwLms1w4iURDYkWeWo9Z3OklnimJE275q"
    "t8FGBKVelhMseFqQ99WDJ8MdbBRfV4fUKbdzBaozhzW3pHNEVJ9mG6eMji6zGVcLy1H9nJUUsZXS"
    "a6Ci6Y+w2BLGra8zqBVEu5RUDqtZydfZlIePL9P7sF3QFSC+g0vMFhd+ry0ZYRvLeiQGWyueKecR"
    "urS1W45pfjWhaimqPA3F6X4beMB/tmq/6mHVzZ66MZKDlevEkqdLSr4GRtO6AHWasZt0tNUa+voa"
    "SzZKKTSe5PWNW8xEEtWvJVASV58EpSU1gx4D/4VOfeRJVA9paE7TTE/noHXfHzXb8JpC1OQtrxxM"
    "6tpSVDeMpnQ4nvEG9xjcXIEdVafkWmlVRO+k4JRJNpzWvaWo7sh56l21SL/PuZbr6qV4Ne/ql5SL"
    "k9rBfhm/UW+09iYNfHFWuvpEY5UjRND42a9GWMOFTn0QLVZTNnS1C85GJa54f9TOUUegGepW4rqj"
    "4OsR8q5DKV0GHQn9+Kno77/mYPKStXIrVH+PG03ru2yVAzE9XUma5BE5xGfzvj2G6CJG8WolbpH3"
    "C5A7pMR8KCUfqW3c59wCvYOIf9AZ1bzCpyN6/ZpuHQP9ufHzmehcqIG4xYq2J75EQ0qkvxvHTReE"
    "I8Zu1PaUizV6v9iRg3EdhOqLRey79bcY8sTG+q7S61ApZgzJd+J/eKSVpK7h9/fHNJqrUYxuo1yM"
    "KqO8p/WVuB6DklxN6YuWYSALEdUHCWXUu2FssbCauSDlwtaHnOGaJdqDAKIVtXq6ALW6gVot3s+L"
    "vm4nTzaYNn3ztBWrduk79N7jLq4m9KV1i/U58pFUDec8da/r1eFE3PkMYkyI65tT4Wj3Sly7MxG5"
    "QOt79AVr/g0bI6p/w6ulNe/qlN5OxTSvx8scv0VvJ95kf3frrayi9eg9DsnYTdqBzmP9Me1V9LQX"
    "wVgmDe+DrbN2ofaDRjyc1rFogw2kdDSpHCS/UeVDLSbvWQ3ki4Mpjb+8SONDKSvpuhmlRDIM5ah2"
    "qsa1b2OTgzmKdwvU7SjuXGyMckwfMHpISm9hsvXHdSJjhnu1N+mdkS5dO5jSp1dGtEfZ17WFqHVT"
    "uAgDll46yFmP3a4D6tXDNBInvcKkIYjJSjaU0ucIBfAeFvuKaEfsEpPYTmi/Vd3ar85yZWKM3ar9"
    "WJH4e3Vah1jjA7rK+voU3zPToZ0G4rVWLnm/Jh/OqlL2dXU+omNHuvRRGkySNajG9eO1yzQ/EPPd"
    "J1CS2TvvqYsq75GUpWJ+MLbYSsiMEYpJUS/jnyhmNocpoNFOCBo6Wp6LWBiB0LHb9A/obY3foh0x"
    "YInCIzKydpEOI6C5pldH95POCQxh/q0m9D2O4Z6zdYx2abcqjcobhDRYtepqznhjXFRWqToViAvK"
    "3xS0Ur7PxTapIV9fW9Wtk4q+tXHpHe6yjh1bjaa0exDTO5T0kuUcPV1M5H0A2XLIjg0JelY6BNXq"
    "/5/DJgCpnLEbXpn/woaaSGjMOmo42in8PIFSUxb09RGS2CVfH4NqMhDTxXDDcq7OKPO398rHKC1J"
    "amVX26/p1fumQ7MOq+uFWRizGTieQ5vBqkLuLPw87v1Q3LrK72FUFP6N6CuDSe3CFgcVZk1au5h3"
    "FnoECe2vr+rRnnOFIXOYwxzmMIc5zGEOc5jDHOYwhznMYQ5tx/8HiiIeJASmY9UAAAAASUVORK5C"
    "YII="
)


def get_login_page(error="", first_run=False):
    err = f'<div class="lw-error visible" id="stepAErr">{error}</div>' if error else '<div class="lw-error" id="stepAErr"></div>'
    first_run_hint = '<p style="text-align:center;font-size:13px;color:#b8956a;margin-top:14px">First time? Use the temporary password printed at the end of your install script output.</p>' if first_run else ''
    return f"""<!DOCTYPE html><html><head><link rel="icon" type="image/svg+xml" href="/favicon.svg">
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Lantern Watch</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;background:#faf8f3;color:#3a3a3a;-webkit-font-smoothing:antialiased;display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100vh;padding:16px}}
.login-top{{text-align:center;margin-bottom:20px}}
.login-top .logo-wrap{{width:240px;margin:0 auto 12px}}
.login-top .logo-wrap svg{{width:100%;height:auto;display:block}}
.login-top .logo-wrap svg path{{stroke:#e8a000;stroke-width:1.25;vector-effect:non-scaling-stroke;paint-order:stroke fill}}
.login-top p{{font-size:13px;color:#6b6b6b;font-style:italic}}
.box{{background:#fff;border-radius:14px;padding:32px;width:100%;max-width:380px;box-shadow:0 8px 30px rgba(26,26,26,0.06);border:1px solid #e8e6e0}}
.step{{display:none}}.step.active{{display:block}}
.step-title{{font-size:16px;font-weight:700;color:#1a1a1a;letter-spacing:-0.01em;margin-bottom:6px}}
.step-sub{{font-size:13px;color:#6b6b6b;margin-bottom:20px;line-height:1.5}}
lw-label{{display:block;font-size:13px;font-weight:500;color:#6b6b6b;margin-bottom:6px}}
.lw-label{{display:block;font-size:13px;font-weight:500;color:#6b6b6b;margin-bottom:6px}}
.lw-input{{width:100%;padding:12px;border:1.5px solid #e8e6e0;border-radius:10px;font-size:1em;color:#1a1a1a;font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;outline:none}}
.lw-input::placeholder{{color:#a8a49b}}
.lw-input:focus{{border-color:#e8a000;box-shadow:0 0 0 3px rgba(232,160,0,0.12)}}
.lw-btn{{width:100%;padding:14px;background:#e8a000;border:none;border-radius:20px;color:white;font-size:1em;font-weight:700;cursor:pointer;margin-top:20px;font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;transition:transform .12s ease,box-shadow .12s ease;box-shadow:0 4px 14px rgba(232,160,0,.28);display:block;text-align:center}}
.lw-btn:hover{{transform:translateY(-1px);box-shadow:0 6px 18px rgba(232,160,0,.36)}}
.lw-btn:disabled{{background:#ddd;cursor:not-allowed;box-shadow:none;transform:none}}
.lw-btn-ghost{{width:100%;padding:10px;background:none;border:none;color:#6b6b6b;font-size:13px;cursor:pointer;margin-top:10px;font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;text-align:center}}
.lw-btn-ghost:hover{{color:#e8a000}}
.lw-error{{color:#dc2626;background:#fdeaea;border:1px solid #f3c2c2;border-radius:8px;padding:10px;margin-bottom:16px;font-size:0.85em;display:none}}
.lw-error.visible{{display:block}}
.lw-success{{color:#16a34a;background:#eaf7ef;border:1px solid #b5e2c5;border-radius:8px;padding:10px;margin-bottom:16px;font-size:0.85em}}
.otp-row{{display:flex;gap:10px;justify-content:center;margin:20px 0}}
.otp-input{{width:44px;height:54px;border:2px solid #e8e6e0;border-radius:10px;font-size:22px;font-weight:700;text-align:center;color:#1a1a1a;font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;outline:none;transition:border-color 150ms ease}}
.otp-input:focus{{border-color:#e8a000;box-shadow:0 0 0 3px rgba(232,160,0,0.12)}}
.otp-input.filled{{border-color:#e8a000}}
.resend{{text-align:center;font-size:13px;color:#6b6b6b;margin-top:14px}}
.resend a{{color:#e8a000;cursor:pointer;font-weight:600;text-decoration:none}}
.resend a.disabled{{color:#b8b4ac;cursor:default;pointer-events:none}}
@keyframes shake{{0%,100%{{transform:translateX(0)}}20%,60%{{transform:translateX(-6px)}}40%,80%{{transform:translateX(6px)}}}}
.shake{{animation:shake 0.4s ease}}
.success-icon{{text-align:center;font-size:3em;margin-bottom:12px}}
</style></head><body>
<div class="login-top">
  <div class="logo-wrap">{_LOGO_SVG}</div>
  <p>Light for your home network</p>
</div>
<div class="box" id="mainBox">

  <!-- Step A: Sign in -->
  <div class="step active" id="stepA">
    {err}
    <form id="loginForm">
    <span class="lw-label">Username</span>
    <input class="lw-input" type="text" id="loginUser" name="username" placeholder="Enter username" autofocus autocomplete="username">
    <span class="lw-label" style="display:block;margin-top:16px">Password</span>
    <input class="lw-input" type="password" id="loginPass" name="password" placeholder="Enter password" autocomplete="current-password">
    <button type="submit" class="lw-btn" id="loginBtn">Sign In</button>
    </form>
    <button class="lw-btn-ghost" id="forgotBtn">Forgot password?</button>
    <a href="/findhelp" style="display:block;text-align:center;margin-top:10px;color:#e8a000;font-weight:600;font-size:0.9em;text-decoration:none">Struggling with something? You're not alone — find help &rarr;</a>
    {first_run_hint}
  </div>

  <!-- Step B: Enter OTP -->
  <div class="step" id="stepB">
    <div class="step-title">Check your notifications</div>
    <div class="step-sub" id="stepBSub">A 6-digit code was sent to your notification channels.</div>
    <div class="lw-error" id="stepBErr"></div>
    <div class="otp-row" id="otpRow">
      <input class="otp-input" type="tel" maxlength="1" inputmode="numeric" pattern="[0-9]">
      <input class="otp-input" type="tel" maxlength="1" inputmode="numeric" pattern="[0-9]">
      <input class="otp-input" type="tel" maxlength="1" inputmode="numeric" pattern="[0-9]">
      <input class="otp-input" type="tel" maxlength="1" inputmode="numeric" pattern="[0-9]">
      <input class="otp-input" type="tel" maxlength="1" inputmode="numeric" pattern="[0-9]">
      <input class="otp-input" type="tel" maxlength="1" inputmode="numeric" pattern="[0-9]">
    </div>
    <button class="lw-btn" id="verifyBtn" disabled>Verify Code</button>
    <div class="resend">Didn't get it? <a id="resendLink" class="disabled">Resend (<span id="resendTimer">60</span>s)</a></div>
    <button class="lw-btn-ghost" id="backToLoginBtn">Back to sign in</button>
  </div>

  <!-- Step C: New password -->
  <div class="step" id="stepC">
    <div class="step-title">Create new password</div>
    <div class="step-sub">Choose a password at least 6 characters long.</div>
    <div class="lw-error" id="stepCErr"></div>
    <span class="lw-label">New password</span>
    <input class="lw-input" type="password" id="newPass" placeholder="New password" autocomplete="new-password">
    <span class="lw-label" style="display:block;margin-top:14px">Confirm password</span>
    <input class="lw-input" type="password" id="confirmPass" placeholder="Confirm new password" autocomplete="new-password">
    <button class="lw-btn" id="resetBtn" style="margin-top:20px">Update Password</button>
  </div>

  <!-- Step D: Success -->
  <div class="step" id="stepD">
    <div class="success-icon">&#x2705;</div>
    <div class="step-title" style="text-align:center">Password updated!</div>
    <div class="step-sub" style="text-align:center">Your new password has been saved. Sign in to continue.</div>
    <button class="lw-btn" id="goLoginBtn">Sign In</button>
  </div>

</div>
<script>
(function() {{
  var _token = "", _resetToken = "", _resendInterval = null;

  // ── Step A — sign in ──────────────────────────────────────────────────────
  document.getElementById('loginForm').addEventListener('submit', function(e) {{
    e.preventDefault();
    var u = document.getElementById('loginUser').value;
    var p = document.getElementById('loginPass').value;
    var form = document.createElement('form');
    form.method = 'POST'; form.action = '/login';
    var fu = document.createElement('input'); fu.type='hidden'; fu.name='username'; fu.value=u; form.appendChild(fu);
    var fp = document.createElement('input'); fp.type='hidden'; fp.name='password'; fp.value=p; form.appendChild(fp);
    document.body.appendChild(form); form.submit();
  }});

  document.getElementById('forgotBtn').addEventListener('click', startRecovery);

  function startRecovery() {{
    var btn = document.getElementById('forgotBtn');
    btn.disabled = true;
    btn.textContent = 'Sending…';
    fetch('/auth/forgot-password', {{method:'POST'}})
      .then(function(r){{return r.json();}})
      .then(function(d) {{
        if (d.ok) {{
          _token = d.token;
          var ch = d.channels && d.channels.length ? 'Sent via ' + d.channels.join(' & ') + '.' : 'Code sent.';
          document.getElementById('stepBSub').textContent = ch + ' Enter the 6-digit code below.';
          show('stepB');
          focusFirstOtp();
          startResendTimer();
        }} else {{
          showErr('stepAErr', d.error || 'Could not send code. Make sure ntfy, Telegram, or email is configured in Settings.');
          btn.disabled = false; btn.textContent = 'Forgot password?';
        }}
      }})
      .catch(function() {{
        showErr('stepAErr', 'Network error. Please try again.');
        btn.disabled = false; btn.textContent = 'Forgot password?';
      }});
  }}

  // ── Step B — OTP ──────────────────────────────────────────────────────────
  var otpInputs = Array.from(document.querySelectorAll('.otp-input'));

  otpInputs.forEach(function(inp, i) {{
    inp.addEventListener('input', function() {{
      this.value = this.value.replace(/[^0-9]/g, '').slice(-1);
      this.classList.toggle('filled', this.value !== '');
      if (this.value && i < 5) otpInputs[i+1].focus();
      updateVerifyBtn();
    }});
    inp.addEventListener('keydown', function(e) {{
      if (e.key === 'Backspace' && !this.value && i > 0) {{
        otpInputs[i-1].value = '';
        otpInputs[i-1].classList.remove('filled');
        otpInputs[i-1].focus();
        updateVerifyBtn();
      }}
    }});
    inp.addEventListener('paste', function(e) {{
      e.preventDefault();
      var text = (e.clipboardData || window.clipboardData).getData('text').replace(/\D/g,'').slice(0,6);
      text.split('').forEach(function(ch, j) {{
        if (otpInputs[i+j]) {{ otpInputs[i+j].value = ch; otpInputs[i+j].classList.add('filled'); }}
      }});
      var next = Math.min(i + text.length, 5);
      otpInputs[next].focus();
      updateVerifyBtn();
    }});
  }});

  function updateVerifyBtn() {{
    var full = otpInputs.every(function(x){{return x.value !== '';}});
    document.getElementById('verifyBtn').disabled = !full;
  }}

  function focusFirstOtp() {{
    otpInputs.forEach(function(x){{x.value='';x.classList.remove('filled');}});
    setTimeout(function(){{otpInputs[0].focus();}}, 150);
  }}

  document.getElementById('verifyBtn').addEventListener('click', function() {{
    var code = otpInputs.map(function(x){{return x.value;}}).join('');
    this.disabled = true; this.textContent = 'Checking…';
    var btn = this;
    fetch('/auth/verify-code', {{
      method:'POST',
      headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{token: _token, code: code}})
    }})
    .then(function(r){{return r.json();}})
    .then(function(d) {{
      if (d.ok) {{
        _resetToken = d.reset_token;
        show('stepC');
        setTimeout(function(){{document.getElementById('newPass').focus();}}, 150);
      }} else {{
        showErr('stepBErr', d.error || 'Incorrect code.');
        document.getElementById('otpRow').classList.add('shake');
        setTimeout(function(){{document.getElementById('otpRow').classList.remove('shake');}}, 500);
        btn.disabled = false; btn.textContent = 'Verify Code';
      }}
    }})
    .catch(function() {{
      showErr('stepBErr', 'Network error. Please try again.');
      btn.disabled = false; btn.textContent = 'Verify Code';
    }});
  }});

  function startResendTimer() {{
    var secs = 60;
    var el = document.getElementById('resendTimer');
    var link = document.getElementById('resendLink');
    link.className = 'disabled';
    if (_resendInterval) clearInterval(_resendInterval);
    _resendInterval = setInterval(function() {{
      secs--;
      if (el) el.textContent = secs;
      if (secs <= 0) {{
        clearInterval(_resendInterval);
        link.className = '';
        link.textContent = 'Resend code';
      }}
    }}, 1000);
  }}

  document.getElementById('resendLink').addEventListener('click', function(e) {{
    e.preventDefault();
    if (this.classList.contains('disabled')) return;
    focusFirstOtp();
    clearErr('stepBErr');
    fetch('/auth/forgot-password', {{method:'POST'}})
      .then(function(r){{return r.json();}})
      .then(function(d) {{
        if (d.ok) {{
          _token = d.token;
          startResendTimer();
        }} else {{
          showErr('stepBErr', d.error || 'Could not resend code.');
        }}
      }});
  }});

  document.getElementById('backToLoginBtn').addEventListener('click', function() {{
    if (_resendInterval) clearInterval(_resendInterval);
    clearErr('stepBErr');
    show('stepA');
  }});

  // ── Step C — new password ─────────────────────────────────────────────────
  document.getElementById('resetBtn').addEventListener('click', function() {{
    var pw  = document.getElementById('newPass').value;
    var pw2 = document.getElementById('confirmPass').value;
    clearErr('stepCErr');
    if (pw.length < 6) {{ showErr('stepCErr', 'Password must be at least 6 characters.'); return; }}
    if (pw !== pw2)    {{ showErr('stepCErr', 'Passwords do not match.'); return; }}
    var btn = this;
    btn.disabled = true; btn.textContent = 'Saving…';
    fetch('/auth/reset-password', {{
      method:'POST',
      headers:{{'Content-Type':'application/json'}},
      body: JSON.stringify({{reset_token: _resetToken, password: pw, confirm: pw2}})
    }})
    .then(function(r){{return r.json();}})
    .then(function(d) {{
      if (d.ok) {{
        show('stepD');
      }} else {{
        showErr('stepCErr', d.error || 'Could not update password.');
        btn.disabled = false; btn.textContent = 'Update Password';
      }}
    }})
    .catch(function() {{
      showErr('stepCErr', 'Network error. Please try again.');
      btn.disabled = false; btn.textContent = 'Update Password';
    }});
  }});

  // ── Step D — success ──────────────────────────────────────────────────────
  document.getElementById('goLoginBtn').addEventListener('click', function() {{
    document.getElementById('newPass').value = '';
    document.getElementById('confirmPass').value = '';
    show('stepA');
    document.getElementById('loginUser').focus();
  }});

  // ── Helpers ───────────────────────────────────────────────────────────────
  function show(id) {{
    document.querySelectorAll('.step').forEach(function(s){{s.classList.remove('active');}});
    document.getElementById(id).classList.add('active');
  }}

  function showErr(id, msg) {{
    var el = document.getElementById(id);
    if (el) {{ el.textContent = msg; el.classList.add('visible'); }}
  }}

  function clearErr(id) {{
    var el = document.getElementById(id);
    if (el) {{ el.textContent = ''; el.classList.remove('visible'); }}
  }}
}})();
</script>
</body></html>"""


# ── Device display name (shared) ──────────────────────────────────────────────

def _row_get(row, key, default=""):
    """dict.get() that also works on a sqlite3.Row.

    Device rows arrive as plain dicts from some queries and as sqlite3.Row from
    others, and sqlite3.Row has no .get() — calling it raises AttributeError and
    takes the whole page down."""
    try:
        v = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if v is None else v


_router_model_cache = {"model": None}


def router_model_name():
    """This router's own model, e.g. 'GL.iNet GL-MT3600BE'. Cached."""
    if _router_model_cache["model"] is None:
        m = ""
        try:
            with open("/tmp/sysinfo/model") as f:
                m = f.read().strip()
        except Exception:
            m = ""
        _router_model_cache["model"] = m or "Router"
    return _router_model_cache["model"]


def device_display_name(name, config, ip_hostnames=None, client_ip="", ident=None):
    """The friendly name shown for a device — used by EVERY page.

    This lives in one place on purpose: the dashboard, Devices list, query log and
    device detail pages each used to work this out themselves, so the same device
    could read "Dell device" on one page and "192.168.8.230" on another.

    Priority: the name you saved > a resolved hostname > this router's model (for
    the router's own lookups) > the hardware maker from the MAC/OUI > the bare IP.
    """
    import re as _re
    _is_ip = _re.compile(r"^\d{1,3}(\.\d{1,3}){3}$").match

    saved     = (config.get("devices", {}).get(name, {}) or {}).get("label", "")
    has_saved = bool(saved) and saved != name

    # The router's own DNS lookups report as "localhost" / 127.0.0.1.
    if not has_saved and (name == "localhost" or client_ip in ("127.0.0.1", "::1")):
        return _demo(name, router_model_name(), config)

    if has_saved or not _is_ip(name):
        return _demo(name, label(name, config), config)

    # Bare IP — try a hostname, then the hardware maker, then give up and show the IP.
    if ident is None:
        try:
            from classify import device_identity
            ident = device_identity(name)
        except Exception:
            ident = {}
    raw = (ip_hostnames or {}).get(name, "") or (ident or {}).get("hostname", "")
    if raw:
        return _demo(name, pretty_hostname(raw), config)
    vendor = (ident or {}).get("vendor", "")
    if vendor:
        return _demo(name, f"{_short_vendor(vendor)} device", config)
    return _demo(name, name, config)


# ── Component builders ────────────────────────────────────────────────────────

def make_card(d, screen_times, max_queries, config, ip_hostnames=None):
    import re
    _is_ip   = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$").match
    name     = d["client_name"]
    friendly = device_display_name(name, config, ip_hostnames,
                                   client_ip=_row_get(d, "client_ip"))
    # Keep the raw IP underneath when the label isn't the IP itself.
    ip_sub   = (f'<div style="font-size:0.75em;color:#94a3b8;margin-top:-2px">{name}</div>'
                if _is_ip(name) and friendly != name else "")
    pct      = round((d["blocked"] / d["total"] * 100) if d["total"] > 0 else 0, 1)
    bar_pct  = round(d["total"] / max_queries * 100)
    danger   = "danger" if pct > 30 else ""

    if pct > 30:   badge = f'<span class="badge badge-red">{pct}% blocked</span>'
    elif pct > 10: badge = f'<span class="badge badge-yellow">{pct}% blocked</span>'
    else:          badge = f'<span class="badge badge-green">{pct}% blocked</span>'

    last       = _local_ts(d["last_seen"])[:16] if d["last_seen"] else "unknown"
    secs       = screen_times.get(name, 0)
    time_str   = fmt_time(secs) if secs > 0 else "-"
    enc        = quote(name)
    client_ip  = d["client_ip"] if "client_ip" in d.keys() else ""
    paused     = get_paused_devices(config)
    is_paused  = client_ip in paused
    schedules  = config.get("schedules", {})
    sched      = schedules.get(client_ip, {})
    enc_ip     = quote(client_ip) if client_ip else ""

    # Schedule info strip
    if sched.get("enabled") and is_paused and paused.get(client_ip, {}).get("scheduled"):
        wake_h, wake_m = map(int, sched["wake"].split(":"))
        wake_ampm = "AM" if wake_h < 12 else "PM"
        wake_12   = max(1, wake_h if wake_h <= 12 else wake_h - 12)
        schedule_info = (f'<div style="font-size:0.75em;color:#D97706;padding:6px 0 2px;font-weight:600">'
                         f'&#x1F319; Resting until {wake_12}:{wake_m:02d} {wake_ampm}</div>')
    elif sched.get("enabled"):
        bed_h, bed_m  = map(int, sched["bedtime"].split(":"))
        bed_ampm = "PM" if bed_h >= 12 else "AM"
        bed_12   = max(1, bed_h if bed_h <= 12 else bed_h - 12)
        schedule_info = (f'<div style="font-size:0.75em;color:#94a3b8;padding:6px 0 2px">'
                         f'&#x23F0; Rest starts at {bed_12}:{bed_m:02d} {bed_ampm}</div>')
    else:
        schedule_info = ""

    # Pause button
    if is_paused:
        pause_btn  = (f'<a href="/device/unpause?ip={quote(client_ip)}&name={enc}">'
                      f'<span style="background:#FEF3C7;color:#D97706;padding:3px 10px;border-radius:99px;'
                      f'font-size:0.75em;font-weight:700;border:1px solid #F4B942">'
                      f'&#x23F5; Paused - Tap to Resume</span></a>')
        card_style = "background:#FFFBF0;border-color:#F4B942"
    else:
        pause_btn  = (f'<a href="/device/pause?ip={quote(client_ip)}&name={enc}">'
                      f'<span style="background:#FEE2E2;color:#DC6B5F;padding:3px 10px;border-radius:99px;'
                      f'font-size:0.75em;font-weight:700;border:1px solid #FCA5A5">'
                      f'&#x23F8; Pause</span></a>')
        card_style = ""

    return (
        f'<div class="device-card" style="{card_style}">'
        f'<div class="device-header"><a href="/device?name={enc}&ip={enc_ip}" style="flex:1">'
        f'<div class="device-name">{friendly}</div>{ip_sub}'
        f'</a>{badge}</div>'
        f'<div class="bar-wrap"><div class="bar-fill {danger}" style="width:{bar_pct}%"></div></div>'
        f'<div class="device-stats">'
        f'<div class="device-stat">Queries: <span>{d["total"]:,}</span></div>'
        f'<div class="device-stat">Blocked: <span>{d["blocked"]:,}</span></div>'
        f'<div class="device-stat">Time online: <span style="color:#F4B942">{time_str}</span></div>'
        f'<div class="device-stat">Last seen: <span>{last}</span></div>'
        f'</div>'
        f'<div style="display:flex;justify-content:space-between;align-items:center;margin-top:8px">'
        f'<a href="/device?name={enc}&ip={enc_ip}" style="font-size:0.7em;color:#D97706;font-weight:600">Tap to see details</a> '
        f'<a href="/device/schedule?name={enc}&ip={enc_ip}" style="font-size:0.7em;color:#94a3b8;margin-left:8px">&#x23F0; Schedule</a>'
        f'{pause_btn}'
        f'</div>'
        f'{schedule_info}'
        f'</div>'
    )


def make_blocked_link(r, config):
    enc = quote(r["domain"])
    return (f'<a href="/domain?name={enc}"><div class="domain-item" style="cursor:pointer">'
            f'<span class="domain-name">{r["domain"]}</span>'
            f'<span class="domain-count red">{r["hits"]:,} blocked</span></div></a>')


def make_adult_link(r):
    enc  = quote(r["domain"])
    last = _local_ts(r["last_seen"])[:16] if r["last_seen"] else "-"
    return (f'<a href="/domain?name={enc}"><div class="domain-item" style="cursor:pointer;background:#FFF7F7">'
            f'<div><div style="color:#DC6B5F;font-weight:600">{r["domain"]}</div>'
            f'<div style="font-size:0.75em;color:#94a3b8">Last: {last}</div></div>'
            f'<span class="domain-count red">{r["hits"]:,} attempts</span></div></a>')


# ── Full page builders ────────────────────────────────────────────────────────

def build_safety_score_card(s, stats_html=""):
    """Home page: a calm, reassuring protection status that wraps the at-a-glance
    stat cards in one panel — no jargon and no flags a non-technical parent can't
    act on. The detailed technical checklist lives in Settings."""
    stats_block = f'<div style="margin-top:14px">{stats_html}</div>' if stats_html else ""

    if s["protected"]:
        head = (
            '<div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap">'
            '<div style="font-size:2.4em">&#x1F6E1;&#xFE0F;</div>'
            '<div style="flex:1;min-width:220px">'
            '<div style="font-weight:800;color:#15803d;font-size:1.3em">Your family is protected</div>'
            '</div></div>'
        )
        return ('<div class="section"><div class="form-card" style="background:#f0fdf4;border:1px solid #bbf7d0">'
                + head + stats_block + '</div></div>')

    head = (
        '<div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap">'
        '<div style="font-size:2.4em">&#x1F3EE;</div>'
        '<div style="flex:1;min-width:200px">'
        '<div style="font-weight:800;color:#b45309;font-size:1.15em">Let\'s finish protecting your family</div>'
        '<div style="color:#475569;font-size:0.9em;margin-top:2px">Your core filtering isn\'t fully on yet — it only takes a minute.</div>'
        '</div>'
        '<a href="/setup/adguard" class="btn" style="width:auto;white-space:nowrap;margin-bottom:0">Finish setup &rarr;</a>'
        '</div>'
    )
    return ('<div class="section"><div class="form-card" style="background:#fffbf0;border:1px solid #f3e3b8">'
            + head + stats_block + '</div></div>')


def build_security_checklist_card(s):
    """Settings page: the detailed, technical security checklist for power users
    — core protections (scored) plus optional hardening. Demoted off the home
    page so non-technical parents only see reassurance."""
    score = s["score"]
    color = "#1d9e75" if score >= 90 else ("#e8a000" if score >= 60 else "#e24b4a")

    def _row(c):
        if c["ok"]:
            return (f'<div style="display:flex;align-items:center;gap:8px;padding:5px 0;font-size:0.9em">'
                    f'<span style="color:#1d9e75;font-weight:800">&#x2713;</span>'
                    f'<span style="color:#475569">{c["label"]}</span></div>')
        detail = f' <span style="color:#94a3b8">&mdash; {c.get("detail","")}</span>' if c.get("detail") else ""
        return (f'<div style="display:flex;align-items:center;gap:8px;padding:5px 0;font-size:0.9em">'
                f'<span style="color:#e8a000;font-weight:800">&#x25CB;</span>'
                f'<span style="color:#475569;flex:1">{c["label"]}{detail}</span>'
                f'<a href="{c["fix_url"]}" style="color:#e8a000;font-weight:700;font-size:0.82em;white-space:nowrap">{c["fix_label"]} &rarr;</a></div>')

    core_rows = "".join(_row(c) for c in s["core"])
    adv_rows  = "".join(_row(c) for c in s["advanced"])
    return (
        '<div class="section"><h2>Security checklist</h2>'
        '<div class="form-card">'
        '<div style="display:flex;align-items:center;gap:16px;margin-bottom:12px">'
        f'<div style="font-size:2.2em;font-weight:800;line-height:1;color:{color}">{score}</div>'
        f'<div><div style="font-weight:700;color:var(--ink)">{s["level"]}</div>'
        '<div style="font-size:0.78em;color:#94a3b8">Core protection score</div></div></div>'
        f'{core_rows}'
        '<div style="margin-top:10px;padding-top:8px;border-top:1px solid var(--line);'
        'font-size:0.72em;color:#94a3b8;text-transform:uppercase;letter-spacing:.06em;font-weight:700">Optional hardening</div>'
        f'{adv_rows}'
        '</div></div>'
    )


def _update_banner_html(config):
    """A subtle 'update available' banner for the top of the dashboard, shown when
    the last-seen GitHub version is newer than the running one. Links to Settings."""
    try:
        from config import VERSION, is_newer_version
        latest = config.get("latest_known_version", "")
        if not (latest and is_newer_version(latest, VERSION)):
            return ""
    except Exception:
        return ""
    return (
        '<a href="/admin?update=1" style="display:flex;align-items:center;gap:10px;text-decoration:none;'
        'margin-bottom:14px;padding:12px 16px;background:#fffbf0;border:1px solid #e8d080;'
        'border-radius:12px">'
        '<span style="font-size:1.2em">&#x1F514;</span>'
        '<span style="flex:1;font-size:0.85em;color:#b7791f;font-weight:600">'
        f'Lantern Watch {latest} is available &mdash; your device names &amp; settings are kept.</span>'
        '<span style="background:#e8a000;color:#fff;padding:6px 14px;border-radius:20px;'
        'font-size:0.8em;font-weight:700;white-space:nowrap">Update</span>'
        '</a>'
    )


def build_main(devices, totals, top_blocked, top_domains, screen_times, adult_domains, config):
    total_q    = totals["total"] or 0
    total_b    = totals["blocked"] or 0
    block_pct  = round((total_b / total_q * 100) if total_q > 0 else 0, 1)
    people     = [d for d in devices if not is_infrastructure(d["client_name"], config) and is_monitored(d["client_name"], config)]
    infra      = [d for d in devices if is_infrastructure(d["client_name"], config)]
    max_q      = max((d["total"] for d in devices), default=1)

    try:
        ip_hostnames = get_ip_hostname_map(config)
    except Exception:
        ip_hostnames = {}

    people_cards  = "".join(make_card(d, screen_times, max_q, config, ip_hostnames) for d in people)
    infra_cards   = "".join(make_card(d, screen_times, max_q, config, ip_hostnames) for d in infra)
    blocked_html  = "".join(make_blocked_link(r, config) for r in top_blocked) or '<div class="domain-item"><span class="domain-name">None today</span></div>'
    allowed_html  = "".join(
        f'<div class="domain-item"><span class="domain-name">{r["domain"]}</span><span class="domain-count">{r["hits"]:,}</span></div>'
        for r in top_domains
    ) or '<div class="domain-item"><span class="domain-name">No data</span></div>'

    # Blocked Content section — the sites you actually block (adult, blocked
    # services, custom blocks, category packs), not the ambient ad/tracker noise.
    from db import get_notable_blocks
    try:
        from adguard import (get_custom_blocks, get_blocked_pack_domains,
                             service_category_for_domain, service_notify_enabled,
                             filter_id_category_map)
        _explicit = set(get_custom_blocks(config)) | set(get_blocked_pack_domains(config))
        _svc_ok = lambda d: service_notify_enabled(service_category_for_domain(d, config), config)
        _fid_cats = filter_id_category_map(config)
        _fam_ok = lambda fids: any(_fid_cats.get(f) == "Family & Content" for f in fids)
    except Exception:
        _explicit, _svc_ok, _fam_ok = set(), None, None
    notable = get_notable_blocks(_explicit, is_notable_service=_svc_ok, is_family_list=_fam_ok)
    if notable:
        adult_rows    = "".join(make_adult_link(r) for r in notable)
        adult_section = ('<div class="section"><h2 class="alert">Blocked Content</h2>'
                         '<div class="alert-box-red">Sites you block that were attempted today. Tap any to see who and when.</div>'
                         f'<div class="domain-list" style="margin-top:10px">{adult_rows}</div></div>')
    else:
        adult_section = ('<div class="section"><h2 class="alert">Blocked Content</h2>'
                         '<div class="alert-box-green">No attempts on your blocked sites today &mdash; ads &amp; trackers are still being filtered quietly in the background.</div></div>')

    # AdGuard stats
    ag     = get_adguard_stats(config)
    ag_html = ""
    if ag:
        bp        = round((ag["blocked_filtering"] / ag["dns_queries"] * 100) if ag["dns_queries"] > 0 else 0, 2)
        since_lbl = get_oldest_log_date()
        ag_html = (
            f'<div class="section"><h2>Network Stats (since {since_lbl})</h2><div class="netstats">'
            f'<div class="netstat-item"><span class="domain-name">DNS Queries</span><span class="domain-count">{ag["dns_queries"]:,}</span></div>'
            f'<div class="netstat-item"><span class="domain-name">Blocked by Filters</span><span class="domain-count red">{ag["blocked_filtering"]:,} ({bp}%)</span></div>'
            f'<div class="netstat-item"><span class="domain-name">Blocked Malware</span><span class="domain-count red">{ag["blocked_malware"]:,}</span></div>'
            f'<div class="netstat-item"><span class="domain-name">Blocked Adult</span><span class="domain-count red">{ag["blocked_adult"]:,}</span></div>'
            f'<div class="netstat-item"><span class="domain-name">Safe Search Enforced</span><span class="domain-count" style="color:#e8a000">{ag["blocked_safesearch"]:,}</span></div>'
            f'<div class="netstat-item"><span class="domain-name">Avg Processing Time</span><span class="domain-count" style="color:#1d9e75">{ag["avg_processing_time"]} ms</span></div>'
            '</div></div>'
        )

    # Total blocked across every category AdGuard reports. Most blocks land in
    # "filtering" (blocklists + custom rules + packs); parental/malware stay ~0
    # on a well-listed setup because the blocklists catch those domains first as
    # FilteredBlackList. So the headline "Blocked" number must sum them, not read
    # the (near-always-zero) parental counter alone.
    ag_blocked = (ag.get("blocked_filtering", 0)
                  + ag.get("blocked_malware", 0)
                  + ag.get("blocked_adult", 0)) if ag else 0
    bw_mb    = ag_blocked * 25 / 1024  # ~25 KB saved per blocked request
    bandwidth_str = f"{bw_mb / 1024:.1f}GB" if bw_mb > 1024 else f"{round(bw_mb)}MB"


    paused       = config.get("paused_devices", {})
    pauseable    = [d for d in people if is_pauseable(d["client_name"], config)]
    kids_paused  = sum(1 for d in pauseable if d["client_ip"] in paused)
    kids_total   = len(pauseable)
    pause_label  = f"Pause All Personal ({kids_total - kids_paused} online)" if kids_total - kids_paused else "Pause All Personal"
    resume_label = f"Resume All Personal ({kids_paused} paused)" if kids_paused else "Resume All Personal"
    pause_dim    = "" if kids_total - kids_paused else "opacity:0.4;pointer-events:none;"
    resume_dim   = "" if kids_paused             else "opacity:0.4;pointer-events:none;"
    pause_bar    = (
        f'<div style="display:flex;gap:10px;margin-bottom:16px">'
        f'<a href="/pause_all" class="btn btn-danger" style="flex:1;text-align:center;{pause_dim}">&#x1F507; {pause_label}</a>'
        f'<a href="/unpause_all" class="btn btn-secondary" style="flex:1;text-align:center;{resume_dim}">&#x25B6; {resume_label}</a>'
        f'</div>'
    )

    stats_bar = (
        f'<div class="stats-bar">'
        f'<div class="stat-card"><div class="num blue">{total_q:,}</div><div class="label">Queries Today</div></div>'
        f'<div class="stat-card"><div class="num green">{block_pct}%</div><div class="label">Block Rate</div></div>'
        f'<div class="stat-card"><div class="num blue">{len(people)}</div><div class="label">Devices</div></div>'
        f'<div class="stat-card"><div class="num red">{ag_blocked:,}</div><div class="label">Blocked</div>'
        f'<div style="font-size:0.65em;color:#94a3b8;margin-top:2px">since {get_oldest_log_date()}</div></div>'
        f'<div class="stat-card"><div class="num green">{bandwidth_str}</div><div class="label">Data Saved</div>'
        f'<div style="font-size:0.7em;color:#1d9e75;margin-top:2px">~{ag_blocked:,} requests</div></div>'
        f'</div>'
    )
    body = (
        stats_bar
        + adult_section + ag_html
        + f'<div class="section"><h2>Devices (Last 24h)</h2>{pause_bar}{people_cards}</div>'
        + f'<div class="section infra-section"><h2>Infrastructure</h2>{infra_cards}</div>'
        + f'<div class="section"><h2>Top Blocked Domains</h2><div class="domain-list">{blocked_html}</div></div>'
        + f'<div class="section"><h2>Top Allowed Domains</h2><div class="domain-list">{allowed_html}</div></div>'
        + f'<div class="refresh">Auto-refreshes every 60s — {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</div>'
    )

    return (
        f'<!DOCTYPE html><html><head><link rel="icon" type="image/svg+xml" href="/favicon.svg">'
        f'<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">'
        f'<title>Lantern Watch</title><style>{CSS}</style>'
        f'<meta http-equiv="refresh" content="60"></head><body>'
        + build_header(config=config)
        + '<div class="page-wrap">' + _update_banner_html(config) + body + '</div>'
        + '<script>if(!sessionStorage.getItem("lw_uc")){sessionStorage.setItem("lw_uc","1");fetch("/admin/check-update").catch(function(){});}</script>'
        + '</body></html>'
    )


def build_detail(client_name, config, client_ip_param=""):
    totals, sites, blocked_sites, hourly, secs, all_time, peak_hour, top_category, ip_address, hostname = get_device_detail(client_name)
    friendly    = device_display_name(client_name, config)

    # Full device identity (demo mode shows stable fakes so screenshots are safe).
    from classify import device_identity, device_kind
    _ident = device_identity(client_name)
    _disp  = demo_ident(client_name, _ident, client_ip_param or ip_address or "", config)
    _kind  = device_kind(client_name, "" if config.get("demo_mode") else label(client_name, config), _ident, None)
    _typ   = effective_type(client_name, config)
    _TYPE_NAMES = {"person": "Personal", "parent": "Admin",
                   "work_device": "Work Device", "infrastructure": "Infrastructure",
                   "smart_device": "Smart Device"}
    _info_rows = []
    def _info(lbl, val):
        if val:
            _info_rows.append(f'<div class="detail-stat">{lbl}: <span>{val}</span></div>')
    _info("IP address",   _disp["ip"])
    _info("MAC address",  _disp["mac"])
    _info("Manufacturer", _disp["vendor"])
    if _disp["hostname"] and _disp["hostname"] != friendly:
        _info("Hostname",  _disp["hostname"])
    _info("Role",         _TYPE_NAMES.get(_typ, _typ))
    if _kind:
        _info("Best guess", f'probably {"an" if _kind[:1].lower() in "aeiou" else "a"} {_kind}')
    # "Talks to" — the device's top domains (same 7-day source as the cards;
    # demo mode shows stable fakes).
    try:
        _talks = get_top_domains_map(per_device=3).get(client_name, [])[:3]
    except Exception:
        _talks = []
    if config.get("demo_mode"):
        _talks = [_demo_domain(x) for x in _talks]
    talks_row = (f'<div style="font-size:0.82em;color:#64748b;margin-top:10px;word-break:break-all">'
                 f'&#x1F4AC; talks to: {", ".join(_talks)}</div>') if _talks else ""
    device_info_html = (
        '<div class="section"><h2>Device Info</h2><div class="form-card">'
        '<div class="detail-stats" style="flex-wrap:wrap;gap:14px">'
        + "".join(_info_rows) + '</div>' + talks_row + '</div></div>'
    ) if (_info_rows or _talks) else ""
    total       = totals["total"] or 0
    blocked     = totals["blocked"] or 0
    pct         = round((blocked / total * 100) if total > 0 else 0, 1)
    all_total   = (all_time["total"]   if all_time and all_time["total"]   is not None else 0)
    all_blocked = (all_time["blocked"] if all_time and all_time["blocked"] is not None else 0)
    first_seen  = _local_ts(all_time["first_seen"])[:16] if all_time and all_time["first_seen"] else "Unknown"
    last_seen_all = _local_ts(all_time["last_seen"])[:16] if all_time and all_time["last_seen"] else "Unknown"

    # Peak hour label
    peak_str = ""
    if peak_hour and peak_hour["hour"]:
        h    = int(peak_hour["hour"])
        ampm = "AM" if h < 12 else "PM"
        h12  = max(1, h if h <= 12 else h - 12)
        peak_str = f"{h12}:00 {ampm}"

    # Top blocked category label
    cat_str = ""
    if top_category and top_category["reason"]:
        r = top_category["reason"]
        if "Parental" in r:     cat_str = "Content Filter"
        elif "SafeBrowsing" in r: cat_str = "Malware"
        elif "SafeSearch" in r:   cat_str = "Safe Search"
        else:                      cat_str = "Ads/Trackers"

    # Hourly activity bar chart
    hour_data  = {r["hour"]: r["hits"] for r in hourly}
    max_hits   = max(hour_data.values()) if hour_data else 1
    hour_bars  = ""
    hour_labels_html = ""
    for h in range(24):
        hstr   = f"{h:02d}"
        hits   = hour_data.get(hstr, 0)
        height = max(2, int(hits / max_hits * 44))
        hour_bars        += f'<div class="hour-bar" style="height:{height}px" title="{hstr}:00 — {hits}"></div>'
        hour_labels_html += f'<div class="hour-label">{h if h % 4 == 0 else ""}</div>'

    sites_html  = "".join(
        f'<div class="domain-item"><span class="domain-name">{r["domain"]}</span><span class="domain-count">{r["hits"]:,}</span></div>'
        for r in sites
    ) or '<div class="domain-item"><span class="domain-name">No data</span></div>'
    blocked_html = "".join(make_blocked_link(r, config) for r in blocked_sites) or '<div class="domain-item"><span class="domain-name">Nothing blocked</span></div>'

    peak_tag = f'<span class="tag tag-gold">Peak: {peak_str}</span>' if peak_str else ""
    cat_tag  = f'<span class="tag tag-red">Top: {cat_str}</span>' if cat_str else ""
    eff_ip   = client_ip_param or ip_address

    paused_devs = config.get("paused_devices", {})
    is_paused   = eff_ip in paused_devs or any(v.get("name", "") == friendly for v in paused_devs.values())
    pause_key   = (client_ip_param or eff_ip) if (client_ip_param or eff_ip) in paused_devs else \
                  next((k for k, v in paused_devs.items() if v.get("name", "") == friendly), client_ip_param or eff_ip)

    pause_btn = (
        f'<a href="/device/unpause?ip={quote(pause_key)}&name={quote(hostname)}&ref=device">'
        f'<span style="background:#FEF3C7;color:#D97706;padding:6px 14px;border-radius:99px;font-size:0.82em;font-weight:700;border:1px solid #F4B942">'
        f'&#x23F5; Paused - Tap to Resume</span></a>'
        if is_paused else
        f'<a href="/device/pause?ip={quote(pause_key)}&name={quote(hostname)}&ref=device">'
        f'<span style="background:#FEE2E2;color:#DC6B5F;padding:6px 14px;border-radius:99px;font-size:0.82em;font-weight:700;border:1px solid #FCA5A5">'
        f'&#x23F8; Pause</span></a>'
    )

    return (
        f'<!DOCTYPE html><html><head><link rel="icon" type="image/svg+xml" href="/favicon.svg"><meta charset="UTF-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1.0">'
        f'<title>{friendly} - Lantern Watch</title><style>{CSS}</style></head><body>'
        + build_header(friendly, config=config)
        + '<div class="page-wrap">'
        +''
        + f'<div class="detail-header">'
        + f'<div class="detail-name">{friendly}</div>'
        + f'<div style="margin-top:6px;margin-bottom:10px">{peak_tag}{cat_tag}</div>'
        + f'<div class="detail-stats">'
        + f'<div class="detail-stat">Queries today: <span>{total:,}</span></div>'
        + f'<div class="detail-stat">Blocked today: <span style="color:#DC6B5F">{blocked:,}</span></div>'
        + f'<div class="detail-stat">Time online: <span style="color:#F4B942">{fmt_time(secs)}</span></div>'
        + f'<div class="detail-stat">Block rate: <span>{pct}%</span></div>'
        + f'</div>'
        + f'<div style="border-top:1px solid #F1F5F9;margin-top:12px;padding-top:12px;display:flex;gap:16px;flex-wrap:wrap">'
        + f'<div class="detail-stat">All-time: <span>{all_total:,}</span></div>'
        + f'<div class="detail-stat">All-time blocked: <span style="color:#DC6B5F">{all_blocked:,}</span></div>'
        + f'<div class="detail-stat">First seen: <span>{first_seen}</span></div>'
        + f'<div class="detail-stat">Last active: <span>{last_seen_all}</span></div>'
        + f'</div>'
        + f'<div style="border-top:1px solid #F1F5F9;margin-top:12px;padding-top:12px;display:flex;gap:12px;align-items:center;flex-wrap:wrap">'
        + f"<a href='/device/schedule?name={quote(hostname)}&ip={quote(pause_key)}' "
          f"style='background:#F8FAFC;border:1px solid #E2E8F0;border-radius:99px;padding:6px 14px;"
          f"font-size:0.82em;font-weight:700;color:#64748b;text-decoration:none'>&#x23F0; Schedule</a>"
        + pause_btn
        + f'</div></div>'
        + device_info_html
        + f'<div class="section"><h2>Activity by Hour</h2>'
        + f'<div class="domain-list" style="padding:12px 16px">'
        + f'<div class="hour-chart">{hour_bars}</div>'
        + f'<div class="hour-labels">{hour_labels_html}</div></div></div>'
        + f'<div class="section"><h2>Sites Visited</h2><div class="domain-list">{sites_html}</div></div>'
        + f'<div class="section"><h2>Blocked Attempts</h2><div class="domain-list">{blocked_html}</div></div>'
        + '</div></body></html>'
    )


def build_domain_detail(domain, config):
    entries, summary, total = get_domain_detail(domain)
    is_adult   = any(r["reason"] == "FilteredParental" for r in entries)
    is_malware = any(r["reason"] == "FilteredSafeBrowsing" for r in entries)

    if is_adult:
        category    = "Content Filter"
        explanation = "This website was blocked by your family content filter."
        cat_badge   = '<span style="background:#DC6B5F;color:white;padding:3px 12px;border-radius:99px;font-size:0.8em;font-weight:700">BLOCKED</span>'
    elif is_malware:
        category    = "Malware / Phishing"
        explanation = "This website has been flagged as dangerous."
        cat_badge   = '<span style="background:#DC6B5F;color:white;padding:3px 12px;border-radius:99px;font-size:0.8em;font-weight:700">DANGEROUS</span>'
    else:
        category    = "Blocked by Filter"
        explanation = "This website was blocked by your active filter lists."
        cat_badge   = '<span style="background:#92400e;color:#fbbf24;padding:3px 12px;border-radius:99px;font-size:0.8em;font-weight:700">FILTERED</span>'

    first_seen = _local_ts(total["first"])[:16] if total["first"] else "-"
    last_seen  = _local_ts(total["last"])[:16]  if total["last"]  else "-"

    who_html = "".join(
        f'<div class="domain-item"><div>'
        f'<div style="color:#1e293b;font-weight:600">{device_display_name(r["client_name"], config)}</div>'
        f'<div style="color:#94a3b8;font-size:0.75em">Last: {_local_ts(r["last_seen"])[:16] if r["last_seen"] else "-"}</div></div>'
        f'<span class="domain-count red">{r["attempts"]:,} times</span></div>'
        for r in summary
    ) or '<div class="domain-item"><span class="domain-name">No data</span></div>'

    timeline_html = ""
    for r in entries[:25]:
        ts       = _local_ts(r["ts"])[:16] if r["ts"] else "-"
        friendly = device_display_name(r["client_name"], config)
        reason   = r["reason"] or "Blocked"
        if "Parental" in reason:      ls = "Content Filter"
        elif "SafeBrowsing" in reason: ls = "Malware"
        elif "SafeSearch" in reason:   ls = "Safe Search"
        else:                          ls = "Ad/Tracker"
        elapsed = f'{float(r["elapsed_ms"]):.0f}ms' if r["elapsed_ms"] else "-"
        timeline_html += (
            f'<div class="domain-item" style="flex-direction:column;align-items:flex-start;gap:4px">'
            f'<div style="display:flex;justify-content:space-between;width:100%">'
            f'<span style="font-weight:600">{friendly}</span>'
            f'<span style="color:#94a3b8;font-size:0.75em">{ts}</span></div>'
            f'<div><span class="badge badge-red">{ls}</span> '
            f'<span style="color:#94a3b8;font-size:0.75em">{elapsed}</span></div></div>'
        )

    return (
        f'<!DOCTYPE html><html><head><link rel="icon" type="image/svg+xml" href="/favicon.svg"><meta charset="UTF-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1.0">'
        f'<title>{domain} - Lantern Watch</title><style>{CSS}</style></head><body>'
        + build_header("Blocked Site Details", config=config)
        + '<div class="page-wrap">'
        +''
        + f'<div style="margin:0 16px 12px;padding:16px;background:#FFF7F7;border-radius:12px;border:2px solid #FCA5A5">'
        + f'<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px">'
        + f'<div style="font-size:1.1em;font-weight:700;color:#DC6B5F;word-break:break-all">{domain}</div>{cat_badge}</div>'
        + f'<div style="color:#DC6B5F;font-size:0.85em;margin-bottom:12px">{explanation}</div>'
        + f'<div style="display:flex;gap:16px;flex-wrap:wrap">'
        + f'<div style="font-size:0.8em;color:#64748b">Category: <b style="color:#DC6B5F">{category}</b></div>'
        + f'<div style="font-size:0.8em;color:#64748b">Attempts: <b style="color:#DC6B5F">{total["cnt"]:,}</b></div>'
        + f'<div style="font-size:0.8em;color:#64748b">First: <b>{first_seen}</b></div>'
        + f'<div style="font-size:0.8em;color:#64748b">Last: <b>{last_seen}</b></div>'
        + f'</div>'
        + f'<form method="POST" action="/domain/clear" style="margin-top:14px" '
        + f'onsubmit="return confirm(\'Remove all log entries for {domain}? This clears its counts everywhere but keeps the rest of your history.\')">'
        + f'<input type="hidden" name="domain" value="{domain}">'
        + f'<button type="submit" class="btn btn-secondary" style="font-size:0.8em;padding:7px 14px">&#x1F5D1;&#xFE0F; Clear this domain from the log</button>'
        + f'</form>'
        + f'</div>'
        + f'<div class="section"><h2>Who Tried to Access This</h2><div class="domain-list">{who_html}</div></div>'
        + f'<div class="section"><h2>Recent Attempts</h2><div class="domain-list">'
        + (timeline_html or '<div class="domain-item"><span class="domain-name">No data</span></div>')
        + '</div></div></div></body></html>'
    )


def build_schedule_page(client_name, client_ip, config):
    friendly    = device_display_name(client_name, config)
    schedules   = config.get("schedules", {})
    sched       = schedules.get(client_ip, {})
    enabled     = "checked" if sched.get("enabled") else ""
    bedtime     = sched.get("bedtime", "21:00")
    wake        = sched.get("wake", "06:00")
    focus_times = sched.get("focus_times", [{}, {}, {}])
    while len(focus_times) < 3:
        focus_times.append({})
    enc = quote(client_name)

    st         = sched.get("screen_time", {})
    st_enabled = "checked" if st.get("enabled") else ""
    st_hours   = st.get("hours", 2)
    st_reset   = st.get("reset", "00:00")
    reset_opts = ""
    for h in range(9):
        hstr      = f"{h:02d}:00"
        label_str = "Midnight" if h == 0 else f"{h}:00 AM"
        sel       = "selected" if st_reset == hstr else ""
        reset_opts += f'<option value="{hstr}" {sel}>{label_str}</option>'

    def focus_block(i, color="#6366f1", bg="#F0F0FF", border="#E0E0FF"):
        ft         = focus_times[i]
        ft_enabled = "checked" if ft.get("enabled", False) else ""
        lbl        = ft.get("label", "")
        start      = ft.get("start", "08:00")
        end        = ft.get("end", "09:00")
        return (
            f'<div class="form-card" style="margin-top:12px;border-color:{border}">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">'
            f'<div style="font-weight:800;color:{color};font-size:1em">&#x1F4DA; Focus Time {i+1}</div>'
            f'<label style="display:flex;align-items:center;gap:6px;font-size:0.85em;color:#64748b">'
            f'<input type="checkbox" name="focus_enabled_{i}" {ft_enabled}> Enable</label>'
            f'</div>'
            f'<input type="text" name="focus_label_{i}" value="{lbl}" '
            f'placeholder="Label (e.g. School, Chores, Morning Routine)" style="margin-bottom:12px;border-color:{border}">'
            f'<div style="background:{bg};border-radius:12px;padding:16px;border:1px solid {border}">'
            f'<div style="display:flex;align-items:flex-end;gap:12px;flex-wrap:wrap">'
            f'<div style="flex:1;min-width:120px"><div class="form-label">Start Time</div>'
            f'<input type="time" name="focus_start_{i}" value="{start}" style="font-size:1.1em;padding:12px;border-color:{color};background:white"></div>'
            f'<div style="flex:2;text-align:center;padding-bottom:10px;color:{color};font-size:0.85em;font-weight:600;min-width:100px">'
            f'&#x1F4DA; Internet paused<br>during focus time</div>'
            f'<div style="flex:1;min-width:120px"><div class="form-label">End Time</div>'
            f'<input type="time" name="focus_end_{i}" value="{end}" style="font-size:1.1em;padding:12px;border-color:{color};background:white"></div>'
            f'</div></div></div>'
        )

    return (
        f'<!DOCTYPE html><html><head><link rel="icon" type="image/svg+xml" href="/favicon.svg">'
        f'<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">'
        f'<title>Schedule — {friendly}</title><style>{CSS}</style></head><body>'
        + build_header(f"Schedule — {friendly}", config=config)
        + '<div class="page-wrap">'
        +''
        + f'<div class="section"><form method="POST" action="/device/schedule/save">'
        + f'<input type="hidden" name="client_name" value="{enc}">'
        + f'<input type="hidden" name="client_ip" value="{quote(client_ip)}">'

        # Hours of Peace
        + f'<div class="form-card">'
        + f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">'
        + f'<div style="font-weight:800;color:#D97706;font-size:1.1em">&#x1F319; Hours of Peace</div>'
        + f'<label style="display:flex;align-items:center;gap:6px;font-size:0.85em;color:#64748b">'
        + f'<input type="checkbox" name="enabled" {enabled}> Enable</label></div>'
        + f'<div style="color:#64748b;font-size:0.85em;margin-bottom:14px">Internet automatically pauses at bedtime for <b style="color:#D97706">{friendly}</b>.</div>'
        + f'<div style="background:#FFFBF0;border-radius:12px;padding:16px;border:1px solid #FEF3C7">'
        + f'<div style="display:flex;align-items:flex-end;gap:12px;flex-wrap:wrap">'
        + f'<div style="flex:1;min-width:120px"><div class="form-label">Bedtime</div>'
        + f'<input type="time" name="bedtime" value="{bedtime}" style="font-size:1.1em;padding:12px;border-color:#F4B942;background:white"></div>'
        + f'<div style="flex:2;text-align:center;padding-bottom:10px;color:#D97706;font-size:0.85em;font-weight:600;min-width:140px">'
        + f'&#x1F319; Internet pauses at bedtime<br>and resumes at wake up &#x2600;&#xFE0F;</div>'
        + f'<div style="flex:1;min-width:120px"><div class="form-label">Wake Up</div>'
        + f'<input type="time" name="wake" value="{wake}" style="font-size:1.1em;padding:12px;border-color:#F4B942;background:white"></div>'
        + f'</div></div></div>'

        # Focus Times
        + f'<div style="margin-top:20px">'
        + f'<div style="font-weight:800;color:#6366f1;font-size:1.1em;margin-bottom:4px">&#x1F4DA; Focus Times</div>'
        + f'<div style="color:#64748b;font-size:0.85em;margin-bottom:4px">Block internet during specific daily times. Enable only the ones you need.</div>'
        + focus_block(0) + focus_block(1) + focus_block(2)
        + f'</div>'

        # Screen Time
        + f'<div style="margin-top:20px">'
        + f'<div style="font-weight:800;color:#0ea5e9;font-size:1.1em;margin-bottom:4px">&#x23F1; Daily Screen Time Limit</div>'
        + f'<div style="color:#64748b;font-size:0.85em;margin-bottom:12px">Internet automatically pauses for <b style="color:#0ea5e9">{friendly}</b> when their daily limit is reached. You will receive a notification.</div>'
        + f'<div class="form-card" style="border-color:#BAE6FD">'
        + f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">'
        + f'<div style="font-weight:700;color:#0ea5e9">&#x23F1; Screen Time</div>'
        + f'<label style="display:flex;align-items:center;gap:6px;font-size:0.85em;color:#64748b">'
        + f'<input type="checkbox" name="st_enabled" {st_enabled}> Enable</label></div>'
        + f'<div style="background:#F0F9FF;border-radius:12px;padding:16px;border:1px solid #BAE6FD">'
        + f'<div style="display:flex;align-items:flex-end;gap:12px;flex-wrap:wrap">'
        + f'<div style="flex:1;min-width:120px"><div class="form-label">Hours Per Day</div>'
        + f'<input type="number" name="st_hours" value="{st_hours}" min="0.5" max="24" step="0.5" style="font-size:1.1em;padding:12px;border-color:#0ea5e9;background:white"></div>'
        + f'<div style="flex:2;text-align:center;padding-bottom:10px;color:#0ea5e9;font-size:0.85em;font-weight:600;min-width:140px">'
        + f'&#x23F1; Pauses when limit is reached<br>&#x1F514; Sends notification</div>'
        + f'<div style="flex:1;min-width:120px"><div class="form-label">Reset Time</div>'
        + f'<select name="st_reset" style="font-size:1em;padding:12px;border-color:#0ea5e9;background:white">{reset_opts}</select></div>'
        + f'</div></div></div></div>'
        + f'<button type="submit" class="btn" style="margin-top:20px">Save Schedule</button>'
        + f'</form></div></div></body></html>'
    )


def build_blocked_page():
    return (
        '<!DOCTYPE html><html><head><link rel="icon" type="image/svg+xml" href="/favicon.svg"><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1.0">'
        '<title>Site Blocked — Lantern Watch</title>'
        f'<style>{CSS}</style></head><body>'
        '<div style="min-height:100vh;display:flex;align-items:center;justify-content:center;'
        'background:#f8f7f4;padding:24px">'
        '<div style="max-width:440px;width:100%;text-align:center">'
        f'<div style="width:216px;margin:0 auto 20px">{_LOGO_SVG}</div>'
        '<h1 style="font-size:1.25em;font-weight:800;color:#2c2c2a;margin-bottom:14px;'
        'line-height:1.4">This site has been blocked<br>by Lantern Watch.</h1>'
        '<p style="color:#64748b;font-size:0.92em;line-height:1.6;margin-bottom:18px">'
        'Access was blocked because this site has been identified as potentially harmful.</p>'
        '<div style="background:#fffbf0;border:1px solid #e8d080;border-radius:12px;'
        'padding:18px 20px;margin-bottom:18px">'
        '<div style="font-size:0.95em;color:#7a5c00;font-style:italic;line-height:1.7">'
        '&#x201C;Your word is a lamp to my feet<br>and a light to my path.&#x201D;'
        '</div>'
        '<div style="font-size:0.8em;color:#94a3b8;margin-top:8px;font-weight:700;'
        'letter-spacing:0.05em">— PSALM 119:105</div>'
        '</div>'
        '<p style="color:#94a3b8;font-size:0.8em;line-height:1.5;margin-bottom:16px">'
        'This activity may be visible to your family&#x2019;s accountability settings.</p>'
        '<p style="color:#475569;font-size:0.9em;line-height:1.6;margin-bottom:22px">'
        'Whatever brought you here today, you are not alone. If you&#x2019;d like encouragement, '
        'accountability, prayer, or support, help is available.</p>'
        '<a href="http://192.168.8.1:8081/findhelp" '
        'style="display:inline-block;background:#e8a000;color:white;padding:13px 32px;'
        'border-radius:20px;font-weight:700;font-size:0.95em;text-decoration:none">'
        'Find Help</a>'
        '<div style="margin-top:16px">'
        '<a href="https://www.google.com" '
        'style="color:#94a3b8;font-size:0.85em;font-weight:600;text-decoration:none">'
        '&#x2190; Return to safety</a></div>'
        '</div></div>'
        '</body></html>'
    )


def build_portal_page(dest, config):
    org = config.get("org_name", "").strip()
    network_name = f"{org}'s network" if org else "this network"
    return (
        '<!DOCTYPE html><html><head><link rel="icon" type="image/svg+xml" href="/favicon.svg"><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1.0">'
        '<title>Network Notice — Lantern Watch</title>'
        f'<style>{CSS}</style></head><body>'
        '<div style="min-height:100vh;display:flex;align-items:center;justify-content:center;'
        'background:#f8f7f4;padding:24px">'
        '<div style="max-width:440px;width:100%;text-align:center">'
        f'<div style="width:180px;margin:0 auto 20px">{_LOGO_SVG}</div>'
        f'<h1 style="font-size:1.2em;font-weight:800;color:#2c2c2a;margin-bottom:16px;line-height:1.4">'
        f'Welcome to {network_name}.</h1>'
        '<div style="background:#fffbf0;border:1px solid #e8d080;border-radius:12px;'
        'padding:18px 20px;margin-bottom:20px;text-align:left">'
        '<p style="color:#7a5c00;font-size:0.9em;line-height:1.7;margin:0">'
        'This network monitors DNS traffic and filters content to keep everyone safe. '
        'By connecting you agree to use this network responsibly and in accordance '
        'with its acceptable use policy.</p>'
        '</div>'
        '<p style="color:#94a3b8;font-size:0.82em;margin-bottom:24px">'
        'Inappropriate content, adult sites, and certain platforms may be blocked.<br>'
        'If you have questions, contact the network administrator.</p>'
        f'<form method="POST" action="http://192.168.8.1:8081/portal/ack">'
        f'<input type="hidden" name="dest" value="{dest}">'
        '<button type="submit" style="width:100%;padding:14px;background:#e8a000;border:none;'
        'border-radius:20px;color:white;font-weight:700;font-size:1em;cursor:pointer">'
        'I Understand &mdash; Connect Me</button>'
        '</form>'
        '</div></div>'
        '</body></html>'
    )


def build_findhelp(config):
    data = [
        ("Peer-to-Peer Community", "&#x1F465;", "#6366f1", "#F0F0FF", "#E0E0FF", [
            ("Celebrate Recovery (CR)", "celebraterecovery.com", "celebraterecovery.ca", None,
             "100% Free", "Weekly local group sessions",
             "The largest Christ-centered 12-step program in the world, hosted by local church "
             "volunteers. Safe, confidential, free environment to overcome hurts, habits, and "
             "hang-ups including compulsive internet habits, adult content, and online gambling. "
             "Gender-specific small groups for real-world face-to-face accountability."),
        ]),
        ("Focused Digital & Habits Recovery", "&#x1F6E1;", "#D97706", "#FFFBF0", "#FEF3C7", [
            ("Pure Desire Ministries", "puredesire.org", None, "1-503-489-0230",
             "Free Resources / Paid Study Materials", "24/7 Digital; Phone during business hours",
             "A gold standard for faith-based sexual integrity and tech-habit recovery. Combines "
             "biblical discipleship with clinical brain science. Programs for men, women, and "
             "teenagers, plus dedicated tracks for spouses navigating betrayal trauma."),
            ("The Freedom Fight", "thefreedomfight.org", None, "1-405-600-4243",
             "Free App & Core Training", "24/7 Digital Access (App-based)",
             "A digital-first ministry for the modern internet age. Uses a smartphone app and "
             "30-day challenge to break the cycle of adult content consumption. Focuses on how "
             "dopamine hooks affect the brain. Highly effective for tech-savvy young adults and teenagers."),
        ]),
        ("Professional Counseling & Intervention", "&#x1F4DE;", "#DC6B5F", "#FFF7F7", "#FCA5A5", [
            ("Focus on the Family Counseling Helpline", "focusonthefamily.com", None, "1-855-771-4357",
             "100% Free Initial Consultation", "Mon-Fri, 6:00 AM - 8:00 PM Mountain Time",
             "A premier national lifeline for families facing tech-related conflicts. Free "
             "confidential consultation with a licensed Christian mental health clinician who "
             "will listen, provide guidance, pray with you, and refer your family to vetted "
             "Christian therapists in your local area."),
            ("Concepts of Truth International", "ffcc4u.com", None, "1-866-482-5433",
             "100% Free Helpline", "24/7 Helpline - Always Available",
             "A 24/7 confidential national helpline staffed by trained life coaches and care "
             "specialists. Immediate frontline listening ear for individuals feeling overwhelmed "
             "by addiction or a secret struggle. Provides crisis care and referrals to "
             "professional Christian networks."),
            ("New Life Ministries", "newlife.com", None, "1-800-639-5433",
             "Free Initial Call / Fee-Based Deep Care", "Mon-Fri, 9:00 AM - 7:00 PM Central Time",
             "Free initial call for prayer and matching to a premium network of licensed "
             "Christian therapists, psychiatrists, and behavioral coaches. Hosts intensive "
             "multi-day virtual workshops to break compulsive patterns and rebuild family relationships."),
            ("Adult & Teen Challenge", "teenchallengeusa.org", "teenchallenge.ca", "1-855-363-2334",
             "Varies / Highly Subsidized", "24/7 Online Admissions",
             "When an issue requires a total life reset. Faith-based mentoring and long-term "
             "residential restoration for individuals facing severe compulsive habits including "
             "online gambling and destructive digital dependencies."),
        ]),
    ]

    def resource_card(name, site, site2, phone, cost, avail, desc, color, bg, border):
        ph = (f'<span style="color:{color};font-weight:700">&#x1F4DE; {phone}</span>'
              if phone else '<span style="color:#94a3b8;font-size:0.85em">Online access only</span>')
        s2 = (f' / <a href="https://{site2}" target="_blank" style="color:{color}">{site2}</a>' if site2 else "")
        return (
            f'<div style="background:white;border-radius:12px;border:1px solid {border};padding:16px;margin-bottom:10px;box-shadow:0 2px 8px rgba(0,0,0,0.04)">'
            f'<div style="font-weight:800;color:#1e293b;margin-bottom:8px">{name}</div>'
            f'<div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px">'
            f'<span style="background:{bg};color:{color};border:1px solid {border};padding:2px 10px;border-radius:99px;font-size:0.75em;font-weight:700">&#x1F4B0; {cost}</span>'
            f'<span style="background:#F1F5F9;color:#475569;padding:2px 10px;border-radius:99px;font-size:0.75em;font-weight:600">&#x1F552; {avail}</span>'
            f'</div>'
            f'<div style="font-size:0.85em;color:#475569;line-height:1.6;margin-bottom:12px">{desc}</div>'
            f'<div style="display:flex;flex-wrap:wrap;gap:12px;align-items:center;border-top:1px solid #F1F5F9;padding-top:10px">'
            f'<div>&#x1F310; <a href="https://{site}" target="_blank" style="color:{color};font-weight:600">{site}</a>{s2}</div>'
            f'<div>{ph}</div>'
            f'</div></div>'
        )

    cards_html = ""
    for cat_name, icon, color, bg, border, items in data:
        items_html  = "".join(resource_card(*i, color, bg, border) for i in items)
        cards_html += (
            f'<div style="margin-bottom:20px">'
            f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">'
            f'<span style="font-size:1.3em">{icon}</span>'
            f'<div style="font-weight:800;color:{color};font-size:0.95em;text-transform:uppercase;letter-spacing:1px">{cat_name}</div>'
            f'</div>{items_html}</div>'
        )

    hero = (
        '<div style="background:#F0FDF4;border:1px solid #86EFAC;border-radius:12px;padding:18px 20px;margin-bottom:16px">'
        '<div style="font-weight:800;color:#16A34A;font-size:1.05em;margin-bottom:6px">&#x1F526; Take a breath</div>'
        '<div style="font-size:0.9em;color:#334155;line-height:1.7">'
        'You made it here, and that&#x2019;s a good first step.<br><br>'
        'This site was blocked because it has been identified as potentially harmful to your goals, '
        'relationships, faith, finances, or well-being. Many people struggle with online habits they '
        'never intended to develop &mdash; whether curiosity, frustration, temptation, or a deeper '
        'struggle, you are not alone.<br><br>'
        'Freedom doesn&#x2019;t usually happen in a single moment. It often begins with one honest '
        'conversation, one prayer, or one decision to ask for help.</div></div>'
    )
    prayer = (
        '<div style="background:#fffbf0;border:1px solid #e8d080;border-radius:12px;padding:16px 20px;margin-bottom:16px">'
        '<div style="font-weight:700;color:#7a5c00;margin-bottom:6px">&#x1F64F; A simple prayer</div>'
        '<div style="font-size:0.9em;color:#7a5c00;font-style:italic;line-height:1.7">'
        'Lord, help me choose what is good, pure, and life-giving today. Give me strength to walk in '
        'truth, wisdom, and freedom. Amen.</div></div>'
    )
    immediate = (
        '<div style="background:#FFF7F7;border:1px solid #FCA5A5;border-radius:12px;padding:16px 20px;margin-bottom:16px">'
        '<div style="font-weight:800;color:#DC6B5F;margin-bottom:8px">&#x1F6A8; Need support right now?</div>'
        '<ul style="margin:0;padding-left:18px;font-size:0.88em;color:#475569;line-height:1.8">'
        '<li>Put down your device and take a short walk.</li>'
        '<li>Call or text a trusted friend, spouse, pastor, mentor, or family member.</li>'
        '<li>Spend a few minutes in prayer before your next decision.</li></ul>'
        '<div style="margin-top:12px;padding-top:12px;border-top:1px solid #FCA5A5;font-size:0.88em;color:#334155;line-height:1.6">'
        'If you&#x2019;re in crisis or thinking about harming yourself, you don&#x2019;t have to face it alone:'
        '<div style="margin-top:8px;font-weight:800;color:#DC6B5F">&#x1F4DE; Call or text '
        '988 &mdash; Suicide &amp; Crisis Lifeline (USA &amp; Canada, 24/7)</div>'
        '<div style="font-size:0.85em;color:#64748b;margin-top:4px">Outside the US or in immediate danger, contact your local emergency services.</div>'
        '</div></div>'
    )
    church = (
        '<div style="background:white;border:1px solid #E2E8F0;border-radius:12px;padding:16px 20px;margin-bottom:16px">'
        '<div style="font-weight:800;color:#475569;margin-bottom:6px">&#x26EA; Start here</div>'
        '<div style="font-size:0.88em;color:#475569;line-height:1.7">If you attend a church, consider reaching '
        'out to a trusted pastor, elder, mentor, or men&#x2019;s / women&#x2019;s leader. Many people find that '
        'healing begins with a real conversation rather than another website.</div></div>'
    )
    resources_heading = (
        '<div style="font-weight:800;color:#1e293b;font-size:1em;margin:22px 0 6px">'
        '&#x1F6E1;&#xFE0F; Recovery &amp; accountability resources</div>'
        '<div style="font-size:0.82em;color:#64748b;line-height:1.6;margin-bottom:14px">'
        'Confidential, faith-based support for individuals and families &mdash; all free or low-cost.</div>'
    )
    spouses = (
        '<div style="background:#FDF2F8;border:1px solid #FBCFE8;border-radius:12px;padding:16px 20px;margin:8px 0 16px">'
        '<div style="font-weight:800;color:#DB2777;margin-bottom:6px">&#x2764;&#xFE0F; For spouses &amp; families</div>'
        '<div style="font-size:0.88em;color:#475569;line-height:1.7">If someone you love is struggling, you deserve '
        'support too. Betrayal, isolation, anxiety, and loss of trust are heavy to carry alone. Pure Desire Ministries, '
        'Focus on the Family, and New Life Ministries (above) all offer dedicated help for spouses, parents, and families. '
        'You do not have to carry this burden alone.</div></div>'
    )
    remember = (
        '<div style="background:#FFFBF0;border:1px solid #FEF3C7;border-radius:12px;padding:16px 20px;margin-top:16px;text-align:center">'
        '<div style="font-weight:800;color:#D97706;margin-bottom:6px">&#x1F526; Remember</div>'
        '<div style="font-size:0.9em;color:#475569;line-height:1.7">Needing help is not weakness. Everyone faces '
        'struggles, and everyone needs support. One small step toward freedom today can make a bigger difference than '
        'you realize.<br><br><b>You are not alone, and there is hope.</b></div></div>'
    )
    return (
        '<!DOCTYPE html><html><head><link rel="icon" type="image/svg+xml" href="/favicon.svg"><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1.0">'
        '<title>Find Help - Lantern Watch</title><style>' + CSS + '</style></head><body>'
        + build_header("Find Help", config=config)
        + '<div class="page-wrap">'
        + '<div class="section">'
        + hero + prayer + immediate + church
        + resources_heading + cards_html + spouses + remember
        + '</div></div></body></html>'
    )


def build_social(config, saved=False, error=""):
    from adguard import normalize_profile
    current_profile = normalize_profile(config.get("social_profile", "moderate"))
    custom          = config.get("social_custom", {})
    saved_msg       = '<div class="success">&#x2705; Profile applied — platforms updated instantly.</div>' if saved else ""
    error_msg       = (f'<div style="margin-bottom:12px;padding:14px;background:#FFF7F7;'
                       f'border:1px solid #FCA5A5;border-radius:12px;color:#DC6B5F">{error}</div>') if error else ""

    profile_cards = ""
    for pid, plabel_str, pdesc, picon, pcolor, pbg, pborder in [
        ("open",     "Open",     "All social media allowed. Safe Search off.",                         "&#x1F513;", "#16A34A", "#F0FDF4", "#86EFAC"),
        ("moderate", "Moderate", "All social media allowed. Adult content blocked by AdGuard, with Safe Search on.", "&#x1F6E1;", "#D97706", "#FFFBF0", "#FEF3C7"),
        ("strict",   "Strict",   "All social media blocked. Safe Search &amp; YouTube Restricted Mode on.", "&#x1F512;", "#DC6B5F", "#FFF7F7", "#FCA5A5"),
        # Custom is à la carte — its full picker (platforms + per-engine Safe
        # Search) is its own section below the YouTube setting, not a quick card.
    ]:
        active        = current_profile == pid
        border_style  = f"border:2px solid {pcolor}" if active else f"border:1px solid {pborder}"
        active_badge  = (f'<span style="background:{pcolor};color:white;padding:2px 8px;border-radius:99px;'
                         f'font-size:0.7em;font-weight:700;margin-left:8px">ACTIVE</span>') if active else ""
        ss_val = PROFILE_SAFE_SEARCH.get(pid)
        if ss_val is True:
            ss_tag = ('<span style="font-size:0.75em;color:#16A34A;font-weight:600;'
                      'background:#F0FDF4;padding:2px 7px;border-radius:4px;margin-left:6px">'
                      '&#x1F50D; Safe Search On</span>')
        elif ss_val is False:
            ss_tag = ('<span style="font-size:0.75em;color:#94a3b8;font-weight:600;'
                      'background:#f8fafc;padding:2px 7px;border-radius:4px;margin-left:6px">'
                      '&#x1F50D; Safe Search Off</span>')
        else:
            ss_tag = ""  # custom — shown as checkbox below
        profile_cards += (
            f'<form method="POST" action="/social/apply">'
            f'<input type="hidden" name="profile" value="{pid}">'
            f'<div style="background:{pbg};{border_style};border-radius:12px;padding:14px 16px;margin-bottom:10px">'
            f'<div style="display:flex;justify-content:space-between;align-items:center">'
            f'<div style="display:flex;align-items:center;gap:8px">'
            f'<span style="font-size:1.3em">{picon}</span>'
            f'<div style="font-weight:800;color:{pcolor}">{plabel_str}</div>{active_badge}</div>'
            f'<button type="submit" style="background:{pcolor};color:white;border:none;border-radius:8px;'
            f'padding:6px 14px;font-weight:700;cursor:pointer;font-size:0.85em">Apply</button>'
            f'</div>'
            f'<div style="font-size:0.82em;color:#64748b;margin-top:6px">{pdesc}{ss_tag}</div>'
            f'</div></form>'
        )

    platforms = [
        ("youtube",   "YouTube",    "&#x1F4FA;"), ("tiktok",    "TikTok",     "&#x1F3B5;"),
        ("discord",   "Discord",    "&#x1F4AC;"), ("instagram", "Instagram",  "&#x1F4F7;"),
        ("facebook",  "Facebook",   "&#x1F44D;"), ("twitter",   "Twitter/X",  "&#x1F426;"),
        ("snapchat",  "Snapchat",   "&#x1F47B;"), ("reddit",    "Reddit",     "&#x1F916;"),
        ("twitch",    "Twitch",     "&#x1F3AE;"), ("pinterest", "Pinterest",  "&#x1F4CC;"),
    ]
    # Platform checkboxes mirror the LIVE state (what the current profile actually
    # allows), not a stale saved custom list — so picking Open/Moderate shows all
    # checked, Strict shows all unchecked, and Custom shows your own selection.
    from adguard import get_blocked_platforms
    try:
        _blocked_now = set(get_blocked_platforms(config))
    except Exception:
        _blocked_now = set()
    plat_checks = ""
    for pid2, plabel2, picon2 in platforms:
        chk         = "" if pid2 in _blocked_now else "checked"
        plat_checks += f'<label class="check-row"><input type="checkbox" name="plat_{pid2}" {chk}><span>{picon2} {plabel2}</span></label>'

    # Per-engine safe search — pre-filled from the live AdGuard state.
    from adguard import get_safesearch_status, SAFE_SEARCH_ENGINES
    try:
        _ss = get_safesearch_status(config)
    except Exception:
        _ss = {}
    _ss_on = bool(_ss.get("enabled"))
    _ENG_LABELS = {"google": "Google", "bing": "Bing", "duckduckgo": "DuckDuckGo",
                   "youtube": "YouTube", "pixabay": "Pixabay", "yandex": "Yandex", "ecosia": "Ecosia"}
    ss_boxes = ""
    for _eng in SAFE_SEARCH_ENGINES:
        _on   = "checked" if (_ss_on and _ss.get(_eng)) else ""
        _note = (' <span style="color:#e8a000;font-size:0.85em">— hides all YouTube comments</span>'
                 if _eng == "youtube" else "")
        ss_boxes += (f'<label class="check-row"><input type="checkbox" name="ss_{_eng}" {_on}>'
                     f'<span>{_ENG_LABELS[_eng]}{_note}</span></label>')
    custom_is_active = current_profile == "custom"
    custom_active = ('<span style="background:#475569;color:white;padding:2px 8px;border-radius:99px;'
                     'font-size:0.7em;font-weight:700;margin-left:8px">ACTIVE</span>'
                     if custom_is_active else "")
    custom_border = ('border:2px solid #475569' if custom_is_active else 'border:1px solid #E2E8F0')
    custom_section = (
        '<div class="form-card" style="margin-top:4px;' + custom_border + '">'
        # Profile-style header so Custom reads as a real profile choice and shows
        # ACTIVE when it's the selected one — matching Open / Moderate / Strict.
        '<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">'
        '<span style="font-size:1.3em">&#x2699;</span>'
        '<div style="font-weight:800;color:#475569">Custom</div>' + custom_active + '</div>'
        '<div style="font-size:0.82em;color:#64748b;margin-bottom:12px">Pick exactly which platforms to allow and which '
        'search engines enforce Safe Search, then <b>Apply Custom Profile</b>.</div>'
        '<form method="POST" action="/social/apply">'
        '<input type="hidden" name="profile" value="custom">'
        '<div class="form-label">Allowed Platforms</div>'
        + plat_checks
        + '<div style="margin-top:14px;padding-top:12px;border-top:1px solid #e2e8f0">'
        + '<div class="form-label">&#x1F50D; Safe Search &mdash; choose which engines</div>'
        + '<div style="font-size:0.78em;color:#94a3b8;margin-bottom:8px">Forces explicit results &amp; images off on each engine you check. '
        + 'Heads-up: YouTube&rsquo;s safe search is Restricted Mode, which hides all comments &mdash; leave it off to keep comments.</div>'
        + ss_boxes
        + '</div>'
        + '<button type="submit" class="btn" style="margin-top:14px">Apply Custom Profile</button>'
        + '</form></div>'
    )

    # Extra Protection — optional curated blocklists (toggle to add/remove).
    from adguard import OPTIONAL_LISTS, get_active_filter_urls
    try:
        _active = get_active_filter_urls(config)
    except Exception:
        _active = set()
    _ep_rows = ""
    for _lst in OPTIONAL_LISTS:
        _on = "checked" if _lst["url"] in _active else ""
        _ep_rows += (f'<label class="check-row" style="align-items:flex-start">'
                     f'<input type="checkbox" name="extra_{_lst["id"]}" {_on} style="margin-top:3px">'
                     f'<span><b>{_lst["label"]}</b><br>'
                     f'<span style="font-size:0.8em;color:#94a3b8">{_lst["desc"]}</span></span></label>')
    from adguard import (is_lite as _is_lite, upstream_hosts as _up_hosts,
                         LITE_DNS_TIERS as _TIERS, lite_dns_tier as _cur_tier)
    if _is_lite(config):
        _sel = _cur_tier(config)
        _tier_rows = ""
        for _tid in ("families", "malware"):
            _t   = _TIERS[_tid]
            _chk = "checked" if _sel == _tid else ""
            _tier_rows += (
                '<label class="check-row" style="align-items:flex-start">'
                f'<input type="radio" name="dns_tier" value="{_tid}" {_chk} style="margin-top:3px">'
                f'<span><b>{_t["label"]}</b> <span style="color:#94a3b8;font-size:0.82em">({_t["ips"]})</span><br>'
                f'<span style="font-size:0.8em;color:#94a3b8">{_t["desc"]}</span></span></label>'
            )
        upstream_info = (
            '<div class="form-card" style="margin-top:16px;background:#F0FDF4;border-color:#86EFAC">'
            '<div style="font-weight:700;color:#166534;margin-bottom:6px">&#x1F310; DNS Filtering (network-wide)</div>'
            '<div style="font-size:0.82em;color:#475569;line-height:1.6;margin-bottom:10px">'
            'Filtered right at the Cloudflare DNS upstream &mdash; network-wide, before sites reach any device. '
            'Blocked sites still land on the Lantern Watch block page. Choose how much the upstream filters:</div>'
            '<form method="POST" action="/social/dns-tier">'
            + _tier_rows
            + '<button type="submit" class="btn" style="margin-top:12px">Save DNS Filtering</button>'
            '</form></div>'
        )
    else:
        _ups = ", ".join(sorted(_up_hosts(config))) or "system default"
        upstream_info = (
            '<div class="form-card" style="margin-top:16px;background:#F0FDF4;border-color:#86EFAC">'
            '<div style="font-weight:700;color:#166534;margin-bottom:6px">&#x1F310; DNS Filtering</div>'
            f'<div style="font-size:0.82em;color:#475569;line-height:1.6">DNS upstream: <b>{_ups}</b>. '
            'Adult, malware &amp; phishing are filtered by Lantern Watch&rsquo;s local blocklists below.</div>'
            '</div>'
        )
    extra_protection = (
        upstream_info
        + '<div class="form-card" style="margin-top:16px">'
        '<div style="font-weight:700;color:#475569;margin-bottom:6px">&#x1F6E1;&#xFE0F; Extra Protection</div>'
        '<div style="font-size:0.82em;color:#94a3b8;margin-bottom:10px">Optional blocklists you can switch on. '
        'Curated, low-risk choices &mdash; ads, phishing, malware &amp; adult content are already blocked by default.</div>'
        '<form method="POST" action="/protection/apply">'
        + _ep_rows
        + '<button type="submit" class="btn" style="margin-top:12px">Save Extra Protection</button>'
        + '</form></div>'
    )

    # YouTube Restricted Mode — the one granular control (Secure by Default = ON).
    # State comes from AdGuard's live Safe Search status (the source of truth), not
    # a config flag, so it always reflects reality and can't be clobbered.
    _yt_on = bool(_ss_on and _ss.get("youtube"))
    youtube_card = (
        '<div class="form-card" style="margin-top:4px">'
        '<div style="font-weight:700;color:#475569;margin-bottom:6px">&#x1F4FA; YouTube Restricted Mode</div>'
        '<div style="font-size:0.82em;color:#94a3b8;margin-bottom:12px">Applies while Safe Search is on (the '
        '<b>Moderate</b> &amp; <b>Strict</b> profiles). On (recommended) hides mature videos and all comments. '
        'Turning it off allows comments and normal videos.</div>'
        '<form method="POST" action="/social/youtube" onsubmit="return lwYtConfirm()">'
        '<label class="toggle-row"><input type="checkbox" id="yt_toggle" name="youtube_restricted" '
        + ("checked" if _yt_on else "")
        + ' onchange="lwYtWarn()"><span>Restricted Mode &mdash; hide mature videos &amp; comments</span></label>'
        '<div id="yt_warn" style="display:' + ("none" if _yt_on else "block")
        + ';margin-top:10px;padding:10px 14px;background:#FFF7F7;border:1px solid #FCA5A5;border-radius:8px;font-size:0.8em;color:#DC6B5F">'
        '&#x26A0;&#xFE0F; <b>Comments allowed.</b> With Restricted Mode off, YouTube shows unmoderated comments and '
        'unfiltered videos &mdash; which can expose viewers to inappropriate content and bullying.</div>'
        '<button type="submit" class="btn" style="margin-top:14px">Save YouTube Setting</button>'
        '</form></div>'
    )

    info_banner = (
        '<div style="background:#F0F9FF;border:1px solid #BAE6FD;border-radius:12px;padding:14px 16px;margin-bottom:16px">'
        '<div style="font-weight:700;color:#0ea5e9;margin-bottom:4px">&#x2139;&#xFE0F; How this works</div>'
        '<div style="font-size:0.85em;color:#475569;line-height:1.6">'
        'Blocked platforms are intercepted at the DNS level — they simply won\'t load on any device. '
        'Changes apply instantly with no internet interruption. '
        'Adult content is blocked separately by AdGuard\'s built-in filters.'
        '</div></div>'
    )

    return (
        '<!DOCTYPE html><html><head><link rel="icon" type="image/svg+xml" href="/favicon.svg"><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1.0">'
        '<title>Social Profiles - Lantern Watch</title><style>' + CSS + '</style></head><body>'
        + build_header("Social Media Profiles", config=config)
        + '<div class="page-wrap">'
        +''
        + saved_msg + error_msg
        + '<div class="section">'
        + info_banner
        + profile_cards + youtube_card + custom_section + extra_protection
        + '</div></div>'
        + '<script>'
        + 'function lwYtWarn(){var c=document.getElementById("yt_toggle");'
        + 'document.getElementById("yt_warn").style.display=c.checked?"none":"block";}'
        + 'function lwYtConfirm(){var c=document.getElementById("yt_toggle");'
        + 'if(!c.checked){return confirm("Allow YouTube comments? This turns off Restricted Mode and can expose viewers to unmoderated content and bullying.");}'
        + 'return true;}'
        + '</script>'
        + '</body></html>'
    )


DEVICE_ACTIVE_HOURS = 48   # hide devices with no activity in this window (kept, not deleted)


def _device_type_row(name, shown, paused, reports, notes):
    """One row of the 'what each device type does' reference table."""
    chk  = '<span style="color:#1d9e75;font-weight:700">&#10003;</span>'
    dash = '<span style="color:#cbd5e1">&mdash;</span>'
    pause_cell  = chk if paused else dash
    report_cell = chk if reports == "yes" else '<span style="color:#94a3b8">Skipped</span>'
    td  = 'padding:7px 10px;border-bottom:1px solid #f1f5f9'
    tdc = td + ';text-align:center'
    return (
        f'<tr>'
        f'<td style="{td};font-weight:600;color:#2c2c2a;white-space:nowrap">{name}</td>'
        f'<td style="{td};color:#475569">{shown}</td>'
        f'<td style="{tdc}">{pause_cell}</td>'
        f'<td style="{tdc}">{report_cell}</td>'
        f'<td style="{tdc}">{chk}</td>'
        f'<td style="{td};color:#64748b">{notes}</td>'
        f'</tr>'
    )


def _short_vendor(v):
    """Trim corporate suffixes from an OUI vendor for display: 'Dell Inc.' -> 'Dell',
    'Samsung Electronics Co.,Ltd' -> 'Samsung'."""
    v = (v or "").split(",")[0].strip()
    for suf in (" Electronics", " Technologies", " Technology", " Corporation",
                " Corp.", " Corp", " Communications", " Systems", " Networks",
                " Inc.", " Inc", " LLC", " Ltd.", " Ltd", " Co.", " GmbH"):
        if v.endswith(suf):
            v = v[: -len(suf)].strip()
    return v


def build_devices_page(config, saved=False, redetect=False, autoname=False, sort="name", flt=""):
    # Only devices active in the last DEVICE_ACTIVE_HOURS; a stale device is hidden,
    # not deleted — it (and any saved name) returns the moment it's seen again.
    all_devices  = get_all_known_devices(active_hours=DEVICE_ACTIVE_HOURS, include_idle=False)
    cfg_devices  = config.get("devices", {})
    rows_html    = ""
    TYPE_ICONS   = {"person": "👤", "parent": "🛡️", "infrastructure": "🖥️", "smart_device": "📡", "work_device": "💼"}
    TYPE_NAMES   = {"person": "Personal", "parent": "Admin", "infrastructure": "Infrastructure", "smart_device": "Smart Device", "work_device": "Work Device"}
    from classify import classify_device, device_identity, label_from_domains, is_cryptic_name, device_kind

    # Build IP→hostname map once; used for devices whose client_name is a bare IP
    try:
        ip_hostnames = get_ip_hostname_map(config)
    except Exception:
        ip_hostnames = {}
    try:
        top_domains_map = get_top_domains_map(per_device=6)
    except Exception:
        top_domains_map = {}

    import re
    _is_ip = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$").match

    # ── Filter + sort the device list ─────────────────────────────────────────
    # "Needs a name" = shows as a bare IP (no real label, and the client name is
    # itself an IP rather than a resolved hostname).
    def _unlabeled(n):
        l = cfg_devices.get(n, {}).get("label", "")
        has_real_label = bool(l) and l != n
        return bool(_is_ip(n)) and not has_real_label
    if flt == "unlabeled":
        all_devices = [d for d in all_devices if _unlabeled(d["client_name"])]

    def _sortkey(d):
        n = d["client_name"]
        if sort == "type":    return effective_type(n, config)
        if sort == "recent":  return _row_get(d, "last_seen") or ""
        if sort == "queries": return _row_get(d, "total") or 0
        l = cfg_devices.get(n, {}).get("label", "")
        return (l if (l and l != n) else n).lower()
    all_devices = sorted(all_devices, key=_sortkey, reverse=(sort in ("recent", "queries")))

    def _slink(key, lbl):
        on = "background:#fff4dc;border-color:#e8a000;color:#b87d00;font-weight:600" if sort == key else "color:#64748b"
        return (f'<a href="/admin/devices?sort={key}&flt={flt}" style="padding:4px 11px;border:1px solid var(--line);'
                f'border-radius:8px;font-size:0.8em;text-decoration:none;{on}">{lbl}</a>')
    def _flink(key, lbl):
        on = "background:#fff4dc;border-color:#e8a000;color:#b87d00;font-weight:600" if flt == key else "color:#64748b"
        return (f'<a href="/admin/devices?sort={sort}&flt={key}" style="padding:4px 11px;border:1px solid var(--line);'
                f'border-radius:8px;font-size:0.8em;text-decoration:none;{on}">{lbl}</a>')
    sortfilter = (
        '<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-bottom:12px">'
        '<span style="color:#94a3b8;font-size:0.8em">Sort:</span>'
        + _slink("name", "Name") + _slink("type", "Role") + _slink("recent", "Recent") + _slink("queries", "Most queries")
        + '<span style="color:#94a3b8;font-size:0.8em;margin-left:10px">Show:</span>'
        + _flink("", "All") + _flink("unlabeled", "Needs a name")
        + f'<span style="color:#cbd5e1;font-size:0.78em;margin-left:auto" title="Devices with no activity in the last {DEVICE_ACTIVE_HOURS} hours are hidden until seen again">{len(all_devices)} device' + ("" if len(all_devices) == 1 else "s") + f' &middot; active in last {DEVICE_ACTIVE_HOURS}h</span>'
        + '</div>'
    )

    for d in all_devices:
        name        = d["client_name"]
        cfg         = cfg_devices.get(name, {})
        stored_type = cfg.get("type")            # None if the user never set one
        _doms      = top_domains_map.get(name, [])
        if redetect:
            guess, confident = classify_device(name, cfg.get("label", ""), config, _doms)
            # Only override a saved type when we have positive evidence — a weak
            # "person" fallback must not clobber a deliberate manual choice.
            cur_type   = guess if (confident or stored_type is None) else stored_type
            auto_typed = True
        else:
            cur_type   = effective_type(name, config, _doms)
            auto_typed = "type" not in cfg        # no saved choice → guessed
        # Hint shown next to the Role field
        if redetect and stored_type and stored_type != cur_type:
            type_hint = f' <span style="color:#e8a000;font-weight:600">· suggested (was {TYPE_NAMES.get(stored_type, stored_type)})</span>'
        elif redetect:
            type_hint = ' <span style="color:#94a3b8;font-weight:400">· suggested</span>'
        elif auto_typed:
            type_hint = ' <span style="color:#94a3b8;font-weight:400">· auto-detected</span>'
        else:
            type_hint = ''
        cur_monitor = cfg.get("monitor", True)
        last        = _local_ts(d["last_seen"])[:16] if d["last_seen"] else "-"
        enc         = quote(name)
        icon        = TYPE_ICONS.get(cur_type, "👤")

        # Display name comes from the shared helper so this page and the dashboard
        # can never disagree about what a device is called.
        ident    = device_identity(name)
        display  = device_display_name(name, config, ip_hostnames,
                                       client_ip=_row_get(d, "client_ip"), ident=ident)
        subtitle = name if _is_ip(name) else ""

        # The router's own DNS lookups report as "localhost" (127.0.0.1) — default
        # it to Infrastructure when the user hasn't given it a role.
        if name == "localhost" or (_row_get(d, "client_ip") in ("127.0.0.1", "::1")):
            subtitle = subtitle or (_row_get(d, "client_ip") or "127.0.0.1")
            if "type" not in cfg:
                cur_type = "infrastructure"
                icon     = TYPE_ICONS.get("infrastructure", icon)

        # Pre-fill the Name field with resolved hostname if no label set yet
        default_label = cfg.get("label") or display
        cur_label     = _demo(name, default_label, config)

        sel_person  = "selected" if cur_type == "person"         else ""
        sel_parent  = "selected" if cur_type == "parent"         else ""
        sel_infra   = "selected" if cur_type == "infrastructure" else ""
        sel_smart   = "selected" if cur_type == "smart_device"   else ""
        sel_work    = "selected" if cur_type == "work_device"    else ""
        mc          = "checked"  if cur_monitor                  else ""
        sub_html    = f'<div style="font-size:0.75em;color:#94a3b8">{subtitle}</div>' if subtitle else ""
        enc_ip      = quote(d["client_ip"] or "")

        # ident is computed above (near the display-name resolution).

        # Auto-name mode: pre-fill the Name with the device's detected identity
        # (hostname, else maker) for still-unlabeled devices, for review + save.
        # "Unlabeled" includes a placeholder label that's just the IP/name itself.
        name_hint = ""
        _cur_lbl  = cfg.get("label", "")
        if autoname and (not _cur_lbl or _cur_lbl == name) and not config.get("demo_mode"):
            # Prefer a real hostname, then the maker, then a traffic-based guess
            # ("Chromecast / Google TV") for devices that hide both.
            suggested = ""
            if ident["hostname"] and ident["hostname"] != name:
                suggested = ident["hostname"]
            elif ident["vendor"]:
                suggested = f'{ident["vendor"]} device'
            elif is_cryptic_name(ident["hostname"] or name):
                # Only when the name itself tells us nothing (e.g. a model code, a bare
                # IP) do we fall back to guessing from traffic. A real hostname
                # like 'Galaxy-Pro' is left exactly as-is.
                suggested = label_from_domains(top_domains_map.get(name, []))
            if suggested and suggested != name:
                cur_label = suggested
                name_hint = ' <span style="color:#94a3b8;font-weight:400">· suggested</span>'

        # Identity hints — IP, hostname, maker, MAC (demo mode shows stable fakes).
        disp  = demo_ident(name, ident, d["client_ip"] or "", config)
        _idb  = []
        if disp["ip"] and disp["ip"] != cur_label and disp["ip"] != name:
            _idb.append(f'&#x1F310; {disp["ip"]}')
        if disp["hostname"] and disp["hostname"] != name and disp["hostname"].lower() != (cur_label or "").lower():
            _idb.append(f'&#x1F4F6; {disp["hostname"]}')
        if disp["vendor"]:
            _idb.append(f'&#x1F3F7;&#xFE0F; {disp["vendor"]}')
        if disp["mac"]:
            _idb.append(f'<span style="color:#cbd5e1">{disp["mac"]}</span>')
        id_line = (f'<div style="font-size:0.72em;color:#94a3b8;margin-top:3px">{" &middot; ".join(_idb)}</div>'
                   if _idb else "")

        # "Probably a ..." — plain-language guess of what the device is.
        kind_line = ""
        _kind = device_kind(name, cur_label if cur_label != name else "", ident, _doms)
        if _kind:
            _art = "an" if _kind[:1].lower() in "aeiou" else "a"
            kind_line = (f'<div style="font-size:0.74em;color:#64748b;margin-top:3px">'
                         f'&#x1F50E; probably {_art} {_kind}</div>')

        # "Talks to" hint — top domains help identify an otherwise-anonymous device:
        # bare IPs, or un-named devices that also hide their maker (randomized MAC).
        talks_line = ""
        _anon = ((not _cur_lbl or _cur_lbl == name) and not ident["vendor"]
                 and is_cryptic_name(ident["hostname"] or name))
        if _unlabeled(name) or _anon:
            _t = top_domains_map.get(name, [])[:3]
            if config.get("demo_mode"):
                _t = [_demo_domain(x) for x in _t]
            if _t:
                talks_line = (f'<div style="font-size:0.72em;color:#94a3b8;margin-top:2px">'
                              f'&#x1F4AC; talks to: {", ".join(_t)}</div>')

        rows_html  += f"""<div class="form-card">
  <div style="display:flex;justify-content:space-between;margin-bottom:10px">
    <div><div style="font-weight:700">{icon} {cur_label}</div>
    {sub_html}<div style="font-size:0.75em;color:#64748b">Last: {last} — {d["total"]:,} queries</div>{kind_line}{id_line}{talks_line}</div>
    <label style="display:flex;align-items:center;gap:6px;font-size:0.8em;color:#64748b">
      <input type="checkbox" name="monitor_{enc}" {mc}> Monitor</label>
  </div>
  <div style="display:flex;gap:8px;flex-wrap:wrap">
    <div style="flex:2;min-width:140px"><div class="form-label">Name{name_hint}</div>
      <input type="text" name="label_{enc}" value="{cur_label}"></div>
    <div style="flex:1;min-width:120px"><div class="form-label">Role{type_hint}</div>
      <select name="type_{enc}" onchange="lwRoleDesc(this)">
        <option value="person" {sel_person}>👤 Personal</option>
        <option value="parent" {sel_parent}>🛡️ Admin</option>
        <option value="work_device" {sel_work}>💼 Work Device</option>
        <option value="infrastructure" {sel_infra}>🖥️ Infrastructure</option>
        <option value="smart_device" {sel_smart}>📡 Smart Device</option>
      </select>
      <div class="role-desc" style="font-size:0.72em;color:#94a3b8;margin-top:3px;line-height:1.3"></div></div>
  </div>
  <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;margin-top:10px;padding-top:8px;border-top:1px solid #1e293b">
    <a href="/device?name={enc}&ip={enc_ip}" style="font-size:0.75em;color:#D97706;font-weight:600">Tap to see details</a>
    <div style="display:flex;gap:14px;align-items:center">
      <a href="/device/schedule?name={enc}&ip={enc_ip}" style="font-size:0.75em;color:#94a3b8">&#x23F0; Schedule</a>
      <button type="submit" formaction="/admin/devices/remove" name="remove" value="{enc}" formnovalidate onclick="return confirm('Forget this device? Its name and type are removed. If still on the network it will reappear unnamed.')" style="background:none;border:none;color:#cbd5e1;font-size:0.75em;cursor:pointer;padding:0">Forget</button>
    </div>
  </div></div>"""

    saved_msg = '<div class="success">Saved!</div>' if saved else ""
    if redetect or autoname:
        _what = "types" if redetect else "names"
        redetect_controls = (
            '<div style="margin-bottom:14px;padding:12px 14px;background:#fffbf0;border:1px solid #f3e3b8;'
            'border-radius:10px;color:#8a6d00;font-size:0.85em">'
            f'&#x1F504; <b>Suggested {_what} are shown below — nothing is saved yet.</b> '
            'Review them, then tap <b>Save All Devices</b> to apply, or '
            '<a href="/admin/devices" style="color:#e8a000;font-weight:700">cancel</a>.</div>'
        )
    else:
        redetect_controls = (
            '<div style="margin-bottom:14px;display:flex;align-items:center;gap:10px;flex-wrap:wrap">'
            '<a href="/admin/devices?redetect=1" class="btn btn-secondary" '
            'style="width:auto;padding:8px 16px;font-size:0.85em;margin-bottom:0">&#x1F504; Re-detect all types</a>'
            '<a href="/admin/devices?autoname=1" class="btn btn-secondary" '
            'style="width:auto;padding:8px 16px;font-size:0.85em;margin-bottom:0">&#x1F3F7;&#xFE0F; Auto-name devices</a>'
            '<span style="color:#94a3b8;font-size:0.78em">Suggest a type or a name for every device from its hostname &amp; maker — you review before saving.</span>'
            '</div>'
        )
    return (
        f'<!DOCTYPE html><html><head><link rel="icon" type="image/svg+xml" href="/favicon.svg"><meta charset="UTF-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1.0">'
        f'<title>Devices - Lantern Watch</title><style>{CSS}</style></head><body>'
        + build_header("Device Manager", config=config)
        + '<div class="page-wrap">'
        + f'{saved_msg}'
        + f'<div class="section"><h2>Manage Devices</h2>'
        + f'<div style="color:#64748b;font-size:0.85em;margin-bottom:12px">'
        + f'Label devices and set their role. <b>All roles get the same AdGuard filtering</b> &mdash; the role only affects grouping, whether <b>Pause All Personal</b> applies, reporting, and a few alert behaviors. '
        + f'<b>Every type is filtered equally</b> &mdash; no type bypasses AdGuard.</div>'
        + f'<div style="overflow-x:auto;margin-bottom:14px">'
        + f'<table style="border-collapse:collapse;width:100%;min-width:560px;font-size:0.8em">'
        + f'<thead><tr style="background:#f8fafc">'
        + f'<th style="padding:7px 10px;text-align:left;border-bottom:2px solid #e2e8f0">Role</th>'
        + f'<th style="padding:7px 10px;text-align:left;border-bottom:2px solid #e2e8f0">Shown in</th>'
        + f'<th style="padding:7px 10px;text-align:center;border-bottom:2px solid #e2e8f0">Pause&nbsp;All</th>'
        + f'<th style="padding:7px 10px;text-align:center;border-bottom:2px solid #e2e8f0">In&nbsp;reports</th>'
        + f'<th style="padding:7px 10px;text-align:center;border-bottom:2px solid #e2e8f0">Filtered</th>'
        + f'<th style="padding:7px 10px;text-align:left;border-bottom:2px solid #e2e8f0">Notes</th>'
        + f'</tr></thead><tbody>'
        + _device_type_row("👤 Personal", "Devices", True,  "yes", "Phones, tablets &amp; laptops used by family members (adults or kids) &mdash; the target of Pause All and schedules")
        + _device_type_row("🛡️ Admin", "Devices", False, "yes", "Same filtering as Personal, but never bulk-paused")
        + _device_type_row("💼 Work Device",     "Devices", False, "yes", "Filtered like any device; auto-exempt from the VPN &ldquo;activity drop&rdquo; alert")
        + _device_type_row("🖥️ Infrastructure",  "Infrastructure", False, "skip", "Routers, NAS, printers, servers")
        + _device_type_row("📡 Smart Device",    "Infrastructure", False, "yes", "TVs, cameras, doorbells, thermostats, cars")
        + f'</tbody></table></div>'
        + redetect_controls
        + sortfilter
        + f'<form method="POST" action="/admin/devices/save">{rows_html}'
        + f'<button type="submit" class="btn">Save All Devices</button></form></div>'
        + '<script>'
        + 'var LW_ROLE_DESC={'
        + '"person":"Phones, tablets and laptops used by family members — included in Pause All and schedules.",'
        + '"parent":"Full protection, but never affected by Pause All Personal.",'
        + '"work_device":"Work laptop or phone — filtered normally, but skipped by the VPN activity-drop alert.",'
        + '"infrastructure":"Routers, NAS, printers and servers — shown separately and kept out of reports.",'
        + '"smart_device":"TVs, cameras, speakers, thermostats, vehicles and other connected devices."'
        + '};'
        + 'function lwRoleDesc(s){var d=s.nextElementSibling;if(d&&d.className=="role-desc")d.textContent=LW_ROLE_DESC[s.value]||"";}'
        + 'document.querySelectorAll(\'select[name^="type_"]\').forEach(lwRoleDesc);'
        + '</script>'
        + '</div></body></html>'
    )


def _build_recovery_status(config):
    ntfy_ok = bool(config.get("ntfy_topic", "").strip())
    tg      = config.get("telegram", {})
    tg_ok   = bool(tg.get("bot_token", "").strip() and tg.get("chat_id", "").strip())
    em      = config.get("email", {})
    em_ok   = bool(em.get("smtp_host", "").strip() and em.get("smtp_user", "").strip()
                   and em.get("smtp_password", "").strip() and em.get("to_address", "").strip())

    def _row(label, ok):
        icon  = "&#x2713;" if ok else "&#x2717;"
        color = "#1d9e75"  if ok else "#94a3b8"
        return (f'<div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid #f0f0f0">'
                f'<span style="width:18px;height:18px;border-radius:50%;background:{"#f0fdf4" if ok else "#f8fafc"};'
                f'display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;color:{color}">{icon}</span>'
                f'<span style="font-size:0.85em;color:#334155">{label}</span>'
                f'<span style="margin-left:auto;font-size:0.78em;font-weight:700;color:{color}">{"Configured" if ok else "Not set"}</span>'
                f'</div>')

    any_ok = ntfy_ok or tg_ok or em_ok
    if any_ok:
        note = '<div style="font-size:0.82em;color:#1d9e75;margin-top:10px">&#x2713; Password recovery is available on the login page.</div>'
    else:
        note = ('<div style="font-size:0.82em;color:#e24b4a;background:#fff7f7;border:1px solid #fca5a5;'
                'border-radius:8px;padding:10px;margin-top:10px">&#x26A0; No notification channels configured. '
                'Password recovery will not work until at least one channel is set up above.</div>')

    return (
        '<div class="section" style="margin-top:0">'
        '<h2>Password Recovery</h2>'
        '<div style="font-size:0.82em;color:#64748b;margin-bottom:12px">'
        'When a user clicks "Forgot password?" on the login page, a one-time code is sent via these channels.'
        '</div>'
        + _row("ntfy push notifications", ntfy_ok)
        + _row("Telegram messages", tg_ok)
        + _row("Email (SMTP)", em_ok)
        + note
        + '</div>'
    )


def _build_health_card():
    h = get_router_health()

    def bar(pct, color):
        return (
            f'<div style="flex:1;height:6px;background:#eeeeee;border-radius:3px;overflow:hidden">'
            f'<div style="width:{min(pct,100)}%;height:100%;background:{color};border-radius:3px"></div></div>'
        )

    def pct_color(pct):
        return "#1d9e75" if pct < 70 else ("#e8a000" if pct < 85 else "#e24b4a")

    def stat_col(label, value, color="#2c2c2a"):
        return (
            f'<div>'
            f'<div style="font-size:0.72em;color:#aaa;font-weight:700;text-transform:uppercase;letter-spacing:0.07em">{label}</div>'
            f'<div style="font-size:1em;font-weight:700;color:{color}">{value}</div>'
            f'</div>'
        )

    ram_pct   = h.get("ram_pct", 0)
    disk_pct  = h.get("pct_used", 0)
    load      = h.get("load_1", 0.0)
    ram_color  = pct_color(ram_pct)
    disk_color = pct_color(disk_pct)
    load_color = "#1d9e75" if load < 1.0 else ("#e8a000" if load < 2.0 else "#e24b4a")

    row_style = "display:flex;align-items:center;gap:12px;margin-bottom:10px"
    lbl_style = "width:64px;font-size:0.8em;color:#888;font-weight:700;flex-shrink:0"
    val_style_fn = lambda c: f"width:120px;text-align:right;font-size:0.8em;font-weight:700;color:{c};flex-shrink:0"

    ram_row = (
        f'<div style="{row_style}">'
        f'<span style="{lbl_style}">RAM</span>'
        + bar(ram_pct, ram_color)
        + f'<span style="{val_style_fn(ram_color)}">{h.get("ram_used_mb",0)} / {h.get("ram_total_mb",0)} MB</span>'
        f'</div>'
    )
    disk_row = (
        f'<div style="{row_style}">'
        f'<span style="{lbl_style}">Storage</span>'
        + bar(disk_pct, disk_color)
        + f'<span style="{val_style_fn(disk_color)}">{h.get("used_mb",0)} / {h.get("total_mb",0)} MB</span>'
        f'</div>'
    )
    meta_row = (
        f'<div style="display:flex;gap:20px;flex-wrap:wrap;margin-top:6px;padding-top:12px;border-top:1px solid #f0f0f0">'
        + stat_col("CPU Load", f'{load:.2f}', load_color)
        + stat_col("Uptime", h.get("uptime_str", "—"))
        + stat_col("DB Size", f'{h.get("db_mb", 0)} MB')
        + f'</div>'
    )

    usb_txt   = ("Connected: " + h["usb"]) if h.get("usb") else "Not connected"
    usb_color = "#1d9e75" if h.get("usb") else "#94a3b8"
    usb_col   = stat_col("USB", usb_txt, usb_color)

    adguard_ok    = h.get("adguard_ok", False)
    port_53_owner = h.get("port_53_owner", "unknown")
    chain_ok      = adguard_ok and port_53_owner == "dnsmasq"

    if chain_ok:
        dns_banner = ""
    elif not adguard_ok:
        dns_banner = (
            '<div style="margin-top:12px;padding:10px 14px;background:#fff3cd;border:1px solid #f0c040;'
            'border-radius:6px;font-size:0.85em;color:#7a5c00">'
            '<strong>AdGuard Home not responding on port 3053.</strong> DNS filtering and query logging '
            'may be unavailable. Check Applications &rarr; AdGuard Home in the GL.iNet admin panel.'
            '</div>'
        )
    elif port_53_owner == "adguard":
        dns_banner = (
            '<div style="margin-top:12px;padding:10px 14px;background:#ffe0e0;border:1px solid #e24b4a;'
            'border-radius:6px;font-size:0.85em;color:#7a1010">'
            '<strong>DNS misconfiguration detected.</strong> AdGuard Home is answering on port 53 directly '
            '(&ldquo;Handle Client Requests&rdquo; appears to be ON). Social media blocking is not active. '
            'In the GL.iNet admin panel go to Applications &rarr; AdGuard Home and turn off '
            '&ldquo;Handle Client Requests&rdquo;, then restart the router.'
            '</div>'
        )
    else:
        dns_banner = (
            '<div style="margin-top:12px;padding:10px 14px;background:#fff3cd;border:1px solid #f0c040;'
            'border-radius:6px;font-size:0.85em;color:#7a5c00">'
            '<strong>DNS chain status unknown.</strong> Could not confirm dnsmasq is handling port 53.'
            '</div>'
        )

    return (
        '<div class="section">'
        '<h2>Router Health</h2>'
        + ram_row + disk_row + meta_row.replace('</div>', usb_col + '</div>', 1)
        + dns_banner
        + '</div>'
    )


def build_blocked_services_page(all_svcs, blocked_ids, ss_on, config, saved_msg=""):
    saved_html = f'<div class="success" style="margin:12px 16px 0">{saved_msg}</div>' if saved_msg else ""
    ss_badge = (
        f'<div style="margin:0 16px 4px;padding:10px 14px;background:#e6f4f0;border-radius:8px;'
        f'font-size:0.82em;color:#1d9e75;font-weight:600">'
        f'&#x1F50D; Safe search is <strong>on</strong> — Google, Bing &amp; YouTube return restricted results. '
        f'Change it on the <a href="/social" style="color:#1d9e75;text-decoration:underline">Social page</a>.</div>'
    ) if ss_on else (
        f'<div style="margin:0 16px 4px;padding:10px 14px;background:#f1f5f9;border-radius:8px;'
        f'font-size:0.82em;color:#64748b">'
        f'&#x1F50D; Safe search is <strong>off</strong>. '
        f'Turn it on from the <a href="/social" style="color:#e8a000;font-weight:600">Social page</a>.</div>'
    )

    if not all_svcs:
        body = '<div class="section"><p style="color:#94a3b8;font-size:0.9em">Could not load service list from AdGuard. Check AdGuard is running and credentials are correct.</p></div>'
    else:
        from adguard import (AGH_SERVICE_GROUPS, CATEGORY_PACKS,
                             get_blocked_pack_domains, service_notify_enabled)
        checked_count = len(blocked_ids)
        id_to_name = {s["id"]: s["name"] for s in all_svcs}

        def _checkbox(value, label, cat_idx, field, checked):
            chk = "checked" if checked else ""
            return (
                f'<label style="display:flex;align-items:center;gap:8px;padding:6px 0;cursor:pointer">'
                f'<input type="checkbox" name="{field}" value="{value}" data-cat="{cat_idx}" {chk} '
                f'style="width:16px;height:16px;accent-color:#e8a000;flex-shrink:0">'
                f'<span style="font-size:0.88em;color:#2c2c2a">{label}</span></label>'
            )

        def _cat_block(title, entries, cat_idx, field, notify_on=False, show_notify=False):
            # entries: list of (value, label, checked)
            n_blocked = sum(1 for _, _, c in entries if c)
            rows = "".join(_checkbox(v, lb, cat_idx, field, c) for v, lb, c in entries)
            # The Notify toggle only applies to AdGuard service groups. Category
            # packs are explicit family blocks that always notify, so they get no
            # toggle (and aren't in service_notify, so a toggle wouldn't persist).
            notify_lbl = ""
            if show_notify:
                notify_chk = "checked" if notify_on else ""
                notify_lbl = (
                    f'<label title="Notify me / show on the dashboard when a device tries a blocked service in this group" '
                    f'style="display:flex;align-items:center;gap:4px;font-size:0.72em;color:#64748b;cursor:pointer;white-space:nowrap">'
                    f'<input type="checkbox" name="svcnotify" value="{title}" {notify_chk} '
                    f'style="width:14px;height:14px;accent-color:#e8a000">&#x1F514; Notify</label>'
                )
            return (
                f'<div style="margin-bottom:18px">'
                f'<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;'
                f'border-bottom:2px solid #f0f0ee;padding-bottom:4px;margin-bottom:6px">'
                f'<span style="font-weight:700;color:#2c2c2a;font-size:0.9em">{title} '
                f'<span style="color:#94a3b8;font-weight:500;font-size:0.85em">({n_blocked}/{len(entries)})</span></span>'
                f'<span style="display:flex;gap:10px;align-items:center">'
                f'{notify_lbl}'
                f'<span style="display:flex;gap:6px">'
                f'<button type="button" onclick="catAll({cat_idx},true)" class="btn btn-secondary" style="font-size:0.72em;padding:3px 9px">All</button>'
                f'<button type="button" onclick="catAll({cat_idx},false)" class="btn btn-secondary" style="font-size:0.72em;padding:3px 9px">None</button>'
                f'</span></span></div>'
                f'<div style="columns:2;column-gap:16px">{rows}</div>'
                f'</div>'
            )

        cat_blocks, cat_idx, used = "", 0, set()
        for cat, ids in AGH_SERVICE_GROUPS.items():
            present = [(i, id_to_name[i], i in blocked_ids) for i in ids if i in id_to_name]
            if not present:
                continue
            used.update(i for i, _, _ in present)
            cat_blocks += _cat_block(cat, present, cat_idx, "svc",
                                     service_notify_enabled(cat, config), show_notify=True)
            cat_idx += 1
        # AGH services not in any named group
        leftovers = [(s["id"], s["name"], s["id"] in blocked_ids) for s in all_svcs if s["id"] not in used]
        if leftovers:
            cat_blocks += _cat_block("Other", leftovers, cat_idx, "svc",
                                     service_notify_enabled("Other", config), show_notify=True)
            cat_idx += 1

        # Curated packs — individual sites, so a parent can block ChatGPT but keep
        # Claude. A site's checkbox value is its domain(s), comma-joined.
        blocked_pack = set(get_blocked_pack_domains(config))
        pack_blocks = ""
        for name, meta in CATEGORY_PACKS.items():
            entries = [
                (",".join(doms), label, all(d in blocked_pack for d in doms))
                for label, doms in meta["sites"]
            ]
            pack_blocks += _cat_block(name, entries, cat_idx, "packdom")
            cat_idx += 1

        body = (
            f'<div class="section">'
            f'<h2>Blocked Services</h2>'
            f'<div style="font-size:0.82em;color:#64748b;margin-bottom:12px">'
            f'Block whole categories or individual services for <strong>everyone</strong> on the network. '
            f'{checked_count} of {len(all_svcs)} services currently blocked. '
            f'Per-device overrides are on each device\'s Schedule page.</div>'
            f'<div style="display:flex;gap:8px;margin-bottom:16px">'
            f'<button type="button" onclick="allToggle(true)" class="btn btn-secondary" style="font-size:0.8em;padding:6px 12px">Block All</button>'
            f'<button type="button" onclick="allToggle(false)" class="btn btn-secondary" style="font-size:0.8em;padding:6px 12px">Unblock All</button>'
            f'</div>'
            f'<form method="POST" action="/blocked-services/save" id="svc-form">'
            f'{cat_blocks}'
            f'<div style="margin-top:22px;border-top:2px solid #f0f0ee;padding-top:14px;margin-bottom:14px">'
            f'<div style="font-weight:700;color:#2c2c2a;font-size:0.95em;margin-bottom:2px">Extra Categories</div>'
            f'<div style="font-size:0.8em;color:#64748b">'
            f'Curated by Lantern Watch &mdash; these cover sites AdGuard\'s built-in list doesn\'t, like AI tools and crypto. '
            f'Tick individual sites, or use All / None. A new block takes a few seconds to apply. '
            f'<span style="color:#1d9e75;font-weight:600">&#x1F514; Anything you tick here will notify you when it\'s attempted.</span></div>'
            f'</div>'
            f'{pack_blocks}'
            f'<div style="margin-top:18px">'
            f'<button type="submit" class="btn">Save</button>'
            f'</div>'
            f'</form>'
            f'<script>'
            f'function catAll(c,v){{document.querySelectorAll(\'#svc-form input[data-cat="\'+c+\'"]\').forEach(function(cb){{cb.checked=v;}});}}'
            f'function allToggle(v){{document.querySelectorAll(\'#svc-form input[name=svc]\').forEach(function(cb){{cb.checked=v;}});}}'
            f'</script>'
            f'</div>'
        )

    # Block a Specific Site — user-added custom domain blocks.
    from adguard import get_custom_blocks
    try:
        _custom = get_custom_blocks(config)
    except Exception:
        _custom = []
    _rows = ""
    for _d in _custom:
        _rows += (
            f'<div style="display:flex;align-items:center;justify-content:space-between;'
            f'padding:8px 0;border-bottom:1px solid #f5f5f5">'
            f'<span style="font-size:0.9em;color:#2c2c2a">&#x1F6AB; {_d}</span>'
            f'<form method="POST" action="/blocked-services/custom/remove" style="margin:0">'
            f'<input type="hidden" name="domain" value="{_d}">'
            f'<button type="submit" class="btn btn-secondary" style="font-size:0.75em;padding:4px 12px">Remove</button>'
            f'</form></div>'
        )
    if not _rows:
        _rows = '<div style="font-size:0.85em;color:#94a3b8;padding:6px 0">No custom sites blocked yet.</div>'
    custom_section = (
        f'<div class="section">'
        f'<h2>Block a Specific Site</h2>'
        f'<div style="font-size:0.82em;color:#64748b;margin-bottom:12px">'
        f'Block any website by domain for <strong>everyone</strong> on the network &mdash; e.g. '
        f'<code>example.com</code>. Paste a full URL if you like; subdomains (like www.) are included automatically. '
        f'A new block takes a few seconds to apply. To edit one, remove it and add the corrected version. '
        f'<span style="color:#1d9e75;font-weight:600">&#x1F514; Sites you block here will notify you when they\'re attempted.</span></div>'
        f'<form method="POST" action="/blocked-services/custom/add" style="margin-bottom:16px">'
        f'<input type="text" name="domain" placeholder="example.com  or  https://www.example.com" required '
        f'style="width:100%;margin-bottom:10px">'
        f'<button type="submit" class="btn">Block</button></form>'
        f'<div style="font-weight:700;color:#475569;font-size:0.85em;margin-bottom:6px">Custom blocks ({len(_custom)})</div>'
        f'{_rows}'
        f'</div>'
    )

    return (
        f'<!DOCTYPE html><html><head><link rel="icon" type="image/svg+xml" href="/favicon.svg"><meta charset="UTF-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1.0">'
        f'<title>Blocked Services - Lantern Watch</title><style>{CSS}</style></head><body>'
        + build_header("Blocked Services", config=config)
        + f'<div class="page-wrap">'
        + saved_html
        + ss_badge
        + body
        + custom_section
        + f'</div></body></html>'
    )


def _blocklist_manager_html(config):
    """Settings -> DNS Blocklists: per-list on/off toggles with rule counts and a
    live 'rule budget' meter, so a low-RAM router owner can trim weight."""
    try:
        from adguard import get_all_filter_lists
        lists = get_all_filter_lists(config)
    except Exception:
        lists = []
    if not lists:
        return ('<div class="section"><h2>DNS Blocklists</h2><div class="form-card">'
                '<div style="color:#94a3b8;font-size:0.9em">Could not load blocklists from AdGuard.</div>'
                '</div></div>')
    total = sum(l["rules_count"] for l in lists if l["enabled"])

    def band(n):
        if n < 400000:
            return ("#1d9e75", "Light &mdash; comfortable on any supported router")
        if n < 600000:
            return ("#e8a000", "Moderate &mdash; fine on 512&nbsp;MB+ routers")
        return ("#DC6B5F", "Heavy &mdash; best on 1&nbsp;GB routers; may strain 256&nbsp;MB models")
    bcol, blab = band(total)

    note = {
        "Security":         ("#DC6B5F", "Phishing, malware, scam &amp; stalkerware. Best kept on &mdash; turning these off to save memory trades away real protection."),
        "Family & Content": ("#1d9e75", "Adult content, gambling, dating."),
        "Ads & Tracking":   ("#64748b", "Ads, trackers, telemetry, smart-TV."),
        "Other":            ("#64748b", "Additional lists."),
    }
    from collections import OrderedDict
    groups = OrderedDict()
    for l in lists:
        groups.setdefault(l["category"], []).append(l)
    rows = ""
    for cat, items in groups.items():
        col, txt = note.get(cat, ("#64748b", ""))
        rows += (f'<div style="margin-top:14px;margin-bottom:2px">'
                 f'<span style="font-weight:700;color:{col};font-size:0.9em">{cat}</span>'
                 f'<div style="font-size:0.74em;color:#94a3b8">{txt}</div></div>')
        for l in items:
            u   = l["url"].replace("&", "&amp;").replace('"', "&quot;")
            chk = "checked" if l["enabled"] else ""
            rows += (f'<label style="display:flex;align-items:center;gap:8px;padding:6px 0;cursor:pointer">'
                     f'<input type="checkbox" name="list" value="{u}" data-rules="{l["rules_count"]}" {chk} '
                     f'class="bl-toggle" style="width:16px;height:16px;accent-color:#e8a000;flex-shrink:0">'
                     f'<span style="flex:1;font-size:0.88em;color:#2c2c2a">{l["name"]}</span>'
                     f'<span style="font-size:0.78em;color:#94a3b8;white-space:nowrap">{l["rules_count"]:,}</span></label>')

    meter = (f'<div class="form-card" style="margin-bottom:10px">'
             f'<div style="display:flex;justify-content:space-between;align-items:baseline">'
             f'<span style="font-weight:700;color:#2c2c2a">Active rules</span>'
             f'<span id="bl-total" style="font-weight:800;color:{bcol}">{total:,}</span></div>'
             f'<div id="bl-band" style="font-size:0.78em;color:{bcol};margin-top:2px">{blab}</div></div>')

    js = ("<script>(function(){"
          "function upd(){var t=0;document.querySelectorAll('.bl-toggle:checked').forEach(function(c){t+=parseInt(c.dataset.rules||0);});"
          "var col,lab;"
          "if(t<400000){col='#1d9e75';lab='Light \\u2014 comfortable on any supported router';}"
          "else if(t<600000){col='#e8a000';lab='Moderate \\u2014 fine on 512\\u00a0MB+ routers';}"
          "else{col='#DC6B5F';lab='Heavy \\u2014 best on 1\\u00a0GB routers; may strain 256\\u00a0MB models';}"
          "var tt=document.getElementById('bl-total');tt.textContent=t.toLocaleString();tt.style.color=col;"
          "var bb=document.getElementById('bl-band');bb.textContent=lab;bb.style.color=col;}"
          "document.querySelectorAll('.bl-toggle').forEach(function(c){c.addEventListener('change',upd);});"
          "})();</script>")

    return (f'<div class="section"><h2>DNS Blocklists</h2>'
            f'<div class="form-card" style="margin-bottom:10px"><div style="font-size:0.82em;color:#64748b">'
            f'Turn lists on or off to balance protection against router load &mdash; more rules block more, but use more memory and a little speed. '
            f'Changes apply in a few seconds. The number on the right is each list\'s rule count. '
            f'Lists refresh automatically every day.</div>'
            f'<form method="POST" action="/admin/blocklists/refresh" style="margin-top:10px">'
            f'<button type="submit" class="btn btn-secondary" style="width:auto;padding:6px 14px;font-size:0.8em;margin-bottom:0">Check for updates now</button>'
            f'</form></div>'
            f'{meter}'
            f'<form method="POST" action="/admin/blocklists/save">'
            f'<div class="form-card">{rows}'
            f'<button type="submit" style="width:100%;margin-top:16px;padding:12px;background:#e8a000;border:none;'
            f'border-radius:8px;color:white;font-weight:700;cursor:pointer">Save Blocklist Selection</button>'
            f'</div></form>{js}</div>')


def _backup_restore_html(config):
    """Settings card: download a full backup file and restore from one. This is
    the safety net for a factory reset / firmware flash that wipes the router —
    the whole setup (device names, profiles, filtering, schedules, channels)
    rides in one file the family keeps on their computer."""
    return (
        '<div class="section"><h2>Backup &amp; Restore</h2>'
        '<div class="form-card">'
        '<div style="font-size:0.85em;color:#64748b;line-height:1.7;margin-bottom:12px">'
        'Save your entire setup &mdash; device names, roles, schedules, filtering, '
        'safe-search, blocked sites, and notification channels &mdash; to a single '
        'file. If a router update or factory reset ever wipes things, reinstall '
        'Lantern Watch and restore this file to bring it all back.'
        '</div>'
        '<label style="display:flex;align-items:center;gap:8px;margin-bottom:10px;cursor:pointer;font-size:0.82em;color:#64748b">'
        '<input type="checkbox" id="bk-logs" style="width:16px;height:16px;accent-color:#D97706"> '
        'Also include activity history (larger file)</label>'
        '<button onclick="lwDownloadBackup()" class="btn" style="margin-bottom:6px">&#x2B07;&#xFE0F; Download Backup</button>'
        '<div style="font-size:0.75em;color:#94a3b8;margin-bottom:16px">'
        'The file contains your passwords and notification keys &mdash; keep it somewhere safe.'
        '</div>'
        + _usb_backup_panel_html()
        + '<div class="form-label" style="border-top:1px solid #eee;padding-top:14px">Restore from a backup</div>'
        '<div style="font-size:0.78em;color:#94a3b8;margin:6px 0 10px">'
        'Choose a backup file to replace your current settings with the ones it holds.'
        '</div>'
        '<input type="file" id="bk-file" accept=".json,application/json" '
        'onchange="lwFileName(this)" style="position:absolute;width:1px;height:1px;opacity:0;overflow:hidden">'
        '<label for="bk-file" class="btn btn-secondary" style="margin-bottom:6px">&#x1F4C1; Choose backup file&hellip;</label>'
        '<div id="bk-fname" style="font-size:0.8em;color:#64748b;margin-bottom:10px;text-align:center;min-height:1em"></div>'
        '<button onclick="lwRestore()" class="btn">&#x267B;&#xFE0F; Restore from Backup</button>'
        '<div id="bk-result" style="display:none;margin-top:10px;padding:10px 14px;border-radius:8px;font-size:0.85em;font-weight:600"></div>'
        '</div></div>'
        '<script>'
        'function lwDownloadBackup(){'
        '  var logs=document.getElementById("bk-logs").checked;'
        '  window.location="/admin/backup/download"+(logs?"?logs=1":"");'
        '}'
        'function lwRestore(){'
        '  var f=document.getElementById("bk-file");'
        '  if(!f.files||!f.files.length){alert("Choose a backup file first.");return;}'
        '  if(!confirm("Restore this backup? It overwrites your current settings, device names, and filtering with the ones in the file.")) return;'
        '  var out=document.getElementById("bk-result");'
        '  var reader=new FileReader();'
        '  reader.onload=function(){'
        '    out.style.cssText="display:block;background:#fffbf0;border:1px solid #e8d080;color:#e8a000;padding:10px 14px;border-radius:8px;font-size:0.85em;font-weight:600";'
        '    out.textContent="Restoring\\u2026 rebuilding your settings and filtering.";'
        '    fetch("/admin/backup/restore",{method:"POST",body:reader.result}).then(function(r){return r.json();}).then(function(d){'
        '      if(d.ok){'
        '        out.style.cssText="display:block;background:#f0fdf4;border:1px solid #86efac;color:#16a34a;padding:10px 14px;border-radius:8px;font-size:0.85em;font-weight:600";'
        '        var w=(d.warnings&&d.warnings.length)?" A few items need a look: "+d.warnings.join(" "):"";'
        '        out.textContent="\\u2705 Restored! Reloading\\u2026"+w;'
        '        setTimeout(function(){location.href="/admin";},2500);'
        '      }else{'
        '        out.style.cssText="display:block;background:#fff7f7;border:1px solid #fca5a5;color:#e24b4a;padding:10px 14px;border-radius:8px;font-size:0.85em;font-weight:600";'
        '        out.textContent="Could not restore: "+(d.error||"unknown error");'
        '      }'
        '    }).catch(function(){'
        '      out.style.cssText="display:block;background:#fff7f7;border:1px solid #fca5a5;color:#e24b4a;padding:10px 14px;border-radius:8px;font-size:0.85em;font-weight:600";'
        '      out.textContent="Connection error during restore.";'
        '    });'
        '  };'
        '  reader.readAsText(f.files[0]);'
        '}'
        'function lwFileName(inp){'
        '  var el=document.getElementById("bk-fname");'
        '  el.textContent=(inp.files&&inp.files.length)?("Selected: "+inp.files[0].name):"";'
        '}'
        'function lwUsbMsg(kind,text){'
        '  var out=document.getElementById("usb-result");if(!out)return;'
        '  var c={info:"#fffbf0;border:1px solid #e8d080;color:#e8a000",ok:"#f0fdf4;border:1px solid #86efac;color:#16a34a",err:"#fff7f7;border:1px solid #fca5a5;color:#e24b4a"}[kind];'
        '  out.style.cssText="display:block;background:"+c+";padding:10px 14px;border-radius:8px;font-size:0.85em;font-weight:600;margin-top:10px";'
        '  out.textContent=text;'
        '}'
        'function lwUsbBackup(){'
        '  lwUsbMsg("info","Saving to USB drive\\u2026");'
        '  fetch("/admin/backup/usb",{method:"POST"}).then(function(r){return r.json();}).then(function(d){'
        '    if(d.written){lwUsbMsg("ok","\\u2705 Saved to the USB drive. Reloading\\u2026");setTimeout(function(){location.href="/admin";},1500);}'
        '    else if(d.reason==="no-usb"){lwUsbMsg("err","No USB drive detected. Plug one into the router and try again.");}'
        '    else if(d.reason==="unchanged"){lwUsbMsg("ok","\\u2705 The USB drive is already up to date.");}'
        '    else{lwUsbMsg("err","Could not save to USB: "+(d.reason||"unknown"));}'
        '  }).catch(function(){lwUsbMsg("err","Connection error saving to USB.");});'
        '}'
        'function lwUsbRestore(){'
        '  if(!confirm("Restore from the USB drive? It overwrites your current settings with the backup saved on the drive.")) return;'
        '  lwUsbMsg("info","Restoring from USB\\u2026 rebuilding your settings and filtering.");'
        '  fetch("/admin/backup/usb/restore",{method:"POST"}).then(function(r){return r.json();}).then(function(d){'
        '    if(d.ok){var w=(d.warnings&&d.warnings.length)?" A few items need a look: "+d.warnings.join(" "):"";lwUsbMsg("ok","\\u2705 Restored from USB! Reloading\\u2026"+w);setTimeout(function(){location.href="/admin";},2500);}'
        '    else{lwUsbMsg("err","Could not restore: "+(d.error||"unknown error"));}'
        '  }).catch(function(){lwUsbMsg("err","Connection error during restore.");});'
        '}'
        'function lwUsbEject(){'
        '  if(!confirm("Safely eject the USB drive? Auto-backup pauses until you unplug and reinsert it (or reboot the router).")) return;'
        '  lwUsbMsg("info","Flushing writes and ejecting\\u2026");'
        '  fetch("/admin/backup/usb/eject",{method:"POST"}).then(function(r){return r.json();}).then(function(d){'
        '    if(d.ok){lwUsbMsg("ok","\\u2705 Safe to remove \\u2014 you can unplug the drive now. Reloading\\u2026");setTimeout(function(){location.href="/admin";},1800);}'
        '    else{lwUsbMsg("err","Could not eject: "+(d.error||"unknown error"));}'
        '  }).catch(function(){lwUsbMsg("err","Connection error during eject.");});'
        '}'
        '</script>'
    )


def _usb_backup_panel_html():
    """The USB drive sub-panel inside the Backup & Restore card. Shows live drive
    status and auto-backup info, or a gentle hint to plug a drive in."""
    try:
        import backup as _bk
        st = _bk.usb_status()
    except Exception:
        st = {"present": False}
    if st.get("ejected"):
        return (
            '<div style="border-top:1px solid #eee;padding-top:14px;margin-bottom:16px">'
            '<div class="form-label">USB auto-backup</div>'
            '<div style="margin-top:8px;padding:12px 14px;background:#fffbf0;border:1px solid #e8d080;border-radius:10px">'
            '<div style="font-weight:700;color:#b7791f;font-size:0.9em">&#x23CF;&#xFE0F; Drive ejected &mdash; safe to unplug</div>'
            '<div style="font-size:0.8em;color:#8a6d1f;margin-top:4px">Auto-backup is paused. Reinsert the drive (or reboot the router) and it resumes on its own.</div>'
            '</div>'
            '<button onclick="lwUsbBackup()" class="btn btn-secondary" style="margin-top:10px;margin-bottom:0">Reconnect &amp; back up now</button>'
            '<div id="usb-result" style="display:none"></div>'
            '</div>'
        )
    if not st.get("present"):
        return (
            '<div style="border-top:1px solid #eee;padding-top:14px;margin-bottom:16px">'
            '<div class="form-label">USB auto-backup</div>'
            '<div style="font-size:0.78em;color:#94a3b8;margin-top:6px">'
            '&#x1F50C; Plug a USB drive into the router and Lantern Watch will '
            'automatically save your setup to it whenever you change a setting &mdash; '
            'a hands-off safety net that survives even a factory reset. '
            '(We only add a <b>LanternWatch</b> folder; your other files are untouched.)'
            '</div></div>'
        )
    free = st.get("free_mb")
    free_txt = (f"{free/1024:.0f} GB free" if free and free >= 1024
                else (f"{free} MB free" if free else ""))
    last = st.get("last_backup")
    if last:
        last = last.replace("T", " ")
        detail = (f'Last auto-backup: <b>{last}</b>'
                  f' &middot; v{st.get("app_version","?")}'
                  f' &middot; {st.get("devices",0)} device'
                  f'{"s" if st.get("devices",0)!=1 else ""}')
    else:
        detail = 'No backup on this drive yet &mdash; click &ldquo;Back up to USB now.&rdquo;'
    return (
        '<div style="border-top:1px solid #eee;padding-top:14px;margin-bottom:16px">'
        '<div class="form-label">USB auto-backup</div>'
        '<div style="margin-top:8px;padding:12px 14px;background:#f0fdf4;border:1px solid #86efac;border-radius:10px">'
        '<div style="font-weight:700;color:#16a34a;font-size:0.9em">&#x2705; USB drive connected'
        + (f' &middot; {free_txt}' if free_txt else '') + '</div>'
        '<div style="font-size:0.8em;color:#166534;margin-top:4px">' + detail + '</div>'
        '<div style="font-size:0.75em;color:#4b8b5f;margin-top:6px">Your setup is saved here automatically whenever you change a setting.</div>'
        '</div>'
        '<div style="display:flex;gap:8px;margin-top:10px">'
        '<button onclick="lwUsbBackup()" class="btn btn-secondary" style="flex:1;margin-bottom:0">Back up to USB now</button>'
        '<button onclick="lwUsbRestore()" class="btn btn-secondary" style="flex:1;margin-bottom:0">Restore from USB</button>'
        '</div>'
        '<button onclick="lwUsbEject()" class="btn btn-secondary" style="margin-top:8px;margin-bottom:0">&#x23CF;&#xFE0F; Safely eject</button>'
        '<div id="usb-result" style="display:none"></div>'
        '</div>'
    )


def build_admin(config, saved=False, cleared=False, cleared_all=False,
                confirm_clear=False, confirm_clear_all=False,
                adguard_applied=False, adguard_apply_error="", refreshed=False):
    ag         = config.get("adguard", {})
    retention_days = int(config.get("retention_days", 60))
    retention_opts = "".join(
        f'<label class="radio-row"><input type="radio" name="retention_days" value="{d}" {"checked" if retention_days == d else ""}> {l}</label>'
        for d, l in [(7, "7 days"), (14, "14 days"), (30, "30 days"), (60, "60 days (recommended)"), (90, "90 days")]
    )
    portal_on  = "checked" if config.get("captive_portal") else ""
    doh_on     = "checked" if config.get("doh_blocking") else ""
    tel_on     = "checked" if config.get("telemetry_enabled") else ""
    acked_count = len(config.get("captive_portal_acked", []))

    saved_msg   = '<div class="success">Settings saved!</div>'       if saved       else ""
    refreshed_msg = '<div class="success">Checking for blocklist updates &mdash; any new rules apply in a moment.</div>' if refreshed else ""
    cleared_msg = '<div class="success">Traffic data cleared!</div>' if cleared     else (
                  '<div class="success">All data cleared!</div>'     if cleared_all else "")
    if adguard_applied:
        adguard_msg = '<div class="success">&#x2705; Family protection applied successfully!</div>'
    elif adguard_apply_error:
        adguard_msg = f'<div style="margin-bottom:12px;padding:12px 16px;background:#FFF7F7;border:1px solid #FCA5A5;border-radius:10px;color:#DC6B5F;font-weight:600">Could not apply protection settings: {adguard_apply_error}</div>'
    else:
        adguard_msg = ""
    tested_msg = ""
    confirm_msg = ""
    if confirm_clear:
        confirm_msg = (
            '<div style="margin-bottom:12px;padding:16px;background:#FFF7F7;border:2px solid #FCA5A5;border-radius:12px">'
            '<div style="color:#DC6B5F;font-weight:700;margin-bottom:8px">Clear traffic data?</div>'
            '<div style="color:#DC6B5F;font-size:0.85em;margin-bottom:12px">Deletes all DNS query history. Device names, schedules, and roles are kept.</div>'
            '<form method="POST" action="/admin/clear">'
            '<button type="submit" style="width:100%;padding:12px;background:#DC6B5F;border:none;border-radius:8px;color:white;font-weight:700;cursor:pointer">Yes, Clear Traffic Data</button>'
            '</form>'
            '<a href="/admin" style="display:block;text-align:center;margin-top:8px;color:#94a3b8;font-size:0.85em">Cancel</a></div>'
        )
    elif confirm_clear_all:
        confirm_msg = (
            '<div style="margin-bottom:12px;padding:16px;background:#FFF7F7;border:2px solid #FCA5A5;border-radius:12px">'
            '<div style="color:#DC6B5F;font-weight:700;margin-bottom:8px">Clear everything?</div>'
            '<div style="color:#DC6B5F;font-size:0.85em;margin-bottom:12px">Deletes query history <strong>and</strong> removes all custom device names, roles, and schedules. This cannot be undone.</div>'
            '<form method="POST" action="/admin/clear_all">'
            '<button type="submit" style="width:100%;padding:12px;background:#DC6B5F;border:none;border-radius:8px;color:white;font-weight:700;cursor:pointer">Yes, Clear Everything</button>'
            '</form>'
            '<a href="/admin" style="display:block;text-align:center;margin-top:8px;color:#94a3b8;font-size:0.85em">Cancel</a></div>'
        )

    return (
        f'<!DOCTYPE html><html><head><link rel="icon" type="image/svg+xml" href="/favicon.svg"><meta charset="UTF-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1.0">'
        f'<title>Settings - Lantern Watch</title><style>{CSS}</style></head><body>'
        + build_header("Settings", config=config)
        + '<div class="page-wrap">'
        +''
        + f'{saved_msg}{refreshed_msg}{tested_msg}{cleared_msg}{confirm_msg}{adguard_msg}'
        + f'<div class="section"><h2>Software</h2>'
        + f'<div class="form-card" style="display:flex;align-items:center;gap:12px">'
        + f'<div style="flex:1"><div style="font-weight:700;color:#2c2c2a">Lantern Watch</div>'
        + f'<div style="font-size:0.82em;color:#94a3b8;margin-top:2px">Version {VERSION}</div></div>'
        + f'<button onclick="checkUpdate()" id="upd-btn" class="btn btn-secondary" style="width:auto;padding:8px 18px;font-size:0.85em;margin-bottom:0">Check for Updates</button>'
        + f'</div>'
        + f'<div id="upd-result" style="display:none;margin-top:8px;padding:10px 14px;border-radius:8px;font-size:0.85em;font-weight:600"></div>'
        + f'<div style="font-size:0.75em;color:#94a3b8;margin-top:10px">Checks GitHub for the latest release. No data is sent — your router just reads the public version list.</div>'
        + f'</div>'
        + '<script>'
        + 'function checkUpdate(){'
        + '  var btn=document.getElementById("upd-btn"),res=document.getElementById("upd-result");'
        + '  btn.textContent="Checking…";btn.disabled=true;'
        + '  fetch("/admin/check-update").then(function(r){return r.json();}).then(function(d){'
        + '    res.style.display="block";'
        + '    if(d.update_available){'
        + '      res.style.cssText="display:block;background:#fffbf0;border:1px solid #e8d080;color:#e8a000;padding:12px 14px;border-radius:8px;font-size:0.85em;font-weight:600";'
        + "      res.innerHTML=\"Version \"+d.latest_version+\" is available. Your device names &amp; settings are kept.<br><button onclick='updateNow()' style='margin-top:8px;padding:8px 20px;background:#e8a000;border:none;border-radius:8px;color:white;font-weight:700;cursor:pointer'>Update Now</button> <a href='\"+(d.update_url||\"https://github.com/LanternWatchApp/lantern-watch\")+\"' target='_blank' rel='noopener' style='color:#94a3b8;font-size:0.85em;margin-left:8px'>view on GitHub</a>\";"
        + '    } else if(d.ok===false){'
        + '      res.style.cssText="display:block;background:#fff7f7;border:1px solid #fca5a5;color:#e24b4a;padding:10px 14px;border-radius:8px;font-size:0.85em;font-weight:600";'
        + '      res.textContent="Could not check: "+(d.error||"unknown error");'
        + '    } else {'
        + '      res.style.cssText="display:block;background:#f0fdf4;border:1px solid #86efac;color:#16a34a;padding:10px 14px;border-radius:8px;font-size:0.85em;font-weight:600";'
        + '      res.textContent="✅ You\'re up to date! (v"+(d.current_version||d.latest_version)+")";'
        + '    }'
        + '    btn.textContent="Check for Updates";btn.disabled=false;'
        + '  }).catch(function(){'
        + '    res.style.cssText="display:block;background:#fff7f7;border:1px solid #fca5a5;color:#e24b4a;padding:10px 14px;border-radius:8px;font-size:0.85em;font-weight:600";'
        + '    res.textContent="Connection error — check that the router has internet access.";'
        + '    btn.textContent="Check for Updates";btn.disabled=false;'
        + '  });'
        + '}'
        + 'function updateNow(){'
        + '  if(!confirm("Update Lantern Watch now? The dashboard restarts in about 30-45 seconds. Your device names and settings are kept.")) return;'
        + '  var res=document.getElementById("upd-result");'
        + '  fetch("/admin/update",{method:"POST"}).catch(function(){});'
        + '  res.style.cssText="display:block;background:#fffbf0;border:1px solid #e8d080;color:#e8a000;padding:12px 14px;border-radius:8px;font-size:0.85em;font-weight:600";'
        + '  res.innerHTML="Updating\\u2026 downloading and installing the new version. The dashboard will restart shortly \\u2014 this page reloads automatically in about 45 seconds.";'
        + '  setTimeout(function(){ location.reload(); }, 45000);'
        + '}'
        # Arriving from the dashboard "Update" banner (/admin?update=1): auto-run
        # the check so the "Update Now" button is shown straight away — no need to
        # hunt for "Check for Updates" first.
        + 'if(location.search.indexOf("update=1")>-1){try{document.getElementById("upd-btn").scrollIntoView({behavior:"smooth",block:"center"});}catch(e){}checkUpdate();}'
        + '</script>'
        + _backup_restore_html(config)
        + build_security_checklist_card(compute_safety_score(config))
        + _build_health_card()
        + _blocklist_manager_html(config)
        + f'<div class="section"><h2>Settings</h2><form method="POST" action="/admin/save" id="admin-settings">'
        # Login
        + f'<div class="form-card"><div class="form-label">Lantern Watch Username</div>'
        + f'<input type="text" name="lw_username" value="{config.get("lw_username", "admin")}">'
        + f'<div class="form-label" style="margin-top:12px">Lantern Watch Password</div>'
        + f'<input type="password" name="lw_password" id="lw_password" placeholder="Leave blank to keep current password">'
        + f'<label style="display:flex;align-items:center;gap:8px;margin-top:8px;cursor:pointer;font-size:0.82em;color:#64748b">'
        + f'<input type="checkbox" onchange="togglePwd(\'lw_password\',this)" style="width:16px;height:16px;accent-color:#D97706"> Show password</label></div>'
        # AdGuard
        + f'<div class="form-card"><div class="form-label">AdGuard URL</div>'
        + f'<input type="text" name="ag_url" value="{ag.get("url", "http://127.0.0.1:3000")}">'
        + f'<div class="form-label" style="margin-top:12px">AdGuard Username</div>'
        + f'<input type="text" name="ag_username" value="{ag.get("username", "")}">'
        + f'<div class="form-label" style="margin-top:12px">AdGuard Password</div>'
        + f'<input type="password" name="ag_password" id="ag_password" value="{ag.get("password", "")}">'
        + f'<label style="display:flex;align-items:center;gap:8px;margin-top:8px;cursor:pointer;font-size:0.82em;color:#64748b">'
        + f'<input type="checkbox" onchange="togglePwd(\'ag_password\',this)" style="width:16px;height:16px;accent-color:#D97706"> Show password</label>'
        + ('' if config.get('adguard_setup_complete') else
           '<div style="margin-top:14px;padding:12px 14px;background:#fffbf0;border:1px solid #e8d080;border-radius:8px;display:flex;align-items:center;gap:12px">'
           '<span style="font-size:1.2em">&#x26A0;&#xFE0F;</span>'
           '<div style="flex:1">'
           '<div style="font-size:0.85em;font-weight:700;color:#e8a000">Recommended protection settings haven\'t been applied.</div>'
           '<div style="font-size:0.78em;color:#64748b;margin-top:2px">Adult content filters, malware blocking, and safe search are not active.</div>'
           '</div>'
           '<a href="/admin/adguard/apply" style="background:#e8a000;color:white;padding:6px 14px;border-radius:20px;font-size:0.82em;font-weight:700;white-space:nowrap;text-decoration:none">Apply Now</a>'
           '</div>')
        + f'</div>'
        # Safe Search is configured on the Social page (/social) — not duplicated here.
        # Notification channels, alert types & summaries now live on the
        # Notifications page (/notifications) — configured + tested there.
        + f'<div class="form-card"><div class="form-label">Query History</div>'
        + f'<div style="color:#94a3b8;font-size:0.78em;margin-bottom:8px">How many days of DNS traffic <b>Lantern Watch</b> keeps in its own history &mdash; separate from AdGuard Home&rsquo;s internal log, which is kept short on this router to save memory. Older records are deleted automatically each day. If storage exceeds 80%, history is trimmed to 7 days.</div>'
        + f'<div style="padding-left:4px">{retention_opts}</div></div>'
        + f'<div class="form-card"><div class="form-label">Network Notice (Captive Portal)</div>'
        + f'<div style="color:#94a3b8;font-size:0.78em;margin-bottom:10px">When enabled, new devices see a one-time acknowledgment page before browsing. Recommended for organizations, churches, or any shared network. Not needed for personal family use.</div>'
        + f'<label class="toggle-row"><input type="checkbox" name="captive_portal" {"checked" if portal_on else ""}>'
        + f'<span>Show network notice to new devices</span></label></div>'
        + f'<div class="form-card" id="doh-setting" style="scroll-margin-top:70px"><div class="form-label">Strict Encrypted-DNS Enforcement</div>'
        + f'<div style="color:#94a3b8;font-size:0.78em;margin-bottom:10px">Lantern Watch already keeps browsers on your filter automatically &mdash; it tells Firefox to switch off its own encrypted DNS and blocks the common DoH providers, with no setup and no breakage. Turn this on for <b>strict enforcement</b>: it additionally blocks DoT (port 853) and known encrypted-DNS resolver IPs, closing more bypass routes for a determined, tech-savvy user. This one can interrupt some apps or smart TVs that insist on their own DNS, so leave it off for typical family use and enable it only if you suspect someone is getting around the filter.</div>'
        + f'<label class="toggle-row"><input type="checkbox" name="doh_blocking" {doh_on}>'
        + f'<span>Strict mode &mdash; also block DoT &amp; resolver IPs</span></label></div>'
        + f'<div class="form-card"><div class="form-label">Share Anonymous Usage Stats</div>'
        + f'<div style="color:#94a3b8;font-size:0.78em;margin-bottom:10px">Optional &amp; off by default. When on, your router sends one anonymous daily ping to help improve Lantern Watch &mdash; a random ID, the version, your router model, and which features are enabled (plus a device <b>count</b>). <b>Never</b> device names, domains, IP/MAC addresses, or any browsing data. You can turn it off anytime.</div>'
        + f'<label class="toggle-row"><input type="checkbox" name="telemetry_enabled" {tel_on}>'
        + f'<span>Share anonymous usage stats</span></label></div>'
        + f'<button type="submit" class="btn">Save Settings</button></form>'
        + '<div class="form-card" style="margin-top:14px"><div class="form-label">Data &amp; Reset</div>'
        + '<div style="font-size:0.8em;color:#64748b;line-height:1.7">'
        + '&#x1F5D1;&#xFE0F; <b>Clear Traffic Data</b> &mdash; deletes only the DNS query history (the log &amp; stats). Device names, schedules, and all filtering stay.<br>'
        + '&#x1F5D1;&#xFE0F; <b>Clear All Data</b> &mdash; deletes the query history <b>and</b> all device names, roles &amp; schedules. Your filtering and password are kept.<br>'
        + '<span style="color:#94a3b8">Want a full factory reset (wipe filtering and start completely fresh)? Use your GL.iNet router&rsquo;s built-in factory reset, then reinstall Lantern Watch.</span>'
        + '</div></div>'
        + '<div style="display:flex;gap:8px;margin-top:8px">'
        + '<a href="/admin/clear" class="btn btn-danger" style="flex:1;text-align:center">Clear Traffic Data</a>'
        + '<a href="/admin/clear_all" class="btn btn-danger" style="flex:1;text-align:center">Clear All Data</a>'
        + '</div>'
        + (
            f'<form method="POST" action="/admin/portal/clear_acks" style="margin-top:8px">'
            f'<button type="submit" class="btn btn-secondary" style="width:100%">'
            f'Reset Network Notice — {acked_count} device{"s" if acked_count != 1 else ""} acknowledged</button>'
            f'</form>'
            if config.get("captive_portal") else ""
        )
        + '<script>function togglePwd(id,cb){var el=document.getElementById(id);if(el)el.type=cb.checked?"text":"password";}</script>'
        + f'</div>'
        + _build_recovery_status(config)
        # Demo Mode lives at the very bottom (white, low-key). The form= attribute
        # ties it to the Settings form above so it still saves with the rest.
        + f'<div class="section"><div class="form-card">'
        + f'<label class="check-row" style="border:none;align-items:flex-start">'
        + f'<input type="checkbox" name="demo_mode" form="admin-settings" {"checked" if config.get("demo_mode") else ""} style="margin-top:3px">'
        + f'<span><b>Demo Mode</b> &nbsp;&mdash;&nbsp; replaces every device name with a fake one on all screens (home, devices, query log) for screenshots &amp; live demos. Your real names are kept and restored when you turn this off.</span></label>'
        + f'<button type="submit" form="admin-settings" class="btn btn-secondary" style="margin-top:10px;width:auto;padding:8px 18px;font-size:0.85em">Save</button>'
        + f'</div></div>'
        + f'</div></body></html>'
    )


def build_notifications(config, cleared=False, saved=False,
                        tested_channel=None, test_ok=None, test_error=""):
    rows = get_notifications(limit=200)

    ICONS = {
        "Blocked Content":              ("&#x1F6AB;", "#DC6B5F", "#FFF7F7", "#FCA5A5"),
        "New Device Detected":          ("&#x1F4F1;", "#6366f1", "#F0F0FF", "#E0E0FF"),
        "High Block Rate":              ("&#x26A0;",  "#D97706", "#FFFBF0", "#FEF3C7"),
        "Activity Drop Detected":       ("&#x1F50D;", "#475569", "#F8FAFC", "#E2E8F0"),
        "Screen Time Limit Reached":    ("&#x23F1;",  "#0ea5e9", "#F0F9FF", "#BAE6FD"),
        "Lantern Watch Daily Summary":  ("&#x1F4CA;", "#0ea5e9", "#F0F9FF", "#BAE6FD"),
        "Lantern Watch Weekly Summary": ("&#x1F4C8;", "#7FB069", "#F0FDF4", "#86EFAC"),
        "Lantern Watch Test":           ("&#x1F514;", "#94a3b8", "#F8FAFC", "#E2E8F0"),
    }

    cleared_msg = '<div class="success">Notification log cleared.</div>' if cleared else ""

    # ── Channel setup guide ───────────────────────────────────────────────────
    ntfy_topic = config.get("ntfy_topic", "")
    _tg        = config.get("telegram", {})
    tg_ok      = bool((_tg.get("bot_token") or config.get("telegram_bot_token")) and
                      (_tg.get("chat_id")   or config.get("telegram_chat_id")))
    _em        = config.get("email", {})
    em_ok      = bool((_em.get("to_address") or config.get("email_to")) and
                      (_em.get("smtp_host")  or config.get("email_smtp")))

    def _badge(ok):
        return (f'<span style="background:{"#16A34A" if ok else "#94a3b8"};color:white;'
                f'padding:2px 8px;border-radius:99px;font-size:0.75em;font-weight:700;margin-left:8px">'
                f'{"CONFIGURED" if ok else "NOT SET"}</span>')

    setup_guide = (
        '<div class="section">'
        '<h2>How to Set Up Each Channel</h2>'

        # ntfy
        '<div style="background:#F0F9FF;border:1px solid #BAE6FD;border-radius:12px;padding:14px 16px;margin-bottom:12px">'
        '<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">'
        '<span style="font-size:1.2em">&#x1F514;</span>'
        f'<span style="font-weight:700;color:#0ea5e9">ntfy Push Notifications</span>{_badge(bool(ntfy_topic))}'
        '<span style="background:#0ea5e9;color:white;padding:2px 8px;border-radius:99px;font-size:0.72em;font-weight:700;margin-left:8px">MOST PRIVATE</span>'
        '</div>'
        '<div style="font-size:0.85em;color:#475569;line-height:1.6;margin-bottom:8px">'
        'Free push notifications to any phone or desktop &mdash; <b>no account, no phone number, no contacts</b>. '
        'Just pick a hard-to-guess topic name and subscribe. The most private option here, and you can even '
        'self-host your own ntfy server for end-to-end control.'
        '</div>'
        '<div style="font-size:0.82em;color:#64748b;line-height:1.8">'
        '<div>1. Download the <b>ntfy</b> app from <b>ntfy.sh</b> (iOS, Android, or Desktop)</div>'
        f'<div>2. Subscribe to topic: <b style="color:#0ea5e9">{ntfy_topic if ntfy_topic else "(set in Settings)"}</b></div>'
        '<div>3. Alerts arrive instantly on all subscribed devices</div>'
        '</div>'
        '</div>'

        # Telegram
        f'<div id="telegram-setup" style="background:#EFF6FF;border:1px solid #BFDBFE;border-radius:12px;padding:14px 16px;margin-bottom:12px">'
        f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">'
        f'<span style="font-size:1.2em">&#x1F4E8;</span>'
        f'<span style="font-weight:700;color:#2563EB">Telegram Notifications</span>{_badge(tg_ok)}'
        f'</div>'
        f'<div style="font-size:0.85em;color:#475569;line-height:1.6;margin-bottom:10px">'
        f'Receive Lantern Watch alerts as Telegram messages. Takes about 5 minutes to set up.'
        f'</div>'

        # Privacy disclosure — Telegram is discoverable by default
        f'<div style="font-size:0.82em;color:#92400e;background:#FEF3C7;border:1px solid #FCD34D;border-radius:8px;padding:10px 12px;margin-bottom:10px;line-height:1.7">'
        f'<div style="font-weight:700;margin-bottom:4px">&#x1F512; Lock down your Telegram privacy first</div>'
        f'By default Telegram lets strangers find you by your phone number and add you to groups — that is a Telegram setting, '
        f'not Lantern Watch (the bot cannot see or share your contacts). Before you start, open Telegram &rarr; '
        f'<b>Settings &rarr; Privacy &amp; Security</b> and set:'
        f'<div style="margin-top:6px;padding-left:4px">'
        f'&bull; <b>Phone Number</b> &rarr; <i>Who can find me by my number</i> &rarr; <b>My Contacts</b> (or Nobody)<br>'
        f'&bull; <b>Who can add me to groups</b> &rarr; <b>My Contacts</b> &nbsp;(stops random group spam)<br>'
        f'&bull; <b>Last Seen</b>, <b>Profile Photo</b>, <b>Calls</b>, <b>Messages</b> &rarr; <b>My Contacts</b><br>'
        f'&bull; <b>Data Settings</b> &rarr; turn <b>off Sync Contacts</b>, then <b>Delete Synced Contacts</b><br>'
        f'&bull; Leave <b>People Nearby</b> off, and you can skip setting a public @username'
        f'</div>'
        f'<div style="margin-top:6px">Lantern Watch only needs your <b>Chat ID</b> — never a username — so you can stay unsearchable.</div>'
        f'</div>'

        f'<div style="font-size:0.82em;font-weight:700;color:#2563EB;margin-bottom:4px">STEP 1 — CREATE YOUR BOT</div>'
        f'<div style="font-size:0.82em;color:#64748b;line-height:1.8;margin-bottom:10px">'
        f'<div>1. Open Telegram and search for <b>@BotFather</b></div>'
        f'<div>2. Send <code>/newbot</code></div>'
        f'<div>3. Choose a name (e.g. <i>"Lantern Watch Alerts"</i>) and a username ending in <code>_bot</code> (e.g. <i>LanternWatchFamily_bot</i>)</div>'
        f'<div>4. BotFather will give you a <b>Bot Token</b> — copy and save it</div>'
        f'</div>'

        f'<div style="font-size:0.82em;font-weight:700;color:#2563EB;margin-bottom:4px">STEP 2 — GET YOUR CHAT ID <span style="font-weight:400;color:#64748b">(Personal alerts)</span></div>'
        f'<div style="font-size:0.82em;color:#64748b;line-height:1.8;margin-bottom:10px">'
        f'<div>1. Search for your new bot in Telegram and tap <b>Start</b></div>'
        f'<div>2. Send it any message (e.g. <i>"hello"</i>)</div>'
        f'<div>3. Open this URL in your browser <span style="color:#94a3b8">(replace TOKEN with your bot token)</span>:<br>'
        f'&nbsp;&nbsp;&nbsp;<code style="word-break:break-all">https://api.telegram.org/botTOKEN/getUpdates</code></div>'
        f'<div>4. Look for <code>"chat":{{"id":</code> — that number is your <b>Chat ID</b></div>'
        f'<div>5. Enter your Bot Token and Chat ID in the boxes above and tap Save</div>'
        f'</div>'

        f'<div style="font-size:0.82em;font-weight:700;color:#2563EB;margin-bottom:4px">STEP 3 — GROUP ALERTS <span style="font-weight:400;color:#64748b">(Optional — send alerts to multiple people)</span></div>'
        f'<div style="font-size:0.82em;color:#64748b;line-height:1.8;margin-bottom:10px">'
        f'<div>1. Create a new Telegram group (e.g. <i>"Family Alerts"</i>)</div>'
        f'<div>2. Add the other parents to the group</div>'
        f'<div>3. Add your Lantern Watch bot to the group by searching its username</div>'
        f'<div>4. Send any message in the group (e.g. <i>"test"</i>)</div>'
        f'<div>5. Open the same getUpdates URL in your browser</div>'
        f'<div>6. Look for <code>"chat":{{"id":</code> — the group Chat ID will be a <b>negative number</b> like <code>-1001234567890</code></div>'
        f'<div>7. Replace the Chat ID in Settings with this negative number and tap Save</div>'
        f'</div>'
        f'<div style="font-size:0.82em;color:#64748b;background:#DBEAFE;border-radius:8px;padding:8px 10px;margin-bottom:10px">'
        f'&#x1F465; Other parents just download Telegram and accept the group invite — they receive all alerts automatically with no setup required.'
        f'</div>'

        f'<div style="font-size:0.82em;color:#2563EB;font-weight:600">'
        f'Tap <b>"Send Test Message"</b> above after saving to confirm everything is working.'
        f'</div>'
        f'</div>'

        # Email
        f'<div id="email-setup" style="background:#FFF1F2;border:1px solid #FECDD3;border-radius:12px;padding:14px 16px">'
        f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">'
        f'<span style="font-size:1.2em">&#x2709;&#xFE0F;</span>'
        f'<span style="font-weight:700;color:#DC2626">Email</span>{_badge(em_ok)}'
        f'</div>'
        f'<div style="font-size:0.85em;color:#475569;line-height:1.6;margin-bottom:8px">'
        f'Receive alerts by email. Works with Gmail, Outlook, iCloud Mail, and any SMTP provider.'
        f'</div>'
        f'<div style="font-size:0.82em;color:#64748b;line-height:1.8">'
        f'<div><b>Gmail</b> (recommended): turn on 2-Step Verification, then create a 16-character App Password at '
        f'<a href="https://myaccount.google.com/apppasswords" target="_blank" rel="noopener" style="color:#DC2626;font-weight:600">myaccount.google.com/apppasswords</a>. Then enter:</div>'
        f'<div style="margin-top:4px;padding-left:12px">'
        f'Send Alerts To: <b>you@gmail.com</b><br>'
        f'SMTP Host: <b>smtp.gmail.com</b> &nbsp; Port: <b>587</b><br>'
        f'From Address: <b>you@gmail.com</b><br>'
        f'App Password: <b>your 16-character code</b> (not your normal password)</div>'
        f'<div style="margin-top:6px"><b>Outlook / Hotmail:</b> Account → Security → App passwords &nbsp;&middot;&nbsp; SMTP Host: <b>smtp-mail.outlook.com</b> &nbsp; Port: <b>587</b></div>'
        f'<div style="margin-top:6px">Enter your settings in the boxes above and tap Save</div>'
        f'</div>'
        f'</div>'

        '</div>'
    )

    # ── Notification log ──────────────────────────────────────────────────────
    if not rows:
        items_html = (
            '<div style="text-align:center;padding:40px 20px;color:#94a3b8">'
            '<div style="font-size:2em;margin-bottom:8px">&#x1F514;</div>'
            '<div style="font-weight:700;margin-bottom:4px">No notifications yet</div>'
            '<div style="font-size:0.85em">Alerts you receive will be listed here. Turn on the alerts you want above and set up a channel to start getting them.</div>'
            '</div>'
        )
    else:
        items_html = ""
        for r in rows:
            icon, color, bg, border = ICONS.get(r["title"], ("&#x1F514;", "#64748b", "#F8FAFC", "#E2E8F0"))
            ts = _local_ts(r["ts"])[:16] if r["ts"] else "-"
            msg      = r["message"] or ""
            msg_safe = msg.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
            if len(msg) > 200:
                short    = msg[:200].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
                msg_html = (
                    f'<div id="msg_{r["id"]}" style="font-size:0.82em;color:#475569;margin-top:6px;line-height:1.6">'
                    f'{short}… <a href="#" onclick="document.getElementById(\'msg_{r["id"]}\').innerHTML=\'{msg_safe}\';return false;" '
                    f'style="color:{color};font-weight:700">Show more</a></div>'
                )
            else:
                msg_html = f'<div style="font-size:0.82em;color:#475569;margin-top:6px;line-height:1.6">{msg_safe}</div>'

            items_html += (
                f'<div style="background:{bg};border:1px solid {border};border-radius:12px;padding:14px 16px;margin-bottom:10px">'
                f'<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px">'
                f'<div style="display:flex;align-items:center;gap:8px">'
                f'<span style="font-size:1.2em">{icon}</span>'
                f'<div style="font-weight:800;color:{color}">{r["title"]}</div></div>'
                f'<div style="font-size:0.75em;color:#94a3b8;white-space:nowrap">{ts}</div>'
                f'</div>'
                f'{msg_html}'
                f'<div style="font-size:0.72em;color:#94a3b8;margin-top:6px">Topic: {r["topic"] or "—"}</div>'
                f'</div>'
            )

    # ── Status banners (after save / test) ────────────────────────────────────
    saved_msg = '<div class="success">Notification settings saved!</div>' if saved else ""
    if tested_channel:
        _ch = {"ntfy": "ntfy", "telegram": "Telegram", "email": "Email"}.get(tested_channel, tested_channel)
        if test_ok:
            tested_msg = f'<div class="success">{_ch} test sent successfully!</div>'
        else:
            tested_msg = f'<div style="margin-bottom:12px;padding:12px 16px;background:#FFF7F7;border:1px solid #FCA5A5;border-radius:10px;color:#DC6B5F;font-weight:600">{_ch} test failed: {test_error or "unknown error"}</div>'
    else:
        tested_msg = ""

    # ── Notification settings form (channels, alert types, summaries) ──────────
    _tg_token = _tg.get("bot_token", "") or config.get("telegram_bot_token", "")
    _tg_chat  = _tg.get("chat_id",   "") or config.get("telegram_chat_id",   "")
    _em_to    = _em.get("to_address",    "")  or config.get("email_to",       "")
    _em_smtp  = _em.get("smtp_host",     "")  or config.get("email_smtp",     "")
    _em_port  = _em.get("smtp_port",    None)  or config.get("email_port",   587)
    _em_from  = _em.get("smtp_user",     "")  or config.get("email_from",     "")
    _em_pass  = _em.get("smtp_password", "")  or config.get("email_password", "")

    _alerts   = config.get("alerts", {})
    _summary  = config.get("summary", {})
    _adult    = "checked" if _alerts.get("adult_content")       else ""
    _newdev   = "checked" if _alerts.get("new_device")          else ""
    _highblk  = "checked" if _alerts.get("high_block_rate")     else ""
    _vpn      = "checked" if _alerts.get("vpn_detection")       else ""
    _update   = "checked" if _alerts.get("update_available", True) else ""
    _daily    = "checked" if _summary.get("daily")              else ""
    _weekly   = "checked" if _summary.get("weekly")             else ""
    _daily_hr = _summary.get("daily_hour", 20)
    _weekly_d = _summary.get("weekly_day", 6)
    _vpn_wl   = ", ".join(config.get("vpn_whitelist", []))
    _time_opts = "".join(
        f'<label class="radio-row"><input type="radio" name="daily_hour" value="{h}" {"checked" if _daily_hr == h else ""}> {l}</label>'
        for h, l in [(17,"5:00 PM"),(18,"6:00 PM"),(19,"7:00 PM"),(20,"8:00 PM"),(21,"9:00 PM"),(22,"10:00 PM")]
    )
    # Sunday-first ordering; value = Python weekday() index (Mon=0 … Sun=6).
    _day_opts = "".join(
        f'<label class="radio-row"><input type="radio" name="weekly_day" value="{i}" {"checked" if _weekly_d == i else ""}> {d}</label>'
        for i, d in [(6,"Sunday"),(0,"Monday"),(1,"Tuesday"),(2,"Wednesday"),(3,"Thursday"),(4,"Friday"),(5,"Saturday")]
    )

    notif_form = (
        '<div class="section"><h2>Notification Settings</h2>'
        '<form method="POST" action="/notifications/save">'
        # ntfy
        + '<div class="form-card">'
        + f'<label style="display:flex;align-items:center;gap:10px;margin-bottom:12px;cursor:pointer"><input type="checkbox" name="ntfy_enabled" {"checked" if config.get("ntfy_enabled", bool(ntfy_topic)) else ""} style="width:18px;height:18px;accent-color:#e8a000"><span style="font-weight:700;color:var(--ink)">ntfy push notifications</span></label>'
        + '<div class="form-label">ntfy Primary Topic</div>'
        + f'<input type="text" name="ntfy_topic" value="{ntfy_topic}" placeholder="e.g. my-family-alerts">'
        + '<div class="form-label" style="margin-top:12px">Additional Topics (comma-separated)</div>'
        + f'<input type="text" name="extra_topics" value="{config.get("extra_topics", "")}">'
        + '<div style="margin-top:12px"><a href="/notifications/test/ntfy" class="btn btn-secondary" style="font-size:0.82em;padding:8px 14px">Send Test Push</a></div></div>'
        # Telegram
        + '<div class="form-card">'
        + '<div style="display:flex;align-items:center;margin-bottom:10px"><span style="font-size:1.2em;margin-right:8px">&#x1F4E8;</span>'
        + f'<span style="font-weight:700;color:#2563EB">Telegram Notifications</span>{_badge(tg_ok)}</div>'
        + f'<label style="display:flex;align-items:center;gap:10px;margin-bottom:12px;cursor:pointer"><input type="checkbox" name="telegram_enabled" {"checked" if _tg.get("enabled", tg_ok) else ""} style="width:18px;height:18px;accent-color:#e8a000"><span style="font-weight:700;color:var(--ink)">Telegram notifications enabled</span></label>'
        + '<div style="color:#64748b;font-size:0.82em;margin-bottom:12px">Get alerts as Telegram messages. <a href="#telegram-setup" style="color:#2563EB;font-weight:600">Setup guide &#x2193;</a></div>'
        + '<div class="form-label">Bot Token</div>'
        + f'<input type="password" name="telegram_bot_token" id="telegram_bot_token" value="{_tg_token}" placeholder="7123456789:AABBccDDee...">'
        + '<label style="display:flex;align-items:center;gap:8px;margin-top:8px;cursor:pointer;font-size:0.82em;color:#64748b"><input type="checkbox" onchange="togglePwd(\'telegram_bot_token\',this)" style="width:16px;height:16px;accent-color:#e8a000"> Show token</label>'
        + '<div class="form-label" style="margin-top:12px">Chat ID</div>'
        + f'<input type="password" name="telegram_chat_id" id="telegram_chat_id" value="{_tg_chat}" placeholder="e.g. -1001234567890 or your personal chat ID">'
        + '<label style="display:flex;align-items:center;gap:8px;margin-top:8px;cursor:pointer;font-size:0.82em;color:#64748b"><input type="checkbox" onchange="togglePwd(\'telegram_chat_id\',this)" style="width:16px;height:16px;accent-color:#e8a000"> Show chat ID</label>'
        + '<div style="margin-top:12px"><a href="/notifications/test/telegram" class="btn btn-secondary" style="font-size:0.82em;padding:8px 14px">Send Test Message</a></div></div>'
        # Email
        + '<div class="form-card">'
        + '<div style="display:flex;align-items:center;margin-bottom:10px"><span style="font-size:1.2em;margin-right:8px">&#x2709;&#xFE0F;</span>'
        + f'<span style="font-weight:700;color:#DC2626">Email Notifications</span>{_badge(em_ok)}</div>'
        + f'<label style="display:flex;align-items:center;gap:10px;margin-bottom:12px;cursor:pointer"><input type="checkbox" name="email_enabled" {"checked" if _em.get("enabled", em_ok) else ""} style="width:18px;height:18px;accent-color:#e8a000"><span style="font-weight:700;color:var(--ink)">Email notifications enabled</span></label>'
        + '<div style="color:#64748b;font-size:0.82em;margin-bottom:12px">Get alerts by email. Works with Gmail, Outlook, and any SMTP provider. <a href="#email-setup" style="color:#DC2626;font-weight:600">Setup guide &#x2193;</a></div>'
        + '<div class="form-label">Send Alerts To</div>'
        + f'<input type="email" name="email_to" value="{_em_to}" placeholder="you@gmail.com">'
        + '<div style="display:flex;gap:10px;margin-top:12px"><div style="flex:2"><div class="form-label">SMTP Host</div>'
        + f'<input type="text" name="email_smtp" value="{_em_smtp}" placeholder="smtp.gmail.com"></div>'
        + '<div style="flex:1"><div class="form-label">Port</div>'
        + f'<input type="number" name="email_port" value="{_em_port}"></div></div>'
        + '<div class="form-label" style="margin-top:12px">From Address</div>'
        + f'<input type="email" name="email_from" value="{_em_from}" placeholder="lanternwatch@gmail.com">'
        + '<div class="form-label" style="margin-top:12px">App Password</div>'
        + '<div style="font-size:0.78em;color:#64748b;margin-bottom:4px">Gmail: create a 16-character code at <a href="https://myaccount.google.com/apppasswords" target="_blank" rel="noopener" style="color:#DC2626;font-weight:600">myaccount.google.com/apppasswords</a> — use that here, not your normal password.</div>'
        + f'<input type="password" name="email_password" id="email_password" value="{_em_pass}">'
        + '<label style="display:flex;align-items:center;gap:8px;margin-top:8px;cursor:pointer;font-size:0.82em;color:#64748b"><input type="checkbox" onchange="togglePwd(\'email_password\',this)" style="width:16px;height:16px;accent-color:#D97706"> Show password</label>'
        + '<div style="margin-top:12px"><a href="/notifications/test/email" class="btn btn-secondary" style="font-size:0.82em;padding:8px 14px">Send Test Email</a></div></div>'
        # Alert types
        + '<div class="form-card"><div class="form-label">Alert Types</div>'
        + f'<label class="check-row"><input type="checkbox" name="alert_adult" {_adult}><span>Content blocked</span></label>'
        + f'<label class="check-row"><input type="checkbox" name="alert_newdevice" {_newdev}><span>New device joins network</span></label>'
        + f'<label class="check-row"><input type="checkbox" name="alert_highblock" {_highblk}><span>High block rate</span></label>'
        + f'<label class="check-row"><input type="checkbox" name="alert_vpn" {_vpn}><span>Possible VPN detected</span></label>'
        + f'<label class="check-row"><input type="checkbox" name="alert_update" {_update}><span>Lantern Watch update available <span style="color:#94a3b8;font-weight:400">(checks GitHub anonymously — no data sent)</span></span></label></div>'
        + '<div class="form-card"><div class="form-label">VPN Whitelist</div>'
        + f'<input type="text" name="vpn_whitelist" value="{_vpn_wl}" placeholder="e.g. Work-Laptop, Dads-iPhone">'
        + '<div style="color:#94a3b8;font-size:0.75em;margin-top:4px">Devices that use a VPN on purpose &mdash; they won\'t trigger an "activity drop" alert. Use the device\'s hostname (as shown on the Devices page), comma-separated. <b>Tip:</b> any device set to the <b>Work Device</b> type is exempt automatically &mdash; you only need this list for other devices.</div></div>'
        # Daily summary
        + '<div class="form-card"><div class="form-label">Daily Summary</div>'
        + f'<label class="check-row"><input type="checkbox" name="daily_summary" {_daily}><span>Send daily summary</span></label>'
        + f'<div style="margin-top:8px;padding-left:4px">{_time_opts}</div>'
        + '<div style="margin-top:10px"><a href="/notifications/test/summary?type=daily" class="btn btn-secondary" style="font-size:0.82em;padding:8px 14px">Send Test Summary Now</a></div></div>'
        # Weekly summary
        + '<div class="form-card"><div class="form-label">Weekly Summary</div>'
        + f'<label class="check-row"><input type="checkbox" name="weekly_summary" {_weekly}><span>Send weekly summary</span></label>'
        + f'<div style="margin-top:8px;padding-left:4px">{_day_opts}</div>'
        + '<div style="margin-top:10px"><a href="/notifications/test/summary?type=weekly" class="btn btn-secondary" style="font-size:0.82em;padding:8px 14px">Send Test Summary Now</a></div></div>'
        + '<button type="submit" class="btn">Save Notification Settings</button></form>'
        + '<script>function togglePwd(id,cb){var el=document.getElementById(id);if(el)el.type=cb.checked?"text":"password";}</script>'
        + '</div>'
    )

    return (
        '<!DOCTYPE html><html><head><link rel="icon" type="image/svg+xml" href="/favicon.svg"><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1.0">'
        '<title>Notifications - Lantern Watch</title><style>' + CSS + '</style></head><body>'
        + build_header("Notifications", config=config)
        + '<div class="page-wrap">'
        + saved_msg + tested_msg + cleared_msg
        + notif_form
        + setup_guide
        + '<div class="section">'
        + f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">'
        + f'<h2 style="margin:0">Recent Alerts &amp; Summaries</h2>'
        + f'<span style="font-size:0.8em;color:#94a3b8">{len(rows)} total</span>'
        + f'</div>'
        + items_html
        + '<form method="POST" action="/notifications/clear" style="margin-top:16px">'
        + '<button type="submit" class="btn btn-danger" style="width:auto;padding:10px 20px">Clear Notification Log</button>'
        + '</form>'
        + '</div></div></body></html>'
    )


def build_querylog_page(entries, devices, total, filters, config):
    device  = filters.get("device", "")
    blocked = filters.get("blocked", "")
    window  = filters.get("window", "1h")
    q       = filters.get("q", "")
    offset  = int(filters.get("offset", 0))
    limit   = 200

    WINDOW_LABELS = {"1h": "Last Hour", "6h": "Last 6 Hours", "24h": "Last 24 Hours",
                     "7d": "Last 7 Days", "30d": "Last 30 Days", "60d": "Last 60 Days", "90d": "Last 90 Days"}

    import re as _re
    _is_ip = _re.compile(r"^\d{1,3}(\.\d{1,3}){3}$").match
    try:
        _ip_hostnames = get_ip_hostname_map(config)
    except Exception:
        _ip_hostnames = {}

    device_opts = '<option value="">All Devices</option>'
    for d in devices:
        sel = 'selected' if d == device else ''
        display = device_display_name(d, config, _ip_hostnames)
        device_opts += f'<option value="{d}" {sel}>{display}</option>'

    win_opts = ""
    for val, lbl in WINDOW_LABELS.items():
        sel = 'selected' if val == window else ''
        win_opts += f'<option value="{val}" {sel}>{lbl}</option>'

    page_count   = len(entries)
    showing_from = offset + 1 if entries else 0
    showing_to   = offset + page_count
    blocked_checked = 'checked' if blocked == "1" else ''

    active_filters = device or blocked or q or window != "1h"
    stats_parts = [f'<strong>{total:,}</strong> entries']
    if device:
        stats_parts.append(f'for <strong>{device}</strong>')
    if q:
        stats_parts.append(f'matching <strong>{q}</strong>')
    stats_msg = ' '.join(stats_parts)

    rows_html = ""
    for e in entries:
        ts_local  = _local_ts(e["ts"])            # UTC -> local 'YYYY-MM-DD HH:MM:SS'
        ts_time   = ts_local[11:19]
        ts_date   = ts_local[:10]
        dev_name  = e["client_name"] or e["client_ip"] or "Unknown"
        dev_ip    = e["client_ip"] or ""
        dev_label = device_display_name(dev_name, config, _ip_hostnames)
        domain    = e["domain"] or "—"
        qtype     = e["qtype"] or ""
        is_blocked = e["blocked"]
        elapsed   = e["elapsed_ms"]
        reason    = e["reason"] or ""

        if "Parental" in reason:       reason_lbl = "Content Filter"
        elif "SafeBrowsing" in reason: reason_lbl = "Malware"
        elif "SafeSearch"   in reason: reason_lbl = "Safe Search"
        elif is_blocked:               reason_lbl = "Blocked"
        else:                          reason_lbl = ""

        status_badge = (
            f'<span class="ql-badge ql-blocked" title="{reason_lbl or reason}">Blocked</span>'
            if is_blocked else
            '<span class="ql-badge ql-allowed">Allowed</span>'
        )
        elapsed_str = f"{elapsed:.0f}" if elapsed else "—"
        dev_url   = f'/querylog?device={quote(dev_name)}&window={window}&blocked={blocked}&q={quote(q)}'
        ip_line   = f'<span class="ql-dev-ip">{dev_ip}</span>' if dev_ip else ''
        dev_link  = f'<a class="ql-dev-link" href="{dev_url}">{dev_label}</a>{ip_line}'

        rows_html += (
            f'<tr>'
            f'<td class="ql-col-time"><span class="ql-ts">{ts_time}</span>'
            f'<span class="ql-date">{ts_date}</span></td>'
            f'<td class="ql-col-dev">{dev_link}</td>'
            f'<td class="ql-col-domain">{domain}</td>'
            f'<td class="ql-col-type"><span class="ql-qtype">{qtype}</span></td>'
            f'<td class="ql-col-status">{status_badge}</td>'
            f'<td class="ql-col-ms">{elapsed_str}</td>'
            f'</tr>'
        )

    if not rows_html:
        rows_html = '<tr><td colspan="6" class="ql-empty">No entries found for the selected filters.</td></tr>'

    base_url  = f'/querylog?device={quote(device)}&window={window}&blocked={blocked}&q={quote(q)}'
    has_prev  = offset > 0
    has_next  = (offset + limit) < total
    prev_btn  = (f'<a class="ql-page-btn" href="{base_url}&offset={max(0, offset - limit)}">&#x2190; Prev</a>'
                 if has_prev else '<span class="ql-page-btn ql-page-off">&#x2190; Prev</span>')
    next_btn  = (f'<a class="ql-page-btn" href="{base_url}&offset={offset + limit}">Next &#x2192;</a>'
                 if has_next else '<span class="ql-page-btn ql-page-off">Next &#x2192;</span>')

    clear_btn = (f'<a href="/querylog" class="ql-btn ql-btn-clear">Clear</a>'
                 if active_filters else '')

    return (
        '<!DOCTYPE html><html><head><link rel="icon" type="image/svg+xml" href="/favicon.svg"><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1.0">'
        '<title>Query Log — Lantern Watch</title>'
        '<style>' + CSS + """
.ql-wrap{max-width:1100px;margin:0 auto;padding:16px}
.ql-filters{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:10px}
.ql-select,.ql-search{padding:8px 12px;border:1.5px solid var(--line);border-radius:8px;
  font-family:var(--font);font-size:13px;color:var(--ink);background:#fff;outline:none}
.ql-select:focus,.ql-search:focus{border-color:var(--orange);box-shadow:0 0 0 3px rgba(232,160,0,0.1)}
.ql-search{width:160px}
.ql-check{font-size:13px;color:var(--muted);display:flex;align-items:center;gap:5px;cursor:pointer;white-space:nowrap}
.ql-btn{padding:8px 16px;background:var(--orange);color:#fff;border:none;border-radius:8px;
  font-family:var(--font);font-size:13px;font-weight:700;cursor:pointer;text-decoration:none;white-space:nowrap}
.ql-btn:hover{background:var(--orange)}
.ql-btn-clear{background:#f0eee9;color:var(--muted)}
.ql-btn-clear:hover{background:#e7e4dd}
.ql-stats{font-size:13px;color:var(--muted);margin-bottom:10px}
.ql-table-wrap{overflow-x:auto;border-radius:12px;border:1px solid var(--line);background:#fff}
.ql-table{width:100%;border-collapse:collapse;font-size:13px}
.ql-table th{background:var(--ink);padding:12px;text-align:left;font-weight:700;color:#fff;
  font-size:11px;text-transform:uppercase;letter-spacing:0.05em;border-bottom:1px solid var(--line);white-space:nowrap}
.ql-table td{padding:8px 12px;border-bottom:1px solid var(--line);vertical-align:middle}
.ql-table tr:nth-child(even) td{background:var(--bg-soft)}
.ql-table tr:last-child td{border-bottom:none}
.ql-table tr:hover td{background:var(--amber-soft)}
.ql-col-time{white-space:nowrap}
.ql-ts{display:block;font-weight:700;color:var(--ink)}
.ql-date{display:block;font-size:11px;color:var(--muted)}
.ql-col-dev{max-width:130px}
.ql-dev-link{color:var(--orange-dark);text-decoration:none;font-weight:700;display:block}
.ql-dev-link:hover{text-decoration:underline}
.ql-dev-ip{display:block;font-size:11px;color:var(--muted);font-weight:400}
.ql-col-domain{max-width:280px;word-break:break-all;font-size:12px;color:var(--body)}
.ql-qtype{background:#f0eee9;color:var(--muted);padding:2px 6px;border-radius:4px;font-size:11px;font-weight:700}
.ql-badge{padding:3px 9px;border-radius:10px;font-size:11px;font-weight:700;white-space:nowrap}
.ql-blocked{background:#fdeaea;color:var(--danger)}
.ql-allowed{background:#eaf7ef;color:var(--ok)}
.ql-col-ms{color:var(--muted);font-size:12px;white-space:nowrap;text-align:right}
.ql-empty{text-align:center;color:var(--muted);padding:40px;font-style:italic}
.ql-pagination{display:flex;align-items:center;justify-content:center;gap:16px;margin-top:14px;padding-bottom:24px}
.ql-page-btn{padding:7px 16px;background:#fff;border:1px solid var(--line);border-radius:8px;
  color:var(--orange-dark);font-size:13px;font-weight:700;text-decoration:none}
.ql-page-btn:hover{background:var(--amber-soft);border-color:var(--orange)}
.ql-page-off{color:#ccc;pointer-events:none;border-color:var(--line)}
.ql-page-info{font-size:13px;color:var(--muted)}
""" + '</style></head><body>'
        + build_header("Query Log", config=config)
        + '<div class="ql-wrap">'
        + '<div class="section">'
        + f'<form method="get" action="/querylog" class="ql-filters">'
        + f'<select name="device" class="ql-select">{device_opts}</select>'
        + f'<select name="window" class="ql-select">{win_opts}</select>'
        + f'<input name="q" class="ql-search" placeholder="Search domain…" value="{q}">'
        + f'<label class="ql-check"><input type="checkbox" name="blocked" value="1" {blocked_checked}>&nbsp;Blocked only</label>'
        + f'<button type="submit" class="ql-btn">Filter</button>'
        + clear_btn
        + '</form>'
        + f'<div class="ql-stats">Showing {showing_from}–{showing_to} of {stats_msg}</div>'
        + '<div class="ql-table-wrap"><table class="ql-table">'
        + '<thead><tr><th>Time</th><th>Device</th><th>Domain</th><th>Type</th><th>Status</th><th>ms</th></tr></thead>'
        + f'<tbody>{rows_html}</tbody>'
        + '</table></div>'
        + f'<div class="ql-pagination">{prev_btn}'
        + f'<span class="ql-page-info">{showing_from}–{showing_to} of {total:,}</span>'
        + f'{next_btn}</div>'
        + '</div></div></body></html>'
    )
