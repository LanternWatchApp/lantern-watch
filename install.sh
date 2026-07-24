#!/bin/sh
# Lantern Watch — hardware-agnostic installer
# Supports: GL-MT6000 (Flint 2), GL-MT3000 (Beryl AX),
#           GL-MT2500 (Brume 2), GL-MT5000 (Brume 3)
#
# Usage:
#   sh install.sh [--profile=home|venue] [--force] [--adguard-pass=PASSWORD]
#                 [--github-pat=TOKEN] [--skip-clone]
#
# --adguard-pass  Optional. If omitted, a random AdGuard service-account password
#                 is generated automatically. You never need to know or type it.
# --github-pat    Optional. Only needed if you fork into a PRIVATE repo — a GitHub
#                 Personal Access Token (classic, repo scope) for the clone step.
# --skip-clone    Skip git clone entirely — use files already in /root/lantern-watch.
#
# One-liner (fresh router):
#   wget -O /tmp/install.sh \
#     https://raw.githubusercontent.com/LanternWatchApp/lantern-watch/main/install.sh
#   sh /tmp/install.sh

set -e

LOGFILE="/root/lanternwatch-install.log"
INSTALL_DIR="/root/lantern-watch"
REPO="https://github.com/LanternWatchApp/lantern-watch.git"
AGH_CONFIG="/etc/AdGuardHome/config.yaml"
TIMESTAMP=$(date '+%Y%m%d_%H%M%S')

# ── Logging ───────────────────────────────────────────────────────────────────

log() {
    MSG="[$(date '+%H:%M:%S')] $*"
    echo "$MSG"
    echo "$MSG" >> "$LOGFILE"
}
die() { log "FATAL: $*"; exit 1; }

trap 'log "INSTALLER FAILED at line $LINENO — check $LOGFILE"' EXIT

# ── Where you're headed ───────────────────────────────────────────────────────
# Printed FIRST so the dashboard address lands near the TOP of the installer
# output. This matters most in the GL.iNet plug-in panel, whose "Installation
# succeeded" dialog shows this log: opkg prints its own dependency downloads
# above us and we can't get ahead of those, so the earlier we say this, the less
# anyone has to scroll to find where to go next. Uses the router's REAL LAN IP —
# never a hardcoded 192.168.8.1, which is wrong on any non-default subnet.
_LAN_IP_EARLY=$(uci get network.lan.ipaddr 2>/dev/null || echo "192.168.8.1")
echo ""
echo "======================================================================"
echo "  Lantern Watch — let's make your network brighter."
echo ""
echo "  When this finishes, open your dashboard to finish setup:"
echo "        http://$_LAN_IP_EARLY:8081"
echo "        (or http://lanternwatch.lan:8081)"
echo ""
echo "  Installing now — about 2 minutes on a fresh router."
echo "======================================================================"
echo ""

# ── Argument parsing ──────────────────────────────────────────────────────────

PROFILE="home"
FORCE="no"
SKIP_CLONE="no"
ADGUARD_PASS=""
GITHUB_PAT=""

for arg in "$@"; do
    case "$arg" in
        --profile=*)      PROFILE="${arg#--profile=}" ;;
        --force)          FORCE="yes" ;;
        --force-full)     FORCE_PROT="full" ;;   # override RAM-based protection profile
        --force-lite)     FORCE_PROT="lite" ;;
        --skip-clone)     SKIP_CLONE="yes" ;;
        --adguard-pass=*) ADGUARD_PASS="${arg#--adguard-pass=}" ;;
        --github-pat=*)   GITHUB_PAT="${arg#--github-pat=}" ;;
    esac
done

# Build authenticated repo URL if a PAT was supplied
if [ -n "$GITHUB_PAT" ]; then
    REPO="https://${GITHUB_PAT}@github.com/LanternWatchApp/lantern-watch.git"
fi

case "$PROFILE" in
    home|venue) ;;
    *) die "Unknown profile '$PROFILE'. Use: home or venue" ;;
esac

# ── Banner ────────────────────────────────────────────────────────────────────

echo ""
echo "============================================="
echo "  Lantern Watch — Installer"
echo "  $(date)"
echo "============================================="
echo ""
log "Profile: $PROFILE  Force: $FORCE"

# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 — MODEL DETECTION
# ══════════════════════════════════════════════════════════════════════════════

log "[1/9] Detecting hardware..."

RAW_MODEL=""
if [ -f /tmp/sysinfo/model ]; then
    RAW_MODEL=$(cat /tmp/sysinfo/model)
else
    RAW_MODEL=$(ubus call system board 2>/dev/null | grep '"model"' | sed 's/.*"model": "\(.*\)".*/\1/' || true)
fi

# Never abort on a missing model string — fall back to auto-detected defaults so
# any GL.iNet router installs cleanly.
[ -z "$RAW_MODEL" ] && RAW_MODEL="unknown GL.iNet" && log "Model string unreadable — using auto-detected defaults."

log "Raw model string: $RAW_MODEL"

# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 — PER-MODEL VARIABLE SETUP
# All model-specific decisions are made here.
# The rest of the script reads only these variables — no model checks below.
# ══════════════════════════════════════════════════════════════════════════════

MODEL_NAME="unknown"
HAS_WIFI="no"       # does this unit have WiFi radios?
DISABLE_WIFI="no"   # should install.sh disable WiFi (pass-through mode)?
LOW_RAM="no"        # < 600 MB — scale down caches
LARGE_STORAGE="no"  # >= 4 GB overlay — allow extended retention
LAN_IFACE="br-lan"  # LAN bridge/interface for iptables/firewall rules
WAN_IFACE="eth1"    # WAN interface (informational; used for firewall checks)

# NOTE on GL-MT5000 (Brume 3): Its three-port layout and exact interface names
# cannot be verified from this Flint 2. Values below use the standard GL.iNet
# convention (WAN=eth0, LAN1+LAN2 bridged as br-lan). Verify with
# `uci show network` on a live Brume 3 before deploying there. The Brume 3 runs
# OpenWrt 21.02 with kernel 5.4 — same UCI paths as the Flint 2 (also 21.02).

