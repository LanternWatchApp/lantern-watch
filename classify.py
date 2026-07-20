#!/usr/bin/env python3
"""
Lantern Watch — classify.py
Best-effort automatic device-type guessing.

Returns one of: person, parent, work_device, infrastructure, smart_device.

The guess is ONLY ever used as a default suggestion. The moment a user picks a
type on the Devices page it is stored in config["devices"][name]["type"] and
always wins (see config.effective_type). So a wrong guess is harmless — it just
gives a sensible starting point for a new device instead of defaulting every
gadget to "Personal".

Signals (all local, no network calls):
  - the device hostname (from /tmp/dhcp.leases) and/or its friendly label
  - the MAC address vendor prefix (OUI)

Fallback is "person": for a parental-control product the cautious default is to
treat an unidentified gadget as a kid's device (filtered + pauseable) until a
parent says otherwise.
"""

import time

# ── Keyword signals ───────────────────────────────────────────────────────────
# Matched as substrings against a lowercased "hostname + label" haystack.
# Evaluated infrastructure → smart → person, so put a token in its most specific
# bucket. Ultra-short/ambiguous tokens (e.g. bare "tv", "car") are omitted on
# purpose to avoid matching inside people's names.

_INFRA_KW = (
    "router", "gateway", "modem", "accesspoint", "access-point", "unifi",
    "ubnt", "ubiquiti", "eero", "orbi", "netgear", "openwrt", "glinet",
    "gl-mt", "gl-inet", "nas", "synology", "diskstation", "qnap", "truenas",
    "freenas", "printer", "brother", "brn", "deskjet", "officejet",
    "laserjet", "mfc-", "canon", "epson", "pihole", "pi-hole", "raspberrypi",
    "raspberry", "homeassistant", "hassio", "proxmox", "esxi", "plex",
    "repeater", "extender",
)

_SMART_KW = (
    "roku", "chromecast", "googlecast", "firetv", "fire-tv", "appletv",
    "apple-tv", "shield", "echo", "alexa", "googlehome", "google-home",
    "nest", "ring", "doorbell", "wyze", "blink", "arlo", "eufy", "camera",
    "ipcam", "shelly", "tuya", "smartlife", "sonoff", "espressif", "tasmota",
    "hue", "lifx", "govee", "kasa", "smartplug", "smartbulb", "thermostat",
    "ecobee", "sonos", "soundbar", "vizio", "hisense", "webos", "bravia",
    "smarttv", "vacuum", "roomba", "roborock", "tesla", "rivian", "vehicle",
    "refrigerator", "fridge", "washer", "dryer", "dishwasher", "garage",
    "myq", "smartthings", "wemo", "meross", "lutron", "switchbot",
    "smartlock", "doorlock", "esp_", "esp-",
)

_PERSON_KW = (
    "iphone", "ipad", "ipod", "android", "galaxy", "pixel", "oneplus",
    "redmi", "xiaomi", "huawei", "oppo", "realme", "motorola", "phone",
    "mobile", "laptop", "macbook", "imac", "desktop", "windows", "surface",
    "chromebook", "thinkpad", "latitude", "inspiron", "lenovo", "tablet",
    "kindle", "nintendo", "playstation", "xbox",
)

# ── MAC OUI → device-type hint (high-confidence vendors only) ──────────────────
# First three octets, lowercase, colon-separated. Small curated set; ambiguous
# vendors (Apple, Samsung — could be a phone, watch, or TV) are intentionally
# omitted so the hostname decides. Easy to extend.
_OUI_TYPE = {}

def _add_oui(t, *prefixes):
    for p in prefixes:
        _OUI_TYPE[p] = t

