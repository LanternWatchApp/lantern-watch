"""
Lantern Watch — portal.py
Captive portal: intercept first HTTP request from new devices and show a
network-use notice. After acknowledgment the device browses normally.

Blocked domains are handled separately by setup_block_page (they return
NXDOMAIN from AdGuard and never reach the LAN), so the captive portal only
deals with allowed first-contact HTTP traffic. The legacy `! -d 192.168.8.2`
guard below is now a harmless no-op kept for rule-matching stability on upgrade.

iptables chain layout (when portal is enabled):
  PREROUTING
    -i br-lan ! -d 192.168.8.2 --dport 80 -j lw_captive   ← our hook
  lw_captive (user chain)
    -s <acked_ip_1> -j RETURN
    -s <acked_ip_2> -j RETURN
    ...
    -j REDIRECT --to-port 8081   ← default: unacknowledged devices hit portal
"""

import subprocess
from config import save_config

_CHAIN      = "lw_captive"
_BLOCK_IP   = "192.168.8.2"
_PORTAL_PORT = 8081


# ── internal helpers ──────────────────────────────────────────────────────────

def _ipt(*args, check=False):
    r = subprocess.run(["iptables", "-t", "nat"] + list(args), capture_output=True)
    return r.returncode == 0 if check else None


def _rebuild_chain(acked_ips):
    """Flush lw_captive and repopulate: RETURN rules for each acked IP, then default REDIRECT."""
    _ipt("-F", _CHAIN)
    for ip in acked_ips:
        _ipt("-A", _CHAIN, "-s", ip, "-j", "RETURN")
    _ipt("-A", _CHAIN, "-j", "REDIRECT", "--to-port", str(_PORTAL_PORT))


# ── public API ────────────────────────────────────────────────────────────────

def setup_captive_portal(config):
    """Enable captive portal — create chain, populate, hook into PREROUTING."""
    _ipt("-N", _CHAIN)          # no-op if already exists
    _rebuild_chain(config.get("captive_portal_acked", []))
    hook = ["-i", "br-lan", "!", "-d", _BLOCK_IP, "-p", "tcp", "--dport", "80", "-j", _CHAIN]
    if not _ipt("-C", "PREROUTING", *hook, check=True):
        _ipt("-A", "PREROUTING", *hook)
    print("[Portal] captive portal enabled")


def teardown_captive_portal():
    """Disable captive portal — remove PREROUTING hook and flush chain."""
    hook = ["-i", "br-lan", "!", "-d", _BLOCK_IP, "-p", "tcp", "--dport", "80", "-j", _CHAIN]
    _ipt("-D", "PREROUTING", *hook)
    _ipt("-F", _CHAIN)
    print("[Portal] captive portal disabled")


def restore_captive_portal(config):
    """Called on boot — re-setup if portal is configured on."""
    if config.get("captive_portal"):
        setup_captive_portal(config)


def is_portal_acked(ip, config):
    return ip in config.get("captive_portal_acked", [])


def ack_portal_ip(ip, config):
    """Record acknowledgment and update iptables chain immediately."""
    acked = list(config.get("captive_portal_acked", []))
    if ip not in acked:
        acked.append(ip)
        config["captive_portal_acked"] = acked
        save_config(config)
    _rebuild_chain(acked)


def clear_portal_acks(config):
    """Remove all acknowledgments — every device will see the portal again."""
    config["captive_portal_acked"] = []
    save_config(config)
    if config.get("captive_portal"):
        _rebuild_chain([])
    print("[Portal] acknowledgments cleared")
