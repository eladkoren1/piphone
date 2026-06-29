#!/bin/bash
# wwan-up.sh — bring up SIM7600G-H data connection
# Called by udev on modem plug-in, or manually

LOG="logger -t wwan-up"
WDM="/dev/cdc-wdm0"
IFACE="wwan0"
APN="internet"

$LOG "Starting wwan0 data connection..."

# wait for cdc-wdm0 to appear (up to 15s)
for i in $(seq 1 15); do
    [ -c "$WDM" ] && break
    $LOG "Waiting for $WDM... ($i/15)"
    sleep 1
done

if [ ! -c "$WDM" ]; then
    $LOG "ERROR: $WDM not found, aborting"
    exit 1
fi

# wait for modem to register on network
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

# start QMI network
$LOG "Starting QMI network (APN=$APN)..."
qmicli -p -d "$WDM" \
    --device-open-net='net-raw-ip|net-no-qos-header' \
    --wds-start-network="apn='$APN',ip-type=4" \
    --client-no-release-cid

# get IP via DHCP
$LOG "Getting IP via udhcpc..."
udhcpc -i "$IFACE" -q -f

# set wwan0 default route with lower metric than wlan0 (600)
# so wwan0 is preferred but wlan0 takes over when wwan0 is down
$LOG "Setting wwan0 default route with metric 100..."
ip route del default dev "$IFACE" 2>/dev/null || true
GW=$(ip route show dev "$IFACE" | grep default | awk '{print $3}' | head -1)
if [ -n "$GW" ]; then
    ip route add default via "$GW" dev "$IFACE" metric 100
else
    ip route add default dev "$IFACE" metric 100
fi

# verify and notify piphone
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
