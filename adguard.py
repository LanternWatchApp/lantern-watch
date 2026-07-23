#!/usr/bin/env python3
"""
Lantern Watch — adguard.py
AdGuard Home API calls and social media blocking.

Social profile blocking uses AGH custom filter rules (||domain^) so that
device IPs are preserved — GL.iNet's dns_enabled flag redirects port-53
traffic directly to AGH:3053 via iptables, bypassing dnsmasq forwarding.
"""

import json
import base64
import subprocess
import threading
import time
import urllib.request

_STATS_CACHE   = {"data": None, "ts": 0.0}
_HOSTNAME_CACHE = {"data": None, "ts": 0.0}
_CACHE_TTL     = 60

# Serializes every read-modify-write cycle on AGH's shared user_rules list —
# social blocking, DoH blocking, and the domain allowlist all live in that one
# list. Without this, two concurrent writers (scheduler thread, alert loop, a
# parent clicking Save) can each do get_custom_rules -> modify -> set_rules and
# silently clobber the other's section. RLock (not Lock) because
# add/remove_allowlist_domain hold it and then call _save_allowlist, which
# re-acquires it on the same thread.
_rules_lock = threading.RLock()

BLOCK_PAGE_IP    = "192.168.8.2"   # Dedicated virtual IP; never conflicts with GL.iNet admin at .1
_SOCIAL_MARKER   = "# Lantern Watch — Social blocking"

# ── Platform domains to BLOCK (not allowlist — we block the ones not selected) ─
PLATFORM_DOMAINS = {
    "youtube":   ["youtube.com", "youtubestudio.com", "googlevideo.com", "ytimg.com",
                  "youtube-nocookie.com"],
    "tiktok":    ["tiktok.com", "tiktokv.com", "tiktokcdn.com", "musical.ly"],
    "discord":   ["discord.com", "discordapp.com", "discord.gg", "discordapp.net"],
    "instagram": ["instagram.com", "cdninstagram.com"],
    "facebook":  ["facebook.com", "fbcdn.net", "fb.com", "fb.me"],
    "twitter":   ["twitter.com", "x.com", "twimg.com", "t.co"],
    "snapchat":  ["snapchat.com", "snap.com", "snapkit.com"],
    "reddit":    ["reddit.com", "redd.it", "redditmedia.com", "redditstatic.com"],
    "twitch":    ["twitch.tv", "twitchapps.com", "jtvnw.net"],
    "pinterest": ["pinterest.com", "pinterest.ca", "pinimg.com"],
}

# ── Named profiles — which platforms are ALLOWED ──────────────────────────────
SOCIAL_PROFILES = {
    "open":     list(PLATFORM_DOMAINS.keys()),   # all allowed
    "moderate": list(PLATFORM_DOMAINS.keys()),   # all allowed
    "strict":   [],                              # nothing allowed
}
# The old "Teen" tier was retired (it duplicated Moderate). Any config still on
# it is treated as Moderate; the YouTube Restricted-Mode toggle now covers the
# one axis Teen implied.
_RETIRED_PROFILES = {"teen": "moderate"}


def normalize_profile(name):
    """Map any retired profile id onto its replacement (teen → moderate)."""
    return _RETIRED_PROFILES.get(name, name)


# ── Safe Search defaults per profile (None = user-controlled, for custom) ─────
PROFILE_SAFE_SEARCH = {
    "open":     False,
    # Moderate keeps Safe Search ON — the fresh-install wizard enables it, so the
    # profile badge and the engine checkboxes now agree (and it's a low-breakage
    # family-safety win for the default profile).
    "moderate": True,
    "strict":   True,
}


def apply_social_profile(profile_name, config, custom_platforms=None, safe_search=None):
    """
    Apply a social profile by writing AGH custom filter rules, then sets global
    Safe Search based on the profile default or explicit safe_search arg.
    Returns True on success.
    """
    all_platforms = list(PLATFORM_DOMAINS.keys())
    profile_name  = normalize_profile(profile_name)   # teen → moderate

    if profile_name == "custom":
        allowed = custom_platforms or []
    else:
        allowed = SOCIAL_PROFILES.get(profile_name, all_platforms)

    blocked = [p for p in all_platforms if p not in allowed]

    try:
        new_rules = []
        if blocked:
            new_rules.append(_SOCIAL_MARKER)
            for platform in blocked:
                for domain in PLATFORM_DOMAINS.get(platform, []):
                    new_rules.append(f"||{domain}^")

        with _rules_lock:
            current = get_custom_rules(config)
            cleaned = []
            skip = False
            for line in current:
                if line.strip() == _SOCIAL_MARKER:
                    skip = True
                elif skip and (line.startswith("||") or line == ""):
                    continue
                else:
                    skip = False
                    cleaned.append(line)

            final = cleaned + ([""] + new_rules if new_rules else [])
            _ag_post(config, "/filtering/set_rules", {"rules": final})
        print(f"[Social] Profile '{profile_name}': blocked={blocked}")
    except Exception as e:
        print(f"[Social] Error applying profile: {e}")
        return False

    # Apply safe search: explicit arg overrides profile default
    ss = safe_search if safe_search is not None else PROFILE_SAFE_SEARCH.get(profile_name)
    if ss is not None:
        try:
            set_safesearch_enabled(config, ss)
            config["social_safe_search"] = ss
            print(f"[Social] Safe search set to {ss}")
        except Exception as e:
            print(f"[Social] Safe search set failed: {e}")

    return True


def clear_social_blocking(config):
    """Remove all social blocking rules from AGH custom rules."""
    return apply_social_profile("open", config)


def get_blocked_platforms(config):
    """Return currently blocked platforms based on saved config."""
    profile     = normalize_profile(config.get("social_profile", "moderate"))
    all_plats   = list(PLATFORM_DOMAINS.keys())
    if profile == "custom":
        allowed = config.get("social_custom", {}).get("platforms", all_plats)
    else:
        allowed = SOCIAL_PROFILES.get(profile, all_plats)
    return [p for p in all_plats if p not in allowed]


def setup_block_page(config):
    """
    On every boot, route blocked domains to the Lantern Watch block page — a
    compassionate notice ("This site has been blocked…", scripture, and a
    prominent Find Help link), part of the mission to help those who are
    struggling find a way out.

    How it works: AdGuard answers a blocked lookup with our block-page virtual
    IP (BLOCK_PAGE_IP, 192.168.8.2). iptables then redirects that IP's traffic:
      • :80  → the dashboard (:8081), which serves /blocked
      • :443 → the local HTTPS block server (:8444), which serves /blocked
    The .2 virtual IP keeps the router's own :80/:443 (GL.iNet UI / nginx)
    completely untouched.

    HTTPS reality: we cannot hold a valid TLS certificate for someone else's
    domain, so a blocked HTTPS site shows a browser certificate warning first;
    on click-through the visitor lands on the block page. HSTS-preloaded sites
    won't allow the click-through — that's an unavoidable browser limitation,
    not a blocking failure (the site is still fully blocked either way).
    Plain-HTTP blocked sites land on the page cleanly.
    """
    # 1. AdGuard: blocked domains → the block-page IP (custom_ip mode).
    try:
        payload = json.dumps({
            "blocking_mode": "custom_ip",
            "blocking_ipv4": BLOCK_PAGE_IP,
            "blocking_ipv6": "::",
        }).encode()
        req = _ag_request(config, "/control/dns_config", payload)
        urllib.request.urlopen(req, timeout=5)
        print(f"[BlockPage] AGH blocking_mode → custom_ip {BLOCK_PAGE_IP}")
    except Exception as e:
        print(f"[BlockPage] AGH config error: {e}")

    # 2. Claim the block-page virtual IP on the LAN bridge (idempotent).
    subprocess.run(
        ["ip", "addr", "add", f"{BLOCK_PAGE_IP}/32", "dev", "br-lan"],
        capture_output=True,
    )

    # 3. Redirect the block-page IP's web ports to our servers. Delete any
    #    existing copies first (idempotent), then add exactly one of each.
    for rule in (
        ["PREROUTING", "-p", "tcp", "--dport", "80",  "-d", BLOCK_PAGE_IP, "-j", "REDIRECT", "--to-port", "8081"],
        ["PREROUTING", "-p", "tcp", "--dport", "443", "-d", BLOCK_PAGE_IP, "-j", "REDIRECT", "--to-port", "8444"],
    ):
        while subprocess.run(["iptables", "-t", "nat", "-D"] + rule, capture_output=True).returncode == 0:
            pass
        subprocess.run(["iptables", "-t", "nat", "-A"] + rule, capture_output=True)
    print("[BlockPage] blocked domains → block page (HTTP :80→8081, HTTPS :443→8444)")

    # 4. Give UPSTREAM blocks the same block page. A filtering upstream (Cloudflare
    #    for Families) answers a blocked lookup with 0.0.0.0, which would otherwise
    #    give the client a blank "can't connect" error. dnsmasq sits in front of
    #    AdGuard (clients → dnsmasq → AGH → upstream) and its `alias` rewrites IPs
    #    coming back from its upstream — so we map 0.0.0.0 → the block-page IP, and
    #    upstream-filtered adult/malware sites land on the Lantern Watch page too.
    #    dnsmasq reads /tmp/dnsmasq.d (tmpfs), so we (re)write this on every boot.
    #    (AGH's own blocks already return the block-page IP, so they're unaffected.)
    try:
        import os
        os.makedirs("/tmp/dnsmasq.d", exist_ok=True)
        alias_file = "/tmp/dnsmasq.d/lw-blockpage.conf"
        desired = f"alias=0.0.0.0,{BLOCK_PAGE_IP}\n"
        current = ""
        try:
            with open(alias_file) as f:
                current = f.read()
        except Exception:
            pass
        if current != desired:
            with open(alias_file, "w") as f:
                f.write(desired)
            subprocess.run(["/etc/init.d/dnsmasq", "restart"], capture_output=True)
            print(f"[BlockPage] dnsmasq alias 0.0.0.0 → {BLOCK_PAGE_IP} (upstream blocks → block page)")
    except Exception as e:
        print(f"[BlockPage] dnsmasq alias setup error: {e}")


# ── Recommended blocklists for initial setup ──────────────────────────────────

