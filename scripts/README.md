# Mobile data — NM/ModemManager setup (no custom scripts)

Mobile data via the SIM7600G-H is handled entirely by NetworkManager +
ModemManager. No custom scripts, systemd units, or udev rules are needed.

## One-time setup

```bash
systemctl enable --now ModemManager

nmcli connection add \
  type gsm \
  ifname '*' \
  con-name 'piphone-wwan' \
  apn 'internet' \
  connection.autoconnect yes

nmcli con mod piphone-wwan ipv4.route-metric 100
nmcli con up piphone-wwan
```

## Fallback priority

wwan0 (mobile data) is set to metric 100 — the highest priority route.
wlan0/br0/eth0 keep their normal NM-assigned metrics (typically 600+),
so they act as automatic fallback. NM adds/removes each connection's
route automatically as it connects/disconnects — no scripts needed.

```bash
nmcli con mod <connection-name> ipv4.route-metric 700
nmcli con up <connection-name>
```

## Verifying

```bash
nmcli con show --active
ip route show
mmcli -m 0
```

## raw-ip mode (important)

This modem needs raw-ip mode, not the 802-3/Ethernet mode NM/MM
defaults to. In 802-3 mode the modem advertises a synthetic gateway
that never answers ARP, so the connection shows "connected" in MM but
no traffic actually passes.

```bash
nmcli con down piphone-wwan
ip link set wwan0 down
echo Y > /sys/class/net/wwan0/qmi/raw_ip
ip link set wwan0 up
nmcli con up piphone-wwan
```

## Switching to 4G/LTE

```bash
nmcli con down piphone-wwan
mmcli -m 0 --set-allowed-modes="3g|4g" --set-preferred-mode="4g"
nmcli con up piphone-wwan
mmcli -m 0 | grep -A2 current
```

## Notes

- If `nmcli con up piphone-wwan` fails with "No valid data port found",
  the modem may still be in raw-ip mode from a previous session — reset
  as above, or the reverse if MM insists on 802-3.
- If MM gets stuck at "wait to get packet service state attached", the
  modem may need a manual attach via MM debug mode + AT+CGATT=1.
  Remove the debug override once resolved.
- piphone's own AT driver (modem/at.py) uses ttyUSB2 exclusively for
  SMS/calls and does not conflict with MM, which primarily uses
  cdc-wdm0 (QMI) for data.

## Voice audio (USB Audio interface)

SIM7600 exposes raw PCM voice audio over a dedicated, unclaimed USB
interface (not a standard USB Audio Class device — ALSA won't see it).

- Interface 5 (unclaimed by any kernel driver):
  - EP 0x8a (bulk IN) — audio FROM modem (remote party's voice)
  - EP 0x06 (bulk OUT) — audio TO modem (your mic)
  - EP 0x8b (interrupt IN) — status/notifications
- Format: 8kHz, 16-bit linear PCM (16kHz possible via AT+CPCMFRM=1)
- Protocol: on "VOICE CALL: BEGIN" URC, send AT+CPCMREG=1 to start
  streaming; on "VOICE CALL: END", send AT+CPCMREG=0 to stop.
- Access via pyusb (raw libusb bulk transfers), bridged to a USB
  sound card via ALSA/pyaudio for actual speaker/mic playback.
