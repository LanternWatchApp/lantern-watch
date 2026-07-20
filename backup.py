#!/usr/bin/env python3
"""
Lantern Watch — backup.py

Save and restore a family's complete Lantern Watch setup so a factory reset or
firmware flash (which wipes the router) can't cost them their work. A backup is a
single JSON file that captures:

  • config      — device names/labels/types, profiles, filtering choices,
                  schedules, notification channels, password (everything in
                  lanternwatch_config.json except transient runtime state)
  • AdGuard     — the custom filter rules (custom site blocks, packs, allowlists),
                  which blocklists are enabled, blocked services, and safe-search

Two ways to keep it (see the two layers):
  Layer 1  Download / upload a backup file (works for everyone, no hardware).
  Layer 2  Auto-save to a plugged-in USB drive (hands-off, survives a reset).

Restore is the inverse: drop the config back, then re-apply the AdGuard state so
the light shines again with little effort.
"""

import glob
import hashlib
import json
import os
import re
import subprocess
from datetime import datetime

import config as C

BACKUP_FORMAT  = "lantern-watch-backup"
BACKUP_VERSION = 1  # schema version of the backup file itself

# Config keys that are transient runtime state, not "settings" — never backed up
# or restored (they'd either be stale or, like install_id, must stay unique per
# device so telemetry counts don't collide when a backup lands on another router).
_TRANSIENT_KEYS = {
    "paused_devices", "blocked_content_cooldowns", "last_adult_alert",
    "demo_map", "install_id", "install_ping_version", "captive_portal_acked",
    "latest_known_version", "update_notified_version",
}


# ── Router model (best effort, for the backup's provenance) ───────────────────

def router_model():
    for path in ("/tmp/sysinfo/model", "/proc/device-tree/model"):
        try:
            with open(path) as f:
                m = f.read().strip().strip("\x00")
                if m:
                    return m
        except Exception:
            pass
    return ""


# ── Create ────────────────────────────────────────────────────────────────────

def _adguard_snapshot(config):
    """Best-effort snapshot of the AdGuard state that isn't already in config.
    Every piece is wrapped so an unreachable AGH still yields a config backup."""
    snap = {}
    try:
        from adguard import get_custom_rules
        snap["user_rules"] = get_custom_rules(config)
    except Exception as e:
        print(f"[Backup] user_rules snapshot error: {e}")
    try:
        from adguard import get_all_filter_lists
        snap["blocklists"] = [{"url": f["url"], "enabled": f["enabled"]}
                              for f in get_all_filter_lists(config)]
    except Exception as e:
        print(f"[Backup] blocklists snapshot error: {e}")
    try:
        from adguard import get_blocked_services
        _all, blocked = get_blocked_services(config)
        snap["blocked_services"] = sorted(blocked)
    except Exception as e:
        print(f"[Backup] blocked_services snapshot error: {e}")
    try:
        from adguard import get_safesearch_status
        snap["safesearch"] = get_safesearch_status(config)
    except Exception as e:
        print(f"[Backup] safesearch snapshot error: {e}")
    return snap


def create_backup(config=None, include_logs=False):
    """Build the backup dict. Settings + AdGuard state always; logs only if asked
    (they're big and are history, not identity — off by default)."""
    if config is None:
        config = C.load_config()
    clean_config = {k: v for k, v in config.items() if k not in _TRANSIENT_KEYS}
    backup = {
        "format":       BACKUP_FORMAT,
        "schema":       BACKUP_VERSION,
        "app_version":  C.VERSION,
        "created":      datetime.now().isoformat(timespec="seconds"),
        "router_model": router_model(),
        "config":       clean_config,
        "adguard":      _adguard_snapshot(config),
    }
    if include_logs:
        backup["logs"] = _dump_logs()
    return backup


def _dump_logs(limit=50000):
    """Export recent query-log rows as plain dicts (optional; large)."""
    import sqlite3
    from db import DB_PATH
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM querylog ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[Backup] log dump error: {e}")
        return []


def serialize(backup):
    """Pretty JSON bytes, ready to write to a file or stream to the browser."""
    return json.dumps(backup, indent=2, ensure_ascii=False).encode("utf-8")


def suggested_filename(backup=None):
    day = datetime.now().strftime("%Y-%m-%d")
    return f"lantern-watch-backup-{day}.json"


# ── Parse / validate ──────────────────────────────────────────────────────────

def parse_backup(raw):
    """Accept a dict, str, or bytes; return a validated backup dict or raise."""
    if isinstance(raw, dict):
        data = raw
    else:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        data = json.loads(raw)
    if not isinstance(data, dict) or data.get("format") != BACKUP_FORMAT:
        raise ValueError("This doesn't look like a Lantern Watch backup file.")
    if not isinstance(data.get("config"), dict) or not data["config"]:
        raise ValueError("Backup is missing its settings — it may be corrupted.")
    return data