case "$RAW_MODEL" in
    *MT6000*|*"Flint 2"*)
        MODEL_NAME="GL-MT6000"
        HAS_WIFI="yes"
        DISABLE_WIFI="yes"   # deployed in wired pass-through mode
        LAN_IFACE="br-lan"
        WAN_IFACE="eth1"
        ;;
    *MT3000*|*"Beryl AX"*)
        MODEL_NAME="GL-MT3000"
        HAS_WIFI="yes"
        DISABLE_WIFI="no"    # WiFi router — leave WiFi enabled
        LAN_IFACE="br-lan"
        WAN_IFACE="eth0"     # confirmed via `uci show network.wan` on live hardware
        LOW_RAM="yes"        # 512 MB — confirmed low RAM
        ;;
    *MT2500*|*"Brume 2"*)
        MODEL_NAME="GL-MT2500"
        HAS_WIFI="no"
        DISABLE_WIFI="no"
        LAN_IFACE="br-lan"
        WAN_IFACE="eth1"
        ;;
    *MT5000*|*"Brume 3"*)
        MODEL_NAME="GL-MT5000"
        HAS_WIFI="no"
        DISABLE_WIFI="no"
        LAN_IFACE="br-lan"   # UNVERIFIED — see note above
        WAN_IFACE="eth0"     # UNVERIFIED — Brume 3 has 3×2.5G; eth0 is typically WAN
        LARGE_STORAGE="yes"  # 8 GB eMMC
        ;;
    *)
        # Any other GL.iNet router. Lantern Watch supports the whole lineup, so we
        # NEVER hard-fail on a model we haven't individually tuned — we fall back to
        # safe, auto-detected defaults (RAM/storage are detected dynamically below,
        # and the interfaces are read from UCI). Model-specific tuning above is an
        # optimization, not a requirement. --force is no longer needed for this.
        log "Model '$RAW_MODEL' not individually tuned — using auto-detected GL.iNet defaults."
        MODEL_NAME="$RAW_MODEL"
        HAS_WIFI="no"          # don't force-toggle WiFi on an unknown unit
        DISABLE_WIFI="no"
        LAN_IFACE="$(uci get network.lan.device 2>/dev/null || uci get network.lan.ifname 2>/dev/null || echo br-lan)"
        WAN_IFACE="$(uci get network.wan.device 2>/dev/null || uci get network.wan.ifname 2>/dev/null || echo eth1)"
        log "Auto-detected interfaces: LAN=$LAN_IFACE WAN=$WAN_IFACE"
        ;;
esac

# Detect RAM and storage dynamically — override LOW_RAM/LARGE_STORAGE if needed
MEM_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
MEM_MB=$((MEM_KB / 1024))
OVERLAY_KB=$(df /overlay 2>/dev/null | awk 'NR==2{print $4}' || df / | awk 'NR==2{print $4}')
OVERLAY_MB=$((OVERLAY_KB / 1024))

# < 600 MB → scale down AdGuard cache/log.
[ "$MEM_MB" -lt 600 ] && LOW_RAM="yes"
[ "$OVERLAY_MB" -ge 4096 ] && LARGE_STORAGE="yes"

# Protection profile from RAM: < 600 MB → LITE (tiny footprint + filtering
# upstream, for 512 MB routers like the Beryl 7), >= 600 MB → FULL (heavy local
# blocklists, for 1 GB+ like the Brume 3). --force-full / --force-lite override.
# A forced choice persists across updates: feed/self-update runs never pass the
# flag, so without this an update would silently revert to the RAM-derived profile.
if [ -z "$FORCE_PROT" ] && [ -f "$INSTALL_DIR/lanternwatch_config.json" ]; then
    FORCE_PROT=$(python3 -c "import json;print(json.load(open('$INSTALL_DIR/lanternwatch_config.json')).get('forced_profile') or '')" 2>/dev/null || echo "")
fi
if [ -n "$FORCE_PROT" ]; then
    PROT_PROFILE="$FORCE_PROT"
    # Guard the footgun: FULL on a small router loads 300K+ rules and AdGuard
    # gets OOM-killed (confirmed on a 481 MB Beryl 7) — DNS then drops.
    if [ "$PROT_PROFILE" = "full" ] && [ "$MEM_MB" -lt 600 ]; then
        log "WARNING: --force-full on a ${MEM_MB}MB router. FULL loads 300K+ filter rules;"
        log "         on a sub-600MB device AdGuard is very likely to be OOM-killed and DNS"
        log "         will drop. LITE is strongly recommended here. Proceeding as forced."
    fi
elif [ "$MEM_MB" -lt 600 ]; then
    PROT_PROFILE="lite"
else
    PROT_PROFILE="full"
fi
log "Protection profile: $PROT_PROFILE (RAM ${MEM_MB}MB${FORCE_PROT:+, forced})"

log "Model: $MODEL_NAME | RAM: ${MEM_MB}MB | Free overlay: ${OVERLAY_MB}MB"
log "HAS_WIFI=$HAS_WIFI DISABLE_WIFI=$DISABLE_WIFI LOW_RAM=$LOW_RAM LARGE_STORAGE=$LARGE_STORAGE"

# ── Profile validation ────────────────────────────────────────────────────────

if [ "$PROFILE" = "venue" ] && [ "$LOW_RAM" = "yes" ]; then
    log "WARNING: --profile venue requested on a low-RAM device (${MEM_MB}MB)."
    log "         Venue settings would exceed safe headroom. Falling back to 'home'."
    PROFILE="home"
fi

# ── Profile-scaled settings ───────────────────────────────────────────────────
# AdGuard cache_size in bytes; AGH retention in hours; dnsmasq cache entries.
# Low-RAM home defaults are conservative; venue on 1GB+ is scaled up.

