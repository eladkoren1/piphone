#!/bin/sh
# udhcpc hook for wwan0 — configure IP/route only, skip DNS
# udhcpc calls this with $1 = bound|renew|deconfig|leasefail

case "$1" in
    bound|renew)
        # set IP
        ip addr flush dev "$interface" 2>/dev/null
        ip addr add "$ip/${mask:-32}" dev "$interface"
        # set default route with metric 100 (lower than wlan0's 600)
        ip route del default dev "$interface" 2>/dev/null || true
        if [ -n "$router" ]; then
            ip route add default via "$router" dev "$interface" metric 100
        else
            ip route add default dev "$interface" metric 100
        fi
        ;;
    deconfig)
        ip addr flush dev "$interface" 2>/dev/null
        ip route del default dev "$interface" 2>/dev/null || true
        ;;
esac
exit 0
