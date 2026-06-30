#!/bin/sh
# udhcpc hook for wwan0.
# Does NOT touch the existing default route (e.g. br0).
# Adds a static 0.0.0.0/0 route via wwan0 at metric 900 so it only
# carries traffic when the lower-metric default (br0/wlan0) is gone.

case "$1" in
    bound|renew)
        ip addr flush dev "$interface" 2>/dev/null
        ip addr add "$ip/${mask:-32}" dev "$interface"

        # remove only our own prior 0.0.0.0/0 route via this iface, if any
        ip route del 0.0.0.0/0 dev "$interface" metric 900 2>/dev/null || true
        if [ -n "$router" ]; then
            ip route add 0.0.0.0/0 via "$router" dev "$interface" metric 900
        else
            ip route add 0.0.0.0/0 dev "$interface" metric 900
        fi
        ;;
    deconfig)
        ip addr flush dev "$interface" 2>/dev/null
        ip route del 0.0.0.0/0 dev "$interface" metric 900 2>/dev/null || true
        ;;
esac
exit 0
