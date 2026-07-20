# Lantern Watch Deploy Script
$router = "root@192.168.8.1"
$remote = "/root/lantern-watch"
$key = "C:\Users\YOUR_USERNAME\.ssh\id_ed25519"  # Update this to your SSH key path

Write-Host "Deploying to router..." -ForegroundColor Yellow

# -O flag required: OpenWrt has no sftp-server, must use legacy SCP protocol
scp -O -i $key dashboard.py  ${router}:${remote}/dashboard.py
scp -O -i $key pages.py      ${router}:${remote}/pages.py
scp -O -i $key alerts.py     ${router}:${remote}/alerts.py
scp -O -i $key collector.py  ${router}:${remote}/collector.py
scp -O -i $key config.py     ${router}:${remote}/config.py
scp -O -i $key adguard.py    ${router}:${remote}/adguard.py
scp -O -i $key scheduler.py  ${router}:${remote}/scheduler.py
scp -O -i $key db.py         ${router}:${remote}/db.py
scp -O -i $key routes.py     ${router}:${remote}/routes.py
# Note: lanternwatch_config.json is NOT overwritten on deploy to preserve live settings.
# To push a fresh config on first install only, run:
#   scp -i $key lanternwatch_config.json ${router}:/root/lanternwatch_config.json

Write-Host "Installing init.d service..." -ForegroundColor Yellow
scp -i $key lanternwatch.initd ${router}:/etc/init.d/lanternwatch
ssh -i $key $router "chmod +x /etc/init.d/lanternwatch && /etc/init.d/lanternwatch enable"

Write-Host "Syntax check..." -ForegroundColor Yellow
ssh -i $key $router "python3 -m py_compile ${remote}/pages.py && python3 -m py_compile ${remote}/alerts.py && python3 -m py_compile ${remote}/collector.py && python3 -m py_compile ${remote}/routes.py && python3 -m py_compile ${remote}/db.py"
if (-not $?) { Write-Host "Syntax error — aborting restart." -ForegroundColor Red; exit 1 }

Write-Host "Restarting all processes..." -ForegroundColor Yellow
ssh -i $key $router @'
kill $(ps | grep 'collector.py'  | grep -v grep | awk '{print $1}') 2>/dev/null
kill $(ps | grep 'alerts.py'     | grep -v grep | awk '{print $1}') 2>/dev/null
kill $(ps | grep 'dashboard.py'  | grep -v grep | awk '{print $1}') 2>/dev/null
sleep 2
cd /root/lantern-watch
python3 -u collector.py  > /tmp/collector.log  2>&1 &
python3 -u alerts.py     > /tmp/alerts.log     2>&1 &
python3 -u dashboard.py  > /tmp/dashboard.log  2>&1 &
sleep 1
echo "Running processes:"
ps | grep python
'@

Write-Host "Done! All processes restarted." -ForegroundColor Green
