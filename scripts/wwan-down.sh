#!/bin/bash
# wwan-down.sh — tear down data connection (airplane mode)

LOG="logger -t wwan-down"
WDM="/dev/cdc-wdm0"
IFACE="wwan0"

$LOG "Bringing down wwan0..."

# stop qmi-network if state file exists
if [ -f /tmp/qmi-network-state-cdc-wdm0 ]; then
    qmi-network "$WDM" stop 2>/dev/null || true
fi

# flush IP and bring interface down
ip addr flush dev "$IFACE" 2>/dev/null || true
ip link set "$IFACE" down 2>/dev/null || true

# notify piphone
curl -s -X POST http://localhost:5000/api/modem/data-down \
    -H "Content-Type: application/json" \
    -d '{}' 2>/dev/null || true

$LOG "wwan0 down"
