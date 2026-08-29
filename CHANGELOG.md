# Changelog

All notable changes to Lantern Watch are recorded here.

Versioning follows [Semantic Versioning](https://semver.org): `MAJOR.MINOR.PATCH`.
No `-beta`/`-alpha` suffixes — the leading `0.` is itself the signal that this is
early-stage software.

- **PATCH** — the third number — bug fixes and small tweaks (`0.14.1`, `0.14.2`, …).
  It is an ordinary integer, so it keeps counting past 9 (`0.14.9` → `0.14.10` →
  `0.14.100`). Never zero-pad it.
- **MINOR** — new features climb the `0.14.0 → 0.15.0 → …` series (double-digit,
  AdGuard-style, for plenty of pre-1.0 headroom).
- **MAJOR** — **`x`**`.0.0` — breaking changes / the 1.0 milestone.

Version numbers only ever climb. Both the in-app update check and `opkg` compare
them numerically, so publishing a lower number than the one already released would
leave existing routers unable to update.

Bump `VERSION` in `config.py`, add an entry here, then commit and tag `v<version>`.

## [0.18.4] — 2026-08-29

### Security
- **Closed a real vulnerability: any device on your home network could reset your dashboard password, or change your household's filtering settings, without ever logging in.** The first-run setup pages were only meant to work without a login during that very first setup, but they never actually stopped working that way afterward. They now correctly require your login once your account exists, exactly as intended. Updating to 0.18.4 closes this immediately.
- Added a short cooldown to the "forgot password" recovery code request, so a device on your network can't repeatedly trigger it to spam your phone or email with codes.

### Fixed
- **The blocked-site HTTPS page could hang instead of showing its warning.** A connection-handling bug meant one stalled visit could freeze the block page for everyone else on the network until it timed out. It now responds instantly.
- The self-signed certificate behind that page regenerates automatically if it's missing a piece modern browsers require. Happens on its own the next time your router restarts, no action needed.
- Cleaned up an inconsistency in how two background processes loaded their settings, which in rare cases (a missing or corrupted settings file) could have partially reset your configuration.

## [0.18.3] — 2026-08-27

### Fixed
- **ntfy push notifications now open the ntfy app when tapped**, instead of
  jumping straight to the dashboard's local address — which failed whenever
  you were away from home on cellular, since that address only exists on
  your home network. The dashboard link is still right there in the
  notification text if you want to tap it while on Wi-Fi.
- Removed the "Additional Topics" field from Notification Settings — it only
  ever affected the daily/weekly summary, not real-time alerts, which made it
  confusing rather than useful.

### Changed
- The "Struggling with something?" link on the sign-in page now wraps onto
  two lines for better readability.

## [0.18.2] — 2026-08-17

### Fixed
- **Devices no longer show up twice.** AdGuard sometimes logs the same device under
  both its name and its bare IP address, which made it appear as two identical cards
  on the dashboard and Devices page. Those are now merged into a single device, with
  their query counts combined.

## [0.18.1] — 2026-08-17

### Security
- **Your dashboard password is now stored as a secure hash, never in plain text.**
  Existing passwords upgrade automatically the next time you sign in, and the
  password is no longer readable in a config file or in a backup.
- **Hardened the dashboard against a cross-site scripting (XSS) trick.** Device
  names and the websites devices look up are now safely escaped everywhere they
  appear, so a device on your network can't sneak code into your dashboard with a
  booby-trapped name or web address.

## [0.18.0] — 2026-08-17

### Added
- **Pause with a timer.** Pausing a device — or everyone — now asks *how long*:
  **30 minutes, 1 hour, the rest of today, or until you turn it back on.** Timed
  pauses lift themselves automatically, so you never have to remember to switch
  them back. Paused devices show exactly when they'll come back ("Paused until
  4:30 PM").
- **Device groups.** On the **Devices** page you can name a few groups — like
  *Kids, TVs, Phones* — and drop devices into them, then **pause a whole group in
  one tap** from the dashboard. Only your family's devices (Personal, Smart and
  Work) can be grouped; your router, NAS and printer never can. And **"Pause
  everyone" only ever touches Personal devices**, so the essentials stay online.
