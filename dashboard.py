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
from adguard import apply_social_profile, get_blocked_platforms, apply_doh_iptables, setup_block_page
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
    # Re-apply DoH iptables rules (cleared on reboot)
    if config.get("doh_blocking"):
        apply_doh_iptables(True)
        print("[Boot] DoH iptables rules restored")
    # Set AGH blocking IP + iptables for block page (virtual IP, port 80 + 443)
    setup_block_page(config)
    # Re-setup captive portal iptables chain if enabled
    restore_captive_portal(config)
    start_scheduler()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Lantern Watch dashboard running on port {PORT}")
    server.serve_forever()
