#!/usr/bin/env python3
"""
Lantern Watch — config.py
Load/save config and device helper functions.
"""

import json
import re as _re

# Versioning: Semantic Versioning MAJOR.MINOR.PATCH (see CHANGELOG.md). While
# pre-1.0 the leading 0. signals it's still maturing: PATCH = fixes, MINOR = new
# features. A pre-release tag (beta/rc) sorts BELOW the same numbered release.
# See is_newer_version().
VERSION          = "0.14.4"
# Update check reads the public GitHub repo directly — the newest git tag is the
# single source of truth. No telemetry is sent; the router just asks GitHub for
# the tag list, anonymously, like any visitor.
GITHUB_REPO         = "LanternWatchApp/lantern-watch"
UPDATE_CHECK_URL    = f"https://api.github.com/repos/{GITHUB_REPO}/tags"
UPDATE_RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases"

# Opt-in anonymous analytics (separate from the update check). Off by default;
# only pings when the user enables "Share anonymous usage stats" in Settings.
# Sends a random install ID, version, router model, feature on/off flags and a
# device COUNT — never names, domains, IPs, or browsing data.
TELEMETRY_URL    = "https://script.google.com/macros/s/AKfycbyD8lYIvvrxs_UzuMNvBARmsEU6QHX2oRcXgaF1tBcJP8L-zKh0ZNF_9x7u_L-5L0VM/exec"


def _version_key(v):
    """Sortable key for a version string. Numeric parts compared left-to-right;
    a pre-release tag (beta/alpha/rc/dev) ranks below the same numbered release."""
    nums       = tuple(int(x) for x in _re.findall(r"\d+", str(v)))
    is_release = 0 if _re.search(r"(?i)(beta|alpha|rc|dev|pre|snapshot)", str(v)) else 1
    return (nums, is_release)


def is_newer_version(latest, current):
    """True only if `latest` is a strictly newer version than `current`. Handles
    0.9.0-beta, 0.9.0.1, 1.0.0, etc. Never reports a downgrade as an update."""
    try:
        return _version_key(latest) > _version_key(current)
    except Exception:
        return False

CONFIG_PATH = "/root/lantern-watch/lanternwatch_config.json"

DEFAULTS = {
    "first_run": True,
    "ntfy_topic": "",
    "extra_topics": "",
    "devices": {},
    "adguard": {
        "url": "http://127.0.0.1:3000",
        "username": "",
        "password": ""
    },
    # Link TARGET used in notifications — the LAN IP, which Telegram/email always
    # make clickable (a bare single-label host like "lanternwatch" never links).
    "dashboard_url": "http://192.168.8.1:8081",
    # Friendly hostnames the dashboard answers to as "itself" rather than treating
    # as a blocked domain. install.sh registers these as AdGuard DNS rewrites
    # pointing at the router, so users can type http://<name>:8081 in a browser.
    # The LAN IP and localhost are always recognized.
    "local_hostnames": ["lanternwatch", "lanternwatch.lan"],
    # Notifications start OFF on a fresh install — nothing is "set up" until the
    # user configures a channel in the notification wizard (which then turns on
    # these sensible defaults). Skipping the wizard leaves everything off.
    "alerts": {
        "adult_content": False,
        "new_device": False,
        "high_block_rate": False,
        "high_block_threshold": 50,
        "vpn_detection": False,
        # Tell the family when a newer Lantern Watch release is out. Default ON —
        # the daily version check sends no data (an anonymous read of GitHub's
        # public tag list), so it's safe for everyone regardless of the stats
        # opt-in. Deduped per version so it notifies once, not nightly.
        "update_available": True,
    },
    "summary": {
        "daily": False,
        "daily_hour": 20,
        "weekly": False,
        "weekly_day": 6,
        "weekly_hour": 20,
    },
    "retention_days": 60,
    # LITE only: which Cloudflare DNS tier the upstream uses — "families"
    # (malware + adult, default) or "malware" (malware only). Chosen on /social.
    "lite_dns_tier": "families",
    # Per-category notifications for AdGuard "Blocked Services". Intentional-
    # navigation categories notify; chatty background telemetry (gaming,
    # streaming, shopping, relay) stays quiet. Editable per group on
    # /blocked-services. Runtime falls back to SERVICE_NOTIFY_DEFAULTS if absent.
    "service_notify": {
        "Social Media": False,
        "Messaging & Chat": False,
        "Dating / Adult": False,
        "Gambling": False,
        "Gaming": False,
        "Streaming & Music": False,
        "Shopping": False,
        "Privacy Bypass": False,
        "Other": False,
    },
    "vpn_whitelist": [],
    "captive_portal": False,
    "captive_portal_acked": [],
    "social_safe_search": True,
    "lw_username": "admin",
    "lw_password": "",
    "telegram": {
        "bot_token": "",
        "chat_id": "",
    },
    "email": {
        "smtp_host": "",
        "smtp_port": 587,
        "smtp_user": "",
        "smtp_password": "",
        "to_address": "",
        "from_name": "Lantern Watch",
    },
}