- **Auto-group devices.** One tap suggests entertainment groups (Phones, Tablets,
  Computers, TVs, Games) from what each device actually is — you review before
  saving, and it will **never** propose grouping a doorbell, camera or appliance.

### Changed
- The dashboard's "Pause All Personal" button is now the plainer **"Pause everyone"**.

### Fixed
- The **Blocked Services** page now describes Safe Search honestly in three states
  — fully on; on for search but with YouTube comments allowed (the Moderate
  default); or off — instead of a misleading on/off.

## [0.17.6] — 2026-08-17

### Fixed
- **Reverted the 0.17.5 AdGuard loopback change.** Binding AdGuard's admin API to
  loopback closed a minor LAN exposure, but it also broke GL.iNet's built-in
  **Tools → AdGuard Home** link, which opens AdGuard's full web UI directly at
  `http://<router>:3000` (only the API is proxied, not the UI). Since the AdGuard
  login is already password-protected, that trade wasn't worth it. AdGuard's UI is
  reachable again; the revert applies automatically on update.

## [0.17.5] — 2026-08-17

### Security
- **AdGuard's admin panel is now off-limits to the rest of your network.** It used
  to listen on every network interface, meaning any device on your Wi-Fi (a child's
  laptop, a guest's phone) could reach the AdGuard admin login directly. It's now
  bound to the router itself (loopback only). Lantern Watch and GL.iNet's own
  AdGuard screen keep working exactly as before (both reach it internally), and
  DNS filtering is completely unaffected. Applied automatically on update.
  **(Reverted in 0.17.6 — it broke GL.iNet's AdGuard menu link.)**

## [0.17.4] — 2026-08-17

### Changed
- **Dating & adult and Gambling are now single one-tick toggles** in the wizard's
  Mature & adult-adjacent section (blocking the whole group at once), matching the
  category packs — keeping that step simple.

## [0.17.3] — 2026-08-17

### Changed
- **The wizard's "Mature & adult-adjacent" block now sits at the top** of the
  Apps & Services step, and gained two more groups: **Dating & adult** (Tinder,
  OnlyFans, Plenty of Fish) and **Gambling** (Betano, Betfair, Betway, Blaze).
- Renamed the lingerie/swim retailers option to **"Retailers (Lingerie & Swim)"**.
- Reworded the step's intro to note you can change any of it per-device later
  under Services.

## [0.17.2] — 2026-08-17

### Changed
- **Better defaults in the setup wizard.** The two most-wanted alerts —
  **adult content blocked** and **a new device joins** — are now ticked by
  default, and the **daily (8 PM) and weekly (Sunday) recaps** default on too.
  (Nothing actually sends until you turn on a notification channel.)
- **12-hour clock** in the wizard's time pickers (e.g. "8:00 PM" instead of
  "20:00").
- **Recap summary is now two independent switches** (Daily and Weekly) in the
  wizard instead of a single choice, so you can have both.

### Added
- **More to block in the wizard's Apps & Services step** — a new *Mature &
  adult-adjacent* section with one-tap category blocks for **Lingerie**,
  **Swimwear**, **Lingerie & swim retailers**, **Alcohol, vaping & cannabis**,
  and **Weapons & tactical**.

## [0.17.1] — 2026-08-17

### Fixed
- **The Blocked Services page no longer goes blank if AdGuard is briefly busy.**
  If AdGuard's built-in service list can't be fetched in the moment (e.g. right
  after setup while it's still starting), the page used to replace *everything* —
  including the curated Lingerie/Swimwear/AI category packs and the "block a
  specific site" box, which don't even need AdGuard — with an error. Now it shows
  a small notice for just the built-in list and keeps the rest of the page usable,
  retries the fetch once, and gives the large catalog more time to load. Saving
  while the list is unavailable can no longer accidentally unblock services.

## [0.17.0] — 2026-08-16

### Added
- **A guided first-run setup wizard.** After you set your password and turn on
  family protection, Lantern Watch now walks you through the choices that used to
  be scattered across the dashboard — each step skippable, with sensible defaults:
  - **Filtering level** — pick **Open**, **Moderate** (recommended), or **Strict**
    for the whole home, with a one-tap **Allow YouTube comments** toggle.
  - **Block specific apps** — tick streaming, gaming, messaging, or AI-chatbot
    services to block household-wide, drawn from AdGuard's live service list.
  - **Network notice** — say whether this is a **home** (quiet) or a **business**
    (shows new devices a one-time acceptable-use notice with your organisation name).
  - **Notifications** — set up free **ntfy** push (with a suggested private topic),
    choose which alerts you want, and optionally schedule a daily or weekly recap.
  - **Backup** — detects a plugged-in USB drive for hands-off backups, offers a
    downloadable backup file, and saves the first backup for you.
  - **Skip → recommended defaults** on any step applies Moderate filtering with
    YouTube comments on, so you're never stuck.

## [0.16.5] — 2026-08-16

Fixes and hardening from a detailed community code review (thanks pspr33).

### Fixed
- **Screen-time limits now actually enforce.** The scheduler was reading the wrong
  database path, so it always saw zero usage and never paused a device that hit its
  daily limit. It now reads the same database the dashboard does. (Existing bedtime
  and Focus Time schedules were unaffected — only screen-time totals.)
- **Pausing a device now also blocks its IPv6.** Pause rules were IPv4-only, so a
  device with routed IPv6 could slip past a bedtime or manual pause. Pauses are now
  applied by hardware (MAC) address on both IPv4 and IPv6, which also keeps a pause
  attached to the right device if its DHCP address changes.

### Changed
- **The installer no longer turns off Wi-Fi by default.** Install on your main
  Wi-Fi router and your network keeps working. Turning the radios off is now opt-in
  (`--wired-passthrough`), for people who run the box wired behind their own AP/mesh.
- **The installer backs up your router config before changing anything.** It
  snapshots your AdGuard, DHCP, wireless, network and firewall settings to
  `/etc/lanternwatch.bak.<timestamp>/` with a `RESTORE.txt` showing how to roll back.
- **Firmware auto-update is only changed if you allow it.** To stop an unattended
  over-the-air update from wiping the install, the installer sets firmware upgrades
  to require a manual confirm — but this is now logged plainly and can be skipped
  with `--keep-auto-update`.
- **New README section — "What the installer changes on your router"** — documents
  exactly what's modified (AdGuard setup, the dedicated `lanternwatch` API account,
  dnsmasq→AdGuard forwarding for per-device visibility, the firmware-confirm) and
  that it's all local, with a snapshot taken first.

## [0.16.4] — 2026-08-12

### Changed
- Housekeeping: replaced real device names used as examples in code comments and
  docs with generic placeholders. No functional change.

## [0.16.3] — 2026-08-12

### Added
- **Homeschool Hub / Brightcove videos work out of the box.** A common ad/tracker
  blocklist quietly blocks `metrics.brightcove.com`, which the Brightcove video
  player waits on — so the video hangs and never plays. It's impossible for a
  non-technical parent to diagnose. Lantern Watch now ships that domain allowed by
  default, so Homeschool Hub (and other Brightcove-powered sites) just play.

### Fixed
- **Allowed sites now survive a router reboot.** The allowlist was stored only in
  AdGuard, so if the router reset AdGuard's custom rules (e.g. after a firmware
  upgrade or certain reboots), your allows vanished. Lantern Watch now keeps its own
  durable copy and re-applies it on every boot — your allows (and the default video
  fix) come back automatically.

