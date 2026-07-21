#!/usr/bin/env python3
"""
Lantern Watch — routes.py
HTTP request handler (GET + POST) and session management.
"""

import hashlib
import json
import os
import socket
import struct
import sqlite3
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote, unquote

from config import load_config, save_config, label, is_first_run, is_pauseable, VERSION, UPDATE_CHECK_URL, UPDATE_RELEASES_URL, is_newer_version, router_lan_ip, dashboard_url
import recovery
from adguard import (apply_social_profile, clear_social_blocking, get_adguard_stats,
                     apply_adguard_setup, get_adguard_setup_status, RECOMMENDED_LISTS,
                     reset_adguard_stats, restore_client_global,
                     get_safesearch_status,
                     get_blocked_services, set_blocked_services)
from db import get_stats, DB_PATH, clear_notifications, get_all_known_devices, get_querylog_entries, get_querylog_devices, get_recent_blocks
from scheduler import pause_device, unpause_device
from pages import (
    get_welcome_page, get_welcome_error_page, get_adguard_wizard_page,
    get_adguard_enable_page, get_notifications_wizard_page, get_login_page,
    send_ntfy, send_test_ntfy, send_test_telegram, send_test_email,
    build_main, build_detail, build_domain_detail, build_schedule_page,
    build_social, build_findhelp, build_blocked_page, build_portal_page,
    build_devices_page, build_admin,
    build_notifications, build_querylog_page, build_blocked_services_page,
    _demo, _FAVICON_SVG,
)

# ── Session store (in-memory; resets on restart) ──────────────────────────────
SESSIONS: set = set()


def make_session_token() -> str:
    return hashlib.sha256(os.urandom(32)).hexdigest()


def check_auth(handler) -> bool:
    cookie = handler.headers.get("Cookie", "")
    for part in cookie.split(";"):
        part = part.strip()
        if part.startswith("lw_session="):
            token = part[11:]
            if token in SESSIONS:
                return True
    return False