RECOMMENDED_LISTS = [
    {
        "id": "phishing_army",
        "name": "Phishing URL Blocklist",
        "url": "https://phishing.army/download/phishing_army_blocklist_extended.txt",
        "description": "Phishing sites sourced from PhishTank and OpenPhish",
        "category": "Security",
    },
    {
        "id": "urlhaus",
        "name": "Malicious URL Blocklist (URLHaus)",
        "url": "https://urlhaus-filter.pages.dev/urlhaus-filter-agh.txt",
        "description": "Active malware URLs tracked by URLHaus",
        "category": "Security",
    },
    {
        "id": "anti_malware",
        "name": "Dandelion Sprout's Anti-Malware List",
        "url": "https://raw.githubusercontent.com/DandelionSprout/adfilt/master/Alternate%20versions%20Anti-Malware%20List/AntiMalwareAdGuardHome.txt",
        "description": "Known malware distribution and command-and-control sites",
        "category": "Security",
    },
    {
        "id": "dating",
        "name": "ShadowWhisperer's Dating List",
        "url": "https://raw.githubusercontent.com/ShadowWhisperer/BlockLists/master/Lists/Dating",
        "description": "Dating sites and adult relationship platforms",
        "category": "Family Safety",
    },
    {
        "id": "scam",
        "name": "Scam Blocklist",
        "url": "https://raw.githubusercontent.com/durablenapkin/scamblocklist/master/adguard.txt",
        "description": "Known scam and fraud sites",
        "category": "Security",
    },
    {
        "id": "stalkerware",
        "name": "Stalkerware Indicators List",
        "url": "https://raw.githubusercontent.com/AssoEchap/stalkerware-indicators/master/generated/hosts",
        "description": "Domains used by stalkerware / spyware apps",
        "category": "Security",
    },
]


# ── Optional (toggleable) blocklists — plain-language, low-breakage ────────────
# Curated so parents get meaningful choice without the footgun of the raw
# 60-list collection. Each maps to one vetted list behind a friendly label.
OPTIONAL_LISTS = [
    {"id": "adguard_dns", "name": "AdGuard DNS filter",
     "url": "https://adguardteam.github.io/AdGuardSDNSFilter/Filters/filter.txt",
     "label": "Block ads &amp; trackers (full list)",
     "desc": "AdGuard's primary ad/tracker filter — a large list. Great on a 1 GB+ "
             "router; left off by default so small (512 MB) routers stay light."},
    {"id": "nsfw", "name": "Adult / Pornography Blocklist",
     "url": "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/nsfw.txt",
     "label": "Block adult / pornography sites",
     "desc": "A large, actively-maintained adult-content blocklist (HaGeZi NSFW) — "
             "much stronger than AdGuard's built-in heuristic alone. ~107K sites."},
    {"id": "gambling", "name": "Gambling Blocklist",
     "url": "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/gambling.txt",
     "label": "Block gambling sites",
     "desc": "Online casinos, sports betting, and gambling sites."},
    {"id": "smart_tv", "name": "Smart-TV Tracker Blocklist",
     "url": "https://raw.githubusercontent.com/Perflyst/PiHoleBlocklist/master/SmartTV-AGH.txt",
     "label": "Block smart-TV tracking",
     "desc": "Stops smart TVs from reporting what you watch."},
    {"id": "extra_ads", "name": "OISD Small",
     "url": "https://small.oisd.nl/",
     "label": "Extra ad &amp; tracker blocking",
     "desc": "A balanced extra ad/tracker list, tuned for low false positives."},
    {"id": "bypass", "name": "VPN / Proxy / DoH Bypass Blocklist",
     "url": "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/doh-vpn-proxy-bypass.txt",
     "label": "Block VPN, proxy &amp; DNS-bypass tools",
     "desc": "Stops VPN, proxy, and encrypted-DNS services commonly used to get "
             "around the filter (HaGeZi). Strong anti-bypass, but may interrupt a "
             "legitimate work VPN — leave off if someone at home relies on one."},
]

# Known-dead / redundant lists to clear out (e.g. a GL.iNet AdAway default that
# no longer loads — 0 rules).
DEAD_LISTS = ["https://adaway.org/hosts.txt"]


def get_active_filter_urls(config):
    """Set of blocklist URLs currently configured in AdGuard Home."""
    try:
        with urllib.request.urlopen(_ag_request(config, "/control/filtering/status"), timeout=5) as r:
            return {f["url"] for f in json.loads(r.read().decode()).get("filters", [])}
    except Exception:
        return set()


def _add_filter_url(config, name, url):
    payload = json.dumps({"name": name, "url": url, "enabled": True}).encode()
    urllib.request.urlopen(_ag_request(config, "/control/filtering/add_url", payload), timeout=10)


def _remove_filter_url(config, url):
    payload = json.dumps({"url": url, "whitelist": False}).encode()
    urllib.request.urlopen(_ag_request(config, "/control/filtering/remove_url", payload), timeout=10)


def apply_optional_lists(config, enabled_ids):
    """Add/remove the optional blocklists so AGH matches `enabled_ids`."""
    active   = get_active_filter_urls(config)
    enabled  = set(enabled_ids or [])
    for lst in OPTIONAL_LISTS:
        want, has = lst["id"] in enabled, lst["url"] in active
        try:
            if want and not has:
                _add_filter_url(config, lst["name"], lst["url"])
            elif has and not want:
                _remove_filter_url(config, lst["url"])
        except Exception as e:
            print(f"[Filters] {lst['id']} toggle error: {e}")
    return enabled


# Optional lists that ship ON for a fresh install, chosen to fit the rule budget
# on low-RAM GL.iNet routers (base lists are ~345K; the crash zone is ~500K):
#   - nsfw (~107K): adult-content blocking is THE core parental feature, and
#     base + nsfw (~450K) still clears the ceiling. Toggleable if not wanted.
#   - smart_tv (~162 rules): negligible cost, clear privacy win.
# Gambling (~289K) and OISD Small (~55K) stay OFF — either would push a fresh
# install over the budget (and OISD largely duplicates the AdGuard DNS filter).
# ── RAM-aware protection profiles (chosen at install) ─────────────────────────
# AdGuard keeps every rule in memory. A 512 MB router (Beryl 7) can't hold the
# heavy local set — we confirmed the kernel OOM-kills AdGuard at ~300K rules
# (144K phishing + 158K GL.iNet ad filter). So RAM decides the profile:
#   LITE  (< 600 MB): tiny local footprint (~15 MB). Only the small family lists
#         locally; adult + malware + phishing come from a FILTERING UPSTREAM
#         (Cloudflare for Families) + AdGuard Safe Browsing — server-side, ~0 RAM.
#   FULL  (>= 600 MB): the full local blocklists, as they run today on 1 GB units.
# Set from RAM at install (config["protection_profile"], see install.sh).
LITE_UPSTREAMS = ["https://family.cloudflare-dns.com/dns-query"]      # adult + malware, server-side
FULL_UPSTREAMS = ["https://cloudflare-dns.com/dns-query", "https://dns.quad9.net/dns-query"]
UPSTREAM_BOOTSTRAP = ["1.1.1.1", "1.0.0.1"]
# LITE DNS-filtering tiers the parent can choose from on /social. We deliberately
# do NOT expose a fully-unfiltered tier (this is a parental-control product), so
# the upstream always blocks at least malware. Both tiers answer blocked lookups
# with 0.0.0.0 → the dnsmasq alias sends them to the Lantern Watch block page.
LITE_DNS_TIERS = {
    "families": {"url": "https://family.cloudflare-dns.com/dns-query",
                 "label": "Malware + Adult", "ips": "1.1.1.3 / 1.0.0.3",
                 "desc": "Blocks adult content and malware/phishing at the DNS level. Recommended for families."},
    "malware":  {"url": "https://security.cloudflare-dns.com/dns-query",
                 "label": "Malware only",  "ips": "1.1.1.2 / 1.0.0.2",
                 "desc": "Blocks malware/phishing only — allows adult sites through DNS (local lists still apply)."},
}
DEFAULT_LITE_TIER = "families"


def lite_dns_tier(config):
    """The chosen LITE DNS tier key, defaulting to the safest (families)."""
    t = config.get("lite_dns_tier")
    return t if t in LITE_DNS_TIERS else DEFAULT_LITE_TIER
# LITE local lists: only the small, family-specific ones (a few K rules total).
LITE_RECOMMENDED_IDS = ["dating", "scam", "stalkerware"]
LITE_OPTIONAL_IDS    = []                      # nothing heavy on 512 MB
FULL_OPTIONAL_IDS    = ["nsfw", "smart_tv"]    # strong adult list + smart-TV


def protection_profile(config):
    """'lite' or 'full'. Defaults to full when unset (existing installs)."""
    return "lite" if config.get("protection_profile") == "lite" else "full"


def is_lite(config):
    return protection_profile(config) == "lite"


def upstreams_for(config):
    if is_lite(config):
        return [LITE_DNS_TIERS[lite_dns_tier(config)]["url"]]
    return FULL_UPSTREAMS


def recommended_ids(config):
    """Recommended blocklists to apply for this profile."""
    if is_lite(config):
        return list(LITE_RECOMMENDED_IDS)
    return [l["id"] for l in RECOMMENDED_LISTS]


def default_optional_ids(config):
    return LITE_OPTIONAL_IDS if is_lite(config) else FULL_OPTIONAL_IDS


def heuristic_toggles(config):
    """AGH's Safe Browsing / Parental heuristics do a per-domain category lookup
    that (a) is redundant with the Cloudflare Families upstream on LITE and (b)
    can hang a 512 MB router under load. So LITE turns them OFF and relies on the
    upstream + the 0.0.0.0→block-page alias (adult/malware still land on the block
    page). Safe Search stays on (a light DNS rewrite, no per-domain lookup). FULL
    keeps all three. Returns kwargs for apply_adguard_setup."""
    if is_lite(config):
        return {"enable_sb": False, "enable_parental": False, "enable_ss": True}
    return {"enable_sb": True, "enable_parental": True, "enable_ss": True}


def upstream_hosts(config):
    """Hostnames of THIS profile's upstream — excluded from our DoH mitigation so
    AGH never blocks its own resolver."""
    import urllib.parse
    return {urllib.parse.urlparse(u).hostname for u in upstreams_for(config)}


def enforce_profile_filters(config):
    """On LITE, disable every enabled AGH filter that isn't in the tiny lite set —
    chiefly GL.iNet's ~158K default ad list and any heavy list a prior install
    added. Keeps the footprint ~15 MB. Idempotent; a no-op on FULL."""
    if not is_lite(config):
        return
    keep = {l["url"] for l in RECOMMENDED_LISTS if l["id"] in recommended_ids(config)}
    keep |= {l["url"] for l in OPTIONAL_LISTS if l["id"] in default_optional_ids(config)}
    for f in get_all_filter_lists(config):
        if f["enabled"] and f["url"] not in keep:
            try:
                set_filter_enabled(config, f["url"], f["name"], False)
                print(f"[Profile] LITE: disabled heavy list '{f['name']}' ({f['rules_count']} rules)")
                _ag_wait_ready(config)
            except Exception as e:
                print(f"[Profile] could not disable '{f['name']}': {e}")


