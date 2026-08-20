#!/bin/bash
# fix-mm-simtech-port.sh — free ttyUSB3 for piphone's exclusive AT use.
#
# ModemManager's SIM7600 (product 9001) udev rules tag ttyUSB3 as
# ID_MM_PORT_TYPE_AT_SECONDARY, which means MM periodically polls it
# for AT commands (registration, signal strength, etc). This causes
# intermittent silence/garbled responses when piphone's own AT driver
# also tries to use that port — MM and piphone fight over the same
# serial line.
#
# Fix: change ttyUSB3's tag from AT_SECONDARY to PORT_IGNORE in MM's
# own rules file, so MM leaves it alone entirely. MM still gets
# ttyUSB2 (AT_PRIMARY) for its own AT needs; piphone uses ttyUSB3
# exclusively.
#
# This patches a package-managed file (/lib/udev/rules.d/), so it
# will be REVERTED by any `apt upgrade modemmanager`. Re-run this
# script after any ModemManager package update.

set -e

RULES_FILE="/lib/udev/rules.d/77-mm-simtech-port-types.rules"
OLD_LINE='ATTRS{idVendor}=="1e0e", ATTRS{idProduct}=="9001", ENV{.MM_USBIFNUM}=="03", SUBSYSTEM=="tty", ENV{ID_MM_PORT_TYPE_AT_SECONDARY}="1"'
NEW_LINE='ATTRS{idVendor}=="1e0e", ATTRS{idProduct}=="9001", ENV{.MM_USBIFNUM}=="03", ENV{ID_MM_PORT_IGNORE}="1"'

if [ ! -f "$RULES_FILE" ]; then
    echo "ERROR: $RULES_FILE not found — is ModemManager installed?"
    exit 1
fi

if grep -qF "$NEW_LINE" "$RULES_FILE"; then
    echo "Already patched, nothing to do."
else
    if ! grep -qF "$OLD_LINE" "$RULES_FILE"; then
        echo "WARNING: expected original line not found — MM package"
        echo "version may differ. Check $RULES_FILE manually against"
        echo "scripts/77-mm-simtech-port-types.rules.patch"
        exit 1
    fi
    cp "$RULES_FILE" "${RULES_FILE}.piphone-backup"
    sed -i "s|$OLD_LINE|$NEW_LINE|" "$RULES_FILE"
    echo "Patched $RULES_FILE (backup at ${RULES_FILE}.piphone-backup)"
fi

udevadm control --reload-rules
udevadm trigger --action=add --subsystem-match=tty
systemctl restart ModemManager

sleep 3
echo ""
echo "Verifying — ttyUSB3 should NOT appear below (only ttyUSB2):"
lsof /dev/ttyUSB2 /dev/ttyUSB3 2>&1