# ── Handler ───────────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):

    # ── GET ───────────────────────────────────────────────────────────────────
    def do_GET(self):
        parsed = urlparse(self.path)

        # Favicon (browser-tab icon) — our lantern mark, served as SVG. Handled
        # up front, before the auth wall, so it appears on the login page too.
        if parsed.path in ("/favicon.svg", "/favicon.ico"):
            body = _FAVICON_SVG.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Cache-Control", "public, max-age=86400")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # Detect whether this request arrived via the iptables port-80 redirect.
        # SO_ORIGINAL_DST (80) returns the pre-NAT destination; if orig port == 80
        # the request was intercepted from a LAN device hitting port 80.
        _via_port80 = False
        try:
            _orig = self.request.getsockopt(socket.SOL_IP, 80, 16)
            _via_port80 = struct.unpack("!H", _orig[2:4])[0] == 80
        except Exception:
            pass

        host = self.headers.get("Host", "")

        # This router's real LAN IP — never assume 192.168.8.1. GL.iNet repeater
        # mode auto-shifts the LAN subnet (e.g. 192.168.10.1) when the uplink
        # already uses 192.168.8.x, which is exactly how a travel router is used.
        _lan_ip = router_lan_ip()

        if _via_port80 and (_lan_ip in host or "192.168.8.1" in host or not host):
            # Request came through the port-80 redirect but is addressed to the
            # router itself (e.g. browser following AdGuard's 302 to port 80).
            # Send them to the GL.iNet panel over HTTPS, which bypasses iptables.
            self._redirect(f"https://{_lan_ip}")
            return

        # Load config here — needed for captive portal check before auth wall.
        config = load_config()

        # Blocked-domain / captive-portal detection: iptables redirects port-80
        # LAN hits to :8081. Requests arrive with a foreign Host header. The
        # dashboard's own addresses — the LAN IP, localhost, and any friendly
        # names in config["local_hostnames"] (registered as AdGuard DNS rewrites)
        # — must NOT be treated as blocked domains.
        host_name = host.split(":")[0].strip().lower()
        self_hosts = {_lan_ip, "192.168.8.1", "127.0.0.1", "localhost"}
        self_hosts.update(h.strip().lower() for h in config.get("local_hostnames", []) if h.strip())
        if host and host_name not in self_hosts:
            if config.get("captive_portal"):
                from portal import is_portal_acked
                if not is_portal_acked(self.client_address[0], config):
                    from urllib.parse import quote as _quote
                    _path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
                    _dest = _quote(f"http://{host}{_path}", safe="")
                    self._redirect(f"http://{_lan_ip}:8081/portal?dest={_dest}")
                    return
            self._redirect(f"http://{_lan_ip}:8081/blocked")
            return

        # Auth wall — only login and first-run setup pages are public
        if parsed.path not in ("/login", "/setup/password", "/setup/adguard", "/blocked", "/findhelp", "/portal") and not check_auth(self):
            # On a brand-new install, take the user straight to "set your password"
            # instead of the login screen. /setup/password is already public, so
            # this removes any need to find the generated temporary password in the
            # installer output — they just open the dashboard and pick a password.
            self._redirect("/setup/password" if is_first_run(config) else "/login")
            return

        try:
            if parsed.path == "/setup/password":
                html = get_welcome_page()

            elif parsed.path == "/setup/notifications":
                html = get_notifications_wizard_page(config)

            elif parsed.path == "/setup/adguard":
                # Skip if already applied (flag set) or if AdGuard already fully configured
                if config.get("adguard_setup_complete"):
                    self._redirect("/setup/notifications")
                    return
                status = get_adguard_setup_status(config)
                if status["connected"]:
                    all_urls = {lst["url"] for lst in RECOMMENDED_LISTS}
                    already_done = (all_urls <= status["existing_urls"]
                                    and (status["parental"] or status["safe_browsing"]))
                    if already_done:
                        config["adguard_setup_complete"] = True
                        save_config(config)
                        self._redirect("/setup/notifications")
                        return
                html = get_adguard_wizard_page()

            elif parsed.path == "/login":
                html = get_login_page(first_run=is_first_run(config))

            elif parsed.path == "/logout":
                cookie = self.headers.get("Cookie", "")
                for part in cookie.split(";"):
                    part = part.strip()
                    if part.startswith("lw_session="):
                        SESSIONS.discard(part[11:])
                self.send_response(302)
                self.send_header("Set-Cookie", "lw_session=; Max-Age=0; Path=/")
                self.send_header("Location", "/login")
                self.end_headers()
                return

            elif parsed.path == "/device/schedule":
                params = parse_qs(parsed.query)
                name   = unquote(params.get("name", [""])[0])
                ip     = params.get("ip", [""])[0]
                html   = build_schedule_page(name, ip, config)

            elif parsed.path == "/device/pause":
                params        = parse_qs(parsed.query)
                ip            = params.get("ip",   [""])[0]
                name          = params.get("name", [""])[0]
                friendly_name = label(unquote(name), config)
                if ip:
                    pause_device(ip, friendly_name, config)
                dest = (f"/device?name={quote(unquote(name))}&ip={quote(ip)}"
                        if params.get("ref", [""])[0] == "device" else "/")
                self._redirect(dest)
                return

            elif parsed.path == "/device/unpause":
                params = parse_qs(parsed.query)
                ip     = params.get("ip",   [""])[0]
                name   = params.get("name", [""])[0]
                if ip:
                    unpause_device(ip, config)
                dest = (f"/device?name={quote(unquote(name))}&ip={quote(ip)}"
                        if params.get("ref", [""])[0] == "device" else "/")
                self._redirect(dest)
                return

            elif parsed.path == "/device":
                params = parse_qs(parsed.query)
                html   = build_detail(
                    unquote(params.get("name", [""])[0]),
                    config,
                    params.get("ip", [""])[0],
                )

            elif parsed.path == "/domain":
                params = parse_qs(parsed.query)
                html   = build_domain_detail(unquote(params.get("name", [""])[0]), config)

            elif parsed.path == "/social":
                params     = parse_qs(parsed.query)
                saved_flag = "saved" in params
                error_flag = "error" in params
                html = build_social(
                    config,
                    saved=saved_flag,
                    error="Failed to connect to AdGuard." if error_flag else "",
                )

            elif parsed.path == "/portal":
                # parse_qs here, like every other branch: `params` is assigned in
                # sibling branches, so Python treats it as a local for the whole
                # method — reading it without assigning raises UnboundLocalError.
                params = parse_qs(parsed.query)
                dest   = params.get("dest", [""])[0]
                html   = build_portal_page(dest, config)

            elif parsed.path == "/blocked":
                html = build_blocked_page()

            elif parsed.path == "/findhelp":
                html = build_findhelp(config)

            elif parsed.path == "/notifications":
                cleared = "cleared" in parse_qs(parsed.query)
                html = build_notifications(config, cleared=cleared)

            elif parsed.path == "/querylog":
                params      = parse_qs(parsed.query)
                ql_filters  = {
                    "device":  params.get("device",  [""])[0],
                    "blocked": params.get("blocked", [""])[0],
                    "window":  params.get("window",  ["1h"])[0],
                    "q":       params.get("q",       [""])[0],
                    "offset":  params.get("offset",  ["0"])[0],
                }
                window   = ql_filters["window"]
                entries, total = get_querylog_entries(
                    device      = ql_filters["device"] or None,
                    blocked_only= ql_filters["blocked"] == "1",
                    window      = window,
                    offset      = int(ql_filters["offset"]),
                    q           = ql_filters["q"] or None,
                )
                devices = get_querylog_devices(window)
                html = build_querylog_page(entries, devices, total, ql_filters, config)

            elif parsed.path == "/api/recent-blocks":
                # Near-real-time feed of blocked DNS queries. The dashboard can
                # poll this (e.g. ?since=<last ts>) for a live "blocked activity"
                # ticker. Device names honor friendly labels and demo mode so the
                # feed matches what every other page shows.
                qp    = parse_qs(parsed.query)
                since = qp.get("since", [""])[0] or None
                limit = qp.get("limit", ["50"])[0]
                rows  = get_recent_blocks(since=since, limit=limit)
                items = []
                for r in rows:
                    cname = r["client_name"] or r["client_ip"]
                    items.append({
                        "ts":     r["ts"],
                        "device": _demo(cname, label(cname, config), config),
                        "domain": r["domain"],
                        "reason": r["reason"],
                    })
                payload = json.dumps({
                    "ok": True,
                    "blocks": items,
                    "latest": items[0]["ts"] if items else since,
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(payload)
                return

            elif parsed.path == "/admin/devices":
                _q = parse_qs(parsed.query)
                html = build_devices_page(config,
                                          redetect=_q.get("redetect", ["0"])[0] == "1",
                                          autoname=_q.get("autoname", ["0"])[0] == "1",
                                          sort=_q.get("sort", ["name"])[0],
                                          flt=_q.get("flt", [""])[0])

            elif parsed.path == "/admin":
                html = build_admin(config)

            elif parsed.path == "/notifications/test/ntfy":
                ok, err = send_test_ntfy(config)
                html = build_notifications(config, tested_channel="ntfy", test_ok=ok, test_error=err)

            elif parsed.path == "/notifications/test/telegram":
                ok, err = send_test_telegram(config)
                html = build_notifications(config, tested_channel="telegram", test_ok=ok, test_error=err)

            elif parsed.path == "/notifications/test/email":
                ok, err = send_test_email(config)
                html = build_notifications(config, tested_channel="email", test_ok=ok, test_error=err)

            elif parsed.path == "/notifications/test/summary":
                from urllib.parse import parse_qs as _pq
                stype = _pq(parsed.query).get("type", ["daily"])[0]
                try:
                    from alerts import send_daily_summary, send_weekly_summary
                    if stype == "weekly":
                        send_weekly_summary(config); _lbl = "Weekly summary"
                    else:
                        send_daily_summary(config); _lbl = "Daily summary"
                    ok, err = True, ""
                except Exception as _e:
                    ok, err, _lbl = False, str(_e), f"{stype} summary"
                html = build_notifications(config, tested_channel=_lbl, test_ok=ok, test_error=err)

            elif parsed.path == "/admin/adguard/apply":
                from adguard import recommended_ids, enforce_profile_filters, heuristic_toggles
                try:
                    enforce_profile_filters(config)   # LITE: drop GL.iNet's heavy default first
                except Exception as _e:
                    print(f"[Admin] profile filter enforce error: {_e}")
                added, errors = apply_adguard_setup(
                    config, recommended_ids(config), **heuristic_toggles(config),
                )
                config["adguard_setup_complete"] = True
                save_config(config)
                print(f"[Admin] AdGuard apply — {added} lists added, errors: {errors}")
                if errors:
                    # Surface ALL warnings (e.g. Safe Browsing / Parental toggles
                    # that didn't take via the API on GL.iNet), even when filter
                    # lists were added successfully. Previously this only showed
                    # when added == 0, so a partial failure looked like full
                    # success and gave parents false confidence.
                    html = build_admin(config, adguard_apply_error="<br>".join(errors))
                else:
                    html = build_admin(config, adguard_applied=True)

            elif parsed.path == "/blocked-services":
                try:
                    all_svcs, blocked_ids = get_blocked_services(config)
                    ss_on = get_safesearch_status(config).get("enabled", False)
                except Exception:
                    all_svcs, blocked_ids, ss_on = [], set(), False
                saved = "saved" in parse_qs(parsed.query)
                html = build_blocked_services_page(all_svcs, blocked_ids, ss_on, config, saved_msg="&#x2705; Saved!" if saved else "")

            elif parsed.path == "/admin/clear":
                html = build_admin(config, confirm_clear=True)

            elif parsed.path == "/admin/clear_all":
                html = build_admin(config, confirm_clear_all=True)

            elif parsed.path == "/admin/check-update":
                # Read the public GitHub repo directly — the newest git tag is the
                # source of truth. No telemetry; just an anonymous tag-list fetch.
                try:
                    req  = urllib.request.Request(
                        UPDATE_CHECK_URL,
                        headers={"User-Agent": "LanternWatch",
                                 "Accept": "application/vnd.github+json"},
                    )
                    resp = urllib.request.urlopen(req, timeout=8)
                    tags = json.loads(resp.read().decode())
                    latest = ""
                    for t in tags:
                        n = (t.get("name") or "").lstrip("v")
                        if n and (not latest or is_newer_version(n, latest)):
                            latest = n
                    # Remember it so the dashboard badge + summary line reflect a
                    # manual check immediately (not just the daily background one).
                    if latest and config.get("latest_known_version") != latest:
                        config["latest_known_version"] = latest
                        save_config(config)
                    data = {
                        "ok": True,
                        "current_version":  VERSION,
                        "latest_version":   latest or VERSION,
                        "update_available": is_newer_version(latest, VERSION) if latest else False,
                        "update_url":       UPDATE_RELEASES_URL,
                    }
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(data).encode())
                except Exception as _e:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok": False, "error": str(_e)}).encode())
                return

            elif parsed.path == "/admin/backup/download":
                # Stream a complete settings backup as a downloadable JSON file.
                import backup as _bk
                data = _bk.serialize(_bk.create_backup(config,
                                     include_logs="logs" in parse_qs(parsed.query)))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Disposition",
                                 f'attachment; filename="{_bk.suggested_filename()}"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return

            elif parsed.path == "/pause_all":
                for dev in get_all_known_devices():
                    name = dev["client_name"]
                    ip   = dev["client_ip"]
                    if ip and is_pauseable(name, config):
                        if ip not in config.get("paused_devices", {}):
                            pause_device(ip, label(name, config), config)
                            config = load_config()
                self._redirect("/")
                return

            elif parsed.path == "/unpause_all":
                paused = config.get("paused_devices", {})
                for ip in list(paused.keys()):
                    pause_name = paused[ip].get("name", "")
                    matched = next(
                        (n for n, d in config.get("devices", {}).items()
                         if d.get("label", n) == pause_name or n == pause_name),
                        None,
                    )
                    if matched is None or is_pauseable(matched, config):
                        unpause_device(ip, config)
                        config = load_config()
                self._redirect("/")
                return

            else:
                # Main dashboard
                devices, totals, top_blocked, top_domains, screen_times, adult_domains = get_stats(config)
                html = build_main(devices, totals, top_blocked, top_domains, screen_times, adult_domains, config)

            self._send_html(html)

        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(f"Error: {e}".encode())

    # ── POST ──────────────────────────────────────────────────────────────────
    def do_POST(self):
        parsed = urlparse(self.path)
        config = load_config()
        try:
            length = int(self.headers.get("Content-Length", 0))
            body   = self.rfile.read(length).decode()
            params = parse_qs(body)

            # ── Captive portal acknowledgment (public endpoint) ──────────────
            if parsed.path == "/portal/ack":
                from portal import ack_portal_ip
                dest = params.get("dest", [""])[0]
                ack_portal_ip(self.client_address[0], config)
                self._redirect(dest or f"http://{router_lan_ip()}:8081/")
                return

            # ── First-run password setup (public endpoint) ────────────────────
            if parsed.path == "/setup/password":
                username = params.get("username", ["admin"])[0].strip() or "admin"
                password = params.get("password", [""])[0]
                confirm  = params.get("confirm",  [""])[0]
                _blocklist = {"lamp623", "lanternwatch", "admin", "password",
                              "12345678", "123456", "qwerty", "letmein"}
                if len(password) < 8:
                    html = get_welcome_error_page("Password must be at least 8 characters.")
                elif password.lower() in _blocklist:
                    html = get_welcome_error_page("That password is too easy to guess — choose something more unique.")
                elif password != confirm:
                    html = get_welcome_error_page("Passwords do not match. Please try again.")
                else:
                    config["lw_username"] = username
                    config["lw_password"] = password
                    config["first_run"]   = False
                    save_config(config)
                    token = make_session_token()
                    SESSIONS.add(token)
                    self.send_response(302)
                    self.send_header("Set-Cookie", f"lw_session={token}; Path=/; HttpOnly")
                    self.send_header("Location", "/setup/adguard")
                    self.end_headers()
                    return
                self._send_html(html)
                return

            # ── Login (public endpoint) ───────────────────────────────────────
            elif parsed.path == "/login":
                username = params.get("username", [""])[0]
                password = params.get("password", [""])[0]
                stored_u = config.get("lw_username", "admin")
                stored_p = config.get("lw_password", "")

                if username == stored_u and password == stored_p:
                    if is_first_run(config):
                        self._redirect("/setup/password")
                        return
                    token = make_session_token()
                    SESSIONS.add(token)
                    self.send_response(302)
                    self.send_header("Set-Cookie", f"lw_session={token}; Path=/; HttpOnly")
                    self.send_header("Location", "/")
                    self.end_headers()
                    return
                else:
                    self._send_html(get_login_page("Invalid username or password"))
                    return

            # ── AdGuard connectivity check (public — called from step 1 wizard) ────
            elif parsed.path == "/setup/check-adguard":
                try:
                    status    = get_adguard_setup_status(config)
                    connected = bool(status.get("connected"))
                except Exception:
                    connected = False
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"connected": connected}).encode())
                return

            # ── Final wizard step (stats opt-in) ─────────────────────────────────
            # Notification channels used to be configured here, but the wizard was
            # simplified to just the anonymous-stats choice — channels, alert types,
            # schedules and social profiles are all set up later in the dashboard.
            elif parsed.path == "/setup/notifications":
                config["telemetry_enabled"] = "telemetry_enabled" in params
                save_config(config)
                if config["telemetry_enabled"]:
                    # Fire one ping now (background thread) so the opt-in shows up
                    # immediately instead of waiting for the daily slot.
                    try:
                        import threading
                        from alerts import send_telemetry
                        threading.Thread(target=send_telemetry, args=(dict(config),),
                                         daemon=True).start()
                    except Exception:
                        pass
                self._redirect("/")
                return

            # ── AdGuard first-run setup (public — runs right after password wizard) ─
            elif parsed.path == "/setup/adguard":
                try:
                    from adguard import recommended_ids, enforce_profile_filters, heuristic_toggles
                    # LITE first: disable GL.iNet's heavy 158K list BEFORE adding
                    # anything, so setup can't OOM a small router mid-setup.
                    try:
                        enforce_profile_filters(config)
                    except Exception as _e:
                        print(f"[Setup] profile filter enforce error: {_e}")
                    added, errors = apply_adguard_setup(
                        config, recommended_ids(config), **heuristic_toggles(config),
                    )
                    # Profile-appropriate optional lists + always-on DoH mitigation.
                    from adguard import (install_default_optional_lists, apply_doh_dns_mitigation,
                                         apply_service_allowlist)
                    try:
                        install_default_optional_lists(config)
                        apply_doh_dns_mitigation(config)
                        apply_service_allowlist(config)
                    except Exception as _e:
                        print(f"[Setup] optional/DoH defaults error: {_e}")
                    config["adguard_setup_complete"] = True
                    save_config(config)
                    print(f"[Setup] AdGuard setup complete — {added} lists added, {len(errors)} errors: {errors}")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok": True, "added": added}).encode())
                except Exception as e:
                    print(f"[Setup] AdGuard setup failed: {e}")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode())
                return

            # ── Password recovery — public endpoints ──────────────────────────
            elif parsed.path == "/auth/forgot-password":
                token, channels = recovery.generate_code(config)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                if token:
                    self.wfile.write(json.dumps({"ok": True, "token": token, "channels": channels}).encode())
                else:
                    self.wfile.write(json.dumps({"ok": False, "error": "No notification channels configured. Ask your admin to set up ntfy, Telegram, or email in Settings."}).encode())
                return

            elif parsed.path == "/auth/verify-code":
                try:
                    data       = json.loads(body) if body else {}
                    tok        = data.get("token", "")
                    code       = data.get("code", "")
                    ok, result = recovery.verify_code(tok, code)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    if ok:
                        self.wfile.write(json.dumps({"ok": True, "reset_token": result}).encode())
                    else:
                        self.wfile.write(json.dumps({"ok": False, "error": result}).encode())
                except Exception as e:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode())
                return

            elif parsed.path == "/auth/reset-password":
                try:
                    data         = json.loads(body) if body else {}
                    reset_token  = data.get("reset_token", "")
                    new_password = data.get("password", "")
                    confirm      = data.get("confirm", "")
                    if not recovery.validate_reset_token(reset_token):
                        raise ValueError("Reset link expired. Please start over.")
                    if len(new_password) < 8:
                        raise ValueError("Password must be at least 8 characters.")
                    if new_password != confirm:
                        raise ValueError("Passwords do not match.")
                    config["lw_password"] = new_password
                    save_config(config)
                    recovery.consume_reset_token(reset_token)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok": True}).encode())
                except Exception as e:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode())
                return

            # ── Auth wall for all other POSTs ─────────────────────────────────
            if not check_auth(self):
                self._redirect("/login")
                return

            # ── Authenticated POST routes ─────────────────────────────────────
            if parsed.path == "/admin/save":
                config["lw_username"]  = params.get("lw_username",  ["admin"])[0].strip() or "admin"
                new_pw = params.get("lw_password", [""])[0]
                if new_pw:
                    config["lw_password"] = new_pw
                config["adguard"] = {
                    "url":      params.get("ag_url",      ["http://127.0.0.1:3000"])[0],
                    "username": params.get("ag_username", [""])[0],
                    "password": params.get("ag_password", [""])[0],
                }
                config["retention_days"] = int(params.get("retention_days", [60])[0] or "60")
                # Notification settings (channels, alert types, summaries) are
                # saved separately from the Notifications page → /notifications/save.
                # Captive portal toggle
                old_portal = config.get("captive_portal", False)
                new_portal  = "captive_portal" in params
                config["captive_portal"] = new_portal
                # Encrypted-DNS (DoH/DoT) bypass blocking toggle
                old_doh = config.get("doh_blocking", False)
                new_doh = "doh_blocking" in params
                config["doh_blocking"] = new_doh
                old_tel = config.get("telemetry_enabled", False)
                config["telemetry_enabled"] = "telemetry_enabled" in params
                config["demo_mode"] = "demo_mode" in params
                if config["demo_mode"]:
                    from pages import build_demo_map
                    config["demo_map"] = build_demo_map(config, DB_PATH)
                save_config(config)
                if config["telemetry_enabled"] and not old_tel:
                    # Just opted in — fire one ping now (background thread) so it
                    # appears immediately, not at the next daily slot.
                    try:
                        import threading
                        from alerts import send_telemetry
                        threading.Thread(target=send_telemetry, args=(dict(config),),
                                         daemon=True).start()
                    except Exception:
                        pass
                from portal import setup_captive_portal, teardown_captive_portal
                if new_portal and not old_portal:
                    setup_captive_portal(config)
                elif not new_portal and old_portal:
                    teardown_captive_portal()
                if new_doh != old_doh:
                    # The gentle DNS-level mitigation (Firefox canary + DoH
                    # hostnames) is always on; this toggle only controls the
                    # stricter iptables enforcement (DoT :853 + DoH resolver IPs).
                    from adguard import apply_doh_iptables
                    try:
                        apply_doh_iptables(new_doh)
                    except Exception as _e:
                        print(f"[DoH] toggle error: {_e}")
                try:
                    import backup as _bk; _bk.auto_backup_usb(config)
                except Exception:
                    pass
                html = build_admin(config, saved=True)

            elif parsed.path == "/admin/devices/save":
                devices = config.get("devices", {})
                for key in params:
                    if key.startswith("label_"):
                        enc_name = key[6:]
                        name     = unquote(enc_name)
                        if name not in devices:
                            devices[name] = {}
                        # In demo mode the Name field shows a fake name — don't let it
                        # overwrite the real label (type/monitor still save normally).
                        if not config.get("demo_mode"):
                            devices[name]["label"] = params[key][0]
                        devices[name]["type"]    = params.get(f"type_{enc_name}", ["person"])[0]
                        devices[name]["monitor"] = f"monitor_{enc_name}" in params
                config["devices"] = devices
                save_config(config)
                # No device type is exempt from filtering. A work laptop's real
                # traffic already tunnels through its corporate VPN (invisible to
                # us), and when the VPN is off it should be protected like any
                # other device. So every device uses global filtering — and we
                # clear any lingering per-client exception (e.g. an old work
                # device that was previously unfiltered).
                for dev_name in devices:
                    restore_client_global(config, dev_name)
                try:
                    import backup as _bk; _bk.auto_backup_usb(config)
                except Exception:
                    pass
                html = build_devices_page(config, saved=True)

            elif parsed.path == "/admin/devices/remove":
                name    = unquote(params.get("remove", [""])[0])
                devices = config.get("devices", {})
                if name in devices:
                    # Remove any Lantern Watch per-client AGH entry so nothing is
                    # left stranded (e.g. an old unfiltered work-device exception).
                    try:
                        restore_client_global(config, name)
                    except Exception:
                        pass
                    del devices[name]
                    config["devices"] = devices
                    config.get("schedules", {}).pop(name, None)
                    save_config(config)
                self._redirect("/admin/devices")
                return

            elif parsed.path == "/admin/clear":
                # Clear AdGuard's own query log FIRST — otherwise the collector
                # re-imports the entries we're about to delete within a minute.
                from adguard import clear_adguard_querylog
                clear_adguard_querylog(config)
                conn = sqlite3.connect(DB_PATH)
                conn.execute("DELETE FROM querylog")
                conn.commit()
                conn.close()
                config["last_adult_alert"] = datetime.utcnow().isoformat() + "Z"
                config["blocked_content_cooldowns"] = {}
                save_config(config)
                reset_adguard_stats(config)
                html = build_admin(config, cleared=True)

            elif parsed.path == "/admin/clear_all":
                from adguard import clear_adguard_querylog
                clear_adguard_querylog(config)
                conn = sqlite3.connect(DB_PATH)
                conn.execute("DELETE FROM querylog")
                conn.commit()
                conn.close()
                config["devices"] = {}
                config["schedules"] = {}
                config["last_adult_alert"] = datetime.utcnow().isoformat() + "Z"
                config["blocked_content_cooldowns"] = {}
                save_config(config)
                reset_adguard_stats(config)
                html = build_admin(config, cleared_all=True)

            elif parsed.path == "/admin/portal/clear_acks":
                from portal import clear_portal_acks
                clear_portal_acks(config)
                html = build_admin(config, saved=True)

            elif parsed.path == "/admin/blocklists/save":
                # Enable/disable each AGH blocklist to match the checked boxes.
                # Unchecked = disable, so we reconcile against the full list.
                from adguard import get_all_filter_lists, set_filter_enabled
                checked = set(params.get("list", []))
                changed = 0
                try:
                    for f in get_all_filter_lists(config):
                        want = f["url"] in checked
                        if want != f["enabled"]:
                            set_filter_enabled(config, f["url"], f["name"], want)
                            changed += 1
                except Exception as e:
                    print(f"[Blocklists] save error: {e}")
                print(f"[Blocklists] {changed} list(s) toggled")
                html = build_admin(config, saved=True)

            elif parsed.path == "/admin/blocklists/refresh":
                # Manual "update now" — AGH also auto-updates every 24h.
                from adguard import refresh_filters
                refresh_filters(config)
                html = build_admin(config, refreshed=True)

            elif parsed.path == "/admin/update":
                # One-click self-update: fetch the current feed .ipk and opkg-install
                # it. Runs in a NEW session so it survives the service restart that
                # opkg's postinst triggers. Device names + settings are preserved by
                # install.sh (it backs up and keeps the existing config).
                import subprocess
                script = (
                    "#!/bin/sh\n"
                    "sleep 2\n"
                    "FN=$(curl -fsSL https://lanternwatch.org/repo/Packages | "
                    "awk -F': ' '/^Filename:/{print $2; exit}')\n"
                    '[ -z "$FN" ] && exit 1\n'
                    'curl -fsSL "https://lanternwatch.org/repo/$FN" -o /tmp/lw_update.ipk || exit 1\n'
                    "opkg install --force-reinstall /tmp/lw_update.ipk\n"
                )
                try:
                    with open("/tmp/lw_update.sh", "w") as _f:
                        _f.write(script)
                    subprocess.Popen(["sh", "/tmp/lw_update.sh"],
                                     stdout=open("/tmp/lw_update.log", "w"),
                                     stderr=subprocess.STDOUT, start_new_session=True)
                    _resp = {"ok": True}
                except Exception as _e:
                    _resp = {"ok": False, "error": str(_e)}
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(_resp).encode())
                return

            elif parsed.path == "/admin/backup/restore":
                # Body is the raw backup JSON (the browser reads the chosen file
                # and POSTs its text). Returns a JSON result with any warnings.
                import backup as _bk
                try:
                    warnings = _bk.restore_backup(body)
                    _bk.auto_backup_usb()  # mirror the restored state to USB too
                    resp = {"ok": True, "warnings": warnings}
                except Exception as _e:
                    resp = {"ok": False, "error": str(_e)}
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(resp).encode())
                return

            elif parsed.path == "/admin/backup/usb":
                # Force an immediate backup to the plugged-in USB drive.
                import backup as _bk
                res = _bk.maybe_write_usb_backup(config, force=True)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(res).encode())
                return

            elif parsed.path == "/admin/backup/usb/eject":
                # Flush + unmount the USB drive so it's safe to physically remove.
                import backup as _bk
                res = _bk.eject_usb()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(res).encode())
                return

            elif parsed.path == "/admin/backup/usb/restore":
                # Restore from the latest backup on the USB drive.
                import backup as _bk
                try:
                    data = _bk.read_usb_backup()
                    if not data:
                        resp = {"ok": False, "error": "No Lantern Watch backup found on the USB drive."}
                    else:
                        resp = {"ok": True, "warnings": _bk.restore_backup(data)}
                except Exception as _e:
                    resp = {"ok": False, "error": str(_e)}
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(resp).encode())
                return

            elif parsed.path == "/notifications/save":
                config.setdefault("alerts", {})
                config.setdefault("summary", {})
                config["ntfy_topic"]   = params.get("ntfy_topic",   [""])[0]
                config["extra_topics"] = params.get("extra_topics",  [""])[0]
                config["ntfy_enabled"] = "ntfy_enabled" in params
                config["alerts"]["adult_content"]   = "alert_adult"     in params
                config["alerts"]["new_device"]      = "alert_newdevice" in params
                config["alerts"]["high_block_rate"] = "alert_highblock" in params
                config["alerts"]["vpn_detection"]   = "alert_vpn"       in params
                config["alerts"]["update_available"] = "alert_update"   in params
                config["summary"]["daily"]          = "daily_summary"   in params
                config["summary"]["daily_hour"]     = int(params.get("daily_hour", [20])[0])
                config["summary"]["weekly"]         = "weekly_summary"  in params
                config["summary"]["weekly_day"]     = int(params.get("weekly_day", [0])[0])
                wl = params.get("vpn_whitelist", [""])[0]
                config["vpn_whitelist"] = [x.strip() for x in wl.split(",") if x.strip()]
                config["telegram"] = {
                    "bot_token": params.get("telegram_bot_token", [""])[0],
                    "chat_id":   params.get("telegram_chat_id",   [""])[0],
                    "enabled":   "telegram_enabled" in params,
                }
                config["email"] = {
                    "to_address":    params.get("email_to",       [""])[0],
                    "smtp_host":     params.get("email_smtp",     [""])[0],
                    "smtp_port":     int(params.get("email_port", ["587"])[0] or "587"),
                    "smtp_user":     params.get("email_from",     [""])[0],
                    "smtp_password": params.get("email_password", [""])[0],
                    "from_name":     "Lantern Watch",
                    "enabled":       "email_enabled" in params,
                }
                # Remove old flat keys from previous schema
                for _k in ("telegram_bot_token", "telegram_chat_id", "email_to",
                           "email_smtp", "email_port", "email_from", "email_password"):
                    config.pop(_k, None)
                save_config(config)
                try:
                    import backup as _bk; _bk.auto_backup_usb(config)
                except Exception:
                    pass
                html = build_notifications(config, saved=True)

            elif parsed.path == "/notifications/clear":
                clear_notifications()
                self._redirect("/notifications?cleared=1")
                return

            elif parsed.path == "/social/apply":
                from adguard import normalize_profile
                profile     = normalize_profile(params.get("profile", ["moderate"])[0])
                safe_search = None
                ss_engines  = None
                if profile == "custom":
                    all_plats = ["youtube", "tiktok", "discord", "instagram", "facebook",
                                 "twitter", "snapchat", "reddit", "twitch", "pinterest"]
                    platforms = [p for p in all_plats if f"plat_{p}" in params]
                    config["social_custom"] = {"platforms": platforms}
                    # Per-engine safe search (custom lets the user pick each one).
                    from adguard import SAFE_SEARCH_ENGINES, set_safesearch_engines
                    ss_engines = {e: (f"ss_{e}" in params) for e in SAFE_SEARCH_ENGINES}
                cust    = config.get("social_custom", {})
                ok      = apply_social_profile(
                    profile,
                    config,
                    custom_platforms=cust.get("platforms"),
                    safe_search=safe_search,   # None for custom → handled per-engine below
                )
                if ss_engines is not None:
                    try:
                        set_safesearch_engines(config, ss_engines)
                        config["social_safe_search"] = any(ss_engines.values())
                    except Exception as _e:
                        print(f"[SafeSearch] per-engine apply error: {_e}")
                config["social_profile"] = profile
                save_config(config)
                try:
                    import backup as _bk; _bk.auto_backup_usb(config)
                except Exception:
                    pass
                self._redirect("/social?saved=1" if ok else "/social?error=1")
                return

            elif parsed.path == "/social/youtube":
                # YouTube Restricted Mode toggle. AdGuard's Safe Search state is the
                # single source of truth — we flip ONLY the YouTube engine (keeping
                # the others as-is) and store nothing in the config, so a concurrent
                # background config save can never revert it. Checked = Restricted
                # Mode on (hides mature videos + comments). Only import names not
                # already imported at module top (get_safesearch_status is).
                from adguard import set_safesearch_engines, SAFE_SEARCH_ENGINES
                restricted = "youtube_restricted" in params
                try:
                    cur = get_safesearch_status(config)
                    eng = {e: bool(cur.get(e)) for e in SAFE_SEARCH_ENGINES}
                    eng["youtube"] = restricted
                    set_safesearch_engines(config, eng)
                except Exception as _e:
                    print(f"[YouTube] toggle error: {_e}")
                self._redirect("/social?saved=1")
                return

            elif parsed.path == "/protection/apply":
                from adguard import (OPTIONAL_LISTS, apply_optional_lists,
                                     remove_dead_lists, refresh_filters)
                enabled = [l["id"] for l in OPTIONAL_LISTS if f"extra_{l['id']}" in params]
                try:
                    apply_optional_lists(config, enabled)
                    remove_dead_lists(config)
                    refresh_filters(config)
                    config["extra_lists"] = enabled
                    save_config(config)
                    self._redirect("/social?saved=1")
                except Exception as _e:
                    print(f"[Protection] apply error: {_e}")
                    self._redirect("/social?error=1")
                return

            elif parsed.path == "/social/dns-tier":
                # LITE only: switch the Cloudflare upstream between Families
                # (malware+adult) and Security (malware-only). Re-applies the
                # upstream AND the DoH mitigation, since the mitigation must not
                # block whichever resolver is now active.
                from adguard import (LITE_DNS_TIERS, DEFAULT_LITE_TIER, is_lite,
                                     apply_upstream_dns, apply_doh_dns_mitigation)
                tier = (params.get("dns_tier", [DEFAULT_LITE_TIER])[0] or DEFAULT_LITE_TIER)
                if tier not in LITE_DNS_TIERS:
                    tier = DEFAULT_LITE_TIER
                try:
                    if is_lite(config):
                        config["lite_dns_tier"] = tier
                        save_config(config)
                        apply_upstream_dns(config, force=True)
                        apply_doh_dns_mitigation(config)
                        try:
                            import backup as _bk; _bk.auto_backup_usb(config)
                        except Exception:
                            pass
                    self._redirect("/social?saved=1")
                except Exception as _e:
                    print(f"[DNSTier] apply error: {_e}")
                    self._redirect("/social?error=1")
                return

            elif parsed.path == "/blocked-services/save":
                selected = set(params.get("svc", []))
                try:
                    set_blocked_services(config, selected)
                except Exception as e:
                    print(f"[BlockedServices] save error: {e}")
                try:
                    from adguard import set_blocked_pack_domains
                    doms = [d for grp in params.get("packdom", []) for d in grp.split(",")]
                    set_blocked_pack_domains(config, doms)
                except Exception as e:
                    print(f"[BlockedServices] packs save error: {e}")
                try:
                    from adguard import SERVICE_NOTIFY_DEFAULTS
                    notify_on = set(params.get("svcnotify", []))
                    config["service_notify"] = {cat: (cat in notify_on)
                                                for cat in SERVICE_NOTIFY_DEFAULTS}
                    save_config(config)
                except Exception as e:
                    print(f"[BlockedServices] notify prefs save error: {e}")
                try:
                    import backup as _bk; _bk.auto_backup_usb(config)
                except Exception:
                    pass
                self._redirect("/blocked-services?saved=1")
                return

            elif parsed.path == "/blocked-services/custom/add":
                from adguard import add_custom_block
                try:
                    d = add_custom_block(config, params.get("domain", [""])[0])
                except Exception as _e:
                    print(f"[CustomBlock] add error: {_e}")
                    d = ""
                self._redirect("/blocked-services?saved=1" if d else "/blocked-services?error=1")
                return

            elif parsed.path == "/blocked-services/custom/remove":
                from adguard import remove_custom_block
                try:
                    remove_custom_block(config, params.get("domain", [""])[0])
                except Exception as _e:
                    print(f"[CustomBlock] remove error: {_e}")
                self._redirect("/blocked-services?saved=1")
                return

            elif parsed.path == "/domain/clear":
                from db import clear_domain
                dom = unquote(params.get("domain", [""])[0]).strip()
                try:
                    n = clear_domain(dom)
                    print(f"[DomainClear] removed {n} rows for {dom}")
                except Exception as _e:
                    print(f"[DomainClear] error for {dom}: {_e}")
                self._redirect("/")
                return

            elif parsed.path == "/device/schedule/save":
                client_name = unquote(params.get("client_name", [""])[0])
                client_ip   = unquote(params.get("client_ip",   [""])[0])
                schedules   = config.get("schedules", {})
                focus_times = []
                for i in range(3):
                    focus_times.append({
                        "enabled": f"focus_enabled_{i}" in params,
                        "label":   params.get(f"focus_label_{i}",  [""])[0],
                        "start":   params.get(f"focus_start_{i}",  ["08:00"])[0],
                        "end":     params.get(f"focus_end_{i}",    ["09:00"])[0],
                    })
                screen_time = {
                    "enabled": "st_enabled" in params,
                    "hours":   float(params.get("st_hours", ["2"])[0]),
                    "reset":   params.get("st_reset", ["00:00"])[0],
                }
                schedules[client_ip] = {
                    "enabled":    "enabled" in params,
                    "bedtime":    params.get("bedtime", ["21:00"])[0],
                    "wake":       params.get("wake",    ["06:00"])[0],
                    "name":       label(client_name, config),
                    "focus_times": focus_times,
                    "screen_time": screen_time,
                }
                config["schedules"] = schedules
                save_config(config)
                self._redirect(f"/device/schedule?name={quote(client_name)}&ip={quote(client_ip)}")
                return

            else:
                html = "<h1>Not found</h1>"

            self._send_html(html)

        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(f"Error: {e}".encode())

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _send_html(self, html: str):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(html.encode())

    def _redirect(self, location: str):
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

    def log_message(self, format, *args):
        pass  # Silence request logging
