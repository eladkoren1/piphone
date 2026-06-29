#!/bin/bash
# wwan-up.sh — bring up SIM7600G-H data connection

LOG="logger -t wwan-up"
WDM="/dev/cdc-wdm0"
IFACE="wwan0"
APN="internet"

$LOG "Starting wwan0 data connection..."

# clean up any stale QMI state from previous session
rm -f /tmp/qmi-network-state-cdc-wdm0
$LOG "Cleared stale QMI state"

# wait for cdc-wdm0 to appear (up to 20s)
for i in $(seq 1 20); do
    [ -c "$WDM" ] && break
    $LOG "Waiting for $WDM... ($i/20)"
    sleep 1
done

if [ ! -c "$WDM" ]; then
    $LOG "ERROR: $WDM not found, aborting"
    exit 1
fi

# wait for modem to register (up to 30s)
for i in $(seq 1 15); do
    qmicli -d "$WDM" --nas-get-signal-strength &>/dev/null && break
    $LOG "Waiting for signal... ($i/15)"
    sleep 2
done

# set raw-ip mode
$LOG "Setting raw-ip mode..."
ip link set "$IFACE" down 2>/dev/null || true
echo Y > /sys/class/net/$IFACE/qmi/raw_ip
ip link set "$IFACE" up

# start QMI network — use qmicli directly, no cached state
$LOG "Starting QMI network (APN=$APN)..."
qmicli -p -d "$WDM" \
    --device-open-net='net-raw-ip|net-no-qos-header' \
    --wds-start-network="apn='$APN',ip-type=4" \
    --client-no-release-cid

if [ $? -ne 0 ]; then
    $LOG "ERROR: QMI failed to start network"
    exit 1
fi

# get IP via DHCP
$LOG "Getting IP via udhcpc..."
udhcpc -i "$IFACE" -q -f -t 5 -T 3

if [ $? -ne 0 ]; then
    $LOG "udhcpc failed, trying to set IP manually via AT+CGPADDR"
    # fallback: get IP from modem directly via AT command
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
except Exception as e:
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

# set wwan0 as preferred default route (metric 100 < wlan0 metric 600)
ip route del default dev "$IFACE" 2>/dev/null || true
GW=$(ip route show dev "$IFACE" | grep default | awk '{print $3}' | head -1)
if [ -n "$GW" ]; then
    ip route add default via "$GW" dev "$IFACE" metric 100
else
    ip route add default dev "$IFACE" metric 100
fi

# add DNS for wwan0
$LOG "Adding wwan0 DNS..."
RESOLV=$(readlink -f /etc/resolv.conf)
$LOG "resolv.conf -> $RESOLV"
if [[ "$RESOLV" == *"systemd"* ]] || [[ "$RESOLV" == *"stub"* ]]; then
    # systemd-resolved: add via resolvectl
    resolvectl dns "$IFACE" 8.8.8.8 8.8.4.4 2>/dev/null &&         $LOG "DNS set via resolvectl" ||         $LOG "resolvectl failed, falling back"
fi
# always also write directly (works for both NM and systemd-resolved)
sed -i '/# wwan0-dns/d' /etc/resolv.conf 2>/dev/null || true
printf "nameserver 8.8.8.8 # wwan0-dns
nameserver 8.8.4.4 # wwan0-dns
"     >> /etc/resolv.conf 2>/dev/null || true
# tell NM to not overwrite resolv.conf for wwan0
nmcli dev set "$IFACE" managed no 2>/dev/null || true

# verify
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