def load_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except Exception:
        return dict(DEFAULTS)


def save_config(config):
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)


_lan_ip_cache = {"ip": None}


def router_lan_ip():
    """This router's ACTUAL LAN IP, read from UCI once and cached.

    Never assume 192.168.8.1. GL.iNet repeater mode automatically shifts the LAN
    subnet (e.g. to 192.168.10.1) when the uplink already uses 192.168.8.x — which
    is the Beryl 7's primary travel use case (hotels, RVs, behind another router).
    Assuming the stock IP there makes the dashboard refuse its own address and
    bounce people to /blocked, or redirect them onto a different router entirely."""
    if _lan_ip_cache["ip"] is None:
        ip = ""
        try:
            import subprocess
            r = subprocess.run(["uci", "get", "network.lan.ipaddr"],
                               capture_output=True, text=True, timeout=5)
            ip = (r.stdout or "").strip()
        except Exception:
            ip = ""
        _lan_ip_cache["ip"] = ip or "192.168.8.1"
    return _lan_ip_cache["ip"]


def dashboard_url(config):
    """Link target for notifications and page links.

    Honours an explicitly-configured dashboard_url, but if it still holds the
    stock 192.168.8.1 while this router's LAN is actually elsewhere, prefer the
    real LAN IP — otherwise every notification link points at the wrong router."""
    url = (config.get("dashboard_url") or "").strip()
    lan = router_lan_ip()
    if not url or ("192.168.8.1" in url and lan != "192.168.8.1"):
        return f"http://{lan}:8081"
    return url


def _strip_dns_suffix(s):
    """Trim a router-local DNS suffix (Galaxy-S21.lan -> Galaxy-S21). Every device
    on the LAN carries one, so it's just noise on every screen."""
    for suffix in (".lan", ".local", ".home", ".internal"):
        if s and s.lower().endswith(suffix):
            return s[: -len(suffix)]
    return s


def label(name, config):
    """Return the friendly display label for a device name, with the redundant
    router-local DNS suffix (.lan/.local/…) trimmed for display."""
    devices = config.get("devices", {})
    raw = devices[name].get("label", name) if name in devices else name
    return _strip_dns_suffix(raw)


def effective_type(name, config, domains=None):
    """The device type to use for behavior and display.

    A type the user has explicitly saved always wins. If none is stored (a new,
    never-classified device), fall back to an automatic best-effort guess from
    the hostname / MAC vendor (and, when provided, the device's top `domains`)
    so new gadgets get a sensible type instead of every one defaulting to
    Personal. See classify.guess_device_type."""
    d = config.get("devices", {}).get(name, {})
    if "type" in d:
        return d["type"]
    try:
        from classify import guess_device_type
        return guess_device_type(name, d.get("label", ""), config, domains)
    except Exception:
        return "person"


def is_infrastructure(name, config):
    """Return True if the device should be shown in the Infrastructure section."""
    return effective_type(name, config) in ("infrastructure", "smart_device")


def is_pauseable(name, config):
    """Return True if the device should be included in Pause All Personal."""
    return effective_type(name, config) == "person"


def is_monitored(name, config):
    """Return True if the device should appear in the dashboard."""
    devices = config.get("devices", {})
    if name in devices:
        return devices[name].get("monitor", True)
    return True


def is_first_run(config):
    """Return True if the dashboard password has never been changed."""
    return config.get("first_run", False)
