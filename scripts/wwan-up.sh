#!/bin/bash
# wwan-up.sh — bring up SIM7600G-H data connection (fallback only)

LOG="logger -t wwan-up"
WDM="/dev/cdc-wdm0"
IFACE="wwan0"
APN="internet"

$LOG "Starting wwan0 data connection..."

rm -f /tmp/qmi-network-state-cdc-wdm0
$LOG "Cleared stale QMI state"

for i in $(seq 1 20); do
    [ -c "$WDM" ] && break
    $LOG "Waiting for $WDM... ($i/20)"
    sleep 1
done

if [ ! -c "$WDM" ]; then
    $LOG "ERROR: $WDM not found, aborting"
    exit 1
fi

for i in $(seq 1 15); do
    qmicli -d "$WDM" --nas-get-signal-strength &>/dev/null && break
    $LOG "Waiting for signal... ($i/15)"
    sleep 2
done

$LOG "Setting raw-ip mode..."
ip link set "$IFACE" down 2>/dev/null || true
echo Y > /sys/class/net/$IFACE/qmi/raw_ip
ip link set "$IFACE" up

$LOG "Starting QMI network (APN=$APN)..."
qmicli -p -d "$WDM" \
    --device-open-net='net-raw-ip|net-no-qos-header' \
    --wds-start-network="apn='$APN',ip-type=4" \
    --client-no-release-cid

if [ $? -ne 0 ]; then
    $LOG "ERROR: QMI failed to start network"
    exit 1
fi

$LOG "Getting IP via udhcpc..."
udhcpc -i "$IFACE" -q -f -t 5 -T 3 -s /opt/piphone/scripts/udhcpc-wwan.sh

if [ $? -ne 0 ]; then
    $LOG "udhcpc failed, trying to set IP manually via AT+CGPADDR"
    IP=$(python3 -c "
import sys
sys.path.insert(0, '/opt/piphone')
from modem.at import find_modem_port, ModemDriver
import time
try:
    port = find_modem_port()
    if not port:
        sys.exit(1)
    m = ModemDriver(port=port)
    time.sleep(1)
    lines = m._cmd('AT+CGPADDR=1')
    for l in lines:
        if l.startswith('+CGPADDR'):
            parts = l.split(',')
            if len(parts) >= 2:
                print(parts[1].strip())
    m.close()
except Exception:
    sys.exit(1)
" 2>/dev/null)

    if [ -n "$IP" ] && [ "$IP" != "0.0.0.0" ]; then
        $LOG "Got IP from modem: $IP, setting manually"
        ip addr flush dev "$IFACE" 2>/dev/null || true
        ip addr add "$IP/32" dev "$IFACE"
    else
        $LOG "ERROR: Could not get IP"
        exit 1
    fi
fi

IP=$(ip addr show "$IFACE" | grep 'inet ' | awk '{print $2}' | cut -d/ -f1)
if [ -n "$IP" ]; then
    $LOG "Connected: $IP on $IFACE"
    curl -s -X POST http://localhost:5000/api/modem/data-up \
        -H "Content-Type: application/json" \
        -d "{\"ip\":\"$IP\",\"iface\":\"$IFACE\"}" 2>/dev/null || true
else
    $LOG "ERROR: No IP assigned"
    exit 1
fi