def apply_upstream_dns(config, force=False):
    """Point AdGuard's upstream at THIS profile's resolver (Cloudflare Families on
    LITE). Tests the DoH endpoint first, so a failed test never breaks DNS, and
    retries — self-healing installs where the install-time test failed because DNS
    wasn't settled yet. Idempotent; skips if already set."""
    import time
    want = upstreams_for(config)
    _ag_wait_ready(config)
    info = _ag_get(config, "/dns_info")
    if not info:
        return False
    if not force and info.get("upstream_dns") == want:
        return True
    for _ in range(3):
        try:
            tp  = json.dumps({"upstream_dns": want, "bootstrap_dns": UPSTREAM_BOOTSTRAP,
                              "fallback_dns": []}).encode()
            res = json.loads(urllib.request.urlopen(
                _ag_request(config, "/control/test_upstream_dns", tp), timeout=25).read().decode())
            if res and all(str(v).upper().startswith("OK") for v in res.values()):
                info["upstream_dns"]  = want
                info["bootstrap_dns"] = UPSTREAM_BOOTSTRAP
                _ag_post(config, "/dns_config", info)
                print(f"[Upstream] set to {want}")
                return True
            print(f"[Upstream] DoH test not OK yet: {res}")
        except Exception as e:
            print(f"[Upstream] test/apply retry: {e}")
        _ag_wait_ready(config)
        time.sleep(5)
    print("[Upstream] could not confirm DoH upstream — kept current (will retry next boot)")
    return False


def install_default_optional_lists(config):
    """Add the optional blocklists that are ON by default for this profile.
    Add-only: never removes a list the user later chose to turn off."""
    default_ids = default_optional_ids(config)
    active = get_active_filter_urls(config)
    for lst in OPTIONAL_LISTS:
        if lst["id"] in default_ids and lst["url"] not in active:
            try:
                _add_filter_url(config, lst["name"], lst["url"])
                print(f"[Filters] default optional list added: {lst['id']}")
            except Exception as e:
                print(f"[Filters] could not add default '{lst['id']}': {e}")


def remove_dead_lists(config):
    """Remove known-dead/redundant blocklists (safe no-op if not present)."""
    active = get_active_filter_urls(config)
    for url in DEAD_LISTS:
        if url in active:
            try:
                _remove_filter_url(config, url)
                print(f"[Filters] removed dead list {url}")
            except Exception as e:
                print(f"[Filters] could not remove {url}: {e}")


def refresh_filters(config):
    """Ask AGH to re-download all blocklists now (so new ones load immediately)."""
    try:
        urllib.request.urlopen(
            _ag_request(config, "/control/filtering/refresh", json.dumps({"whitelist": False}).encode()),
            timeout=15)
    except Exception as e:
        print(f"[Filters] refresh error: {e}")


# ── Blocklist manager (Settings → DNS Blocklists) ─────────────────────────────

def _list_category_map():
    """URL -> display category, for grouping lists in the blocklist manager."""
    norm = {"Family Safety": "Family & Content", "Security": "Security",
            "Ads & Tracking": "Ads & Tracking"}
    m = {}
    for l in RECOMMENDED_LISTS:
        m[l["url"]] = norm.get(l.get("category"), "Other")
    opt_cat = {"nsfw": "Family & Content", "gambling": "Family & Content",
               "smart_tv": "Ads & Tracking", "extra_ads": "Ads & Tracking",
               "adguard_dns": "Ads & Tracking", "bypass": "Security"}
    for l in OPTIONAL_LISTS:
        m[l["url"]] = opt_cat.get(l["id"], "Other")
    return m


_CAT_ORDER = {"Security": 0, "Family & Content": 1, "Ads & Tracking": 2, "Other": 3}


def get_all_filter_lists(config):
    """Every blocklist AGH knows about, annotated with category + rule count,
    for the Settings blocklist manager. Sorted by category, then name."""
    cmap = _list_category_map()
    try:
        st = json.loads(urllib.request.urlopen(
            _ag_request(config, "/control/filtering/status"), timeout=8).read().decode())
    except Exception as e:
        print(f"[Filters] status error: {e}")
        return []
    out = []
    for f in (st.get("filters") or []):
        out.append({
            "name":        f.get("name", ""),
            "url":         f.get("url", ""),
            "enabled":     bool(f.get("enabled")),
            "rules_count": int(f.get("rules_count", 0)),
            "category":    cmap.get(f.get("url"), "Other"),
        })
    out.sort(key=lambda x: (_CAT_ORDER.get(x["category"], 9), x["name"].lower()))
    return out


_FID_CAT_CACHE = {"ts": 0.0, "map": None}


def filter_id_category_map(config):
    """AGH filter_list_id -> our category (Security / Family & Content / Ads &
    Tracking / Other). Used to decide which blocklist hits are notify-worthy —
    a `FilteredBlackList` block only says "a list caught it", not which one.
    Cached ~5 min (filter ids are stable until a list is added/removed)."""
    import time
    now = time.time()
    if _FID_CAT_CACHE["map"] is not None and (now - _FID_CAT_CACHE["ts"] < 300):
        return _FID_CAT_CACHE["map"]
    cmap = _list_category_map()  # url -> category
    try:
        st = json.loads(urllib.request.urlopen(
            _ag_request(config, "/control/filtering/status"), timeout=8).read().decode())
        m = {f.get("id"): cmap.get(f.get("url"), "Other") for f in (st.get("filters") or [])}
        _FID_CAT_CACHE.update(ts=now, map=m)
    except Exception as e:
        print(f"[Filters] fid category map error: {e}")
    return _FID_CAT_CACHE["map"] or {}


def set_filter_enabled(config, url, name, enabled):
    """Enable or disable a single AGH blocklist via the set_url API."""
    payload = json.dumps({
        "url": url, "whitelist": False,
        "data": {"name": name, "url": url, "enabled": bool(enabled)},
    }).encode()
    urllib.request.urlopen(_ag_request(config, "/control/filtering/set_url", payload), timeout=10)


# ── AdGuard API helpers ───────────────────────────────────────────────────────

def _ag_request(config, path, payload=None):
    ag   = config.get("adguard", {})
    url  = ag.get("url", "http://127.0.0.1:3000")
    user = ag.get("username", "")
    pwd  = ag.get("password", "")
    req  = urllib.request.Request(
        f"{url}{path}",
        data=payload,
        headers={"Content-Type": "application/json"} if payload else {},
    )
    if user and pwd:
        auth = base64.b64encode(f"{user}:{pwd}".encode()).decode()
        req.add_header("Authorization", f"Basic {auth}")
    return req


def get_adguard_setup_status(config):
    """
    Return current AdGuard state needed for the setup wizard:
    whether we can connect, which filter URLs are already configured,
    and whether safe browsing / parental / safe search are enabled.
    """
    result = {
        "connected":    False,
        "existing_urls": set(),
        "safe_browsing": False,
        "parental":      False,
        "safe_search":   False,
    }
    try:
        with urllib.request.urlopen(_ag_request(config, "/control/filtering/status"), timeout=5) as r:
            data = json.loads(r.read().decode())
            result["existing_urls"] = {f["url"] for f in data.get("filters", [])}
            result["connected"] = True
    except Exception as e:
        print(f"[AdGuard setup] filtering/status error: {e}")
        return result

    for key, path in [("safe_browsing", "/control/safebrowsing/status"),
                      ("parental",      "/control/parental/status"),
                      ("safe_search",   "/control/safesearch/status")]:
        try:
            with urllib.request.urlopen(_ag_request(config, path), timeout=5) as r:
                result[key] = json.loads(r.read().decode()).get("enabled", False)
        except Exception:
            pass

    return result


def _enable_protection_verified(config, kind, label):
    """Enable an AGH global protection (kind = "safebrowsing" | "parental") via
    POST, then GET its /status to confirm it actually took. Returns (ok, msg);
    msg is "" on success and a human-readable warning on failure.

    Why verify instead of trusting the POST: GL.iNet's AGH build frequently
    rejects these endpoints (HTTP 415) or accepts the POST without persisting,
    so the POST result alone is not trustworthy — the wizard would otherwise
    report success for a toggle that did nothing, giving parents false
    confidence. We deliberately do NOT restart the AdGuard process to force it
    through: that drops the DNS listener and causes a network-wide outage. The
    install-time config.yaml edit (install.sh) handles the 415 case instead.
    """
    posted_err = None
    try:
        urllib.request.urlopen(_ag_request(config, f"/control/{kind}/enable", b"{}"), timeout=5)
    except Exception as e:
        posted_err = e
        print(f"[AdGuard] {label} enable POST failed: {e}")

    # Status is the source of truth — confirm the setting actually stuck.
    try:
        with urllib.request.urlopen(_ag_request(config, f"/control/{kind}/status"), timeout=5) as r:
            enabled = json.loads(r.read().decode()).get("enabled", False)
    except Exception as e:
        msg = (f"{label} toggle failed via API ({posted_err or e}) and its status could "
               f"not be confirmed — may require a router restart to take effect.")
        print(f"[AdGuard] {msg}")
        return False, msg

    if enabled:
        print(f"[AdGuard] {label} enabled (confirmed via status).")
        return True, ""

    msg = (f"{label} toggle failed via API — AdGuard still reports it disabled "
           f"(GL.iNet's build often returns 415 for this endpoint). It may require a "
           f"router restart to take effect, or it is applied at install time via config.yaml.")
    print(f"[AdGuard] {msg}")
    return False, msg


def _disable_protection(config, kind):
    """Best-effort turn OFF an AGH global protection (safebrowsing | parental).
    Used on LITE, where these heuristics are redundant with the Cloudflare
    Families upstream and their per-domain lookups can hang a low-RAM router.
    The authoritative disable is the config.yaml edit in install.sh (GL.iNet's
    API often 415s these); this covers the API-works case + re-applies."""
    try:
        urllib.request.urlopen(_ag_request(config, f"/control/{kind}/disable", b"{}"), timeout=5)
        print(f"[AdGuard] {kind} disabled (LITE — adult/malware handled upstream)")
    except Exception as e:
        print(f"[AdGuard] {kind} disable via API skipped ({e}); config.yaml handles it")