def describe(backup):
    """A short human summary of what a backup contains (for the confirm dialog)."""
    cfg = backup.get("config", {})
    ag  = backup.get("adguard", {})
    return {
        "app_version": backup.get("app_version", "?"),
        "created":     backup.get("created", "?"),
        "devices":     len(cfg.get("devices", {})),
        "schedules":   len(cfg.get("schedules", {})),
        "custom_blocks": sum(1 for r in ag.get("user_rules", [])
                             if isinstance(r, str) and r.startswith("||")),
        "has_logs":    "logs" in backup,
    }


# ── Restore ───────────────────────────────────────────────────────────────────

def restore_backup(raw):
    """Restore a backup. Writes config first, then rebuilds the AdGuard state so
    the router matches. Every AGH step is best-effort — a config-only restore is
    still valuable if AdGuard is momentarily unreachable. Returns a warnings list
    (empty = clean)."""
    backup = parse_backup(raw)
    warnings = []

    # 1. Settings — the big one (device names, profiles, channels, password).
    #    Merge onto DEFAULTS so a backup from an older schema still gets any newer
    #    keys, and preserve THIS device's own transient state (install_id etc.).
    live = C.load_config()
    restored = dict(C.DEFAULTS)
    restored.update(backup["config"])
    for k in _TRANSIENT_KEYS:
        if k in live:
            restored[k] = live[k]
    restored["first_run"] = False
    C.save_config(restored)
    config = restored

    # 2. Rebuild AdGuard state from the snapshot + restored config.
    ag = backup.get("adguard", {})

    # 2a. Blocklists: make sure the default-on optional lists and any the user had
    #     enabled exist, then reconcile each list's enabled/disabled state.
    try:
        from adguard import (install_default_optional_lists, apply_optional_lists,
                             get_all_filter_lists, set_filter_enabled)
        install_default_optional_lists(config)
        apply_optional_lists(config, config.get("extra_lists", []))
        want = {b["url"]: b["enabled"] for b in ag.get("blocklists", [])}
        for f in get_all_filter_lists(config):
            if f["url"] in want and want[f["url"]] != f["enabled"]:
                set_filter_enabled(config, f["url"], f["name"], want[f["url"]])
    except Exception as e:
        warnings.append(f"Some blocklists may need review ({e}).")

    # 2b. Custom rules verbatim — restores custom site blocks, packs, allowlists,
    #     and the social/DoH/allowlist marker sections exactly as they were.
    if "user_rules" in ag:
        try:
            from adguard import _ag_post
            _ag_post(config, "/filtering/set_rules", {"rules": ag["user_rules"]})
        except Exception as e:
            warnings.append(f"Custom block rules couldn't be restored ({e}).")

    # 2c. Social profile (rules + safe search for standard profiles).
    try:
        from adguard import apply_social_profile
        profile = config.get("social_profile", "moderate")
        custom  = config.get("social_custom", {}).get("platforms")
        apply_social_profile(profile, config, custom_platforms=custom)
    except Exception as e:
        warnings.append(f"Social profile couldn't be re-applied ({e}).")

    # 2d. Safe search, exactly as captured (covers per-engine custom choices).
    ss = ag.get("safesearch")
    if isinstance(ss, dict):
        try:
            from adguard import set_safesearch_engines, SAFE_SEARCH_ENGINES
            set_safesearch_engines(config, {e: bool(ss.get(e)) for e in SAFE_SEARCH_ENGINES})
        except Exception as e:
            warnings.append(f"Safe-search couldn't be re-applied ({e}).")

    # 2e. Blocked services.
    if "blocked_services" in ag:
        try:
            from adguard import set_blocked_services
            set_blocked_services(config, ag["blocked_services"])
        except Exception as e:
            warnings.append(f"Blocked services couldn't be re-applied ({e}).")

    # 2f. Always-on infrastructure (idempotent — tops up rules a pre-this-version
    #     backup lacked: service allowlist, gentle DoH mitigation).
    try:
        from adguard import apply_service_allowlist, apply_doh_dns_mitigation
        apply_doh_dns_mitigation(config)
        apply_service_allowlist(config)
    except Exception as e:
        warnings.append(f"DoH/allowlist rules couldn't be refreshed ({e}).")

    # 2g. Strict DoH enforcement + captive portal, per the restored toggles.
    try:
        from adguard import apply_doh_iptables
        apply_doh_iptables(bool(config.get("doh_blocking")))
    except Exception as e:
        warnings.append(f"Strict DoH enforcement couldn't be re-applied ({e}).")
    try:
        if config.get("captive_portal"):
            from portal import setup_captive_portal
            setup_captive_portal(config)
    except Exception as e:
        warnings.append(f"Captive portal couldn't be re-applied ({e}).")

    return warnings


