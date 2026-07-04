#!/usr/bin/env python3
"""
Lantern Watch — config.py
Load/save config and device helper functions.
"""

import json
import re as _re

# Versioning: AdGuard-style 0.MAJOR.MINOR with a -beta suffix while pre-1.0
# (e.g. 0.9.0-beta → 0.9.0.1 → 0.9.0.2 …). A '-beta' build sorts BELOW the same
# numbered release. See is_newer_version().
VERSION          = "0.9.0-beta"
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
    "alerts": {
        "adult_content": True,
        "new_device": True,
        "high_block_rate": True,
        "high_block_threshold": 50,
        "vpn_detection": True,
    },
    "summary": {
        "daily": True,
        "daily_hour": 21,
        "weekly": False,
        "weekly_day": 0,
        "weekly_hour": 21,
    },
    "vpn_whitelist": [],
    "guest_expire_days": 3,
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


def label(name, config):
    """Return the friendly display label for a device name."""
    devices = config.get("devices", {})
    if name in devices:
        return devices[name].get("label", name)
    return name


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
