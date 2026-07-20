# Family Home Deployment

> Plug Lantern Watch in front of your network — connect and you're protected.

The GL-MT6000 replaces your existing router. The ISP modem drops into bridge / modem-only mode. Every device on your network flows through Lantern Watch and gets DNS filtering, parental controls, and the monitoring dashboard automatically.

```mermaid
flowchart TD
    ISP["ISP Modem / ONT<br/>(bridge / modem-only mode)"]
    ISP -->|"Ethernet WAN"| ROUTER

    subgraph ROUTER ["Lantern Watch — GL-MT6000 (Flint 2)"]
        DNS["AdGuard Home<br/>DNS filtering · safe search · malware blocking"]
        SOCIAL["dnsmasq<br/>social media blocking rules"]
        SCHED["Scheduler<br/>bedtime · focus times · screen time"]
        FW["Firewall · NAT · DHCP<br/>device pausing · iptables"]
        DASH["Dashboard — port 8081"]
    end

    ROUTER -->|"LAN (1G + 2.5G ports)"| WIRED["Wired devices<br/>PCs · NAS · Smart TV"]
    ROUTER -->|"WiFi 6"| WIFI["Wireless devices<br/>Phones · Tablets · Laptops"]
```

**DNS chain:** devices → dnsmasq :53 (social blocking rules) → AdGuard Home :3053 (DNS filtering) → upstream DNS.

No remote access component. All protection runs locally on the router. Nothing phones home.
