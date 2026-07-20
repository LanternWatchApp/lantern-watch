#!/usr/bin/env python3
"""
Lantern Watch — dashboard.py
Entry point: starts the scheduler and HTTP server.
"""

from http.server import HTTPServer
from socketserver import ThreadingMixIn


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

from config import load_config
from adguard import apply_social_profile, get_blocked_platforms, apply_doh_iptables, apply_doh_dns_mitigation, setup_block_page
from portal import restore_captive_portal
from db import get_or_create_install_id
from scheduler import restore_paused_on_boot, start_scheduler
from routes import Handler

PORT = 8081

if __name__ == "__main__":
    config = load_config()
    get_or_create_install_id()  # Seed anonymous install UUID on first boot
    restore_paused_on_boot(config)
    # Re-apply social blocking rules lost when /tmp was cleared on reboot
    if get_blocked_platforms(config):
        profile = config.get("social_profile", "moderate")
        custom  = config.get("social_custom", {}).get("platforms")
        apply_social_profile(profile, config, custom_platforms=custom)
        print(f"[Boot] Social profile '{profile}' restored")
    # Always-on gentle DoH mitigation (Firefox canary + DoH hostnames) at DNS
    # level — no breakage. Idempotent; also covers existing installs on upgrade.
    try:
        apply_doh_dns_mitigation(config)
    except Exception as _e:
        print(f"[Boot] DoH DNS mitigation error: {_e}")
    # Always-on service allowlist — never block our own push/updates/telemetry.
    try:
        from adguard import apply_service_allowlist
        apply_service_allowlist(config)
    except Exception as _e:
        print(f"[Boot] service allowlist error: {_e}")
    # Ensure the profile's filtering upstream is set (self-heals a fresh install
    # where the DoH test failed too early — e.g. Cloudflare Families on LITE).
    try:
        from adguard import apply_upstream_dns
        apply_upstream_dns(config)
    except Exception as _e:
        print(f"[Boot] upstream DNS error: {_e}")
    # Re-apply the stricter DoH iptables rules (cleared on reboot) only if opted in
    if config.get("doh_blocking"):
        apply_doh_iptables(True)
        print("[Boot] DoH iptables rules restored")
    # Mirror settings to a plugged-in USB drive (no-op if none). Writes only when
    # content changed — so a version bump after an update refreshes the drive copy.
    try:
        import backup as _backup
        _res = _backup.maybe_write_usb_backup(config)
        if _res.get("written"):
            print(f"[Boot] USB backup updated: {_res.get('path')}")
    except Exception as _e:
        print(f"[Boot] USB backup error: {_e}")
    # Set AGH blocking IP + iptables for block page (virtual IP, port 80 + 443)
    setup_block_page(config)
    # HTTPS block-page server (serves /blocked over TLS on :8444 for blocked
    # HTTPS sites, after the browser cert warning)
    from blockserver import start_block_server
    start_block_server()
    # Re-setup captive portal iptables chain if enabled
    restore_captive_portal(config)
    start_scheduler()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Lantern Watch dashboard running on port {PORT}")
    server.serve_forever()