# ══ Layer 2 — USB drive auto-backup ═══════════════════════════════════════════
# A plugged-in USB drive gives hands-off, hardware-backed resilience: the backup
# is written to the drive automatically, so it survives even a full factory reset
# (the drive is external). We only ever ADD a "LanternWatch" folder — existing
# files on the user's drive are never touched or formatted.

USB_SUBDIR = "LanternWatch"          # our folder on the drive
USB_LATEST = "lantern-watch-backup.json"
USB_MOUNT  = "/mnt/lw-usb"           # where WE mount it if nothing else has
_KEEP_DATED = 7                      # rotating history copies to retain
# Marker (in tmpfs, so it auto-clears on reboot) recording a drive the user
# "safely ejected". While set, auto-backup won't remount that drive — otherwise
# our own next status check / settings save would silently remount it.
_EJECT_MARKER = "/tmp/lw_usb_ejected"


def _usb_partitions():
    """USB storage partitions as [(device, fstype)]. The internal eMMC
    (mmcblk*) is excluded — only removable sd* devices count."""
    parts, seen = [], set()
    try:
        out = subprocess.run(["block", "info"], capture_output=True,
                             text=True, timeout=8).stdout
        for line in out.splitlines():
            dev = line.split(":", 1)[0].strip()
            if dev.startswith("/dev/sd") and re.search(r"\d$", dev):
                m = re.search(r'TYPE="([^"]+)"', line)
                parts.append((dev, m.group(1) if m else ""))
                seen.add(dev)
    except Exception:
        pass
    for dev in sorted(glob.glob("/dev/sd*")):          # fallback: raw /dev scan
        if re.search(r"\d$", dev) and dev not in seen:
            parts.append((dev, ""))
    return parts


def _mounts():
    """device -> mountpoint from /proc/mounts."""
    m = {}
    try:
        with open("/proc/mounts") as f:
            for line in f:
                col = line.split()
                if len(col) >= 2 and col[0].startswith("/dev/"):
                    m[col[0]] = col[1].replace("\\040", " ")
    except Exception:
        pass
    return m


def _read_eject_marker():
    try:
        with open(_EJECT_MARKER) as f:
            return f.read().strip() or None
    except Exception:
        return None


def _clear_eject_marker():
    try:
        os.remove(_EJECT_MARKER)
    except Exception:
        pass


def _eject_in_effect():
    """The ejected device if the eject still stands, else None — self-healing:
    clears the marker once the drive is physically pulled (device gone) or gets
    remounted (e.g. GL.iNet on reinsertion), so auto-backup resumes on its own."""
    dev = _read_eject_marker()
    if not dev:
        return None
    if dev not in [d for d, _ in _usb_partitions()]:
        _clear_eject_marker(); return None          # physically removed → forget
    if dev in _mounts():
        _clear_eject_marker(); return None          # got remounted → honor that
    return dev                                       # still ejected, sitting idle


def eject_usb():
    """Flush writes and unmount the USB drive so it's safe to physically remove.
    Marks it ejected so auto-backup leaves it alone until it's pulled/reinserted
    (or the router reboots). Returns a status dict."""
    parts  = _usb_partitions()
    mounts = _mounts()
    dev = mp = None
    for d, _f in parts:
        if d in mounts:
            dev, mp = d, mounts[d]
            break
    if not dev:                                      # nothing mounted
        if parts:                                    # but a drive is plugged in
            try:
                with open(_EJECT_MARKER, "w") as f:
                    f.write(parts[0][0])
            except Exception:
                pass
            return {"ok": True, "already": True}
        return {"ok": False, "error": "No USB drive to eject."}
    try:
        subprocess.run(["sync"], timeout=15)
        subprocess.run(["umount", mp], capture_output=True, text=True, timeout=20)
        if dev in _mounts():                         # busy — try a lazy detach
            subprocess.run(["umount", "-l", mp], capture_output=True, timeout=10)
        if dev in _mounts():
            return {"ok": False, "error": "The drive is busy — try again in a moment."}
        with open(_EJECT_MARKER, "w") as f:
            f.write(dev)
        print(f"[USB] safely ejected {dev} ({mp})")
        return {"ok": True}
    except Exception as e:
        print(f"[USB] eject error: {e}")
        return {"ok": False, "error": str(e)}


