#!/bin/bash
# wwan-down.sh — tear down data connection

LOG="logger -t wwan-down"
WDM="/dev/cdc-wdm0"
IFACE="wwan0"

$LOG "Bringing down wwan0..."

# stop qmi network
rm -f /tmp/qmi-network-state-cdc-wdm0
qmi-network "$WDM" stop 2>/dev/null || true


# bring interface down — NM will regenerate resolv.conf from remaining connections
ip addr flush dev "$IFACE" 2>/dev/null || true
ip link set "$IFACE" down 2>/dev/null || true

# give NM 2s to regenerate resolv.conf from wlan0
sleep 2

# notify piphone
curl -s -X POST http://localhost:5000/api/modem/data-down \
    -H "Content-Type: application/json" \
    -d '{}' 2>/dev/null || true

$LOG "wwan0 down — resolv.conf: $(cat /etc/resolv.conf | grep nameserver | tr '\n' ' ')"
