"""
modem/debug_cli.py — interactive CLI for testing ModemDriver.
Run directly in PyCharm debugger or terminal.
Set breakpoints anywhere in at.py and this will trigger them.

Usage:
    python modem/debug_cli.py
    python modem/debug_cli.py --port /dev/ttyUSB3
"""

import sys, os, time, argparse, logging

# allow running from project root or from modem/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(threadName)s] %(name)s: %(message)s"
)

from modem.at import ModemDriver

# ── colour helpers (degrade gracefully on Windows) ───────────────────────────
try:
    from colorama import Fore, Style, init as colorama_init
    colorama_init()
    def ok(s):    return Fore.GREEN  + s + Style.RESET_ALL
    def err(s):   return Fore.RED    + s + Style.RESET_ALL
    def info(s):  return Fore.CYAN   + s + Style.RESET_ALL
    def warn(s):  return Fore.YELLOW + s + Style.RESET_ALL
except ImportError:
    def ok(s):   return s
    def err(s):  return s
    def info(s): return s
    def warn(s): return s

MENU = """
╔══════════════════════════════════╗
║        piphone debug CLI         ║
╠══════════════════════════════════╣
║  1  AT ping (AT)                 ║
║  2  Signal quality (AT+CSQ)      ║
║  3  Registration (AT+CREG?)      ║
║  4  Operator (AT+COPS?)          ║
║  5  IMSI (AT+CIMI)               ║
║  6  Full modem status            ║
║──────────────────────────────────║
║  7  List inbox                   ║
║  8  Send SMS                     ║
║  9  Read SMS by index            ║
║  10 Delete SMS by index          ║
║──────────────────────────────────║
║  11 Dial number                  ║
║  12 Hang up                      ║
║  13 Answer call                  ║
║  14 Call status                  ║
║──────────────────────────────────║
║  15 Raw AT command               ║
║  16 USSD                         ║
║──────────────────────────────────║
║  0  Exit                         ║
╚══════════════════════════════════╝
"""

def prompt(label, default=None):
    suffix = f" [{default}]" if default else ""
    val = input(info(f"  {label}{suffix}: ")).strip()
    return val if val else default

def pprint_result(result):
    if isinstance(result, dict):
        for k, v in result.items():
            color = ok if k == "ok" and v is True else (err if k == "ok" and v is False else info)
            print(f"  {color(str(k))}: {v}")
    elif isinstance(result, list):
        if not result:
            print(warn("  (empty)"))
        for item in result:
            if isinstance(item, dict):
                print()
                pprint_result(item)
            else:
                print(f"  {item}")
    else:
        print(f"  {result}")

def run(modem: ModemDriver):
    print(ok("\n✓ Modem connected\n"))

    while True:
        print(MENU)
        choice = input(info("Choice: ")).strip()

        try:
            if choice == "0":
                print(warn("Goodbye."))
                modem.close()
                break

            elif choice == "1":
                lines = modem._cmd("AT")
                print(ok("OK") if "OK" in lines else err("No response"))
                print(lines)

            elif choice == "2":
                lines = modem._cmd("AT+CSQ")
                print(lines)

            elif choice == "3":
                lines = modem._cmd("AT+CREG?")
                print(lines)

            elif choice == "4":
                lines = modem._cmd("AT+COPS?")
                print(lines)

            elif choice == "5":
                lines = modem._cmd("AT+CIMI")
                print(lines)

            elif choice == "6":
                print(info("\n  Fetching status..."))
                result = modem.get_status()
                pprint_result(result)

            elif choice == "7":
                threads = modem.get_inbox()
                if not threads:
                    print(warn("  Inbox empty"))
                for t in threads:
                    print(f"\n  [{t['number']}] {len(t['messages'])} messages, {t['unread']} unread")
                    for m in t["messages"]:
                        arrow = ok("→") if m["dir"] == "out" else info("←")
                        print(f"    {arrow} [{m['id']}] {m['ts']}  {m['text'][:60]}")

            elif choice == "8":
                number = prompt("To (e.g. 054...)")
                if not number:
                    print(warn("  Cancelled"))
                    continue
                text = prompt("Message")
                if not text:
                    print(warn("  Cancelled"))
                    continue
                print(info("  Sending..."))
                result = modem.send_sms(number, text)
                pprint_result(result)

            elif choice == "9":
                idx = prompt("Message index")
                lines = modem._cmd(f"AT+CMGR={idx}")
                print(lines)

            elif choice == "10":
                idx = prompt("Message index to delete")
                result = modem.delete_sms(idx)
                pprint_result(result)

            elif choice == "11":
                number = prompt("Number to dial")
                if not number:
                    print(warn("  Cancelled"))
                    continue
                result = modem.dial(number)
                pprint_result(result)

            elif choice == "12":
                result = modem.hangup()
                pprint_result(result)

            elif choice == "13":
                result = modem.answer()
                pprint_result(result)

            elif choice == "14":
                result = modem.call_status()
                pprint_result(result)

            elif choice == "15":
                cmd = prompt("AT command")
                if not cmd:
                    continue
                t0    = time.time()
                lines = modem._cmd(cmd, timeout=15)
                ms    = int((time.time() - t0) * 1000)
                print(f"  ({ms}ms) {lines}")

            elif choice == "16":
                code = prompt("USSD code (e.g. *100#)")
                result = modem.ussd(code)
                pprint_result(result)

            else:
                print(warn("  Unknown option"))

        except KeyboardInterrupt:
            print(warn("\n  Interrupted"))
        except Exception as e:
            print(err(f"  Error: {e}"))
            import traceback; traceback.print_exc()

        input(info("\n  [Enter to continue]"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="piphone modem debug CLI")
    parser.add_argument("--port", default=None,
                        help="Serial port (default: auto-detect)")
    args = parser.parse_args()

    print(info("Connecting to modem..."))
    try:
        modem = ModemDriver(port=args.port)
    except Exception as e:
        print(err(f"Failed to connect: {e}"))
        sys.exit(1)

    run(modem)