## [0.16.2] — 2026-08-12

### Changed
- **Removed the "Prevent DNS Bypass" (plain-DNS forcing) feature added in 0.16.0.**
  On GL.iNet routers it collided with the router's own DNS plumbing and per-device
  tracking — with it on, the dashboard could show almost no devices (everything
  attributed to the router). The idea is sound but not turnkey-safe to ship on by
  default, so it's pulled; upgrading clears the leftover firewall rule automatically.
  **Encrypted-DNS bypass protection (DoH/DoT) is unchanged.** Advanced users who
  want to force all devices through the filter can use GL.iNet's built-in *Override
  DNS Settings of All Clients* (with the caveat that it can affect per-device
  reporting).

## [0.16.1] — 2026-08-12

### Fixed
- **DNS-bypass prevention no longer breaks per-device tracking.** The 0.16.0
  force-redirect sent client DNS to dnsmasq's port 53, which forwards to AdGuard as
  the router — so every device collapsed to `127.0.0.1` and the dashboard showed
  almost nothing. It now redirects straight to **AdGuard's DNS port** (auto-detected,
  3053 on GL.iNet), which preserves the real client IP, so bypass is still blocked
  *and* every device shows up correctly again. Fixes upgrades from 0.16.0
  automatically on restart (the stale rule is cleared).

