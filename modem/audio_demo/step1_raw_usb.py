"""
step1_raw_usb.py — verify we can claim the SIM7600's audio USB interface
and see raw bytes flowing when a call is active.

No audio playback yet — just proves the USB mechanism works.

Usage:
    1. In one terminal: python3 modem/debug_cli.py  -> option 11, dial a number
    2. Once the call connects (or answer an incoming call), run this script
    3. Watch for byte counts increasing on EP 0x8a (audio FROM modem)

Run as root (or with udev rules granting USB access).
"""

import usb.core
import usb.util
import time
import sys

VENDOR_ID  = 0x1e0e
PRODUCT_ID = 0x9001

# from our lsusb -v findings
AUDIO_INTERFACE = 5
EP_IN  = 0x8a   # bulk IN  — audio FROM modem (remote party's voice)
EP_OUT = 0x06   # bulk OUT — audio TO modem (your mic)
EP_INT = 0x8b   # interrupt IN — status


def find_device():
    dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
    if dev is None:
        print("Modem not found on USB")
        sys.exit(1)
    return dev


def main():
    dev = find_device()
    print(f"Found device: {dev.idVendor:04x}:{dev.idProduct:04x}")

    # detach kernel driver if somehow attached (shouldn't be, per our lsusb check)
    if dev.is_kernel_driver_active(AUDIO_INTERFACE):
        print(f"Interface {AUDIO_INTERFACE} has a kernel driver attached — detaching")
        dev.detach_kernel_driver(AUDIO_INTERFACE)
    else:
        print(f"Interface {AUDIO_INTERFACE} is free (no kernel driver) — good")

    # claim the interface
    usb.util.claim_interface(dev, AUDIO_INTERFACE)
    print(f"Claimed interface {AUDIO_INTERFACE}")

    print("\nListening for PCM bytes on EP 0x8a (audio FROM modem)...")
    print("Make sure a call is ACTIVE and you've sent AT+CPCMREG=1")
    print("(use debug_cli.py option 15 to send it manually for this test)")
    print("Press Ctrl+C to stop\n")

    total_bytes = 0
    reads = 0
    t0 = time.time()

    try:
        while True:
            try:
                data = dev.read(EP_IN, 512, timeout=2000)
                total_bytes += len(data)
                reads += 1
                if reads % 20 == 0:
                    elapsed = time.time() - t0
                    rate = total_bytes / elapsed if elapsed > 0 else 0
                    print(f"  reads={reads} total_bytes={total_bytes} "
                         f"rate={rate:.0f} B/s  last_chunk={len(data)}B "
                         f"sample={bytes(data[:8]).hex()}")
            except usb.core.USBTimeoutError:
                print("  ... no data (timeout) — is the call active + CPCMREG=1 sent?")
            except usb.core.USBError as e:
                print(f"  USB error: {e}")
                time.sleep(1)

    except KeyboardInterrupt:
        print(f"\nStopped. Total: {reads} reads, {total_bytes} bytes")

    finally:
        usb.util.release_interface(dev, AUDIO_INTERFACE)
        print("Released interface")


if __name__ == "__main__":
    main()
