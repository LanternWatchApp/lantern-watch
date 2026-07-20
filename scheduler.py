#!/usr/bin/env python3
"""
Lantern Watch — scheduler.py
Device pause/unpause (manual and scheduled) and the background scheduler thread.
"""

import re
import sqlite3
import subprocess
import threading
import time
import urllib.request
from datetime import datetime, timedelta

from config import load_config, save_config, dashboard_url

_IS_IP = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def _resolve_name(raw_name, config):
    """Return a human-readable device name, resolving bare IPs via DHCP leases."""
    # Check config labels first
    devices = config.get("devices", {})
    if raw_name in devices:
        stored = devices[raw_name].get("label", raw_name)
        if not _IS_IP.match(stored):
            return stored
    # Fall back to DHCP lease file for bare IPs
    if _IS_IP.match(raw_name):
        try:
            with open("/tmp/dhcp.leases") as f:
                for line in f:
                    parts = line.strip().split()
                    if len(parts) >= 4 and parts[2] == raw_name and parts[3] != "*":
                        host = parts[3]
                        for suffix in (".lan", ".local", ".home", ".internal"):
                            if host.lower().endswith(suffix):
                                host = host[: -len(suffix)]
                        host = host.replace("-", " ").replace("_", " ")
                        return " ".join(w.capitalize() for w in host.split())
        except Exception:
            pass
    return raw_name

DB_PATH     = "/root/lanternwatch.db"
SESSION_GAP = 300  # seconds; matches db.py

try:
    from alerts import send_telegram, send_email
except Exception:
    def send_telegram(config, message, title=""): pass
    def send_email(config, message, title=""): pass


def _screen_secs(ip, since):
    """Seconds a device (by IP) has been online since the given timestamp."""
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT ts FROM querylog WHERE client_ip=? AND ts > ? ORDER BY ts ASC",
            (ip, since),
        ).fetchall()
        conn.close()
        if len(rows) < 2:
            return 0
        total = 0
        prev = datetime.fromisoformat(rows[0][0][:19].replace("T", " "))
        for row in rows[1:]:
            curr = datetime.fromisoformat(row[0][:19].replace("T", " "))
            gap = (curr - prev).total_seconds()
            if gap < SESSION_GAP:
                total += gap
            prev = curr
        return int(total)
    except Exception:
        return 0


def _ntfy(topic, message, title, tags="stopwatch", priority="high"):
    try:
        headers = {
            "Title":        title.encode("utf-8").decode("latin-1", errors="ignore"),
            "Priority":     priority,
            "Tags":         tags,
            "Content-Type": "text/plain; charset=utf-8",
        }
        req = urllib.request.Request(
            f"https://ntfy.sh/{topic}",
            data=message.encode("utf-8"),
            headers=headers,
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"ntfy error: {e}")


# ── Pause / unpause ───────────────────────────────────────────────────────────

def get_paused_devices(config):
    return config.get("paused_devices", {})


def pause_device(ip, friendly_name, config, scheduled=False):
    """
    Block internet for a device via iptables.
    Clears any existing DROP rules for the IP first to prevent stacking.
    """
    try:
        # Drain existing rules before inserting a fresh one
        while subprocess.run(
            ["iptables", "-D", "FORWARD", "-s", ip, "-j", "DROP"],
            capture_output=True,
        ).returncode == 0:
            pass
        subprocess.run(
            ["iptables", "-I", "FORWARD", "-s", ip, "-j", "DROP"],
            check=True,
        )
        paused = config.get("paused_devices", {})
        paused[ip] = {
            "name":      friendly_name,
            "paused_at": datetime.now().isoformat(),
            "scheduled": scheduled,
        }
        config["paused_devices"] = paused
        save_config(config)
        return True
    except Exception as e:
        print(f"Pause error: {e}")
        return False


def unpause_device(ip, config):
    """
    Remove all DROP rules for a device and mark it as unpaused in config.
    """
    try:
        while subprocess.run(
            ["iptables", "-D", "FORWARD", "-s", ip, "-j", "DROP"],
            capture_output=True,
        ).returncode == 0:
            pass
        paused = config.get("paused_devices", {})
        paused.pop(ip, None)
        config["paused_devices"] = paused
        save_config(config)
        return True
    except Exception as e:
        print(f"Unpause error: {e}")
        return False


def restore_paused_on_boot(config):
    """Re-apply iptables DROP rules for any devices that were paused before restart."""
    paused = config.get("paused_devices", {})
    for ip in paused:
        subprocess.run(["iptables", "-I", "FORWARD", "-s", ip, "-j", "DROP"])
    if paused:
        print(f"Restored {len(paused)} paused device(s) from config")


# ── Background scheduler ──────────────────────────────────────────────────────

def _minutes(hhmm: str) -> int:
    """Convert 'HH:MM' to minutes since midnight."""
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