def apply_adguard_setup(config, list_ids, enable_sb, enable_parental, enable_ss):
    """
    Non-destructively apply the selected setup items.
    Blocklists are only added if their URL is not already configured.
    Returns (added_count, error_list).
    """
    errors  = []
    added   = 0

    _ag_wait_ready(config)   # let AGH settle before we start (it may still be booting)

    # Fetch existing URLs to avoid duplicates
    existing_urls = set()
    try:
        with urllib.request.urlopen(_ag_request(config, "/control/filtering/status"), timeout=5) as r:
            data = json.loads(r.read().decode())
            existing_urls = {f["url"] for f in data.get("filters", [])}
    except Exception as e:
        errors.append(f"Could not read existing filters: {e}")

    lists_by_id = {l["id"]: l for l in RECOMMENDED_LISTS}
    for lid in list_ids:
        lst = lists_by_id.get(lid)
        if not lst or lst["url"] in existing_urls:
            continue
        try:
            payload = json.dumps({"name": lst["name"], "url": lst["url"], "enabled": True}).encode()
            urllib.request.urlopen(_ag_request(config, "/control/filtering/add_url", payload), timeout=15)
            added += 1
            _ag_wait_ready(config)   # each add reloads AGH — wait before the next
        except Exception as e:
            errors.append(f"Failed to add '{lst['name']}': {e}")

    if added:
        try:
            payload = json.dumps({"whitelist": False}).encode()
            urllib.request.urlopen(_ag_request(config, "/control/filtering/refresh", payload), timeout=10)
        except Exception:
            pass

    if enable_sb:
        ok, msg = _enable_protection_verified(config, "safebrowsing", "Safe Browsing")
        if not ok:
            errors.append(msg)
    else:
        _disable_protection(config, "safebrowsing")

    if enable_parental:
        ok, msg = _enable_protection_verified(config, "parental", "Parental controls")
        if not ok:
            errors.append(msg)
    else:
        _disable_protection(config, "parental")

    if enable_ss:
        # Only force the secure-by-default "all engines on" state when Safe Search
        # is currently OFF — i.e. a genuine first-time setup. If it's already on,
        # PRESERVE the user's per-engine choices, notably YouTube Restricted Mode
        # (which they may have deliberately turned off to allow comments). Blanket-
        # re-enabling every engine here is what made an app update silently switch
        # YouTube Restricted Mode back on. The /social YouTube toggle stays the
        # source of truth; setup must not override it on a re-run.
        if get_safesearch_status(config).get("enabled"):
            print("[AdGuard setup] Safe search already on — preserving engine choices (incl. YouTube)")
        else:
            try:
                payload = json.dumps({
                    "enabled": True, "google": True, "youtube": True,
                    "bing": True, "duckduckgo": True, "pixabay": True,
                    "ecosia": True, "yandex": True,
                }).encode()
                # GL.iNet AGH build requires PUT for safesearch/settings
                req = _ag_request(config, "/control/safesearch/settings", payload)
                req.method = "PUT"
                urllib.request.urlopen(req, timeout=5)
                print("[AdGuard setup] Safe search enabled (all engines)")
            except Exception as e:
                print(f"[AdGuard setup] Safe search error: {e}")
                errors.append(f"Safe search: {e}")

    return added, errors


def get_safesearch_status(config):
    """Return current global safe search state: {"enabled": bool, ...engines}"""
    try:
        with urllib.request.urlopen(_ag_request(config, "/control/safesearch/status"), timeout=5) as r:
            return json.loads(r.read().decode())
    except Exception:
        return {"enabled": False}


def set_safesearch_enabled(config, enabled):
    """Enable or disable safe search across all engines. Secure by Default: when
    safe search is on, EVERY engine is on — including YouTube ("Restricted Mode",
    which hides mature videos and all comments). AdGuard is the source of truth
    for this state; the YouTube toggle on /social flips just the YouTube engine
    afterwards via set_safesearch_engines (no separate config flag to fall out of
    sync)."""
    payload = json.dumps({
        "enabled": enabled, "youtube": bool(enabled),
        "google": True, "bing": True, "duckduckgo": True,
        "pixabay": True, "ecosia": True, "yandex": True,
    }).encode()
    req = _ag_request(config, "/control/safesearch/settings", payload)
    req.method = "PUT"
    urllib.request.urlopen(req, timeout=5)


# Search engines AdGuard can force into safe search, in display order.
SAFE_SEARCH_ENGINES = ["google", "bing", "duckduckgo", "youtube",
                       "pixabay", "yandex", "ecosia"]

def set_safesearch_engines(config, engines):
    """Set safe search PER ENGINE. `engines` is {engine: bool}. The global
    'enabled' flag is on if any engine is on. Lets a user pick, e.g., Google/Bing
    images safe but YouTube normal (YouTube safe search = Restricted Mode = no
    comments)."""
    payload = {"enabled": any(bool(engines.get(e)) for e in SAFE_SEARCH_ENGINES)}
    for e in SAFE_SEARCH_ENGINES:
        payload[e] = bool(engines.get(e))
    req = _ag_request(config, "/control/safesearch/settings", json.dumps(payload).encode())
    req.method = "PUT"
    urllib.request.urlopen(req, timeout=5)


def get_blocked_services(config):
    """Return (all_services, blocked_ids).
    all_services = [{"id": str, "name": str}, ...]  sorted by name
    blocked_ids  = set of currently-blocked service ID strings
    """
    all_svcs, blocked = [], set()
    try:
        with urllib.request.urlopen(_ag_request(config, "/control/blocked_services/all"), timeout=5) as r:
            data = json.loads(r.read().decode())
            all_svcs = sorted(
                [{"id": s["id"], "name": s["name"]} for s in data.get("blocked_services", [])],
                key=lambda s: s["name"].lower(),
            )
    except Exception:
        pass
    try:
        with urllib.request.urlopen(_ag_request(config, "/control/blocked_services/get"), timeout=5) as r:
            blocked = set(json.loads(r.read().decode()).get("ids", []))
    except Exception:
        pass
    return all_svcs, blocked


def set_blocked_services(config, service_ids):
    """Set globally blocked services for all clients.
    AdGuard's /blocked_services/set expects a BARE JSON array of service IDs.
    (The {"ids":..., "schedule":...} object is only for the newer /update
    endpoint, which the GL.iNet AGH build v0.107.x does not expose — it 400s.)
    Reading state back uses /blocked_services/get, which returns {"ids":[...]}."""
    payload = json.dumps(list(service_ids)).encode()
    urllib.request.urlopen(
        _ag_request(config, "/control/blocked_services/set", payload), timeout=5)


def reset_adguard_stats(config):
    """Reset AdGuard Home's built-in stats counters and invalidate the local cache."""
    global _STATS_CACHE
    try:
        ag   = config.get("adguard", {})
        url  = ag.get("url", "http://127.0.0.1:3000")
        user = ag.get("username", "")
        pwd  = ag.get("password", "")
        auth = base64.b64encode(f"{user}:{pwd}".encode()).decode()
        req  = urllib.request.Request(
            f"{url}/control/stats_reset",
            method="POST",
            headers={"Authorization": f"Basic {auth}"},
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print(f"[AdGuard] stats_reset error: {e}")
    _STATS_CACHE = {"data": None, "ts": 0.0}


def clear_adguard_querylog(config):
    """Clear AdGuard Home's own query log. Without this, clearing Lantern Watch's
    query history is undone within a minute — the collector mirrors AGH's log,
    so any entries still in AGH get re-imported. Returns True on success."""
    try:
        ag   = config.get("adguard", {})
        url  = ag.get("url", "http://127.0.0.1:3000")
        user = ag.get("username", "")
        pwd  = ag.get("password", "")
        auth = base64.b64encode(f"{user}:{pwd}".encode()).decode()
        req  = urllib.request.Request(
            f"{url}/control/querylog_clear",
            method="POST",
            headers={"Authorization": f"Basic {auth}"},
        )
        urllib.request.urlopen(req, timeout=8)
        return True
    except Exception as e:
        print(f"[AdGuard] querylog_clear error: {e}")
        return False


def get_adguard_stats(config):
    now = time.time()
    if _STATS_CACHE["data"] is not None and now - _STATS_CACHE["ts"] < _CACHE_TTL:
        return _STATS_CACHE["data"]
    req = _ag_request(config, "/control/stats")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode())
            result = {
                "dns_queries":         data.get("num_dns_queries", 0),
                "blocked_filtering":   data.get("num_blocked_filtering", 0),
                "blocked_malware":     data.get("num_replaced_safebrowsing", 0),
                "blocked_adult":       data.get("num_replaced_parental", 0),
                "blocked_safesearch":  data.get("num_replaced_safesearch", 0),
                "avg_processing_time": round(data.get("avg_processing_time", 0) * 1000, 1),
                "num_dns_queries":     data.get("num_dns_queries", 0),
            }
            _STATS_CACHE["data"] = result
            _STATS_CACHE["ts"]   = now
            return result
    except Exception as e:
        print(f"AdGuard stats error: {e}")
        return _STATS_CACHE["data"] if _STATS_CACHE["data"] is not None else {}


# ── Internal GET/POST helpers ─────────────────────────────────────────────────

def _ag_get(config, path):
    try:
        with urllib.request.urlopen(_ag_request(config, "/control" + path), timeout=5) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f"[AGH GET {path}] {e}")
        return {}


def _ag_wait_ready(config, timeout=25):
    """Wait until AdGuard's API answers again. On GL.iNet, AGH reloads its DNS
    server after almost every config change and briefly refuses connections —
    so back-to-back setup calls otherwise fail (and retrying in a tight loop can
    thrash a low-RAM router). Returns True once it responds."""
    import time
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(_ag_request(config, "/control/status"), timeout=3) as r:
                if getattr(r, "status", 200) == 200:
                    return True
        except Exception:
            pass
        time.sleep(1.5)
    return False


def _ag_post(config, path, payload, retries=3):
    """POST to AGH, tolerating the post-change reload window: on a connection
    error, wait for AGH to come back and retry (gently — not a tight loop)."""
    data = json.dumps(payload).encode()
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(_ag_request(config, "/control" + path, data), timeout=8) as r:
                return True
        except Exception as e:
            if attempt < retries:
                _ag_wait_ready(config)
                continue
            print(f"[AGH POST {path}] {e}")
            return False


# ── DoH blocking ──────────────────────────────────────────────────────────────

_DOH_MARKER = "# Lantern Watch — DoH blocking"
DOH_BLOCK_DOMAINS = [
    "dns.cloudflare.com", "1dot1dot1dot1.cloudflare-dns.com",
    "family.cloudflare-dns.com", "security.cloudflare-dns.com",
    "dns.google", "dns.google.com",
    "dns.quad9.net", "dns11.quad9.net",
    "doh.opendns.com", "doh.familyshield.opendns.com",
    "dns.nextdns.io",
    "doh.mullvad.net",
    "doh.adguard.com", "doh.adguard-dns.com", "unfiltered.adguard-dns.com",
    "doh.cleanbrowsing.org",
    "dns.sb", "doh.sb",
    "freedns.controld.com", "p0.freedns.controld.com",
    "doh.xfinity.com",
]


