# Business / Edge Deployment

> **Status: future / experimental — not yet deployed.**

> Insert Lantern Watch between your ISP and your existing network.

The GL-MT5000 (Brume 3) slots in between the ISP device and an existing internal switch. It does not replace any WiFi infrastructure — access points, managed switches, and VLAN setups are left in place. The Brume 3 adds DNS filtering, firewall policies, and the monitoring dashboard at the network edge.

```mermaid
flowchart TD
    ISP["ISP Modem / Router"]
    ISP -->|"Ethernet WAN"| BRUME

    subgraph BRUME ["Lantern Watch — GL-MT5000 (Brume 3)"]
        DNS["AdGuard Home<br/>DNS filtering · safe search · malware blocking"]
        FW["Firewall · Routing policies<br/>logging · monitoring"]
        VLAN["Optional VLAN support"]
        DASH["Dashboard — port 8081"]
    end

    BRUME -->|"2× 2.5G LAN ports"| SWITCH["Existing network switch"]
    SWITCH --> AP["WiFi access points (existing)"]
    SWITCH --> WS["Wired workstations<br/>Printers · IoT devices"]
```

The Brume 3 has no built-in WiFi. It is fanless and built for 24/7 always-on use at the edge. Existing access points, switches, and any managed VLAN setup continue to operate unchanged.

No remote access component. All protection runs locally on the device.