if [ "$LOW_RAM" = "yes" ]; then
    # GL-MT3000: 512 MB RAM, limited flash — keep small
    AGH_CACHE_BYTES=2097152   # 2 MB
    AGH_RETENTION_H=168       # 7 days
    DNSMASQ_CACHE=1000
else
    case "$PROFILE" in
        home)
            AGH_CACHE_BYTES=4194304   # 4 MB (current deployed value)
            AGH_RETENTION_H=336       # 14 days
            DNSMASQ_CACHE=2000
            ;;
        venue)
            if [ "$LARGE_STORAGE" = "yes" ]; then
                AGH_CACHE_BYTES=8388608   # 8 MB
                AGH_RETENTION_H=720       # 30 days
                DNSMASQ_CACHE=8192
            else
                AGH_CACHE_BYTES=6291456   # 6 MB
                AGH_RETENTION_H=504       # 21 days
                DNSMASQ_CACHE=4096
            fi
            ;;
    esac
fi

log "Profile settings — AGH cache: $((AGH_CACHE_BYTES/1024/1024))MB | Retention: ${AGH_RETENTION_H}h | dnsmasq cache: $DNSMASQ_CACHE"

# ══════════════════════════════════════════════════════════════════════════════
# STEP 3 — PRE-FLIGHT CHECKS
# ══════════════════════════════════════════════════════════════════════════════

log "[2/9] Pre-flight checks..."

# Python 3
if ! command -v python3 >/dev/null 2>&1; then
    log "Python 3 not found. Installing..."
    opkg update >> "$LOGFILE" 2>&1
    opkg install python3 >> "$LOGFILE" 2>&1 || die "Failed to install python3"
fi
log "Python: $(python3 --version 2>&1)"

# git — only needed to CLONE the repo (the SSH/Option-B method). When the app
# files are already in place (--skip-clone, e.g. the .ipk package) git is never
# used, so don't require it. This stops a .ipk install from aborting on a router
# where the git package can't be fetched.
if [ "$SKIP_CLONE" != "yes" ]; then
    if ! command -v git >/dev/null 2>&1; then
        log "git not found. Installing..."
        opkg update >> "$LOGFILE" 2>&1 || true
        opkg install git git-http ca-bundle >> "$LOGFILE" 2>&1 || die "Failed to install git"
    fi
    log "git: $(git --version 2>&1)"
else
    log "git: not required (--skip-clone)"
fi

# AdGuard Home config must exist — the GL.iNet admin panel creates it
if [ ! -f "$AGH_CONFIG" ]; then
    die "AdGuard Home config not found at $AGH_CONFIG.
     Please enable AdGuard Home in the GL.iNet admin panel first,
     then re-run this installer."
fi
log "AdGuard config: $AGH_CONFIG (OK)"

# Detect LAN IP for AdGuard URL (used in config stamping below)
LAN_IP=$(uci get network.lan.ipaddr 2>/dev/null || echo "192.168.8.1")
log "LAN IP: $LAN_IP"

# ── AdGuard native user ───────────────────────────────────────────────────────
# GL.iNet's auth middleware blocks all AdGuard API requests unless AdGuard has
# a native user in its users: section. On a fresh GL.iNet install users: is
# empty ([]) and GL.iNet admin panel credentials do NOT populate it.
# Add a lanternwatch user with a bcrypt password so the collector can auth.

# Always (re)write the lanternwatch user so AdGuard's config.yaml and the
# dashboard's config.json share the same password. On a reinstall this RE-SYNCS
# a stale hash instead of skipping — leaving a mismatch was what caused every
# AdGuard API call to 403 after a repair/reinstall.
if [ -z "$ADGUARD_PASS" ]; then
    ADGUARD_PASS=$(python3 -c "import secrets,string; print(''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(24)))")
    log "  AdGuard: no --adguard-pass supplied — generated service account password."
fi
export _AGH_PASS="$ADGUARD_PASS"
python3 - <<'PYEOF'
import crypt, os, re, sys
conf_path = "/etc/AdGuardHome/config.yaml"
pw = os.environ.get("_AGH_PASS", "")
h  = crypt.crypt(pw, crypt.mksalt(crypt.METHOD_BLOWFISH))
with open(conf_path) as f:
    conf = f.read()

if re.search(r'^\s*- name: lanternwatch', conf, re.MULTILINE):
    # Existing user — replace just its password line (re-sync).
    new_conf = re.sub(r'(^\s*- name: lanternwatch\s*\n\s*password: )\S+',
                      lambda m: m.group(1) + h, conf, count=1, flags=re.MULTILINE)
    action = "re-synced existing lanternwatch user password"
elif re.search(r'^users: \[\]\s*$', conf, re.MULTILINE):
    # Fresh GL.iNet config — replace empty "users: []".
    new_conf = re.sub(r'^users: \[\]\s*$',
                      lambda m: "users:\n  - name: lanternwatch\n    password: " + h,
                      conf, count=1, flags=re.MULTILINE)
    action = "added lanternwatch user"
elif re.search(r'^users:\s*$', conf, re.MULTILINE):
    # Other users already present — insert ours under the existing users: key.
    new_conf = re.sub(r'^(users:\s*\n)',
                      lambda m: m.group(1) + "  - name: lanternwatch\n    password: " + h + "\n",
                      conf, count=1, flags=re.MULTILINE)
    action = "inserted lanternwatch user"
else:
    print("WARNING: could not locate a users: section in AdGuard config — check format", file=sys.stderr)
    sys.exit(1)

if new_conf == conf:
    print("WARNING: AdGuard user block unchanged — check config format", file=sys.stderr)
    sys.exit(1)
with open(conf_path, "w") as f:
    f.write(new_conf)
print("  AdGuard: " + action)
PYEOF
log "  AdGuard: restarting to apply user credentials..."
/etc/init.d/adguardhome restart >> "$LOGFILE" 2>&1
sleep 6
log "  AdGuard: restarted."