def get_custom_rules(config):
    """Return current AGH custom filter rules as a list of strings."""
    return _ag_get(config, "/filtering/status").get("user_rules", []) or []


# ── User-added custom site blocks ─────────────────────────────────────────────
_CUSTOM_MARKER = "# Lantern Watch — custom blocks"

def normalize_block_domain(raw):
    """Turn user input (a URL or a domain) into a bare blockable domain, or '' if
    it doesn't look like one. 'https://www.ebay.com/deals?x=1' -> 'ebay.com'."""
    import re
    s = (raw or "").strip().lower()
    s = re.sub(r"^[a-z]+://", "", s)                                   # scheme
    s = s.split("/")[0].split("?")[0].split("#")[0].split(":")[0]     # host only
    if s.startswith("www."):
        s = s[4:]
    if re.match(r"^(?=.{1,253}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
                r"(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$", s):
        return s
    return ""


def get_custom_blocks(config):
    """User-added blocked domains (from the custom-block rule section)."""
    out, in_section = [], False
    for line in get_custom_rules(config):
        if line.strip() == _CUSTOM_MARKER:
            in_section = True
            continue
        if in_section and line.startswith("||") and line.endswith("^"):
            out.append(line[2:-1])
        elif in_section and line == "":
            continue
        else:
            in_section = False
    return out


def set_custom_blocks(config, domains):
    """Replace the custom-block rule section with `domains` (dedup, keep order)."""
    seen, clean = set(), []
    for d in domains:
        if d and d not in seen:
            seen.add(d); clean.append(d)
    with _rules_lock:
        cleaned, skip = [], False
        for line in get_custom_rules(config):
            if line.strip() == _CUSTOM_MARKER:
                skip = True
            elif skip and (line.startswith("||") or line == ""):
                continue
            else:
                skip = False
                cleaned.append(line)
        block = (["", _CUSTOM_MARKER] + [f"||{d}^" for d in clean]) if clean else []
        return _ag_post(config, "/filtering/set_rules", {"rules": cleaned + block})


def add_custom_block(config, raw):
    """Add a user domain block. Returns the normalized domain, or '' if invalid."""
    d = normalize_block_domain(raw)
    if not d:
        return ""
    blocks = get_custom_blocks(config)
    if d not in blocks:
        blocks.append(d)
        set_custom_blocks(config, blocks)
    return d


def remove_custom_block(config, domain):
    """Remove a user domain block."""
    domain = (domain or "").strip().lower()
    set_custom_blocks(config, [d for d in get_custom_blocks(config) if d != domain])


# ── Curated category packs ────────────────────────────────────────────────────
# Domains AdGuard's built-in Blocked Services engine doesn't cover (AI tools,
# lingerie, crypto, etc.). Applied as custom filter rules under _PACKS_MARKER.
# Enabled pack names live in config["blocked_packs"]; the rule section is always
# regenerated from that selection, so config is the source of truth.
_PACKS_MARKER = "# Lantern Watch — category packs"

# Each pack lists individual sites as (friendly label, [domain(s)]). A parent can
# tick sites one by one — block ChatGPT but keep Claude. Multiple domains under
# one label are treated as a single site (blocked/unblocked together).
CATEGORY_PACKS = {
    "AI Assistants": {
        "sites": [
            ("ChatGPT",       ["openai.com", "chatgpt.com"]),
            ("Claude",        ["claude.ai", "anthropic.com"]),
            ("Perplexity",    ["perplexity.ai"]),
            ("Midjourney",    ["midjourney.com"]),
            ("Character.AI",  ["character.ai"]),
            ("Poe",           ["poe.com"]),
            ("Cursor",        ["cursor.com"]),
            ("Copy.ai",       ["copy.ai"]),
            ("Lindy",         ["lindy.ai"]),
        ],
    },
    # (The "Dating" pack was removed 2026-07-09 — every site in it is already
    #  blocked by the default-on ShadowWhisperer Dating List, which is far more
    #  comprehensive, so the pack was pure redundancy.)
    "Lingerie": {
        "sites": [
            ("Victoria's Secret",        ["victoriassecret.com"]),
            ("Victoria's Secret PINK",   ["pink.com"]),
            ("Adore Me",                 ["adoreme.com"]),
            ("SKIMS",                    ["skims.com"]),
            ("Savage X Fenty",           ["savagex.com"]),
            ("ThirdLove",                ["thirdlove.com"]),
            ("CUUP",                     ["cuup.com", "shopcuup.com"]),
            ("Aerie",                    ["aerie.com"]),
            ("Soma",                     ["soma.com"]),
            ("Journelle",                ["journelle.com", "journele.com"]),
            ("Cosabella",                ["cosabella.com"]),
            ("Agent Provocateur",        ["agentprovocateur.com"]),
            ("Hanky Panky",              ["hankypanky.com"]),
            ("Wacoal",                   ["wacoal-america.com"]),
            ("Natori",                   ["natori.com"]),
            ("Spanx",                    ["spanx.com"]),
            ("b.tempt'd",                ["btemptd.com"]),
            ("Chantelle",                ["chantelle.com"]),
            ("Eberjey",                  ["eberjey.com"]),
            ("Fleur du Mal",             ["fleurdumal.com"]),
            ("Kiki de Montparnasse",     ["kikidemontparnasse.com"]),
            ("HerRoom",                  ["herroom.com"]),
            ("Bare Necessities",         ["barenecessities.com"]),
            ("Anya Lust",                ["anyalust.com"]),
            ("Negative Underwear",       ["negativeunderwear.com"]),
            ("Lonely Label",             ["lonelylabel.com"]),
            ("Bluebella",                ["bluebella.com", "bluebella.us"]),
            ("Honey Birdette",           ["honeybirdette.com"]),
            ("Playful Promises",         ["playfulpromises.com"]),
            ("Gooseberry Intimates",     ["gooseberryintimates.com"]),
            ("Lounge Underwear",         ["lounge.com", "loungeundergarments.com"]),
            ("Frederick's of Hollywood", ["fredericks.com"]),
            ("Intimissimi",              ["intimissimi.com"]),
            ("Aubade",                   ["aubade.com"]),
            ("Simone Pérèle",            ["simone-perele.com"]),
            ("Fleur of England",         ["fleurofengland.com"]),
            ("Bordelle",                 ["bordelle.co.uk"]),
            ("Empress Mimi",             ["empressmimi.com"]),
            ("Yandy",                    ["yandy.com"]),
            ("Leg Avenue",               ["legavenue.com"]),
            ("La Senza",                 ["lasenza.com", "lasenza.ca"]),
            ("La Vie en Rose",           ["lavieenrose.com"]),
            ("Knix",                     ["knix.com", "knix.ca"]),
            ("Understance",              ["understance.com"]),
            ("Montelle",                 ["montelleintimates.com", "montellelingerie.com"]),
            ("Change Lingerie",          ["change.com"]),
            ("Bravissimo",               ["bravissimo.com"]),
            ("Figleaves",                ["figleaves.com"]),
            ("Curvy Kate",               ["curvykate.com"]),
            ("Pour Moi",                 ["pourmoi.co.uk"]),
            ("Panache",                  ["panache-lingerie.com"]),
            ("Elomi",                    ["elomilingerie.com"]),
            ("Glamorise",                ["glamourise.com"]),
            ("Cacique (Lane Bryant)",    ["cacique.lanebryant.com"]),
            ("Torrid",                   ["torrid.com"]),
            ("Arula",                    ["arula.com"]),
            ("Livy",                     ["livy.com"]),
            ("Fortnight",                ["fortnightlingerie.com"]),
            ("Blush Lingerie",           ["blushlingerie.com"]),
            ("Blush Tan",                ["blushtan.com"]),
            ("Secrets in Lace",          ["secretsinlace.com"]),
            ("Silhouette",               ["silhouettefinelingerie.com"]),
            ("Linea Germania",           ["lineagermania.com", "lineagermania.ca"]),
            ("Dynamic",                  ["dynamic.ca"]),
            ("Mary Young",               ["maryyoung.com"]),
            ("Secrets From Your Sister", ["secretsfromyoursister.com"]),
            ("Fine Body Lingerie",       ["finebodylingerie.com"]),
            ("Night Frills",             ["nightfrills.ca"]),
            ("NK iMode",                 ["nkimode.com"]),
            ("Christine Vancouver",      ["christinevancouver.com"]),
            ("ELGA Milano",              ["elgamilano.com"]),
            ("Reservation Lingerie",     ["reservationlingerie.com"]),
            ("Taylor Jay",               ["shoptaylorjay.com"]),
            ("Addition Elle",            ["additionelle.com"]),
            ("Penningtons",              ["penningtons.com"]),
            ("Lilianne Lingerie",        ["liliannelingerie.com"]),
        ],
    },
    "Swimwear": {
        "sites": [
            ("Cupshe",                   ["cupshe.com"]),
            ("Swimsuits For All",        ["swimsuitsforall.com"]),
            ("Andie Swim",               ["andieswim.com"]),
            ("Albion Fit",               ["albionfit.com"]),
            ("Summersalt",               ["summersalt.com"]),
            ("L*Space",                  ["lspace.com"]),
            ("Frankies Bikinis",         ["frankiesbikinis.com"]),
            ("Triangl",                  ["triangl.com"]),
            ("Vitamin A",                ["vitaminaswim.com"]),
            ("Monday Swimwear",          ["mondayswimwear.com"]),
            ("Solid & Striped",          ["solidandstriped.com"]),
            ("Mikoh",                    ["mikoh.com"]),
            ("Minnow Swim",              ["minnowswim.com"]),
            ("We Wore What",             ["weworewhat.com"]),
            ("PacSun",                   ["pacsun.com"]),
            ("Thandie",                  ["thandie.com"]),
            ("Mara Hoffman",             ["marahoffman.com"]),
            ("Everything But Water",     ["everythingbutwater.com"]),
            ("Zulu & Zephyr",            ["zuluandzephyr.com"]),
            ("Jade Swim",                ["jade-swim.com"]),
            ("Zaful",                    ["zaful.com"]),
            ("Bikini.com",               ["bikini.com"]),
            ("Miraclesuit",              ["miraclesuit.com"]),
            ("Seafolly",                 ["seafolly.com"]),
            ("Maaji",                    ["maaji.co"]),
            ("JOLYN",                    ["jolyn.com"]),
            ("Body Glove",               ["bodyglove.com"]),
            ("Beach Riot",               ["beachriot.com"]),
            ("Montce",                   ["montce.com"]),
            ("Kulani Kinis",             ["kulaniskin.com"]),
            ("Black Bough Swim",         ["blackboughswim.com"]),
            ("Venus",                    ["venus.com"]),
            ("Beachsissi",               ["beachsissi.com"]),
            ("Left on Friday",           ["leftonfriday.com"]),
            ("Londre",                   ["londrebodywear.com", "londrebodywear.ca"]),
            ("Nani Swimwear",            ["naniwear.com"]),
            ("Carve Designs",            ["carvedesigns.com"]),
            ("Athleta",                  ["athleta.com"]),
            ("Speedo",                   ["speedo.com"]),
            ("TYR",                      ["tyr.com"]),
            ("Arena",                    ["arena-us.com"]),
            ("Zoggs",                    ["zoggsswimwear.com"]),
            ("Fin Swimwear",             ["finswimwear.com"]),
            ("Robin Piccone",            ["robinpiccone.com"]),
            ("BECCA",                    ["beccaswim.com"]),
            ("Gottex",                   ["gottex-swimwear.com"]),
            ("Bleu by Rod Beattie",      ["bleubyrrodbeattie.com"]),
            ("Sunsets",                  ["sunsetsinc.com"]),
            ("La Blanca",                ["lablanca.com"]),
            ("Bikini Village",           ["bikinivillage.com"]),
            ("Swimco",                   ["swimco.com"]),
            ("Gigi C",                   ["gigi-c.com"]),
            ("Mia Swimwear",             ["miaswimwear.com"]),
            ("Unika",                    ["unika.ca"]),
            ("June Swimwear",            ["june-swimwear.com"]),
            ("Hoaka Swimwear",           ["hoaka-swimwear.com"]),
            ("Minimi Swimwear",          ["minimiswimwear.com"]),
            ("Saltwater Swim",           ["saltwaterswim.ca"]),
            ("Azura Swimwear",           ["azuraswimwear.com"]),
            ("Body & Beach",             ["bodyandbeach.ca"]),
            ("Selfish Swimwear",         ["selfishswimwear.com"]),
            ("Minnow Bathers",           ["minnowbathers.com"]),
            ("O'Neill",                  ["theoneilstore.com"]),
            ("Rip Curl",                 ["ripcurl.ca"]),
            ("Roxy",                     ["roxy.com", "roxy.ca"]),
            ("Billabong",                ["billabong.com", "billabong.ca"]),
            ("Hurley",                   ["hurley.ca"]),
            ("Beach Bunny",             ["beachbunnyswimwear.com"]),
            ("SwimOutlet",              ["swimoutlet.com"]),
        ],
    },
    # These block the WHOLE retailer site, not just its lingerie/swim section.
    "Retailers (Lingerie & Swim)": {
        "sites": [
            ("J.Crew",                   ["jcrew.com"]),
            ("Target",                   ["target.com"]),
            ("SHEIN",                    ["shein.com"]),
            ("Revolve",                  ["revolve.com"]),
            ("Free People",              ["freepeople.com"]),
            ("Simons",                   ["simons.ca"]),
            ("Reitmans",                 ["reitmans.com"]),
            ("H&M",                      ["hm.com"]),
            ("Zara",                     ["zara.com"]),
            ("Hudson's Bay",             ["hudsonbay.com"]),
            ("Roots",                    ["roots.com"]),
            ("MEC",                      ["mountainequipmentcoop.com"]),
            ("Forever 21",               ["forever21.ca"]),
            ("Garage",                   ["garageclothing.com"]),
            ("Ardene",                   ["ardene.com"]),
            ("Urban Outfitters",         ["urbanoutfitters.com"]),
            ("Anthropologie",            ["anthropologie.com"]),
            ("Lululemon",                ["lululemon.com"]),
            ("Local Eclectic",           ["localeclectic.com"]),
        ],
    },
    "Alcohol, Vaping & Cannabis": {
        "sites": [
            ("Total Wine", ["totalwine.com"]),
            ("LCBO",       ["lcbo.com"]),
            ("Drizly",     ["drizly.com"]),
            ("JUUL",       ["juul.com"]),
            ("Vuse",       ["vuse.com"]),
            ("Cigar.com",  ["cigar.com"]),
            ("Leafly",     ["leafly.com"]),
            ("Weedmaps",   ["weedmaps.com"]),
        ],
    },
    "Weapons & Tactical": {
        "sites": [
            ("Cabela's",              ["cabelas.com"]),
            ("Bass Pro Shops",        ["basspro.com"]),
            ("MidwayUSA",             ["midwayusa.com"]),
            ("Brownells",             ["brownells.com"]),
            ("Blade HQ",              ["bladehq.com"]),
            ("Bud's Gun Shop",        ["budsgunshop.com"]),
            ("Palmetto State Armory", ["palmettostatearmory.com"]),
        ],
    },
    "Crypto & Investing": {
        "sites": [
            ("Coinbase",      ["coinbase.com"]),
            ("Binance",       ["binance.com"]),
            ("Crypto.com",    ["crypto.com"]),
            ("Kraken",        ["kraken.com"]),
            ("Gemini",        ["gemini.com"]),
            ("OKX",           ["okx.com"]),
            ("Bybit",         ["bybit.com"]),
            ("KuCoin",        ["kucoin.com"]),
            ("CoinMarketCap", ["coinmarketcap.com"]),
            ("Robinhood",     ["robinhood.com"]),
            ("TradingView",   ["tradingview.com"]),
            ("Wealthsimple",  ["wealthsimple.com"]),
            ("Webull",        ["webull.com"]),
            ("E*TRADE",       ["etrade.com"]),
        ],
    },
    "Cloud & File-Sharing": {
        "sites": [
            ("Dropbox",    ["dropbox.com"]),
            ("Box",        ["box.com"]),
            ("MEGA",       ["mega.nz"]),
            ("MediaFire",  ["mediafire.com"]),
            ("WeTransfer", ["wetransfer.com"]),
            ("AnonFiles",  ["anonfiles.com"]),
        ],
    },
}