_add_oui("smart_device",
    "44:65:0d", "68:37:e9", "fc:65:de", "50:dc:e7", "0c:47:c9", "74:c2:46",  # Amazon
    "f4:f5:d8", "6c:ad:f8", "1c:f2:9a", "54:60:09", "da:a1:19", "48:d6:d5",  # Google / Nest
    "dc:3a:5e", "b0:a7:37", "cc:6d:a0", "ac:3a:7a", "d0:4d:2c",              # Roku
    "00:0e:58", "5c:aa:fd", "78:28:ca", "94:9f:3e", "b8:e9:37",              # Sonos
    "00:17:88", "ec:b5:fa",                                                  # Philips Hue
    "24:0a:c4", "30:ae:a4", "8c:aa:b5", "a0:20:a6", "7c:9e:bd", "24:6f:28",  # Espressif / IoT
)
_add_oui("infrastructure",
    "24:5a:4c", "78:8a:20", "fc:ec:da", "e0:63:da", "68:d7:9a", "b4:fb:e4",  # Ubiquiti
    "00:11:32", "00:c0:b7",                                                  # Synology
    "00:80:77", "30:05:5c",                                                  # Brother
    "b8:27:eb", "dc:a6:32", "e4:5f:01", "28:cd:c1",                          # Raspberry Pi
)

# ── Lease lookup (cached, IP → {mac, hostname}) ───────────────────────────────
_lease_cache = {"t": 0.0, "by_ip": {}}

def _leases_by_ip():
    now = time.time()
    if now - _lease_cache["t"] > 30:
        by_ip = {}
        try:
            from adguard import get_dhcp_leases
            for lease in get_dhcp_leases():
                if lease.get("ip"):
                    by_ip[lease["ip"]] = {
                        "mac":      lease.get("mac", ""),
                        "hostname": lease.get("hostname", ""),
                    }
        except Exception:
            pass
        _lease_cache["by_ip"] = by_ip
        _lease_cache["t"] = now
    return _lease_cache["by_ip"]


_IP_MATCH = None
def _is_ip(s):
    global _IP_MATCH
    if _IP_MATCH is None:
        import re
        _IP_MATCH = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$").match
    return bool(_IP_MATCH(s or ""))


# ── MAC OUI → maker name (best-effort identity for IP-only devices) ───────────
# Common consumer / IoT / network vendors. Partial coverage; we always show the
# raw MAC too so an unrecognized maker can still be looked up.
_OUI_VENDOR = {}
def _add_vendor(name, *prefixes):
    for p in prefixes:
        _OUI_VENDOR[p] = name

_add_vendor("Apple",            "3c:07:54","a4:83:e7","ac:bc:32","f0:18:98","dc:a9:04","88:66:a5","68:a8:6d","90:b0:ed","f4:f1:5a")
_add_vendor("Samsung",          "8c:77:12","fc:a1:3e","e8:50:8b","5c:0a:5b","34:23:ba","78:1f:db","a0:21:95")
_add_vendor("Amazon",           "44:65:0d","68:37:e9","fc:65:de","50:dc:e7","0c:47:c9","74:c2:46","ac:63:be")
_add_vendor("Google / Nest",    "f4:f5:d8","6c:ad:f8","1c:f2:9a","54:60:09","da:a1:19","48:d6:d5","18:b4:30")
_add_vendor("Roku",             "dc:3a:5e","b0:a7:37","cc:6d:a0","ac:3a:7a","d0:4d:2c")
_add_vendor("Sonos",            "00:0e:58","5c:aa:fd","78:28:ca","94:9f:3e","b8:e9:37")
_add_vendor("Philips Hue",      "00:17:88","ec:b5:fa")
_add_vendor("Espressif (IoT)",  "24:0a:c4","30:ae:a4","8c:aa:b5","a0:20:a6","7c:9e:bd","24:6f:28")
_add_vendor("Ubiquiti",         "24:5a:4c","78:8a:20","fc:ec:da","e0:63:da","68:d7:9a","b4:fb:e4")
_add_vendor("TP-Link",          "50:c7:bf","ac:84:c6","b0:48:7a","1c:61:b4")
_add_vendor("Synology",         "00:11:32","00:c0:b7")
_add_vendor("Raspberry Pi",     "b8:27:eb","dc:a6:32","e4:5f:01","28:cd:c1")
_add_vendor("Brother",          "00:80:77","30:05:5c")
_add_vendor("HP",               "3c:d9:2b","9c:b6:54","ec:8e:b5","70:5a:0f")
_add_vendor("Intel",            "3c:a9:f4","7c:5c:f8","e4:a4:71","88:b1:11")
_add_vendor("Microsoft / Xbox", "00:15:5d","28:18:78","7c:1e:52")
_add_vendor("Nintendo",         "0c:fe:45","58:bd:a3","98:b6:e9","9c:e6:35")
_add_vendor("Sony / PlayStation","fc:0f:e6","78:c8:81","30:f9:ed")
_add_vendor("LG",               "3c:bd:d8","a8:16:b2","c4:36:6c")
_add_vendor("eero",             "f8:bb:bf")
_add_vendor("Wyze",             "2c:aa:8e","7c:78:b2")
_add_vendor("Tuya / Smart Life","10:d5:61","84:e3:42","d8:1f:12")
_add_vendor("Xiaomi",           "64:09:80","78:11:dc","f0:b4:29","0c:1d:af")
_add_vendor("Tesla",            "4c:fc:aa","54:f8:f0","dc:44:27")


