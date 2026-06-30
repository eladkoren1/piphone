#!/bin/sh
# udhcpc hook for wwan0 — IP configuration only.
# Does NOT touch any routes. Routing is managed manually:
#   ip route add 0.0.0.0/0 via <gateway> dev wwan0 metric 200

case "$1" in
    bound|renew)
        ip addr flush dev "$interface" 2>/dev/null
        ip addr add "$ip/${mask:-32}" dev "$interface"
        ;;
    deconfig)
        ip addr flush dev "$interface" 2>/dev/null
        ;;
esac
exit 0