# Every domain that legitimately belongs to a pack — used to validate submissions.
_ALL_PACK_DOMAINS = {d for p in CATEGORY_PACKS.values()
                     for _, ds in p["sites"] for d in ds}


def get_blocked_pack_domains(config):
    """Domains currently blocked via the curated category-pack rule section."""
    out, in_section = [], False
    for line in get_custom_rules(config):
        if line.strip() == _PACKS_MARKER:
            in_section = True
            continue
        if in_section and line.startswith("||") and line.endswith("^"):
            out.append(line[2:-1])
        elif in_section and line == "":
            continue
        else:
            in_section = False
    return out


def set_blocked_pack_domains(config, domains):
    """Rewrite the pack rule section to exactly `domains` (only recognised pack
    domains are kept; deduped, order preserved). State lives in the rules, like
    custom blocks — no config field to persist."""
    seen, clean = set(), []
    for d in domains:
        d = (d or "").strip().lower()
        if d in _ALL_PACK_DOMAINS and d not in seen:
            seen.add(d); clean.append(d)
    with _rules_lock:
        cleaned, skip = [], False
        for line in get_custom_rules(config):
            if line.strip() == _PACKS_MARKER:
                skip = True
            elif skip and (line.startswith("||") or line == ""):
                continue
            else:
                skip = False
                cleaned.append(line)
        block = (["", _PACKS_MARKER] + [f"||{d}^" for d in clean]) if clean else []
        _ag_post(config, "/filtering/set_rules", {"rules": cleaned + block})
    return clean


def get_doh_blocking_status(config):
    return any(_DOH_MARKER in r for r in get_custom_rules(config))


# Firefox (and Chrome-family) look up this canary domain before enabling their
# own DoH. If it fails to resolve (NXDOMAIN), the browser leaves DoH OFF and uses
# our filtered DNS — the gentle, no-breakage way to keep browsers on our filter.
DOH_CANARY_DOMAIN = "use-application-dns.net"


def apply_doh_dns_mitigation(config):
    """Always-on, low-breakage DoH mitigation applied purely at the DNS level
    (no firewall, no touching port 443/853 or resolver IPs, so it can't break
    ordinary sites or devices):
      • answer the Firefox canary (use-application-dns.net) with NXDOMAIN, so
        Firefox voluntarily disables its own DoH and uses our filtered DNS;
      • block the well-known public DoH provider hostnames, so apps/browsers that
        hardcode them fall back to normal DNS.
    The stricter enforcement (blocking DoT :853 + known DoH resolver IPs via
    iptables) stays behind the opt-in `doh_blocking` toggle — see
    apply_doh_iptables. Idempotent; safe to call on every boot/setup.
    """
    with _rules_lock:
        current = get_custom_rules(config)
        # Strip existing DoH block section (marker + the rules that follow it)
        cleaned, in_doh = [], False
        for line in current:
            if line.strip() == _DOH_MARKER:
                in_doh = True
                continue
            if in_doh and (line.startswith("||") or line == ""):
                continue
            in_doh = False
            cleaned.append(line)

        # Never block THIS profile's own upstream in the DoH mitigation, or AGH
        # loses its resolver (e.g. Cloudflare Families on LITE).
        _up_hosts = upstream_hosts(config)
        block_domains = [d for d in DOH_BLOCK_DOMAINS if d not in _up_hosts]

        doh_rules = (
            ["", _DOH_MARKER, f"||{DOH_CANARY_DOMAIN}^$dnsrewrite=NXDOMAIN"]
            + [f"||{d}^$important" for d in block_domains]
        )
        new_rules = cleaned + doh_rules

        # Collapse multiple blank lines
        final, prev_blank = [], False
        for line in new_rules:
            is_blank = line.strip() == ""
            if not (is_blank and prev_blank):
                final.append(line)
            prev_blank = is_blank

        return _ag_post(config, "/filtering/set_rules", {"rules": final})


# ── Service allowlist ─────────────────────────────────────────────────────────
# Domains Lantern Watch itself relies on — always allowlisted (@@ rules) so
# filtering / Safe Browsing / Parental can never block our own push, updates, or
# telemetry. AdGuard's Safe Browsing was flagging ntfy.sh (our push service, and
# the ntfy phone app) as "malware" — a false positive that would silently break
# ntfy notifications for anyone who uses that channel.
_ALLOW_MARKER = "# Lantern Watch — service allowlist"
SERVICE_ALLOWLIST = [
    "ntfy.sh",                    # ntfy push notifications (+ the ntfy phone app)
    "api.telegram.org",           # Telegram notifications
    "lanternwatch.org",           # update feed
    "api.github.com",             # update-version check (git tags)
    "github.com",                 # install / clone
    "raw.githubusercontent.com",  # blocklists + install script
    "script.google.com",          # anonymous install / usage ping
]


