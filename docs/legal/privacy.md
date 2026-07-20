# Lantern Watch — Privacy Policy

*Last updated: July 2026*

## The short version

Lantern Watch runs entirely on your own router. We do not have access to your network, your devices, or your family's internet activity.

Two small things leave your router, and neither contains personal information:

1. **An anonymous install record**, sent once a day, so we can count how many routers are actively running Lantern Watch. Every install sends this.
2. **Optional usage stats**, only if you turn them on — a few on/off flags about which features you use.

Checking for updates sends us nothing at all: your router simply reads a public list of released version numbers from GitHub, the same way any visitor to a web page would.

---

## 1. What we do NOT collect

We never collect, store, or see:

- The names or identities of any devices on your network
- IP addresses of your devices
- DNS queries your family makes
- Websites visited
- Blocked domains
- Any content filtered by AdGuard Home
- Your dashboard username or password
- Your notification credentials (ntfy topic, Telegram token, email address)
- Your router's IP address or network configuration

All of this data lives exclusively on your router and is never transmitted anywhere.

## 2. Checking for updates sends us nothing

When Lantern Watch checks whether a newer version exists, it reads GitHub's **public list of release tags** — the same public page anyone can open in a browser. No request is made to us, and nothing about you or your router is transmitted. This happens automatically once a day, and when you press "Check for Updates".

## 3. The anonymous install record

So we can tell how many routers are actively running Lantern Watch, each install sends a small record **once a day and when it starts up**. Every install sends this, whether or not you turn on the optional usage stats below.

| Field | What it is |
|---|---|
| `install_id` | A **random** identifier created when Lantern Watch is installed. It is not derived from your hardware, MAC address, or anything about your network. Resetting the router or reinstalling creates a brand-new one. |
| `version` | Your Lantern Watch software version |
| `router_model` | Your router hardware model (e.g. "GL.iNet GL-MT6000") |
| `ram_mb` | How much memory your router has — tells us whether the lighter or fuller protection profile suits real hardware |
| `protection_profile` | Whether this router runs the "lite" or "full" filtering profile |
| `openwrt_version` | Your OpenWrt firmware version |

No field identifies you, your household, or your location, and there is no way to link one of these records to a person.

## 4. Optional usage stats (pre-selected during setup — easy to turn off)

During setup you're asked whether to share anonymous usage stats. **The box is pre-selected — untick it if you'd rather not**, and you can change it at any time in **Settings → Share anonymous usage stats**. When it is on, the daily record above also includes:

| Field | What it is |
|---|---|
| `adguard_connected` | Whether AdGuard Home is reachable (true/false) |
| `device_count` | How many devices are labelled in your config — a number only, never names |
| `social_profile` | Which social media profile is selected (e.g. "open", "moderate", "strict") |
| `lite_dns_tier` | Which DNS filtering level is selected |
| `features.*` | On/off flags for screen time, bedtime, focus times, social blocking |
| `notifications.*` | Which notification types are set up (ntfy / Telegram / email) — **never** the topic, token, address, or any credential |

Turning it back off stops this immediately; only the anonymous install record in section 3 continues.

## 5. Notification channels

If you configure ntfy, Telegram, or email notifications, your credentials are stored in `lanternwatch_config.json` on your router only. They are never sent to Lantern Watch servers.

Alerts are sent directly from your router to your chosen notification service (ntfy.sh, Telegram, or your SMTP provider). Lantern Watch is not a relay — we never see your alerts.

## 6. Self-hosted nature

Lantern Watch is self-hosted software. When you install it, you are running it on hardware you own, on a network you control. We have no servers, no accounts, no cloud dashboard. There is no way for us to access your data even if we wanted to.

## 7. Affiliate links

The Lantern Watch website and documentation contain affiliate links (currently Amazon). Clicking these links may place tracking cookies managed by Amazon. We earn a small commission on qualifying purchases. This tracking is entirely on Amazon's side and subject to [Amazon's privacy policy](https://www.amazon.com/gp/help/customer/display.html?nodeId=468496).

## 8. Website analytics

[lanternwatch.org](https://lanternwatch.org) may use basic, privacy-respecting analytics (page views, no personal identifiers). No third-party advertising trackers are used.

## 9. Children's privacy

Lantern Watch is designed to help parents protect children's online safety. We do not collect any data about children or anyone else. All data stays on your router.

## 10. Contact

If you have privacy questions: [lanternwatchapp@gmail.com](mailto:lanternwatchapp@gmail.com)
