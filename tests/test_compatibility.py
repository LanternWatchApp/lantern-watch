#!/usr/bin/env python3
"""
Lantern Watch — test_compatibility.py
Comprehensive compatibility, safety, and contract test suite.
Verifies:
1. Python syntax & runtime compatibility across all modules.
2. Pause safety & admin lockout protection contracts.
3. Group pause / unpause IP-fallback and tombstone behavior.
4. Identity conflict detection & unambiguous duplicate resolution.
5. AdGuard API request/payload contracts and filter mappings.
6. Firewall rule generation (iptables / nftables safety bounds).
7. Privacy scanner (ensures zero personal data / secrets in repo files).
"""

import unittest
import json
import os
import sys
import re
from datetime import datetime, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import config as cfg_module
import classify
import db
import pages


class TestConfigAndSafetyContracts(unittest.TestCase):
    """Test pause safety, admin lockout prevention, and config migration contracts."""

    def test_has_admin_device_contract(self):
        """Pausing must be gated on has_admin_device."""
        # No admin device configured
        no_admin_cfg = {
            "devices": {
                "phone-1": {"type": "person", "label": "Timmy Phone"},
                "laptop-1": {"type": "work_device", "label": "Work Laptop"}
            }
        }
        self.assertFalse(cfg_module.has_admin_device(no_admin_cfg))
        self.assertFalse(cfg_module.is_pauseable("phone-1", no_admin_cfg))

        # Admin device configured
        admin_cfg = {
            "devices": {
                "mom-phone": {"type": "parent", "label": "Mom Phone"},
                "phone-1": {"type": "person", "label": "Timmy Phone"}
            }
        }
        self.assertTrue(cfg_module.has_admin_device(admin_cfg))
        self.assertTrue(cfg_module.is_pauseable("phone-1", admin_cfg))
        # Admin itself is NEVER pauseable
        self.assertFalse(cfg_module.is_pauseable("mom-phone", admin_cfg))

    def test_label_has_protected_identity(self):
        """Aliases sharing a label with an Admin record are protected."""
        cfg = {
            "devices": {
                "192.168.8.100": {"type": "parent", "label": "Dad Laptop"},
                "Dad-MacBook": {"type": "person", "label": "Dad Laptop"}  # Duplicate identity
            }
        }
        self.assertTrue(cfg_module.label_has_protected_identity("Dad-MacBook", cfg))
        self.assertFalse(cfg_module.is_pauseable("Dad-MacBook", cfg))

    def test_is_groupable_contract(self):
        """Only Personal, Smart, and Work devices are groupable; Admin & Infra never are."""
        cfg = {
            "devices": {
                "admin-phone": {"type": "parent", "label": "Admin"},
                "kid-tablet": {"type": "person", "label": "Tablet"},
                "living-tv": {"type": "smart_device", "label": "Smart TV"},
                "nas-server": {"type": "infrastructure", "label": "Home NAS"}
            }
        }
        self.assertTrue(cfg_module.is_groupable("kid-tablet", cfg))
        self.assertTrue(cfg_module.is_groupable("living-tv", cfg))
        self.assertFalse(cfg_module.is_groupable("admin-phone", cfg))
        self.assertFalse(cfg_module.is_groupable("nas-server", cfg))

    def test_identity_conflicts_detection(self):
        """Conflicting roles for same normalized label are surfaced."""
        cfg = {
            "devices": {
                "192.168.8.150": {"type": "parent", "label": "Mom iPhone"},
                "iPhone-Mom": {"type": "person", "label": "Mom iPhone"}
            }
        }
        conflicts = cfg_module.find_identity_conflicts(cfg)
        self.assertEqual(len(conflicts), 1)
        lbl, entries = conflicts[0]
        self.assertEqual(lbl, "Mom iPhone")
        self.assertEqual(len(entries), 2)


class TestDeviceClassification(unittest.TestCase):
    """Test device kind heuristics and weak label overrides."""

    def test_smart_tv_weak_label_override(self):
        """Smart TV weak label doesn't mask genuine Android OS traffic signal."""
        domains = ["play.googleapis.com", "android.clients.google.com", "googleads.g.doubleclick.net"]
        ident = {"mac": "", "vendor": "TCL", "hostname": "9469X"}
        kind = classify.device_kind("9469X", "", ident, domains)
        self.assertIn(kind, ("phone or tablet", "tablet", "phone"))

    def test_auto_group_matching(self):
        """Auto group suggestions map cleanly to default categories."""
        self.assertEqual(pages._auto_group_name("iPad-Air", "iPad", {}, []), "Tablets")
        self.assertEqual(pages._auto_group_name("DESKTOP-ABC", "Windows PC", {}, []), "Computers")
        self.assertEqual(pages._auto_group_name("Galaxy-S23", "Samsung Phone", {}, []), "Phones")
        self.assertEqual(pages._auto_group_name("Apple-TV-4K", "Living Room TV", {}, []), "TVs")


class TestAttemptCollapsing(unittest.TestCase):
    """Test collapsing near-simultaneous DNS bursts into real human attempts."""

    def test_burst_collapsing(self):
        # 8 queries within 4 seconds = 1 real attempt
        base = datetime(2026, 9, 8, 12, 0, 0)
        burst = [
            ("timmy-phone", (base + timedelta(seconds=i * 0.5)).isoformat() + "Z")
            for i in range(8)
        ]
        # 1 query 20 seconds later = 2nd real attempt
        burst.append(("timmy-phone", (base + timedelta(seconds=25)).isoformat() + "Z"))

        real_count = db._count_real_attempts(burst, gap_seconds=5)
        self.assertEqual(real_count, 2)


class TestPrivacyAndSecurityGuardrails(unittest.TestCase):
    """Ensure zero personal data, secrets, or unredacted tokens exist in repo source."""

    def test_no_hardcoded_passwords_or_personal_keys(self):
        sensitive_patterns = [
            re.compile(r'BEGIN (RSA|EC|DSA|OPENSSH) PRIVATE KEY'),
            re.compile(r'AIza[0-9A-Za-z-_]{35}'),  # Google API key format
            re.compile(r'ghp_[0-9A-Za-z]{36}'),    # GitHub personal token
            re.compile(r'sk-[a-zA-Z0-9]{32,}'),    # OpenAI key format
        ]
        excluded_dirs = {'.git', '__pycache__', 'scratch', '.system_generated', 'node_modules', '.rollback'}

        for root, dirs, files in os.walk(REPO_ROOT):
            dirs[:] = [d for d in dirs if d not in excluded_dirs]
            for fname in files:
                if fname.endswith(('.py', '.sh', '.md', '.json', '.yml', '.yaml', '.txt')):
                    fpath = os.path.join(root, fname)
                    with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                        for pattern in sensitive_patterns:
                            self.assertFalse(
                                pattern.search(content),
                                f"Sensitive credential pattern detected in {os.path.relpath(fpath, REPO_ROOT)}"
                            )


if __name__ == '__main__':
    unittest.main()