# ── Remove --glinet flag from AdGuard init script ─────────────────────────────
# GL.iNet firmware ≥ 4.x starts AdGuard with --glinet, which enforces GL.iNet
# session-cookie auth and breaks the HTTP Basic Auth used by the LW collector.
# Removing the flag restores standard AdGuard auth with no other side effects.
AGH_INITD="/etc/init.d/adguardhome"
if grep -q 'AdGuardHome --glinet' "$AGH_INITD" 2>/dev/null; then
    cp "$AGH_INITD" "${AGH_INITD}.bak"
    sed -i 's/AdGuardHome --glinet /AdGuardHome /' "$AGH_INITD"
    /etc/init.d/adguardhome restart >> "$LOGFILE" 2>&1
    sleep 6
    log "  AdGuard: removed --glinet flag (cookie-only auth), restarted with standard auth."
fi

# Confirm dnsmasq is forwarding to AdGuard on port 3053
if ! uci show dhcp 2>/dev/null | grep -q "3053"; then
    log "WARNING: dnsmasq does not appear to be forwarding to AdGuard (port 3053)."
    log "         This will be corrected in the dnsmasq configuration step."
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 4 — DISABLE GL.iNET AUTO-UPGRADE
# GL.iNet has wiped this router before via OTA firmware update. Disable that.
# Setting method='stable' removes it from the gray/beta update channel.
# Setting prompt='1' (and gray_prompt='1') requires manual confirmation in the
# GL.iNet admin panel before any firmware upgrade is applied — preventing
# silent overnight wipes that destroy the Lantern Watch installation.
# ══════════════════════════════════════════════════════════════════════════════

log "[3/9] Disabling GL.iNet auto-upgrade..."

uci set upgrade.general.prompt='1'
uci set upgrade.general.gray_prompt='1'
uci set upgrade.general.method='stable'
uci commit upgrade
log "Auto-upgrade: prompt required, channel set to stable."

# ══════════════════════════════════════════════════════════════════════════════
# STEP 5 — BACKUP EXISTING CONFIG
# ══════════════════════════════════════════════════════════════════════════════

log "[4/9] Backing up existing config..."

CONFIG="$INSTALL_DIR/lanternwatch_config.json"
BAK_DIR="/etc/lanternwatch.bak.$TIMESTAMP"

if [ -f "$CONFIG" ]; then
    mkdir -p "$BAK_DIR"
    cp "$CONFIG" "$BAK_DIR/lanternwatch_config.json"
    log "Existing config backed up to $BAK_DIR"
elif [ -f "/etc/lanternwatch_config.json" ]; then
    # Recovery: config survived in /etc even if /root/lantern-watch was wiped
    mkdir -p "$BAK_DIR"
    cp "/etc/lanternwatch_config.json" "$BAK_DIR/lanternwatch_config.json"
    log "Found config in /etc — backed up to $BAK_DIR"
fi

# Keep only the 3 most recent backups
ls -dt /etc/lanternwatch.bak.* 2>/dev/null | tail -n +4 | xargs rm -rf 2>/dev/null || true

# ══════════════════════════════════════════════════════════════════════════════
# STEP 6 — CLONE OR PULL REPO
# ══════════════════════════════════════════════════════════════════════════════

log "[5/9] Downloading Lantern Watch..."

if [ "$SKIP_CLONE" = "yes" ]; then
    [ -d "$INSTALL_DIR" ] || die "INSTALL_DIR $INSTALL_DIR not found and --skip-clone was set. Deploy app files first."
    log "  --skip-clone: using existing files at $INSTALL_DIR"
elif [ -d "$INSTALL_DIR/.git" ]; then
    log "Existing git repo found — pulling latest..."
    cd "$INSTALL_DIR"
    GIT_TERMINAL_PROMPT=0 git -c credential.helper= pull origin main >> "$LOGFILE" 2>&1
    log "Repository updated."
elif [ -d "$INSTALL_DIR" ]; then
    # Directory exists but not a git repo (was deployed via SCP/deploy.ps1).
    # Move it aside — config was already backed up in the previous step.
    log "Non-git install found at $INSTALL_DIR — moving aside before fresh clone..."
    mv "$INSTALL_DIR" "${INSTALL_DIR}.pre-install.$TIMESTAMP"
    GIT_TERMINAL_PROMPT=0 git -c credential.helper= clone "$REPO" "$INSTALL_DIR" >> "$LOGFILE" 2>&1
    log "Repository cloned fresh."
else
    log "Fresh clone..."
    GIT_TERMINAL_PROMPT=0 git -c credential.helper= clone "$REPO" "$INSTALL_DIR" >> "$LOGFILE" 2>&1
    log "Repository cloned."
fi
cd "$INSTALL_DIR"

# ══════════════════════════════════════════════════════════════════════════════
# STEP 7 — CONFIG SETUP
# Preserve existing config if present; otherwise create from example template.
# Write hardware profile into config so dashboard + scheduler can read it.
# On a fresh install a unique per-install dashboard password is generated here
# and written to the config. It is printed at the end of this script.
# ══════════════════════════════════════════════════════════════════════════════

log "[6/9] Configuring..."

TEMP_PASS=""

if [ -f "$BAK_DIR/lanternwatch_config.json" ] && [ ! -f "$CONFIG" ]; then
    # Firmware-wipe recovery: restore backed-up config
    cp "$BAK_DIR/lanternwatch_config.json" "$CONFIG"
    log "Config restored from backup."
elif [ ! -f "$CONFIG" ]; then
    cp "$INSTALL_DIR/lanternwatch_config.example.json" "$CONFIG"
    TEMP_PASS=$(python3 -c "import secrets,string; print(''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(12)))")
    log "Config created from example template."
else
    log "Existing config preserved."
fi