# ── Full offline OUI database (downloaded once; lookups are 100% local) ────────
# We fetch the PUBLIC IEEE registry (a generic list — no device data ever leaves
# the network, just like updating a blocklist) into a compact local file, then
# resolve every MAC offline against it. The curated map above is the fast path /
# fallback when the DB hasn't been downloaded yet.
import os
_OUI_DB_PATH  = os.path.join(os.path.dirname(__file__), "oui-db.txt")
_oui_db_cache = {"map": None}

def _oui_db():
    if _oui_db_cache["map"] is None:
        m = {}
        try:
            with open(_OUI_DB_PATH) as f:
                for line in f:
                    pre, _, ven = line.partition("\t")
                    if pre and ven:
                        m[pre.strip()] = ven.strip()
        except Exception:
            pass
        _oui_db_cache["map"] = m
    return _oui_db_cache["map"]

def _lookup_vendor(mac):
    if not mac:
        return ""
    pre = mac.lower()[:8]
    return _OUI_VENDOR.get(pre) or _oui_db().get(pre, "")

def refresh_oui_db(url="https://standards-oui.ieee.org/oui/oui.csv"):
    """Download the public IEEE OUI registry → compact local lookup file.
    Sends no device data (fetches a generic public list). Returns entry count."""
    import urllib.request, csv, io
    try:
        req  = urllib.request.Request(url, headers={"User-Agent": "LanternWatch"})
        data = urllib.request.urlopen(req, timeout=90).read().decode("utf-8", "replace")
    except Exception as e:
        print(f"[OUI] download failed: {e}")
        return 0
    n, tmp = 0, _OUI_DB_PATH + ".tmp"
    try:
        with open(tmp, "w") as out:
            for row in csv.reader(io.StringIO(data)):
                # Registry, Assignment(6 hex e.g. BC5C17), Organization Name, Address
                if len(row) >= 3 and len(row[1]) == 6 and all(c in "0123456789abcdefABCDEF" for c in row[1]):
                    a    = row[1].lower()
                    pre  = f"{a[0:2]}:{a[2:4]}:{a[4:6]}"
                    name = row[2].strip().strip('"')
                    if name:
                        out.write(f"{pre}\t{name}\n")
                        n += 1
        if n:
            os.replace(tmp, _OUI_DB_PATH)
            _oui_db_cache["map"] = None
            print(f"[OUI] database updated: {n} entries")
    except Exception as e:
        print(f"[OUI] parse/write failed: {e}")
        return 0
    return n


