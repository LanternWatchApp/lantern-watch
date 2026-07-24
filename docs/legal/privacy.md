# Lantern Watch — Privacy Policy

*Last updated: July 2026*

## The short version

Lantern Watch gives you content filtering, parental controls, and screen-time limits — running entirely on your own router. It's free and open-source, with no cloud account. Filtering combines community blocklists on your router with trusted public DNS providers (like Cloudflare and Quad9), and **we operate zero servers that see or log your browsing.**

Two small things leave your router *to us*, and neither contains personal information:

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

**Lantern Watch operates no servers that receive any of this.** There is nowhere for it to go on our end — we have no cloud, no account system, and no database of users. (Your router does send domain lookups to a public DNS provider to resolve them — see the next section — but those go to Cloudflare or Quad9, never to Lantern Watch, and with no account attached.)

## 2. How DNS filtering works — and where your queries go

We want to be completely transparent about this, because "runs on your router" doesn't mean your DNS never touches the internet. Content filtering runs through **AdGuard Home on your own router**, and a lookup takes this path:

```
your device → your router (AdGuard Home) → a public DNS resolver
```

- **On routers with enough memory (the "Full" profile):** adult, malware, phishing, and tracker filtering is done **locally on your router**, using community blocklists (OISD, the AdGuard DNS filter, phishing/URLhaus, anti-malware, dating, scam, and stalkerware lists, and more). Anything that isn't blocked is then resolved by a trusted public upstream — **`cloudflare-dns.com` and `dns.quad9.net`** (encrypted DNS-over-HTTPS). Those upstreams only *resolve* domains; they don't do the filtering.
- **On lighter travel routers (the "Lite" profile):** to keep memory low, adult and malware filtering is handled **by the upstream itself — Cloudflare for Families (`family.cloudflare-dns.com`)** — instead of large local lists. A few small community lists (dating, scam, stalkerware) still run locally on the router.

Either way:

- Your DNS queries are resolved by a **public provider** (Cloudflare / Quad9), because that is simply how DNS works — every device on the internet resolves names through *some* resolver. These providers are privacy-respecting (they don't sell your data and keep minimal logs), and **no account ties those lookups to you**.
- **None of it goes to Lantern Watch.** We are not in the DNS path, we run no resolver, and we never receive your queries.

You can see and change which blocklists are enabled — and, on Lite, which upstream filtering level is used — any time in the dashboard.

## 3. Checking for updates sends us nothing

When Lantern Watch checks whether a newer version exists, it reads GitHub's **public list of release tags** — the same public page anyone can open in a browser. No request is made to us, and nothing about you or your router is transmitted. This happens automatically once a day, and when you press "Check for Updates".

## 4. The anonymous install record

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

## 5. Optional usage stats (pre-selected during setup — easy to turn off)

During setup you're asked whether to share anonymous usage stats. **The box is pre-selected — untick it if you'd rather not**, and you can change it at any time in **Settings → Share anonymous usage stats**. When it is on, the daily record above also includes:

| Field | What it is |
|---|---|
| `adguard_connected` | Whether AdGuard Home is reachable (true/false) |
| `device_count` | How many devices are labelled in your config — a number only, never names |
| `social_profile` | Which social media profile is selected (e.g. "open", "moderate", "strict") |
| `lite_dns_tier` | Which DNS filtering level is selected |
| `features.*` | On/off flags for screen time, bedtime, focus times, social blocking |
| `notifications.*` | Which notification types are set up (ntfy / Telegram / email) — **never** the topic, token, address, or any credential |

Turning it back off stops this immediately; only the anonymous install record in section 4 continues.

## 6. Notification channels

If you configure ntfy, Telegram, or email notifications, your credentials are stored in `lanternwatch_config.json` on your router only. They are never sent to Lantern Watch servers.

Alerts are sent directly from your router to your chosen notification service (ntfy.sh, Telegram, or your SMTP provider). Lantern Watch is not a relay — we never see your alerts.

## 7. Self-hosted nature

Lantern Watch is self-hosted software. When you install it, you are running it on hardware you own, on a network you control. We have no servers, no accounts, no cloud dashboard. There is no way for us to access your data even if we wanted to.

## 8. Affiliate links

The Lantern Watch website and documentation contain affiliate links (currently Amazon). Clicking these links may place tracking cookies managed by Amazon. We earn a small commission on qualifying purchases. This tracking is entirely on Amazon's side and subject to [Amazon's privacy policy](https://www.amazon.com/gp/help/customer/display.html?nodeId=468496).

## 9. Website analytics

[lanternwatch.org](https://lanternwatch.org) may use basic, privacy-respecting analytics (page views, no personal identifiers). No third-party advertising trackers are used.

## 10. Children's privacy

Lantern Watch is designed to help parents protect children's online safety. We do not collect any data about children or anyone else — Lantern Watch has no servers that receive your data.

## 11. Contact

If you have privacy questions: [lanternwatchapp@gmail.com](mailto:lanternwatchapp@gmail.com)