# Stamp hardware model, profile, AdGuard connection details, and (on fresh
# installs) the generated per-install dashboard password into config.
export _AGH_PASS="$ADGUARD_PASS"
export _TEMP_PASS="$TEMP_PASS"
python3 - <<PYEOF
import json, os, sys
try:
    with open("$CONFIG") as f:
        c = json.load(f)
    c["hw_model"]   = "$MODEL_NAME"
    c["hw_profile"] = "$PROFILE"
    c["hw_low_ram"] = $([ "$LOW_RAM" = "yes" ] && echo "True" || echo "False")
    c["protection_profile"] = "$PROT_PROFILE"   # 'lite' (<600MB) or 'full' — picks list set + upstream
    fp = "$FORCE_PROT"
    if fp:
        c["forced_profile"] = fp   # persist a --force-lite/--force-full choice across updates
    ag = c.get("adguard", {})
    existing_url = ag.get("url", "")
    # Talk to AdGuard over loopback — always reachable, and immune to LAN-IP
    # changes (e.g. repeater mode shifting the subnet). Normalize the auto-set
    # values (empty / loopback / this router's LAN IP); leave a custom URL alone.
    if not existing_url or "127.0.0.1" in existing_url or "$LAN_IP" in existing_url:
        ag["url"] = "http://127.0.0.1:3000"
    if not ag.get("username"):
        ag["username"] = "lanternwatch"
    pw = os.environ.get("_AGH_PASS", "")
    if pw:
        ag["password"] = pw
    c["adguard"] = ag
    temp_pass = os.environ.get("_TEMP_PASS", "")
    if temp_pass:
        c["lw_password"] = temp_pass
    with open("$CONFIG", "w") as f:
        json.dump(c, f, indent=2)
    print("  hw_model=$MODEL_NAME hw_profile=$PROFILE written to config")
    print("  adguard.url=" + ag.get("url", "") + " adguard.username=" + ag.get("username", ""))
except Exception as e:
    print("WARNING: Could not update config: " + str(e), file=sys.stderr)
PYEOF

# ── AdGuard Home retention + cache ────────────────────────────────────────────
# Applied later, in STEP 10b — AFTER the family-protection API writes. Doing it
# here gets silently clobbered: the API filter-list calls make AdGuard rewrite
# config.yaml from its in-memory (default) state, wiping any retention edit made
# beforehand. See the retention awk pass just before the AdGuard restart below.

# ── dnsmasq: DNS forwarding + cache + confdir ─────────────────────────────────
# Forward to AdGuard on 3053 and set the cache size. (confdir is still set below
# for generic dnsmasq drop-ins, but social blocking NO LONGER uses dnsmasq — it
# is done with AdGuard custom filter rules via the /filtering/set_rules API; see
# adguard.py. There is no lanternwatch-social.conf.)

log "  Verifying AdGuard Home is listening on port 3053..."

agh_listening() {
    netstat -tlnp 2>/dev/null | grep -q ':3053 ' || ss -tlnp 2>/dev/null | grep -q ':3053 '
}

# Auto-enable AdGuard Home so the user doesn't have to flip the panel toggle.
# Only acts when it's off; if it's already running this is a no-op. Falls back
# to the manual instructions below if it can't be brought up.
if ! agh_listening; then
    if uci -q get adguardhome.config > /dev/null 2>&1; then
        log "  AdGuard Home is off — enabling it automatically..."
        uci set adguardhome.config.enabled='1'
        uci commit adguardhome
        /etc/init.d/adguardhome enable  >> "$LOGFILE" 2>&1 || true
        /etc/init.d/adguardhome restart >> "$LOGFILE" 2>&1 \
            || /etc/init.d/adguardhome start >> "$LOGFILE" 2>&1 || true
        # Wait up to ~40s for AdGuard Home to bind port 3053.
        i=0
        while [ "$i" -lt 40 ]; do
            agh_listening && break
            sleep 1
            i=$((i + 1))
        done
    fi
fi

if ! agh_listening; then
    die "AdGuard Home could not be started on port 3053.
     Enable it manually in the GL.iNet admin panel:
       Applications -> AdGuard Home -> toggle ON
     Then re-run this installer."
fi
log "  AdGuard Home: port 3053 OK."

log "  Configuring dnsmasq..."

uci set dhcp.@dnsmasq[0].noresolv='1'
uci -q delete dhcp.@dnsmasq[0].server 2>/dev/null || true
uci add_list dhcp.@dnsmasq[0].server='127.0.0.1#3053'
uci set dhcp.@dnsmasq[0].confdir='/tmp/dnsmasq.d'
uci set dhcp.@dnsmasq[0].cachesize="$DNSMASQ_CACHE"
# Pass original client IPs to AdGuard via EDNS Client Subnet so individual
# devices appear in AdGuard logs and the Lantern Watch device list.
uci set dhcp.@dnsmasq[0].addsubnet='32,128'
uci commit dhcp

# Ensure the dnsmasq confdir exists (tmpfs; recreated on boot). Used only for
# generic dnsmasq drop-ins — Lantern Watch writes nothing here anymore.
mkdir -p /tmp/dnsmasq.d

/etc/init.d/dnsmasq restart >> "$LOGFILE" 2>&1
log "  dnsmasq restarted with cache=$DNSMASQ_CACHE entries."

# Enable GL.iNet's iptables REDIRECT so DNS queries reach AdGuard with real
# device IPs instead of 127.0.0.1. This sets dns_enabled=1 which makes
# firewall.dns_order add: -A adg_redirect -p udp --dst-type LOCAL -j REDIRECT --to-ports 3053
if uci -q get adguardhome.config > /dev/null 2>&1; then
    uci set adguardhome.config.dns_enabled='1'
    uci commit adguardhome
    /etc/firewall.dns_order >> "$LOGFILE" 2>&1 || true
    log "  GL.iNet DNS redirect enabled (adguardhome.config.dns_enabled=1)."