# ── Traffic fingerprint → friendly device label ───────────────────────────────
# When a device hides its maker (randomized/private MAC) and has no useful
# hostname, the domains it talks to are often a dead giveaway — a "mystery IP"
# hitting Netflix + YouTube is obviously a TV. Each entry is (label, signatures);
# the first label whose signature appears in any of the device's top domains is
# returned. Ordered most-specific → most-generic so a brand wins over "Smart TV",
# and a TV wins over a bare "streaming" guess. Used only as a suggested label the
# parent reviews and can overwrite, so a loose guess is harmless.
_DOMAIN_LABEL = (
    ("Roku",                    ("roku.com",)),
    ("Fire TV / Echo (Amazon)", ("aiv-delivery", "amazonvideo", "device-metrics", "kindle", "fireoscaptiveportal", "amazon-dss")),
    ("Apple TV / Apple device", ("aaplimg", "mzstatic", "push.apple", "appattest", "tvs.apple")),
    ("Sonos speaker",           ("sonos.com", "sonos-")),
    ("Nest / Google camera",    ("dropcam", "nest.com")),
    ("Ring doorbell",           ("ring.com",)),
    ("Xbox",                    ("xboxlive", "xbox.com")),
    ("PlayStation",             ("playstation.net", "playstation.com", "scea.com")),
    ("Nintendo Switch",         ("nintendo.net", "nintendo.com", "nintendowifi")),
    ("Samsung TV",              ("samsungads", "samsungcloudsolution", "samsungotn", "samsungrm", "samsungtvservices")),
    ("LG TV",                   ("lgtvcommon", "lgtvsdp", "lgsmartad", "lgappstv")),
    ("Vizio TV",                ("vizio",)),
    ("Hisense / Vidaa TV",      ("vidaa", "hisense")),
    ("Chromecast / Google TV",  ("googlecast", "clients3.google")),
    ("Smart TV",                ("html-load.com", "smartclip", "conviva", "tvinteractive")),
    ("Streaming device / TV",   ("netflix", "nflxvideo", "youtube", "googlevideo", "hulu", "disney", "plex.tv", "twitch")),
    # Generic last resort: gvt2/gvt1 are Google connectivity beacons hit by phones,
    # tablets, Chrome and TVs alike — only safe to call "a Google/Android device".
    ("Google / Android device", ("gvt2.com", "gvt1.com")),
)

_ALPHA_RUN = None
def is_cryptic_name(name):
    """True when a device name tells a human nothing — a bare IP, or a code-like
    string (all digits, or alphanumeric like 'AX12B', 'ESP_1A2B') with no real
    word in it. Names like 'Galaxy-S23', 'Pixel-7', 'Office-PC' are NOT cryptic: they
    already identify the device, so the traffic-fingerprint guesser stays out.
    Heuristic: cryptic unless it contains a run of >= 4 letters (a real word)."""
    global _ALPHA_RUN
    n = (name or "").strip()
    if not n or _is_ip(n):
        return True
    if _ALPHA_RUN is None:
        import re
        _ALPHA_RUN = re.compile(r"[a-z]{4,}").search
    base = n.split(".")[0].lower()   # drop a trailing .lan / domain suffix
    return _ALPHA_RUN(base) is None


def label_from_domains(domains):
    """Best-effort friendly label (e.g. 'Chromecast / Google TV') guessed from the
    domains a device talks to. Returns '' when nothing recognizable matches."""
    hay = " ".join(d.lower() for d in (domains or []))
    if not hay:
        return ""
    for label, sigs in _DOMAIN_LABEL:
        if any(s in hay for s in sigs):
            return label
    return ""

# Which traffic-fingerprint labels imply a smart device for *typing* purposes.
# Every TV / streamer / speaker / camera / doorbell / console is a smart_device.
# 'Google / Android device' is deliberately excluded — it's often just a phone.
_LABEL_TYPE = {
    "Roku": "smart_device", "Fire TV / Echo (Amazon)": "smart_device",
    "Apple TV / Apple device": "smart_device", "Sonos speaker": "smart_device",
    "Nest / Google camera": "smart_device", "Ring doorbell": "smart_device",
    "Xbox": "smart_device", "PlayStation": "smart_device",
    "Nintendo Switch": "smart_device", "Samsung TV": "smart_device",
    "LG TV": "smart_device", "Vizio TV": "smart_device",
    "Hisense / Vidaa TV": "smart_device", "Chromecast / Google TV": "smart_device",
    "Smart TV": "smart_device", "Streaming device / TV": "smart_device",
}