def check_schedules():
    """
    Runs in a background thread every 60 seconds.
    Enforces Hours of Peace (bedtime) and Focus Times for each device.
    """
    while True:
        try:
            config  = load_config()
            schedules = config.get("schedules", {})
            now     = datetime.now()
            current = now.hour * 60 + now.minute

            for ip, sched in schedules.items():
                if not sched.get("enabled"):
                    continue

                paused   = config.get("paused_devices", {})
                friendly = _resolve_name(sched.get("name", ip), config)

                # ── Hours of Peace ────────────────────────────────────────────
                bed  = _minutes(sched["bedtime"])
                wake = _minutes(sched["wake"])

                # Overnight schedule (e.g. 21:00 → 06:00)
                if bed > wake:
                    is_rest = current >= bed or current < wake
                else:
                    is_rest = bed <= current < wake

                if is_rest and ip not in paused:
                    pause_device(ip, friendly, config, scheduled=True)
                    config = load_config()
                    print(f"[Schedule] Paused {friendly} — Hours of Peace")

                elif not is_rest and ip in paused and paused[ip].get("scheduled"):
                    # Don't unpause if a Focus Time is currently active
                    focus_active = _focus_is_active(sched, current)
                    if not focus_active:
                        unpause_device(ip, config)
                        config = load_config()
                        print(f"[Schedule] Resumed {friendly} — rest window ended")

            # ── Focus Times (separate pass so config is fresh) ────────────────
            config = load_config()
            for ip, sched in config.get("schedules", {}).items():
                if not sched.get("enabled"):
                    continue
                paused   = config.get("paused_devices", {})
                friendly = _resolve_name(sched.get("name", ip), config)

                for ft in sched.get("focus_times", []):
                    if not ft.get("enabled", True):
                        continue
                    fstart = _minutes(ft["start"])
                    fend   = _minutes(ft["end"])
                    in_focus = fstart <= current < fend

                    if in_focus and ip not in paused:
                        pause_device(ip, friendly, config, scheduled=True)
                        config = load_config()
                        print(f"[Schedule] Paused {friendly} — Focus Time: {ft.get('label', '')}")
                    # Note: focus-time unpausing is handled by the Hours-of-Peace
                    # pass above; we deliberately don't auto-unpause here to avoid
                    # conflicts when both blocks overlap.

            # ── Screen Time ───────────────────────────────────────────────────
            config = load_config()
            now    = datetime.now()
            for ip, sched in config.get("schedules", {}).items():
                if not sched.get("enabled"):
                    continue
                st = sched.get("screen_time", {})
                if not st.get("enabled"):
                    continue

                limit_secs = float(st.get("hours", 2)) * 3600
                paused     = config.get("paused_devices", {})
                friendly   = sched.get("name", ip)

                # Start of this device's screen-time "day"
                reset_h, reset_m = map(int, st.get("reset", "00:00").split(":"))
                reset_mins = reset_h * 60 + reset_m
                if now.hour * 60 + now.minute >= reset_mins:
                    day_start = now.replace(hour=reset_h, minute=reset_m, second=0, microsecond=0)
                else:
                    day_start = (now - timedelta(days=1)).replace(
                        hour=reset_h, minute=reset_m, second=0, microsecond=0)

                secs = _screen_secs(ip, day_start.strftime("%Y-%m-%d %H:%M:%S"))

                today_date    = now.strftime("%Y-%m-%d")
                already_fired = sched.get("st_paused_date", "") == today_date

                if secs >= limit_secs and ip not in paused and not already_fired:
                    pause_device(ip, friendly, config, scheduled=True)
                    config = load_config()
                    config["schedules"][ip]["st_paused_date"] = today_date
                    save_config(config)
                    print(f"[Schedule] Paused {friendly} — Screen Time limit reached")

                    hours_used = round(secs / 3600, 1)
                    dash_url     = dashboard_url(config)
                    msg   = (f"{friendly}'s screen time limit of {st['hours']}h has been reached "
                             f"({hours_used}h used today). Internet paused."
                             f"\n\nDashboard: {dash_url}")
                    title = "Screen Time Limit Reached"
                    from alerts import _log_notification
                    _log_notification(title, msg, config.get("ntfy_topic", ""))
                    topic = config.get("ntfy_topic", "")
                    if topic:
                        _ntfy(topic, msg, title)
                    send_telegram(config, msg, title)
                    send_email(config, msg, title)

        except Exception as e:
            print(f"Scheduler error: {e}")

        time.sleep(60)


def _focus_is_active(sched, current_minutes):
    """Return True if any enabled focus time window covers current_minutes."""
    for ft in sched.get("focus_times", []):
        if not ft.get("enabled", True):
            continue
        fstart = _minutes(ft["start"])
        fend   = _minutes(ft["end"])
        if fstart <= current_minutes < fend:
            return True
    return False


def start_scheduler():
    """Spawn the background scheduler as a daemon thread."""
    t = threading.Thread(target=check_schedules, daemon=True)
    t.start()
