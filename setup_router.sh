#!/bin/sh
# Lantern Watch — setup_router.sh
# Fresh install script for GL.iNet routers running OpenWrt.
# Run once via SSH after enabling AdGuard Home in the GL.iNet admin panel.
#
# Usage:
#   ssh root@192.168.8.1
#   wget -O /tmp/setup.sh https://raw.githubusercontent.com/LanternWatchApp/lantern-watch/main/setup_router.sh
#   sh /tmp/setup.sh

set -e

REPO="https://github.com/LanternWatchApp/lantern-watch.git"
INSTALL_DIR="/root/lantern-watch"

echo ""
echo "==================================="
echo "  Lantern Watch — Router Setup"
echo "==================================="
echo ""

# ── Step 1: Dependencies ──────────────────────────────────────────────────────
echo "[1/5] Installing dependencies..."
opkg update > /dev/null 2>&1
opkg install python3 git git-http ca-bundle > /dev/null 2>&1
echo "      Done."

# ── Step 2: Clone or update repo ──────────────────────────────────────────────
echo "[2/5] Downloading Lantern Watch..."
if [ -d "$INSTALL_DIR/.git" ]; then
    cd "$INSTALL_DIR"
    git pull origin main
    echo "      Updated existing installation."
else
    git clone "$REPO" "$INSTALL_DIR"
    echo "      Cloned fresh installation."
fi
cd "$INSTALL_DIR"

# ── Step 3: Create config if needed ──────────────────────────────────────────
echo "[3/5] Setting up config..."
CONFIG="$INSTALL_DIR/lanternwatch_config.json"
TEMP_PASS=""
if [ ! -f "$CONFIG" ]; then
    cp "$INSTALL_DIR/lanternwatch_config.example.json" "$CONFIG"
    TEMP_PASS=$(python3 -c "import secrets,string; print(''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(12)))")
    python3 -c "
import json,sys
c=json.load(open(sys.argv[1]))
c['lw_password']=sys.argv[2]
json.dump(c,open(sys.argv[1],'w'),indent=2)
" "$CONFIG" "$TEMP_PASS"
    echo "      Created config from example."
else
    echo "      Existing config kept (not overwritten)."
fi

# ── Step 4: Install auto-start service ───────────────────────────────────────
echo "[4/5] Installing auto-start service..."
cp "$INSTALL_DIR/lanternwatch.initd" /etc/init.d/lanternwatch
chmod +x /etc/init.d/lanternwatch
/etc/init.d/lanternwatch enable
echo "      Service installed and enabled."

# ── Step 5: Start services ────────────────────────────────────────────────────
echo "[5/5] Starting Lantern Watch..."
/etc/init.d/lanternwatch start
sleep 3

echo ""
echo "==================================="
echo "  Setup Complete!"
echo "==================================="
echo ""
echo "  Dashboard: http://192.168.8.1:8081"
echo ""
if [ -n "$TEMP_PASS" ]; then
echo "  First login:"
echo "    Username:  admin"
echo "    Password:  $TEMP_PASS"
echo ""
echo "  Save this password now — it stops working once"
echo "  the setup wizard runs and you create a new one."
else
echo "  Use your existing Lantern Watch credentials to log in."
fi
echo ""
echo "  Make sure AdGuard Home is enabled in"
echo "  your GL.iNet admin panel first."
echo "==================================="
echo ""

ps | grep -E "dashboard|collector|alerts" | grep -v grep