def type_from_domains(domains):
    """Device type implied by traffic ('' if none/ambiguous). See _LABEL_TYPE."""
    return _LABEL_TYPE.get(label_from_domains(domains), "")


# ── Friendly "probably a ..." kind guess (display hint only) ───────────────────
# Plain-language category a parent recognizes (phone, smart TV, video doorbell,
# NAS, Wi-Fi router/booster, printer, game console...). Layered: hostname + maker
# keywords first; for a cryptic name, fall back to the traffic fingerprint. This
# only ever produces a hint shown on the page — it never changes behavior.
_KIND_RULES = (
    ("garage-door opener",      ("myq", "liftmaster", "chamberlain")),
    ("video doorbell",          ("doorbell",)),
    ("security camera",         ("camera", "ipcam", "wyze", "blink", "arlo", "eufycam", "dropcam", "nestcam")),
    ("smart speaker",           ("sonos", "homepod", "soundbar", "echo", "alexa", "googlehome", "google-home", "nest-mini", "nest-audio")),
    ("streaming device",        ("roku", "chromecast", "googlecast", "firetv", "fire-tv", "appletv", "apple-tv", "shield")),
    ("smart TV",                ("smarttv", "webos", "bravia", "vizio", "hisense", "funai", "qingdao", "skyworth", "konka", "aquos")),
    ("game console",            ("xbox", "playstation", "nintendo")),
    ("printer",                 ("printer", "brother", "brn", "deskjet", "officejet", "laserjet", "canon", "epson", "mfc-")),
    ("NAS / file server",       ("nas", "synology", "diskstation", "qnap", "truenas", "freenas")),
    ("Wi-Fi router or booster", ("router", "gateway", "repeater", "extender", "booster", "orbi", "eero", "deco", "velop", "mesh", "netgear", "openwrt", "glinet", "gl-mt", "unifi", "ubiquiti")),
    ("car / vehicle",           ("tesla", "rivian", "intellilink", "carplay", "uconnect", "vehicle")),
    ("tablet",                  ("ipad", "tablet", "kindle", "galaxy-tab")),
    ("laptop",                  ("laptop", "macbook", "thinkpad", "latitude", "inspiron", "chromebook", "xps")),
    ("computer",                ("desktop", "imac", "mac-mini", "mac-pro", "workstation", "windows")),
    ("phone",                   ("iphone", "galaxy", "pixel", "oneplus", "redmi", "oppo", "realme", "motorola", "moto-", "huawei", "phone", "mobile")),
    ("smart-home device",       ("shelly", "tuya", "smartlife", "sonoff", "tasmota", "hue", "lifx", "govee", "kasa", "smartplug", "smartbulb", "thermostat", "ecobee", "smartthings", "wemo", "meross", "lutron", "switchbot", "smartlock", "esp_", "esp-", "espressif")),
)

# Traffic-brand label → friendly kind (cryptic devices only).
_LABEL_KIND = {
    "Roku": "streaming device", "Fire TV / Echo (Amazon)": "Fire TV or Echo",
    "Apple TV / Apple device": "Apple TV or device", "Sonos speaker": "smart speaker",
    "Nest / Google camera": "security camera", "Ring doorbell": "video doorbell",
    "Xbox": "game console", "PlayStation": "game console", "Nintendo Switch": "game console",
    "Samsung TV": "smart TV", "LG TV": "smart TV", "Vizio TV": "smart TV",
    "Hisense / Vidaa TV": "smart TV", "Chromecast / Google TV": "streaming device or TV",
    "Smart TV": "smart TV", "Streaming device / TV": "smart TV or streaming device",
    "Google / Android device": "Google or Android device",
}

# Generic traffic patterns for cryptic devices not matched by any brand label.
_DOMAIN_KIND = (
    ("computer", ("_msdcs", "_ldap._tcp", "wpad.", "windowsupdate", "msftconnecttest", "ctldl.windows")),
)

