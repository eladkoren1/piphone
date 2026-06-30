#!/bin/sh
# udhcpc hook for wwan0 — sole owner of wwan0's IP + default route.
# Mobile data is FALLBACK ONLY: metric 900, always higher (lower
# priority) than wired/wifi connections (typically metric 100-600).

case "$1" in
    bound|renew)
        ip addr flush dev "$interface" 2>/dev/null
        ip addr add "$ip/${mask:-32}" dev "$interface"

        ip route del default dev "$interface" 2>/dev/null || true
        if [ -n "$router" ]; then
            ip route add default via "$router" dev "$interface" metric 900
        else
            ip route add default dev "$interface" metric 900
        fi
        ;;
    deconfig)
        ip addr flush dev "$interface" 2>/dev/null
        ip route del default dev "$interface" 2>/dev/null || true
        ;;
esac
exit 0