def usb_mountpoint(auto_mount=True):
    """Return the mount path of a USB drive, mounting it ourselves if the router
    hasn't already. None if no drive is present/mountable. We prefer an existing
    mount (GL.iNet's own auto-mounter) and only self-mount as a fallback."""
    if _eject_in_effect():
        return None                                  # respect a safe-eject
    parts = _usb_partitions()
    if not parts:
        return None
    mounts = _mounts()
    for dev, _fst in parts:
        if dev in mounts:
            return mounts[dev]
    if not auto_mount:
        return None
    dev, _fst = parts[0]
    try:
        os.makedirs(USB_MOUNT, exist_ok=True)
        subprocess.run(["mount", dev, USB_MOUNT], capture_output=True, timeout=20)
        return _mounts().get(dev)
    except Exception as e:
        print(f"[USB] mount {dev} failed: {e}")
        return None


def usb_dir(create=True):
    """Path to our LanternWatch folder on the drive, or None if no drive."""
    mp = usb_mountpoint(auto_mount=True)
    if not mp:
        return None
    d = os.path.join(mp, USB_SUBDIR)
    if create:
        try:
            os.makedirs(d, exist_ok=True)
        except Exception as e:
            print(f"[USB] cannot create {d}: {e}")
            return None
    return d


def _content_hash(backup):
    """Hash of a backup's meaningful content (ignoring the timestamp) so we can
    skip rewriting an identical file — saving needless flash writes."""
    b = {k: v for k, v in backup.items() if k not in ("created", "router_model", "content_hash")}
    return hashlib.sha256(
        json.dumps(b, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _rotate_dated(folder, data):
    """Keep a rolling set of dated snapshots alongside the 'latest' file, so a
    bad restore or accidental change can be rolled back to an earlier day."""
    try:
        dated = os.path.join(folder, f"lantern-watch-backup-{datetime.now():%Y-%m-%d}.json")
        with open(dated, "wb") as f:
            f.write(data)
        history = sorted(glob.glob(os.path.join(folder, "lantern-watch-backup-*.json")))
        for old in history[:-_KEEP_DATED]:
            try:
                os.remove(old)
            except Exception:
                pass
    except Exception as e:
        print(f"[USB] dated snapshot error: {e}")


def maybe_write_usb_backup(config=None, force=False):
    """Write a backup to the USB drive if one is present and the content changed
    (or force=True). Returns a status dict. Safe to call often — it no-ops when
    nothing is plugged in and skips writes when the content is unchanged."""
    if force:
        _clear_eject_marker()   # explicit "back up now" = intent to use the drive
    d = usb_dir()
    if not d:
        return {"written": False, "reason": "no-usb"}
    try:
        b = create_backup(config)
        h = _content_hash(b)
        path = os.path.join(d, USB_LATEST)
        if not force and os.path.exists(path):
            try:
                with open(path) as f:
                    if json.load(f).get("content_hash") == h:
                        return {"written": False, "reason": "unchanged", "path": path}
            except Exception:
                pass
        b["content_hash"] = h
        data = serialize(b)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        _rotate_dated(d, data)
        print(f"[USB] backup written to {path} (v{b['app_version']})")
        return {"written": True, "path": path, "app_version": b["app_version"]}
    except Exception as e:
        print(f"[USB] write error: {e}")
        return {"written": False, "reason": str(e)}


def read_usb_backup():
    """Load the latest backup from the USB drive, or None if none is present."""
    d = usb_dir(create=False)
    if not d:
        return None
    path = os.path.join(d, USB_LATEST)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        print(f"[USB] read error: {e}")
        return None


def usb_status():
    """Summary of the USB drive + its Lantern Watch backup, for the Settings UI."""
    if _eject_in_effect():
        return {"present": False, "ejected": True}
    mp = usb_mountpoint(auto_mount=True)
    if not mp:
        return {"present": False}
    info = {"present": True, "mountpoint": mp}
    try:
        st = os.statvfs(mp)
        info["free_mb"] = int(st.f_bavail * st.f_frsize / (1024 * 1024))
    except Exception:
        pass
    latest = os.path.join(mp, USB_SUBDIR, USB_LATEST)
    if os.path.exists(latest):
        try:
            with open(latest) as f:
                b = json.load(f)
            info["last_backup"]  = b.get("created")
            info["app_version"]  = b.get("app_version")
            info["devices"]      = len(b.get("config", {}).get("devices", {}))
        except Exception:
            pass
    return info


def auto_backup_usb(config=None):
    """Fire-and-forget USB mirror after a settings change. Never raises."""
    try:
        return maybe_write_usb_backup(config)
    except Exception as e:
        print(f"[USB] auto-backup error: {e}")
        return {"written": False, "reason": str(e)}