else
    log "  WARNING: adguardhome UCI not found — DNS redirect not configured."
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 8 — WIFI DISABLE (model-specific)
# Only runs when DISABLE_WIFI=yes (currently: GL-MT6000 in pass-through mode).
# Skipped entirely on models with no WiFi radios (MT2500, MT5000) and on the
# MT3000 where WiFi is the primary access method.
# ══════════════════════════════════════════════════════════════════════════════

if [ "$DISABLE_WIFI" = "yes" ]; then
    log "[7a/9] Disabling WiFi radios (pass-through mode)..."
    # Iterate over all wifi-iface sections and disable each one
    IDX=0
    while uci get "wireless.@wifi-iface[$IDX]" >/dev/null 2>&1; do
        uci set "wireless.@wifi-iface[$IDX].disabled=1"
        IDX=$((IDX+1))
    done
    if [ "$IDX" -gt 0 ]; then
        uci commit wireless
        wifi down 2>/dev/null || true
        log "  Disabled $IDX WiFi interface(s)."
    else
        log "  No wifi-iface sections found in UCI (WiFi may already be off)."
    fi
elif [ "$HAS_WIFI" = "no" ]; then
    log "[7a/9] WiFi: not present on $MODEL_NAME — skipping."
else
    log "[7a/9] WiFi: enabled (home AP mode on $MODEL_NAME)."
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 9 — INIT.D SERVICE
# ══════════════════════════════════════════════════════════════════════════════

log "[7b/9] Installing auto-start service..."

if [ -f "$INSTALL_DIR/lanternwatch.initd" ]; then
    cp "$INSTALL_DIR/lanternwatch.initd" /etc/init.d/lanternwatch
elif [ ! -f /etc/init.d/lanternwatch ]; then
    die "lanternwatch.initd not found in $INSTALL_DIR and /etc/init.d/lanternwatch does not exist."
else
    log "  lanternwatch.initd not in repo dir — keeping existing /etc/init.d/lanternwatch"
fi
chmod +x /etc/init.d/lanternwatch
/etc/init.d/lanternwatch enable
log "  Service installed and enabled."

# ══════════════════════════════════════════════════════════════════════════════
# STEP 10 — START SERVICES + VERIFY
# ══════════════════════════════════════════════════════════════════════════════

log "[8/9] Starting Lantern Watch..."

# Stop any stale processes from a previous install
/etc/init.d/lanternwatch stop >> "$LOGFILE" 2>&1 || true
sleep 2

/etc/init.d/lanternwatch start >> "$LOGFILE" 2>&1
sleep 5

# Verify dashboard responds
DASHBOARD_UP="no"
ATTEMPT=0
while [ "$ATTEMPT" -lt 6 ]; do
    if python3 -c "
import socket, sys
try:
    s = socket.create_connection(('127.0.0.1', 8081), timeout=3)
    s.close()
    sys.exit(0)
except Exception:
    sys.exit(1)
" 2>/dev/null; then
        DASHBOARD_UP="yes"
        break
    fi
    ATTEMPT=$((ATTEMPT+1))
    sleep 3
done

# ── STEP 10b — APPLY ADGUARD FAMILY PROTECTION ────────────────────────────────
log "  Applying AdGuard family protection (filter lists + safebrowsing + safe search)..."
AGH_SETUP_RESULT=$(python3 - <<PYEOF
import json, sys
sys.path.insert(0, "$INSTALL_DIR")
try:
    from adguard import (apply_adguard_setup, recommended_ids, enforce_profile_filters,
                         install_default_optional_lists, apply_doh_dns_mitigation,
                         apply_service_allowlist, protection_profile, heuristic_toggles)
    with open("$CONFIG") as f:
        config = json.load(f)
    print("  profile=" + protection_profile(config), file=sys.stderr)
    # LITE first: disable GL.iNet's heavy 158K default list BEFORE adding anything,
    # so setup runs against a lean AdGuard and can't OOM a 512 MB router mid-setup.
    try:
        enforce_profile_filters(config)
    except Exception as e:
        print("  profile filter enforce skipped: " + str(e), file=sys.stderr)
    list_ids = recommended_ids(config)   # tiny family set on LITE, full set on FULL
    # LITE turns off Safe Browsing/Parental (redundant w/ upstream + can hang a
    # small router); Safe Search stays on. FULL keeps all three.
    added, errors = apply_adguard_setup(config, list_ids, **heuristic_toggles(config))
    # Profile-appropriate optional lists + always-on gentle DoH mitigation +
    # service allowlist.
    try:
        install_default_optional_lists(config)
        apply_doh_dns_mitigation(config)
        apply_service_allowlist(config)
    except Exception as e:
        print("  optional/DoH defaults skipped: " + str(e), file=sys.stderr)
    if errors:
        for e in errors:
            print("  WARNING: " + str(e), file=sys.stderr)
    print("added=" + str(added) + " errors=" + str(len(errors)))
except Exception as e:
    print("  AdGuard setup skipped: " + str(e), file=sys.stderr)
    print("added=0 errors=1")
PYEOF
)
log "  AdGuard family protection: $AGH_SETUP_RESULT"

# ── Dashboard DNS rewrites ────────────────────────────────────────────────────
# Register each config["local_hostnames"] entry as an AdGuard DNS rewrite -> the
# router's LAN IP, so users can reach the dashboard at http://<name>:8081 instead
# of the bare IP. Done here (an API call) BEFORE the retention YAML edit below so
# AdGuard's config rewrite can't clobber the retention values.
log "  Registering dashboard DNS rewrites..."
DNS_REWRITE_RESULT=$(python3 - <<PYEOF
import json, sys, urllib.request
sys.path.insert(0, "$INSTALL_DIR")
try:
    from adguard import _ag_request
    with open("$CONFIG") as f:
        config = json.load(f)
    names = config.get("local_hostnames", [])
    for h in names:
        payload = json.dumps({"domain": h, "answer": "$LAN_IP"}).encode()
        try:
            urllib.request.urlopen(_ag_request(config, "/control/rewrite/add", payload), timeout=10)
        except Exception as e:
            print("  WARNING: rewrite '" + h + "' failed: " + str(e), file=sys.stderr)
    print("rewrites=" + (",".join(names) if names else "none"))
except Exception as e:
    print("  DNS rewrite setup skipped: " + str(e), file=sys.stderr)
    print("rewrites=error")
PYEOF
)
log "  Dashboard DNS rewrites: $DNS_REWRITE_RESULT"

