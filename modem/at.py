"""
modem/at.py — real SIM7600G-H driver over ttyUSB2
Drop-in replacement for modem/stub.py — same public interface.
"""

import serial, threading, time, queue, json, uuid, logging

log = logging.getLogger("modem.at")


class ModemDriver:
    def __init__(self, port="/dev/ttyUSB2", baudrate=115200, timeout=5):
        self._port     = port
        self._baudrate = baudrate
        self._timeout  = timeout
        self._lock     = threading.Lock()
        self._subscribers = []
        self._sub_lock = threading.Lock()
        self._call_state = {"active": False, "number": None,
                            "direction": None, "started": None}
        self._inbox    = []

        self._ser = serial.Serial(port, baudrate, timeout=1)
        self._init_modem()
        self._load_inbox()

        # background URC reader
        self._running = True
        threading.Thread(target=self._urc_loop, daemon=True).start()
        threading.Thread(target=self._poll_loop, daemon=True).start()

    # ── init ──────────────────────────────────────────────────────────────────

    def _init_modem(self):
        self._cmd("ATE0")           # echo off
        self._cmd("AT+CMEE=2")      # verbose errors
        self._cmd('AT+CMGF=1')      # text mode SMS
        self._cmd('AT+CNMI=2,1,0,0,0')  # notify on new SMS (no auto-deliver to TE)
        self._cmd('AT+CLIP=1')      # caller ID on incoming calls
        log.info("Modem initialized on %s", self._port)

    # ── raw AT ───────────────────────────────────────────────────────────────

    def _cmd(self, command: str, wait=True) -> list[str]:
        """Send an AT command, return response lines. Thread-safe."""
        with self._lock:
            self._ser.reset_input_buffer()
            self._ser.write((command + "\r").encode())
            if not wait:
                return []
            return self._read_response()

    def _read_response(self, timeout=5) -> list[str]:
        lines = []
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = self._ser.readline().decode(errors="replace").strip()
            if not line:
                continue
            lines.append(line)
            if line in ("OK", "ERROR", "NO CARRIER", "BUSY", "NO ANSWER") \
                    or line.startswith("+CMS ERROR") \
                    or line.startswith("+CME ERROR"):
                break
        return lines

    def _ok(self, lines: list[str]) -> bool:
        return any(l == "OK" for l in lines)

    # ── SMS ──────────────────────────────────────────────────────────────────

    def _load_inbox(self):
        """Read all stored SMS from SIM/modem into self._inbox."""
        lines = self._cmd('AT+CMGL="ALL"', wait=True)
        self._inbox = self._parse_cmgl(lines)
        log.info("Loaded %d messages", sum(len(t["messages"]) for t in self._inbox))

    def _parse_cmgl(self, lines: list[str]) -> list:
        """
        Parse AT+CMGL="ALL" output into thread list.
        Each +CMGL line: +CMGL: <index>,<stat>,<oa/da>,[<alpha>],[<scts>]
        Followed by the message text.
        """
        threads_map = {}   # number → thread dict
        i = 0
        while i < len(lines):
            line = lines[i]
            if line.startswith("+CMGL:"):
                parts = line[7:].split(",")
                idx   = parts[0].strip()
                stat  = parts[1].strip().strip('"')   # "REC UNREAD" etc.
                number = parts[2].strip().strip('"')
                try:
                    scts = parts[4].strip().strip('"') if len(parts) > 4 else ""
                    ts   = scts[6:11] if len(scts) >= 11 else ""  # HH:MM from YY/MM/DD,HH:MM
                except Exception:
                    ts = ""
                i += 1
                text = lines[i].strip() if i < len(lines) else ""
                direction = "in" if "REC" in stat else "out"
                unread    = stat == "REC UNREAD"

                if number not in threads_map:
                    threads_map[number] = {
                        "id":        number,
                        "thread_id": number,
                        "name":      number,
                        "number":    number,
                        "color":     "#1d4ed8",
                        "messages":  [],
                        "unread":    0,
                    }
                threads_map[number]["messages"].append({
                    "id":     idx,
                    "dir":    direction,
                    "text":   text,
                    "ts":     ts,
                    "status": "" if direction == "in" else "delivered",
                })
                if unread:
                    threads_map[number]["unread"] += 1
            i += 1

        return list(threads_map.values())

    def get_inbox(self) -> list:
        with self._lock:
            import copy
            return copy.deepcopy(self._inbox)

    def send_sms(self, number: str, text: str) -> dict:
        now = time.strftime("%H:%M")
        # AT+CMGS initiates, then we send text + ctrl-z
        with self._lock:
            self._ser.reset_input_buffer()
            self._ser.write(f'AT+CMGS="{number}"\r'.encode())
            # wait for '>' prompt
            deadline = time.time() + 5
            buf = ""
            while time.time() < deadline:
                ch = self._ser.read(1).decode(errors="replace")
                buf += ch
                if ">" in buf:
                    break
            else:
                return {"ok": False, "error": "No > prompt from modem"}

            self._ser.write((text + "\x1a").encode())  # text + ctrl-z
            lines = self._read_response(timeout=10)

        if not self._ok(lines):
            err = next((l for l in lines if "ERROR" in l), "unknown error")
            return {"ok": False, "error": err}

        # parse +CMGS: <mr> for message reference
        mr_line = next((l for l in lines if l.startswith("+CMGS:")), "")
        msg_id  = mr_line.split(":")[-1].strip() if mr_line else str(uuid.uuid4())

        msg = {"id": msg_id, "dir": "out", "text": text,
               "ts": now, "status": "delivered"}

        with self._lock:
            thread = next((t for t in self._inbox if t["number"] == number), None)
            if thread:
                thread["messages"].append(msg)
            else:
                self._inbox.insert(0, {
                    "id": number, "thread_id": number,
                    "name": number, "number": number,
                    "color": "#1d4ed8", "messages": [msg], "unread": 0,
                })

        return {"ok": True, "id": msg_id, "ts": now}

    def delete_sms(self, msg_id: str) -> dict:
        lines = self._cmd(f"AT+CMGD={msg_id}")
        return {"ok": self._ok(lines)}

    # ── Voice ─────────────────────────────────────────────────────────────────

    def dial(self, number: str) -> dict:
        lines = self._cmd(f"ATD{number};")   # semicolon = voice call
        if self._ok(lines):
            self._call_state = {"active": True, "number": number,
                                "direction": "out", "started": time.time()}
            return {"ok": True, "number": number}
        return {"ok": False, "error": lines[-1] if lines else "no response"}

    def hangup(self) -> dict:
        lines = self._cmd("ATH")
        self._call_state = {"active": False, "number": None,
                            "direction": None, "started": None}
        return {"ok": self._ok(lines)}

    def answer(self) -> dict:
        lines = self._cmd("ATA")
        if self._ok(lines):
            self._call_state["started"] = time.time()
        return {"ok": self._ok(lines)}

    def call_status(self) -> dict:
        cs = dict(self._call_state)
        if cs["active"] and cs["started"]:
            cs["duration"] = int(time.time() - cs["started"])
        return cs

    # ── Modem info ────────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        creg  = self._cmd("AT+CREG?")
        csq   = self._cmd("AT+CSQ")
        cops  = self._cmd("AT+COPS?")
        cimi  = self._cmd("AT+CIMI")

        # parse +CSQ: <rssi>,<ber>
        rssi_raw = 99
        for l in csq:
            if l.startswith("+CSQ:"):
                try: rssi_raw = int(l.split(":")[1].split(",")[0].strip())
                except: pass

        # convert to dBm: dBm = -113 + 2*rssi
        dbm   = -113 + 2 * rssi_raw if rssi_raw < 99 else None
        bars  = max(0, min(5, (rssi_raw * 5) // 31)) if rssi_raw < 99 else 0

        # parse +COPS: 0,0,"019 Mobile",7
        operator, rat_code = "Unknown", 7
        for l in cops:
            if l.startswith("+COPS:"):
                parts = l[6:].split(",")
                if len(parts) >= 3:
                    operator = parts[2].strip().strip('"')
                if len(parts) >= 4:
                    try: rat_code = int(parts[3].strip())
                    except: pass

        rat_map = {0:"GSM", 2:"UTRAN", 3:"GSM/EDGE", 4:"UTRAN/HSDPA",
                   7:"LTE", 11:"NR", 13:"LTE/NR"}
        rat = rat_map.get(rat_code, str(rat_code))

        # parse registration
        registered = any("+CREG: 0,1" in l or "+CREG: 0,5" in l for l in creg)
        roaming    = any("+CREG: 0,5" in l for l in creg)

        # IMSI → phone number not directly available; use CIMI as identifier
        imsi = next((l for l in cimi if l.isdigit() or (l[:3].isdigit())), "")

        return {
            "ok": True,
            "signal_rssi": dbm,
            "signal_bars": bars,
            "operator": operator,
            "rat": rat,
            "registered": registered,
            "roaming": roaming,
            "imei": "",
            "sim_number": imsi,
            "dummy": False,
        }

    def ussd(self, code: str) -> dict:
        lines = self._cmd(f'AT+CUSD=1,"{code}",15', wait=True)
        resp  = next((l for l in lines if l.startswith("+CUSD:")), "")
        return {"ok": True, "response": resp}

    # ── SSE subscriptions ─────────────────────────────────────────────────────

    def subscribe(self):
        q = queue.Queue()
        with self._sub_lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q):
        with self._sub_lock:
            self._subscribers = [s for s in self._subscribers if s is not q]

    def _push(self, payload: str):
        with self._sub_lock:
            for q in self._subscribers:
                try: q.put_nowait(payload)
                except queue.Full: pass

    # ── URC background reader ─────────────────────────────────────────────────

    def _urc_loop(self):
        """
        Reads unsolicited result codes (URCs) from the modem.
        Handles: +CMTI (new SMS), RING, +CLIP (caller ID), NO CARRIER.
        Runs on a daemon thread — does NOT hold self._lock (would deadlock with _cmd).
        Uses a separate serial read outside the lock window.
        """
        buf = ""
        while self._running:
            try:
                # read without holding _lock so _cmd() can still run
                ch = self._ser.read(1).decode(errors="replace")
                if not ch:
                    continue
                buf += ch
                if "\n" not in buf:
                    continue
                line = buf.strip()
                buf  = ""

                if not line:
                    continue

                log.debug("URC: %s", line)

                # new SMS stored: +CMTI: "SM",3
                if line.startswith("+CMTI:"):
                    parts = line.split(",")
                    idx   = parts[-1].strip()
                    self._handle_new_sms(idx)

                # incoming call ring
                elif line == "RING":
                    self._call_state["active"]    = True
                    self._call_state["direction"] = "in"
                    self._push(json.dumps({"type": "ring", "from": "unknown"}))

                # caller ID
                elif line.startswith("+CLIP:"):
                    number = line.split('"')[1] if '"' in line else ""
                    self._call_state["number"] = number
                    self._push(json.dumps({"type": "ring", "from": number}))

                # call ended
                elif line == "NO CARRIER":
                    self._call_state = {"active": False, "number": None,
                                        "direction": None, "started": None}
                    self._push(json.dumps({"type": "hangup"}))

            except Exception as e:
                log.warning("URC loop error: %s", e)
                time.sleep(1)

    def _handle_new_sms(self, idx: str):
        """Fetch a single SMS by index and add to inbox."""
        lines = self._cmd(f"AT+CMGR={idx}")
        if not lines:
            return
        # +CMGR: "REC UNREAD","+972501234567",,"24/01/15,18:42:00+08"
        header = next((l for l in lines if l.startswith("+CMGR:")), "")
        text   = next((l for l in lines
                       if l and not l.startswith("+CMGR:") and l != "OK"), "")
        if not header:
            return
        parts  = header[7:].split(",")
        number = parts[1].strip().strip('"') if len(parts) > 1 else "unknown"
        ts     = time.strftime("%H:%M")

        msg = {"id": idx, "dir": "in", "text": text, "ts": ts, "status": ""}

        with self._lock:
            thread = next((t for t in self._inbox if t["number"] == number), None)
            if thread:
                thread["messages"].append(msg)
                thread["unread"] = thread.get("unread", 0) + 1
            else:
                self._inbox.insert(0, {
                    "id": number, "thread_id": number,
                    "name": number, "number": number,
                    "color": "#059669", "messages": [msg], "unread": 1,
                })

        self._push(json.dumps({
            "type": "sms", "from": number, "name": number, "preview": text
        }))


    def _poll_loop(self):
        """Fallback: poll for unread SMS every 10s in case URC is missed."""
        while self._running:
            time.sleep(10)
            try:
                lines = self._cmd('AT+CMGL="REC UNREAD"')
                msgs  = self._parse_cmgl(lines)
                for t in msgs:
                    for m in t["messages"]:
                        # check if already in inbox
                        with self._lock:
                            existing = next((x for x in self._inbox
                                             if x["number"]==t["number"]), None)
                            already  = existing and any(
                                x["id"]==m["id"] for x in existing["messages"])
                        if not already:
                            self._handle_new_sms(m["id"])
            except Exception as e:
                log.warning("Poll error: %s", e)
    def close(self):
        self._running = False
        self._ser.close()
