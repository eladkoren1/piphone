"""
cpcmreg_helper.py — manually send AT+CPCMREG=1/0 for testing.

NOTE: modem/at.py does not yet recognize "VOICE CALL: BEGIN"/"END" as
URCs (only RING/CLIP/NO CARRIER are handled today), so this is manual-
only for now. Automatic CPCMREG on call-connect is a later step once
the raw USB mechanism itself is verified.

Run this in a separate terminal from step1_raw_usb.py. It uses piphone's
own ModemDriver (ttyUSB2 AT port) so it won't conflict with step1,
which only touches the separate USB audio interface (interface 5).

Usage:
    python3 cpcmreg_helper.py
    1. dial or answer a call from the phone app / debug_cli first
    2. once VOICE CALL: BEGIN appears in the piphone app log, press Enter here
"""

import sys, os, argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from modem.at import ModemDriver


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default=None)
    args = parser.parse_args()

    print("Connecting to modem...")
    modem = ModemDriver(port=args.port)
    print("Connected.\n")

    input("Call should already be ACTIVE (dialed/answered elsewhere).\n"
         "Press Enter to send AT+CPCMREG=1 (start PCM stream)...")
    r = modem._cmd("AT+CPCMREG=1")
    print(f"  -> {r}")

    print("\nPCM should now be flowing. Switch to step1_raw_usb.py's "
         "terminal to watch for bytes.")
    input("\nPress Enter when done to send AT+CPCMREG=0 (stop)...")
    r = modem._cmd("AT+CPCMREG=0")
    print(f"  -> {r}")

    modem.close()


if __name__ == "__main__":
    main()