# ── Encrypted upstream DNS (DoH) ──────────────────────────────────────────────
# Switch AdGuard's upstreams from plain (ISP can see every lookup) to encrypted
# Cloudflare + Quad9 over DoH — private and fast. Done via the API (like the
# rewrites above) BEFORE the retention YAML edit. Tested first: if the DoH
# endpoints aren't reachable (no internet yet / DoH blocked), keep the working
# plain defaults so an install never breaks DNS.
log "  Setting encrypted upstream DNS (DoH)..."
UPSTREAM_RESULT=$(python3 - <<PYEOF
import json, sys, urllib.request
sys.path.insert(0, "$INSTALL_DIR")
try:
    from adguard import _ag_request, UPSTREAM_DNS, UPSTREAM_BOOTSTRAP
    with open("$CONFIG") as f:
        config = json.load(f)
    # Cloudflare for Families over DoH — filters adult + malware server-side, so
    # it costs ~zero local RAM (the same lean default runs on every router).
    NEW_UP = UPSTREAM_DNS
    BOOT   = UPSTREAM_BOOTSTRAP
    tp  = json.dumps({"upstream_dns": NEW_UP, "bootstrap_dns": BOOT, "fallback_dns": []}).encode()
    res = json.loads(urllib.request.urlopen(_ag_request(config, "/control/test_upstream_dns", tp), timeout=25).read().decode())
    if res and all(str(v).upper().startswith("OK") for v in res.values()):
        info = json.loads(urllib.request.urlopen(_ag_request(config, "/control/dns_info"), timeout=10).read().decode())
        info["upstream_dns"]  = NEW_UP
        info["bootstrap_dns"] = BOOT
        urllib.request.urlopen(_ag_request(config, "/control/dns_config", json.dumps(info).encode()), timeout=15)
        print("upstreams=Cloudflare for Families (filtered DoH)")
    else:
        print("upstreams=kept-default (DoH test failed)")
except Exception as e:
    print("  Encrypted upstream setup skipped: " + str(e), file=sys.stderr)
    print("upstreams=kept-default")
PYEOF
)
log "  Encrypted upstream DNS: $UPSTREAM_RESULT"

# ── AdGuard retention + cache (direct YAML edit) ──────────────────────────────
# Done HERE, AFTER the family-protection API writes above — those calls make
# AdGuard rewrite config.yaml from its in-memory (default) state, so a retention
# edit made earlier would be clobbered. The single restart at the end of this
# block loads these values.
log "  Setting AdGuard retention and cache..."
awk -v section="" \
    -v retention="${AGH_RETENTION_H}h" \
    -v cache_bytes="$AGH_CACHE_BYTES" \
    -v qlog_done=0 -v stats_done=0 -v cache_done=0 '
  /^querylog:/    { section="querylog" }
  /^statistics:/  { section="statistics" }
  /^dns:/         { section="dns" }
  /^[a-z_]/ && !/^querylog:/ && !/^statistics:/ && !/^dns:/ { section="" }
  section=="querylog"   && /^[[:space:]]*interval:/ && qlog_done==0  {
    sub(/interval:.*/, "interval: " retention); qlog_done=1
  }
  section=="statistics" && /^[[:space:]]*interval:/ && stats_done==0 {
    sub(/interval:.*/, "interval: " retention); stats_done=1
  }
  section=="dns"        && /^[[:space:]]*cache_size:/ && cache_done==0 {
    sub(/cache_size:.*/, "cache_size: " cache_bytes); cache_done=1
  }
  { print }
' "$AGH_CONFIG" > /tmp/agh_config_new.yaml \
  && mv /tmp/agh_config_new.yaml "$AGH_CONFIG"

# Safebrowsing + parental controls: GL.iNet AGH API returns 415 for these
# endpoints even with correct empty-body POST. Set directly in config.yaml.
#
# IMPORTANT: only flip the GLOBAL settings under the top-level `filtering:`
# section. A naive `sed ...'/g'` also rewrites the SAME keys inside per-client
# entries under `clients:`, silently overriding the per-client overrides that
# set_client_unfiltered / set_client_blocked_services manage (e.g. an
# unfiltered work device). Use a section-aware awk pass (same pattern as the
# retention/cache edit above) so only the filtering: block is touched.
# LITE: OFF (Cloudflare Families upstream + the 0.0.0.0→block-page alias handle
# adult/malware, and these heuristics' per-domain lookups can hang a 512 MB box).
# FULL: ON. Match BOTH true/false so we can flip either way.
SBP_VAL="true"; [ "$PROT_PROFILE" = "lite" ] && SBP_VAL="false"
awk -v val="$SBP_VAL" '
  /^filtering:/ { section="filtering" }
  /^[a-z_]/ && !/^filtering:/ { section="" }
  section=="filtering" && /^[[:space:]]*safebrowsing_enabled:[[:space:]]*(true|false)/ {
    sub(/safebrowsing_enabled:.*/, "safebrowsing_enabled: " val)
  }
  section=="filtering" && /^[[:space:]]*parental_enabled:[[:space:]]*(true|false)/ {
    sub(/parental_enabled:.*/, "parental_enabled: " val)
  }
  { print }
' "$AGH_CONFIG" > /tmp/agh_sbp_new.yaml \
  && mv /tmp/agh_sbp_new.yaml "$AGH_CONFIG"