def device_kind(name, label="", ident=None, domains=None):
    """Plain-language guess of what a device is ('phone', 'smart TV', 'video
    doorbell'...) for a friendly 'probably a ...' hint. '' when we can't tell."""
    if ident is None:
        ident = device_identity(name)
    hostname = ident.get("hostname") or name
    vendor   = ident.get("vendor", "")
    hay = f"{hostname} {label} {vendor}".lower()
    for kind, sigs in _KIND_RULES:
        if any(s in hay for s in sigs):
            return kind
    if is_cryptic_name(hostname):
        k = _LABEL_KIND.get(label_from_domains(domains))
        if k:
            return k
        dhay = " ".join(d.lower() for d in (domains or []))
        for kind, sigs in _DOMAIN_KIND:
            if any(s in dhay for s in sigs):
                return kind
    return ""


def _arp_mac(ip):
    """MAC for an IP from the kernel ARP table (active devices whose DHCP lease
    may have expired)."""
    try:
        with open("/proc/net/arp") as f:
            next(f)
            for line in f:
                p = line.split()
                if len(p) >= 4 and p[0] == ip and p[3] != "00:00:00:00:00:00":
                    return p[3].lower()
    except Exception:
        pass
    return ""


def device_identity(name):
    """Best-effort identity for a device key (often a bare IP):
    {'mac', 'vendor', 'hostname'} — helps a person recognize an IP-only device."""
    name = name or ""
    by_ip = _leases_by_ip()
    mac = hostname = ""
    if _is_ip(name):
        lease    = by_ip.get(name, {})
        hostname = lease.get("hostname", "")
        mac      = lease.get("mac", "") or _arp_mac(name)
    else:
        hostname = name
        for _ip, lease in by_ip.items():
            if lease.get("hostname", "").lower() == name.lower():
                mac = lease.get("mac", "")
                break
    return {"mac": mac, "vendor": _lookup_vendor(mac), "hostname": hostname}


def classify_device(name, label="", config=None, domains=None):
    """Classify a device. Returns (type, confident).

    `confident` is False only for the cautious "person" fallback reached when
    nothing about the device was recognizable — callers (e.g. Re-detect) can use
    that to avoid overriding a type the user already set on a weak guess.

    `domains` (a device's top domains) is an optional extra signal: a device a
    parent can't name otherwise is typed from what it talks to (a Netflix/YouTube
    box is a smart device, not a kid). See type_from_domains.
    """
    name = name or ""
    by_ip = _leases_by_ip()
    hostname, mac = "", ""
    if _is_ip(name):
        lease    = by_ip.get(name, {})
        hostname = lease.get("hostname", "")
        mac      = lease.get("mac", "") or _arp_mac(name)
    else:
        hostname = name
        for _ip, lease in by_ip.items():
            if lease.get("hostname", "").lower() == name.lower():
                mac = lease.get("mac", "")
                break

    # Fold the resolved maker name into the haystack so brand keywords (Roku,
    # Sonos, Brother, ...) classify even an IP-only device by its MAC vendor.
    vendor = _lookup_vendor(mac)
    hay = f"{hostname} {label} {vendor}".lower()

    if any(w in hay for w in _INFRA_KW):
        return "infrastructure", True
    if any(w in hay for w in _SMART_KW):
        return "smart_device", True
    if any(w in hay for w in _PERSON_KW):
        return "person", True

    # MAC vendor OUI → type hint (high-confidence curated vendors).
    if mac:
        hint = _OUI_TYPE.get(mac.lower()[:8])
        if hint:
            return hint, True

    # Traffic fingerprint → type, but ONLY for cryptic names. A readable, human
    # hostname (Galaxy-S23, Bedroom-iPad) is a person's device even if it streams
    # Netflix; only an anonymous box gets typed by what it talks to.
    if is_cryptic_name(hostname or name):
        t = type_from_domains(domains)
        if t:
            return t, True

    return "person", False


def guess_device_type(name, label="", config=None, domains=None):
    """Best-effort device-type guess (see classify_device). Always returns one
    of the five type strings; falls back to 'person' when unrecognizable."""
    return classify_device(name, label, config, domains)[0]