## [0.16.0] — 2026-08-12

### Added
- **Prevent DNS bypass — the filter can no longer be defeated by changing a
  device's DNS.** A parental filter is only as good as its weakest bypass, and the
  easiest one is setting a device's DNS to a public server like `8.8.8.8`. Lantern
  Watch now **forces every device's plain DNS through the filter** automatically, so
  that trick stops working — no hunting through router settings required. It's **on
  by default** and re-applied on every boot (the firewall rule is scoped to your LAN,
  so the router's own lookups are untouched). This completes the bypass story: plain
  DNS is now sealed here, and encrypted DNS (DoH/DoT) was already handled by the
  existing Encrypted-DNS protection. A new **Settings → Prevent DNS Bypass** toggle
  lets you turn it off for a specific device that legitimately needs its own DNS.

## [0.15.6] — 2026-08-12

### Fixed
- **Device role detection now matches the smarter naming — and it matters for
  Pause All and bedtime.** Two safety corrections:
  - A cryptic-named phone or tablet (e.g. a kid's `9469X` Android tablet) is now
    typed **Personal**, not "Smart Device" — so it's included in **Pause All** and
    **bedtime/Focus schedules** instead of being skipped. Any device whose traffic
    shows a phone/tablet/computer OS (Android, iPhone/iPad, Mac, Windows) is treated
    as a person's device; genuine appliances (Roku, Fire TV, Eufy cam, …) stay Smart
    Device.
  - **Wi-Fi mesh nodes / boosters** (Boost, Deco, Velop, AmpliFi, Nest Wifi, …) are
    now typed **Infrastructure**, so a schedule or Pause All can't accidentally knock
    out the whole network's Wi-Fi.
- The **"probably a…" hint now agrees with the name** — a device named "… Android
  device" reads "probably a phone or tablet," not "probably a smart TV."

### Changed
- Eufy cameras/hubs are recognized from their traffic (`eufylife.com`).
- Device names with a DHCP-escaped space (`Eufy\ Device`) now display cleanly
  ("Eufy Device").
- Auto-name no longer appends a vague label to an already-clear name (no more
  "sample-nas-01 — Streaming device / TV").

## [0.15.5] — 2026-08-12

### Changed
- **Auto-name now keeps the person's name AND adds what the device is.** Previously
  "Auto-name devices" replaced a meaningful name like `sample-mobile-01` with a
  generic "Samsung Android device", losing *who* it belongs to. It now combines
  them — **"sample-mobile-01 — Samsung Android device"** — so a parent sees both
  the person and the device at a glance (easier to know who to go coach). A cryptic
  name (a model code like `9469X`, or a bare IP) is still replaced by the maker+OS
  guess alone, since it tells a human nothing. OUI maker names are also tidied
  ("FUNAI ELECTRIC CO., LTD." → "Funai Electric").

## [0.15.4] — 2026-08-12

### Changed
- **Smarter device auto-naming.** The Devices page **"Auto-name devices"** button now
  recognizes a device's **maker and operating system** from the domains it talks to —
  so a phone that hides its maker behind a randomized MAC and shows up as a cryptic
  code (e.g. `9469X`) is suggested as **"TCL / Alcatel Android device"** instead of a
  vague "Google / Android device." It reads low-frequency tells like `tct-rom.com`
  (TCL/Alcatel), `mediatek.com` (chipset), `settings-win.data.microsoft` (Windows) and
  the Google/Apple platform domains, and composes names like *Samsung Android device*,
  *Windows PC*, *Apple iPhone or iPad*. A phone with a shopping/streaming app is no
  longer mistaken for a Fire TV. As before, it's only a **suggestion you review and
  can edit** before saving — nothing is applied automatically.

## [0.15.3] — 2026-08-10

### Changed
- **"What is this site?" now gives you a real answer even when there's no page to
  show.** A lot of blocked domains (like `ob.thisgreencolumn.com`) aren't websites
  at all — they're background ad/analytics/tracking endpoints with nothing to
  visit. Instead of a bland "Couldn't load a preview," Peek now explains that:
  domains whose name/pattern match a tracker (`ob.`, `ads.`, `analytics.`,
  `pixel.`, `metrics.`, …) are called out as background ad/tracking services, and
  anything else that won't load is described as a likely background service rather
  than a site someone visits. The result reads as an informative answer, not an
  error.

## [0.15.2] — 2026-08-04

### Added
- **Decide about a blocked site right on its details page** (tap any blocked domain
  from the dashboard → `/domain?name=…`). Every blocked site now shows a plain-
  English category of what it is — *Adult content, Ads & trackers, Malware /
  phishing, Gambling, Dating…* — plus three ways to judge it before you commit:
  - **🔍 What is this site?** — the router privately fetches just the site's name and
    description (bypassing the block) and shows them to *you only*. Nothing is
    unblocked and no one else's access changes — a safe way to tell what a cryptic
    domain actually is.
  - **👁️ Preview site (15 min)** — temporarily lets the site through and opens it in a
    new tab so you can see the real page, then **auto-blocks it again after 15
    minutes** unless you hit **Keep allowed**. (Heads-up: a DNS allow briefly opens
    the site for every device on the network, and it can take a few seconds to load.)
  - **✓ Allow this site** — let it through for good, one tap.
  If a site is already allowed, the page shows that state with a **Block again**
  button instead.

## [0.15.1] — 2026-08-04

### Added
- **Allow a blocked site in one tap — and see what each block actually is.**
  The Query Log's **Top Blocked** list now labels every domain with a plain-English
  category — *Ads & trackers, Adult content, Malware / phishing, Trackers, Gambling,
  Dating, Safe search* — so a cryptic entry like `ob.thisgreencolumn.com` reads as
  what it is, not just "Blocked." Next to each is an **Allow** button: if a real site
  is being caught by mistake, one tap lets it through (and the fix is live in a few
  seconds, no AdGuard trip required). The same category now shows on every blocked
  row in the Full Log.
- **An "Allowed sites" panel with one-tap Block again.** Everything you've allowed is
  listed together, each with a **Block again** button — so if you allow something and
  then think better of it, you can reverse it from the same page.

### Fixed
- **A just-allowed domain now takes effect immediately.** Allowing a site waits for
  AdGuard to reload its rules before returning, instead of the change only landing on
  the next reload — so the site works as soon as the confirmation appears.

## [0.15.0.1] — 2026-07-29

### Added
- The dashboard's **"Queries Today"** number is now a clickable link straight to
  the Query Log (and its new Summary view).

## [0.15.0] — 2026-07-29

### Added
- **Query Log Summary — see what your devices are actually doing, at a glance.**
  The Query Log page now opens on a **Summary** that ranks, over any time window:
  your **top devices** (with each one's block rate), the **domains** they reach
  most, and the **top blocked** trackers and ads. **Tap a device** to drill into
  exactly what it's talking to — a pivot from "which device" to "doing what." The
  full row-by-row log is one tap away under **Full Log** (and searching or
  filtering blocked-only jumps you straight there). Turns a log of hundreds of
  thousands of rows into a two-second answer to "what's happening on my network?"

## [0.14.7] — 2026-07-28

### Fixed
- **The Tools menu links (AdGuard, GL.iNet) work again from any device.** 0.14.5
  pointed the app's *internal* AdGuard connection at loopback (`127.0.0.1`), which
  is correct — but the Tools menu reused that same URL for the links your browser
  follows, so from a phone or laptop they pointed at `127.0.0.1` (your own device)
  and went nowhere. The browser-facing links now use the router's real LAN IP,
  while the internal connection stays on loopback.

## [0.14.6] — 2026-07-28

### Fixed
- **The Network Notice (captive portal) no longer locks you out of your own
  router.** When enabled, it was intercepting web traffic to the router itself —
  so it blocked the GL.iNet admin panel *and* the Lantern Watch dashboard, with no
  way through except a factory reset. The portal now always exempts the router's
  own addresses (its LAN IP and loopback), so admin pages stay reachable while new
  devices are still shown the notice. (This surfaced now because the captive portal
  only started working correctly in 0.14.0 — before that it did nothing at all.)

## [0.14.5] — 2026-07-24

Hardening from a full end-to-end audit. Everything tested healthy; these close the
minor gaps the audit surfaced (no user-facing feature was broken).

### Fixed
- **Lite updates no longer switch off small blocklists you enabled.** On Lite, an
  update used to disable *every* list outside the tiny default set to protect a
  512 MB router from running out of memory — including a modest list you'd turned
  on yourself. It now only disables genuinely heavy lists (the ones that actually
  cause the memory problem), so your own choices survive updates.
- **A forced protection profile now sticks.** Installing with `--force-lite` /
  `--force-full` is remembered across updates, instead of reverting to the
  RAM-based default on the next update (which never re-passes the flag).
- **The app talks to AdGuard over loopback** (`127.0.0.1`) instead of the LAN IP,
  so a router whose LAN address changes (e.g. repeater mode) can't lose its
  connection to AdGuard.

### Changed
- **"Clear Data" now reclaims the disk space** it frees (the database file used to
  stay large after a clear until the next write cycle).
- **Build safeguard:** the package build now fails if any app module isn't included
  — the exact class of mistake behind the missing Backup & Restore file in 0.14.3.

## [0.14.4] — 2026-07-23

### Fixed
- **Updating no longer resets your Safe Search / YouTube Restricted Mode choice.**
  The installer re-applies family protection on every run, and that step blanket-
  re-enabled every Safe Search engine — so an update silently switched YouTube
  Restricted Mode back on, undoing anyone who had turned it off to allow comments.
  Setup now only applies the secure-by-default all-engines-on state when Safe
  Search is currently off (a genuine first-time setup); if it's already on, it
  preserves your per-engine choices. The fix is centralized, so it also covers the
  manual "Apply Now" button — re-applying protection keeps your YouTube choice.
  (Everything else you configure already survives updates; it lives in your config
  file, which updates never overwrite. Safe Search was the one setting stored in
  AdGuard rather than the config, which is why it was the one thing affected.)

## [0.14.3] — 2026-07-23

### Fixed
- **Backup & Restore now actually ships.** The `backup.py` module was never added
  to the package build, so every installed copy was missing it — which silently
  disabled the whole feature: downloadable backup files *and* USB auto-backup. The
  app didn't crash (each backup call is a guarded, on-demand import), it just did
  nothing. `backup.py` is now included in the package, so plugging in a USB drive
  saves your setup automatically again, and manual backup/restore works. (USB
  detection itself was fine — it reads the live mount table and handles GL.iNet's
  `/tmp/mountd/...` location.)

## [0.14.2] — 2026-07-20

### Changed
- **Simplified the last step of first-run setup.** It no longer asks you to enter
  ntfy, Telegram, or email details up front — that was a lot to face on a first
  run. The step is now just the optional "share anonymous usage stats" choice, and
  notifications, schedules, and social profiles are all set up later from the
  dashboard whenever you want them.

## [0.14.1] — 2026-07-20

### Fixed
- **Device names now match on every page.** The dashboard, query log, and device
  detail pages showed a bare IP address (e.g. `192.168.8.230`) while the Devices
  page showed the friendly name (e.g. "Dell device") for the very same device.
  Each page worked the name out for itself and the copies had drifted apart; they
  now share one routine, so they cannot disagree again.
- **The router shows its own model** — e.g. "GL.iNet GL-MT3600BE" — instead of
  appearing as "localhost".

## [0.14.0] — 2026-07-20

First public release.

### Protection
- **AdGuard Home set up for you**, in one click during the first-run wizard —
  adult content, malware and phishing blocking, and safe search on Google, Bing
  and YouTube. No AdGuard settings to touch.
- **RAM-aware protection profiles, chosen automatically.** Routers with 1 GB+ run
  the **Full** profile with the complete local blocklists. Smaller 512 MB travel
  routers run **Lite**, which keeps memory low by filtering adult and malware
  content at a Cloudflare for Families DNS upstream instead of loading hundreds of
  thousands of rules on the router. Every parental feature works identically on
  both.
- **Choose your DNS filtering level** on Lite — Malware + Adult (default) or
  Malware only.
- **Blocked sites land on a Lantern Watch page** carrying a prominent **Find Help**
  link, including when the block came from the DNS upstream.
- **Encrypted-DNS bypass protection**, always on, plus an optional strict mode.
- **DNS blocklist manager** with per-list toggles and a live rule-budget meter.

### Family controls
- **Device dashboard** — query counts, block rates, and time online per device.
- **Pause the internet** for a device instantly or on a schedule.
- **Hours of Peace** bedtime cutoff, **Focus Times**, and **screen time limits**.
- **Social media profiles** — Open, Moderate, Strict, or Custom — applied instantly,
  plus a secure-by-default **YouTube Restricted Mode** toggle.
- **Device roles** (Personal, Admin, Work, Infrastructure, Smart Device) that
  control grouping and bulk pausing. Every role stays fully filtered.
- **Network Notice** — an optional acceptable-use notice new devices acknowledge
  before browsing.

### Notifications
- **ntfy, Telegram, and email**, each with a test button, for blocked content, new
  devices, high block rates, possible VPN use, and screen time limits.
- **Daily and weekly summaries** at a time you choose, and an in-dashboard log of
  every alert sent.
- **Update alerts** when a newer version is released.

### Living with it
- **Backup & restore** your whole setup to a file, plus optional USB auto-backup
  that survives a factory reset.
- **One-click updater** — "Update Now" installs the latest release and restarts.
- **Query history** you control: 7 to 90 days, trimmed automatically.
- **Router health** — live RAM, storage, CPU load, uptime, and database size.
- **Password recovery** by one-time code to your notification channel.

### Privacy
- Everything runs on your own router. Names, devices, domains, IP addresses, and
  browsing history never leave it.
- A small **anonymous record** is sent once a day so active installs can be
  counted: a random identifier, version, router model, memory size, and protection
  profile. The identifier is random — never derived from your hardware.
- **Optional usage stats** (which features are switched on) are offered during
  setup and can be turned off at any time in Settings.
- Update checks read GitHub's public release list and send nothing at all.
