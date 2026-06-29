# piphone data scripts

## Install

```bash
# copy scripts
cp wwan-up.sh wwan-down.sh /opt/piphone/scripts/
chmod +x /opt/piphone/scripts/wwan-up.sh /opt/piphone/scripts/wwan-down.sh

# install systemd service
cp wwan.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable wwan.service   # auto-start on boot

# install udev rule (auto-start on USB plug-in)
cp 99-wwan.rules /etc/udev/rules.d/
udevadm control --reload-rules
```

## Usage

```bash
# manual start/stop
systemctl start wwan.service
systemctl stop wwan.service      # airplane mode

# check status
systemctl status wwan.service
journalctl -u wwan-up -f

# one-shot (without service)
/opt/piphone/scripts/wwan-up.sh
/opt/piphone/scripts/wwan-down.sh
```

## Airplane mode from UI
Tap the Data icon on the home screen to toggle.