def apply_service_allowlist(config):
    """Ensure Lantern Watch's own service domains are allowlisted, so filtering,
    Safe Browsing, or Parental can't block our push / updates / telemetry.
    Idempotent; applied at setup and on every boot."""
    with _rules_lock:
        current = get_custom_rules(config)
        cleaned, in_sec = [], False
        for line in current:
            if line.strip() == _ALLOW_MARKER:
                in_sec = True
                continue
            if in_sec and (line.startswith("@@||") or line == ""):
                continue
            in_sec = False
            cleaned.append(line)
        allow_rules = ["", _ALLOW_MARKER] + [f"@@||{d}^$important" for d in SERVICE_ALLOWLIST]
        new_rules = cleaned + allow_rules
        final, prev_blank = [], False
        for line in new_rules:
            is_blank = line.strip() == ""
            if not (is_blank and prev_blank):
                final.append(line)
            prev_blank = is_blank
        return _ag_post(config, "/filtering/set_rules", {"rules": final})


# Known DoH resolver IPs — port 443 and 853 (DoT) iptables blocking
DOH_BLOCK_IPS = [
    "1.1.1.1", "1.0.0.1",               # Cloudflare
    "8.8.8.8", "8.8.4.4",               # Google
    "9.9.9.9", "149.112.112.112",       # Quad9
    "208.67.222.222", "208.67.220.220", # OpenDNS
    "94.140.14.14", "94.140.15.15",     # AdGuard DNS
]


def apply_doh_iptables(enabled):
    """
    Add or remove FORWARD chain iptables rules for DoT (port 853) and known DoH
    resolver IPs on port 443. Clears existing rules first — safe to call on each save.
    """
    for ip in DOH_BLOCK_IPS:
        while subprocess.run(
            ["iptables", "-D", "FORWARD", "-p", "tcp", "-d", ip, "--dport", "443", "-j", "REJECT"],
            capture_output=True,
        ).returncode == 0:
            pass
    for proto in ("tcp", "udp"):
        while subprocess.run(
            ["iptables", "-D", "FORWARD", "-p", proto, "--dport", "853", "-j", "REJECT"],
            capture_output=True,
        ).returncode == 0:
            pass
    if enabled:
        try:
            for ip in DOH_BLOCK_IPS:
                subprocess.run(
                    ["iptables", "-I", "FORWARD", "-p", "tcp", "-d", ip, "--dport", "443", "-j", "REJECT"],
                    check=True, capture_output=True,
                )
            for proto in ("tcp", "udp"):
                subprocess.run(
                    ["iptables", "-I", "FORWARD", "-p", proto, "--dport", "853", "-j", "REJECT"],
                    check=True, capture_output=True,
                )
            print("[DoH] iptables rules applied (port 853 + known DoH IPs on 443)")
        except Exception as e:
            print(f"[DoH] iptables error: {e}")


# ── DHCP helpers (reads GL.iNet/dnsmasq lease file, UCI static leases) ───────

def get_dhcp_leases():
    """Parse /tmp/dhcp.leases → list of {mac, ip, hostname}."""
    leases = []
    try:
        with open("/tmp/dhcp.leases") as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) >= 4:
                    leases.append({
                        "mac":      parts[1].lower(),
                        "ip":       parts[2],
                        "hostname": parts[3] if parts[3] != "*" else "",
                    })
    except Exception as e:
        print(f"[DHCP] leases read error: {e}")
    return leases


def get_static_leases():
    """Return dict of {mac: {mac, ip, hostname, section}} from UCI dhcp config."""
    import subprocess
    result = subprocess.run(["uci", "show", "dhcp"], capture_output=True, text=True)
    sections, current = {}, None
    for line in result.stdout.splitlines():
        if "=host" in line:
            current = line.split("=")[0]
            sections[current] = {"section": current}
        elif current and ".mac=" in line:
            mac = line.split("=", 1)[1].strip("'").lower()
            sections[current]["mac"] = mac
        elif current and ".ip=" in line:
            sections[current]["ip"] = line.split("=", 1)[1].strip("'")
        elif current and ".name=" in line:
            sections[current]["hostname"] = line.split("=", 1)[1].strip("'")
    return {v["mac"]: v for v in sections.values() if "mac" in v}


def add_static_lease(mac, ip, hostname):
    """Add a static DHCP lease via UCI. Returns True on success."""
    import subprocess
    try:
        mac = mac.lower()
        for cmd in [
            ["uci", "add", "dhcp", "host"],
            ["uci", "set", f"dhcp.@host[-1].mac={mac}"],
            ["uci", "set", f"dhcp.@host[-1].ip={ip}"],
            ["uci", "set", f"dhcp.@host[-1].name={hostname}"],
            ["uci", "commit", "dhcp"],
        ]:
            subprocess.run(cmd, check=True, capture_output=True)
        subprocess.run(["/etc/init.d/dnsmasq", "reload"], capture_output=True)
        print(f"[DHCP] Static lease added: {mac} -> {ip} ({hostname})")
        return True
    except Exception as e:
        print(f"[DHCP] add_static_lease error: {e}")
        return False


def remove_static_lease(mac):
    """Remove a static DHCP lease by MAC address. Returns True on success."""
    import subprocess
    mac = mac.lower()
    static = get_static_leases()
    if mac not in static:
        return False
    try:
        section = static[mac]["section"]
        subprocess.run(["uci", "delete", section], check=True, capture_output=True)
        subprocess.run(["uci", "commit", "dhcp"], check=True, capture_output=True)
        subprocess.run(["/etc/init.d/dnsmasq", "reload"], capture_output=True)
        print(f"[DHCP] Static lease removed: {mac}")
        return True
    except Exception as e:
        print(f"[DHCP] remove_static_lease error: {e}")
        return False


# ── AGH per-client service blocking ──────────────────────────────────────────

AGH_SERVICE_GROUPS = {
    "Social Media": [
        "youtube", "tiktok", "instagram", "facebook", "twitter", "snapchat",
        "reddit", "twitch", "pinterest", "discord", "bluesky", "tumblr",
        "vimeo", "dailymotion", "imgur", "4chan", "9gag", "amino",
        "bigo_live", "clubhouse", "mastodon", "flickr", "kook", "wizz",
        "vk", "ok",
    ],
    "Gaming": [
        "steam", "epic_games", "roblox", "minecraft", "playstation", "xboxlive",
        "leagueoflegends", "valorant", "nintendo", "battle_net", "riot_games",
        "electronic_arts", "ubisoft", "rockstar_games", "gog",
        "activision_blizzard", "blizzard_entertainment", "nvidia",
        "wargaming", "fifa", "origin",
    ],
    "Streaming & Music": [
        "netflix", "disneyplus", "hulu", "amazon_streaming", "spotify",
        "apple_streaming", "hbomax", "crunchyroll", "peacock_tv", "pluto_tv",
        "paramountplus", "discoveryplus", "deezer", "soundcloud", "tidal",
        "plex", "iheartradio", "nebula", "espn", "rakuten_viki",
        "lionsgateplus", "samsung_tv_plus",
    ],
    "Messaging & Chat": [
        "whatsapp", "signal", "telegram", "viber", "skype", "line", "kik",
        "wechat", "kakaotalk", "qq", "slack", "olvid",
    ],
    "Dating / Adult": [
        "tinder", "onlyfans", "plenty_of_fish",
    ],
    "Gambling": [
        "betano", "betfair", "betway", "blaze",
    ],
    "Shopping": [
        "amazon", "aliexpress", "ebay", "shein", "temu", "shopee",
    ],
    "Privacy Bypass": [
        "icloud_private_relay", "cloudflare",
    ],
}

# Which blocked-service CATEGORIES notify / show in the dashboard "Blocked
# Content". All OFF by default — a fresh install shows no pre-checked Notify
# boxes; the parent opts in per category on /blocked-services once they block
# something they want alerts for. "Other" = AGH services we don't group.
SERVICE_NOTIFY_DEFAULTS = {
    "Social Media":       False,
    "Messaging & Chat":   False,
    "Dating / Adult":     False,
    "Gambling":           False,
    "Gaming":             False,
    "Streaming & Music":  False,
    "Shopping":           False,
    "Privacy Bypass":     False,
    "Other":              False,
}


def service_notify_enabled(category, config):
    """Whether blocked-service hits in this category should notify + appear in
    Blocked Content. Falls back to the smart defaults when unset (existing
    configs predate the setting, and load_config does not merge defaults)."""
    if not category:
        return False
    prefs = (config or {}).get("service_notify") or {}
    if category in prefs:
        return bool(prefs[category])
    return SERVICE_NOTIFY_DEFAULTS.get(category, False)


def _service_rule_domain(rule):
    """Extract a base domain from an AGH blocked-service rule like '||tiktok.com^'."""
    r = (rule or "").strip()
    if not r or r.startswith("!") or r.startswith("@@"):
        return None
    if r.startswith("||"):
        r = r[2:]
    for sep in ("^", "$", "/"):
        r = r.split(sep, 1)[0]
    r = r.lstrip("*.").lower().strip(".")
    return r if ("." in r and " " not in r) else None


_SVC_CAT_CACHE = {"ts": 0.0, "index": None}


def _service_domain_category_index(config):
    """base-domain -> service category, built from AGH's own service rules and
    AGH_SERVICE_GROUPS. Cached ~1h (the service catalog is effectively static)."""
    import time
    now = time.time()
    if _SVC_CAT_CACHE["index"] is not None and (now - _SVC_CAT_CACHE["ts"] < 3600):
        return _SVC_CAT_CACHE["index"]
    sid_cat = {}
    for cat, ids in AGH_SERVICE_GROUPS.items():
        for sid in ids:
            sid_cat[sid] = cat
    try:
        raw = urllib.request.urlopen(
            _ag_request(config, "/control/blocked_services/all"), timeout=8).read().decode()
        d = json.loads(raw)
        svcs = d.get("blocked_services", d) if isinstance(d, dict) else d
        index = {}
        for s in (svcs or []):
            cat = sid_cat.get(s.get("id"), "Other")
            for rule in s.get("rules", []):
                dom = _service_rule_domain(rule)
                if dom:
                    index[dom] = cat
        _SVC_CAT_CACHE.update(ts=now, index=index)
    except Exception as e:
        print(f"[Services] category index error: {e}")
    return _SVC_CAT_CACHE["index"] or {}


def service_category_for_domain(domain, config):
    """Category of the blocked SERVICE a domain belongs to (walking up the domain
    for subdomains), or None if it isn't a known blocked-service domain."""
    idx = _service_domain_category_index(config)
    if not idx:
        return None
    d = (domain or "").lower().strip(".")
    while d:
        if d in idx:
            return idx[d]
        if "." not in d:
            break
        d = d.split(".", 1)[1]
    return None