/etc/init.d/adguardhome restart >> "$LOGFILE" 2>&1 || true
sleep 5
log "  AdGuard safebrowsing + parental set to $SBP_VAL via config.yaml (profile=$PROT_PROFILE, per-client overrides preserved)."

# ══════════════════════════════════════════════════════════════════════════════
# STEP 11 — PERSIST ACROSS "KEEP SETTINGS" FIRMWARE UPGRADES
# OpenWrt's sysupgrade restores every path listed in /etc/sysupgrade.conf after
# flashing new firmware. Without this, a deliberate "Keep Settings" upgrade
# wipes /root/lantern-watch (app, config, and the SQLite history) and reverts
# AdGuard to users: [], silently breaking the collector.
#
# This COMPLEMENTS — does not replace — the auto-upgrade lockout in STEP 4:
#   - STEP 4 stops *silent/automatic* OTA wipes (the failure mode that hit us).
#   - This step lets a *deliberate* Keep-Settings upgrade carry our files over.
# A full reset / "don't keep settings" upgrade still wipes everything; recover
# by re-running install.sh (config is also backed up to /etc/lanternwatch.bak.*).
# ══════════════════════════════════════════════════════════════════════════════

log "  Registering Lantern Watch paths in /etc/sysupgrade.conf..."

SYSUPGRADE_CONF="/etc/sysupgrade.conf"
touch "$SYSUPGRADE_CONF"

# /root/lantern-watch/ already covers the config JSON and the DB, but the
# config and DB are listed explicitly too so the intent is obvious and a future
# relocation of either file is caught.
LW_KEEP_PATHS="
/root/lantern-watch/
/root/lantern-watch/lanternwatch_config.json
/root/lantern-watch/lanternwatch.db
/etc/init.d/lanternwatch
"
for p in $LW_KEEP_PATHS; do
    if grep -qxF "$p" "$SYSUPGRADE_CONF" 2>/dev/null; then
        log "    = $p (already listed)"
    else
        echo "$p" >> "$SYSUPGRADE_CONF"
        log "    + $p"
    fi
done
log "  sysupgrade.conf updated — app, config, DB, and init script will survive a Keep-Settings upgrade."
log "  (Note: autostart may need 're-enable' after such an upgrade: /etc/init.d/lanternwatch enable)"

# ══════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════════════

# Disable the failure trap before printing summary
trap - EXIT

log "[9/9] Done."
echo ""
echo "======================================================================"
echo "  Congratulations — your network just got brighter!"
echo "======================================================================"
echo ""
echo "  [✓] Lantern Watch is installed and running."
echo ""
echo "  Thank you for joining the Lantern Watch community and taking"
echo "  control of your family's digital space."
echo ""
echo "  👉 Finish your setup here:"
echo "        http://$LAN_IP:8081"
echo "        (or http://lanternwatch.lan:8081)"
echo ""
echo "  🔒 Initial setup checklist:"
echo "     1. Open the dashboard and create your admin username + password"
echo "        (no router login needed — you set your own)."
echo "     2. Run the one-click Protection wizard (adult content, malware"
echo "        blocking, safe search) and set up notifications."
echo "     3. Open Devices to name what's on your network, then pick a"
echo "        Social profile or blocklist to start filtering."
echo ""
echo "  💬 Need help or want to contribute? Visit our community hub:"
echo "        https://github.com/LanternWatchApp/lantern-watch"
echo ""
echo "======================================================================"
echo ""
echo "============================================="
echo "  Lantern Watch — Installation Summary"
echo "============================================="
echo ""
echo "  Model:        $MODEL_NAME ($RAW_MODEL)"
echo "  RAM:          ${MEM_MB} MB"
echo "  Free storage: ${OVERLAY_MB} MB"
echo "  WiFi:         $([ "$HAS_WIFI" = "yes" ] && echo "present" || echo "not present") — $([ "$DISABLE_WIFI" = "yes" ] && echo "disabled (pass-through)" || ([ "$HAS_WIFI" = "yes" ] && echo "enabled" || echo "skipped"))"
echo "  Profile:      $PROFILE"
echo "  AGH log ret:  ${AGH_RETENTION_H}h"
echo "  AGH cache:    $((AGH_CACHE_BYTES/1024/1024)) MB"
echo "  dnsmasq cache:  $DNSMASQ_CACHE entries"
echo ""

if [ "$DASHBOARD_UP" = "yes" ]; then
    echo "  Dashboard:    http://$(uci get network.lan.ipaddr 2>/dev/null || echo '192.168.8.1'):8081  [OK]"
else
    echo "  Dashboard:    NOT responding on port 8081 — check $LOGFILE"
fi

echo ""
SVC_STATUS=$(/etc/init.d/lanternwatch status 2>/dev/null || echo "unknown")
echo "  Service:      $SVC_STATUS"
echo ""

if [ -n "$TEMP_PASS" ]; then
    echo "  ┌─ First login ──────────────────────────────────────────────┐"
    echo "  │                                                              │"
    echo "  │  Username:  admin                                            │"
    echo "  │  Password:  $TEMP_PASS  (unique to this install)  │"
    echo "  │                                                              │"
    echo "  │  You will be required to set a new password on first login. │"
    echo "  │  Save this now — it stops working once the wizard completes.│"
    echo "  └──────────────────────────────────────────────────────────────┘"
fi

echo ""
echo "  Log file:     $LOGFILE"
echo "  AdGuard setup: $AGH_SETUP_RESULT"
echo "  AdGuard note: retention/cache changes apply on next reboot"
echo ""
echo "  Privacy: once a day this router sends an anonymous record so active"
echo "           installs can be counted — a random ID, version, model, RAM"
echo "           and protection profile. Nothing about your network, devices"
echo "           or browsing is ever sent. Usage stats (which features you"
echo "           use) are separate and can be switched off any time in"
echo "           Settings -> Share anonymous usage stats."
echo ""
echo "============================================="
echo ""
