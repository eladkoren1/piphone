#!/bin/bash
# setup-dns.sh — configure NM to always include fallback DNS
# Run once after install

# Method 1: NM global DNS config
mkdir -p /etc/NetworkManager/conf.d
cat > /etc/NetworkManager/conf.d/dns-fallback.conf << 'NMEOF'
[global-dns-domain-*]
servers=8.8.8.8,8.8.4.4
NMEOF

# Method 2: also add to the active WiFi connection directly
WIFI_CON=$(nmcli -t -f NAME,TYPE con show --active | grep wireless | cut -d: -f1 | head -1)
if [ -n "$WIFI_CON" ]; then
    echo "Adding fallback DNS to WiFi connection: $WIFI_CON"
    nmcli con mod "$WIFI_CON" ipv4.dns "8.8.8.8 8.8.4.4"
    nmcli con mod "$WIFI_CON" ipv4.ignore-auto-dns no
fi

systemctl restart NetworkManager
sleep 2
echo "resolv.conf:"
cat /etc/resolv.conf