AGH_SERVICE_LABELS = {
    "xboxlive":             "Xbox Live",
    "battle_net":           "Battle.net",
    "leagueoflegends":      "League of Legends",
    "epic_games":           "Epic Games",
    "electronic_arts":      "EA / Origin",
    "rockstar_games":       "Rockstar Games",
    "activision_blizzard":  "Activision Blizzard",
    "blizzard_entertainment": "Blizzard",
    "amazon_streaming":     "Prime Video",
    "apple_streaming":      "Apple TV+",
    "hbomax":               "Max (HBO)",
    "peacock_tv":           "Peacock",
    "pluto_tv":             "Pluto TV",
    "paramountplus":        "Paramount+",
    "discoveryplus":        "Discovery+",
    "plenty_of_fish":       "Plenty of Fish",
    "iheartradio":          "iHeartRadio",
    "rakuten_viki":         "Viki",
    "lionsgateplus":        "Lionsgate+",
    "samsung_tv_plus":      "Samsung TV+",
    "bigo_live":            "BIGO Live",
    "kakaotalk":            "KakaoTalk",
    "ok":                   "OK.ru",
    "wargaming":            "Wargaming",
    "icloud_private_relay": "iCloud Private Relay",
    "4chan":                 "4chan",
    "9gag":                 "9GAG",
}


def get_agh_clients(config):
    """Return {name: client_dict} for all named AGH clients."""
    data = _ag_get(config, "/clients")
    clients = data.get("clients") or []
    return {c["name"]: c for c in clients}


def pretty_hostname(raw):
    """Clean a raw DHCP hostname into a readable display name."""
    if not raw:
        return ""
    name = raw.strip()
    for suffix in (".lan", ".local", ".home", ".internal"):
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)]
    name = name.replace("-", " ").replace("_", " ")
    return " ".join(w.capitalize() for w in name.split())


def get_ip_hostname_map(config):
    """
    Build {ip: hostname} from DHCP leases, static leases, and AGH auto-clients.
    Returns empty dict on any error — callers treat missing entries as unknown.
    """
    now = time.time()
    if _HOSTNAME_CACHE["data"] is not None and now - _HOSTNAME_CACHE["ts"] < _CACHE_TTL:
        return _HOSTNAME_CACHE["data"]

    result = {}

    # Dynamic DHCP leases
    try:
        for lease in get_dhcp_leases():
            ip, host = lease.get("ip", ""), lease.get("hostname", "")
            if ip and host:
                result[ip] = host
    except Exception:
        pass

    # Static/pinned leases (override dynamic — more reliable)
    try:
        for entry in get_static_leases().values():
            ip, host = entry.get("ip", ""), entry.get("hostname", "")
            if ip and host:
                result[ip] = host
    except Exception:
        pass

    # AGH auto-discovered clients (have a 'host' field per IP)
    try:
        data = _ag_get(config, "/clients")
        for ac in data.get("auto_clients") or []:
            ip   = ac.get("ip", "")
            host = ac.get("host", "")
            if ip and host and host != ip:
                result[ip] = host
    except Exception:
        pass

    _HOSTNAME_CACHE["data"] = result
    _HOSTNAME_CACHE["ts"]   = now
    return result


def get_client_blocked_services(config, client_name):
    """Return list of blocked service IDs for a named AGH client ([] if none/unknown)."""
    clients = get_agh_clients(config)
    if client_name not in clients:
        return []
    svc = clients[client_name].get("blocked_services")
    if isinstance(svc, list):
        return svc
    if isinstance(svc, dict):
        return svc.get("ids") or []
    return []


def get_client_protection(config, client_name):
    """
    Return per-client protection settings dict, or defaults if client doesn't exist.
    Keys: parental, safebrowsing, safe_search
    """
    clients = get_agh_clients(config)
    c = clients.get(client_name, {})
    ss = c.get("safe_search") or {}
    return {
        "parental":     c.get("parental_enabled", False),
        "safebrowsing": c.get("safebrowsing_enabled", False),
        "safe_search":  ss.get("enabled", False),
    }


def set_client_blocked_services(config, client_name, ip, service_ids,
                                 parental=None, safebrowsing=None, safe_search=None):
    """
    Create or update an AGH named client with blocked services and optional
    per-client protection overrides. Tags device as user_child automatically.
    """
    existing = get_agh_clients(config)
    use_global_svc = len(service_ids) == 0

    # YouTube left off on purpose — its safe search is Restricted Mode, which
    # disables all comments. Image/search safety without breaking YouTube.
    safe_search_obj = {"enabled": bool(safe_search), "google": bool(safe_search),
                       "youtube": False, "bing": bool(safe_search),
                       "duckduckgo": bool(safe_search), "pixabay": bool(safe_search)}

    if client_name in existing:
        client = dict(existing[client_name])
        client["blocked_services"] = service_ids
        client["use_global_blocked_services"] = use_global_svc
        # Managed devices must keep blocklists/custom rules on. When we flip
        # use_global_settings off (below), AGH falls back to this client's own
        # filtering_enabled, which defaults to False — so set it explicitly or
        # the device silently stops filtering.
        client["filtering_enabled"] = True
        if parental is not None:
            client["parental_enabled"] = parental
            client["use_global_settings"] = False
        if safebrowsing is not None:
            client["safebrowsing_enabled"] = safebrowsing
            client["use_global_settings"] = False
        if safe_search is not None:
            client["safe_search"] = safe_search_obj
            client["use_global_settings"] = False
        if "user_child" not in client.get("tags", []):
            client.setdefault("tags", []).append("user_child")
        return _ag_post(config, "/clients/update", {"name": client_name, "data": client})
    else:
        client = {
            "name":                        client_name,
            "ids":                         [ip] if ip else [],
            "use_global_blocked_services": use_global_svc,
            "blocked_services":            service_ids,
            "tags":                        ["user_child"],
            "ignore_querylog":             False,
            "ignore_statistics":           False,
            "use_global_settings":         (parental is None and safebrowsing is None and safe_search is None),
            # Keep blocklists/custom rules active even when use_global_settings is
            # False (otherwise AGH defaults this client's filtering to off).
            "filtering_enabled":           True,
        }
        if parental is not None:
            client["parental_enabled"] = parental
        if safebrowsing is not None:
            client["safebrowsing_enabled"] = safebrowsing
        if safe_search is not None:
            client["safe_search"] = safe_search_obj
        return _ag_post(config, "/clients/add", client)


# ── Domain allowlist (AGH custom rules with @@|| exceptions) ─────────────────

_ALLOWLIST_MARKER = "# Lantern Watch — Allowed Domains"


def get_allowlisted_domains(config):
    """Return list of manually allowed domains from AGH custom rules."""
    rules = get_custom_rules(config)
    domains, in_section = [], False
    for rule in rules:
        if rule.strip() == _ALLOWLIST_MARKER:
            in_section = True
            continue
        if in_section:
            if rule.startswith("@@||") and rule.endswith("^"):
                domains.append(rule[4:-1])
            elif rule == "" or rule.startswith("#"):
                continue
            else:
                break
    return domains


def add_allowlist_domain(config, domain):
    domain = domain.strip().lower().lstrip("@@||").rstrip("^")
    if not domain:
        return False
    # Hold the lock across read + write so a concurrent allowlist edit (or a
    # social/DoH write) can't land between get and save. _save_allowlist
    # re-acquires the same RLock on this thread.
    with _rules_lock:
        current = get_allowlisted_domains(config)
        if domain not in current:
            current.append(domain)
        return _save_allowlist(config, current)


def remove_allowlist_domain(config, domain):
    with _rules_lock:
        current = [d for d in get_allowlisted_domains(config) if d != domain.strip().lower()]
        return _save_allowlist(config, current)


# ── Per-client filtering control ──────────────────────────────────────────────

def _ag_get_clients(config):
    """Return the list of manually configured AdGuard clients."""
    try:
        req  = _ag_request(config, "/control/clients")
        data = urllib.request.urlopen(req, timeout=5).read().decode()
        return json.loads(data).get("clients") or []
    except Exception:
        return []


def set_client_unfiltered(config, identifier, label=""):
    """
    Create or update an AdGuard per-client entry that disables all filtering
    for the given identifier (IP address or hostname).
    """
    existing = _ag_get_clients(config)
    matched  = next((c for c in existing if identifier in c.get("ids", [])), None)
    payload_data = {
        "name":                        label or identifier,
        "ids":                         [identifier],
        "use_global_settings":         False,
        "filtering_enabled":           False,
        "parental_enabled":            False,
        "safebrowsing_enabled":        False,
        "safesearch_enabled":          False,
        "use_global_blocked_services": True,
    }
    try:
        if matched:
            body = json.dumps({"name": matched["name"], "data": payload_data}).encode()
            req  = _ag_request(config, "/control/clients/update", body)
        else:
            body = json.dumps(payload_data).encode()
            req  = _ag_request(config, "/control/clients/add", body)
        urllib.request.urlopen(req, timeout=5)
        print(f"[AdGuard] unfiltered client set: {identifier} ({label})")
    except Exception as e:
        print(f"[AdGuard] set_client_unfiltered error for {identifier}: {e}")


def restore_client_global(config, identifier):
    """
    Remove a Lantern Watch-managed AdGuard per-client entry so the device
    returns to global filtering settings.  Only deletes if the entry exists.
    """
    existing = _ag_get_clients(config)
    matched  = next((c for c in existing if identifier in c.get("ids", [])), None)
    if not matched:
        return
    try:
        body = json.dumps({"name": matched["name"]}).encode()
        req  = _ag_request(config, "/control/clients/delete", body)
        urllib.request.urlopen(req, timeout=5)
        print(f"[AdGuard] restored global filtering: removed client entry for {identifier}")
    except Exception as e:
        print(f"[AdGuard] restore_client_global error for {identifier}: {e}")


def _save_allowlist(config, domains):
    with _rules_lock:
        rules = get_custom_rules(config)
        cleaned, in_section = [], False
        for line in rules:
            if line.strip() == _ALLOWLIST_MARKER:
                in_section = True
                continue
            if in_section and (line.startswith("@@||") or line == ""):
                continue
            in_section = False
            cleaned.append(line)
        new_rules = cleaned + (["", _ALLOWLIST_MARKER] + [f"@@||{d}^" for d in domains] if domains else [])
        final, prev_blank = [], False
        for line in new_rules:
            is_blank = line.strip() == ""
            if not (is_blank and prev_blank):
                final.append(line)
            prev_blank = is_blank
        return _ag_post(config, "/filtering/set_rules", {"rules": final})
