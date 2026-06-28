"""
modem/at.py — SIM7600G-H driver with proper single-reader architecture.

One thread owns the serial port exclusively (_reader_loop).
All AT commands go through _cmd() which posts a request and waits for
the reader to collect the response. URCs are handled in the same loop.
No more races between _cmd() and _urc_loop().
"""

import serial, threading, time, queue, json, uuid, logging

log = logging.getLogger("modem.at")


class ModemDriver:
    def __init__(self, port="/dev/ttyUSB2", baudrate=115200):
        self._port     = port
        self._baudrate = baudrate
        self._lock     = threading.Lock()          # protects _inbox, _call_state
        self._sub_lock = threading.Lock()          # protects _subscribers

        self._subscribers = []
        self._call_state  = {"active": False, "number": None,
                             "direction": None, "started": None}
        self._inbox       = []

        # cmd queue: each item is (command_str, response_queue)
        self._cmd_queue = queue.Queue()
        # currently pending response collector (set by reader loop)
        self._pending   = None   # (expected_end, response_queue, lines_so_far)

        self._ser = serial.Serial(port, baudrate, timeout=0.1)
        self._running = True

        # single reader thread — owns the port exclusively
        self._reader = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader.start()

        self._init_modem()
        self._load_inbox()

        # poll thread as fallback for missed URCs
        threading.Thread(target=self._poll_loop, daemon=True).start()

    # ── raw serial: single reader owns the port ───────────────────────────────

    def _reader_loop(self):
        """
        Sole reader of self._ser. Handles two modes:
        - Idle: watch for URCs line by line
        - Collecting: gather response lines for a pending _cmd() call
        """
        buf = b""
        collecting = False
        resp_q     = None
        resp_lines = []

        while self._running:
            try:
                # check for a new command request
                if not collecting:
                    try:
                        cmd_str, resp_q = self._cmd_queue.get_nowait()
                        self._ser.reset_input_buffer()
                        self._ser.write((cmd_str + "\r").encode())
                        resp_lines  = []
                        collecting  = True
                    except queue.Empty:
                        pass

                chunk = self._ser.read(256)
                if not chunk:
                    continue

                buf += chunk
                while b"\n" in buf:
                    line_b, buf = buf.split(b"\n", 1)
                    line = line_b.decode(errors="replace").strip()
                    if not line:
                        continue

                    if collecting:
                        resp_lines.append(line)
                        if self._is_final(line):
                            resp_q.put(resp_lines)
                            collecting = False
                            resp_q     = None
                            resp_lines = []
                    else:
                        # URC
                        self._handle_urc(line)

            except serial.SerialException as e:
                log.error("Serial error: %s — retrying in 3s", e)
                time.sleep(3)
                try:
                    self._ser.close()
                    self._ser = serial.Serial(self._port, self._baudrate, timeout=0.1)
                    log.info("Reconnected to %s", self._port)
                    self._init_modem()
                except Exception as e2:
                    log.error("Reconnect failed: %s", e2)
            except Exception as e:
                log.warning("Reader loop error: %s", e)

    def _is_final(self, line: str) -> bool:
        return line in ("OK", "ERROR", "NO CARRIER", "BUSY",
                        "NO ANSWER", "NO DIALTONE") \
               or line.startswith("+CMS ERROR") \
               or line.startswith("+CME ERROR") \
               or line.startswith(">")          # prompt for CMGS

    def _cmd(self, command: str, timeout: float = 8.0) -> list[str]:
        """Send AT command, block until response. Thread-safe."""
        resp_q = queue.Queue()
        self._cmd_queue.put((command, resp_q))
        try:
            return resp_q.get(timeout=timeout)
        except queue.Empty:
            log.warning("Timeout waiting for response to: %s", command)
            return []

    def _ok(self, lines: list[str]) -> bool:
        return any(l == "OK" for l in lines)

    # ── URC handler (called from reader loop, no lock needed for serial) ──────

    def _handle_urc(self, line: str):
        log.debug("URC: %s", line)

        if line.startswith("+CMTI:"):
            # new SMS stored: +CMTI: "SM",3
            idx = line.split(",")[-1].strip()
            threading.Thread(target=self._handle_new_sms,
                             args=(idx,), daemon=True).start()

        elif line == "RING":
            with self._lock:
                self._call_state["active"]    = True
                self._call_state["direction"] = "in"
            self._push(json.dumps({"type": "ring", "from": "unknown"}))

        elif line.startswith("+CLIP:"):
            number = line.split('"')[1] if '"' in line else ""
            with self._lock:
                self._call_state["number"] = number
            self._push(json.dumps({"type": "ring", "from": number}))

        elif line == "NO CARRIER":
            with self._lock:
                self._call_state = {"active": False, "number": None,
                                    "direction": None, "started": None}
            self._push(json.dumps({"type": "hangup"}))

    # ── init ──────────────────────────────────────────────────────────────────

    def _init_modem(self):
        time.sleep(0.5)
        self._cmd("ATE0")
        self._cmd("AT+CMEE=2")
        self._cmd("AT+CMGF=1")
        self._cmd("AT+CNMI=2,1,0,0,0")
        self._cmd("AT+CLIP=1")
        log.info("Modem initialized on %s", self._port)

    # ── SMS ───────────────────────────────────────────────────────────────────

    def _load_inbox(self):
        lines = self._cmd('AT+CMGL="ALL"', timeout=15)
        with self._lock:
            self._inbox = self._parse_cmgl(lines)
        log.info("Loaded %d threads from modem",  len(self._inbox))

    def _parse_cmgl(self, lines: list[str]) -> list:
        threads_map = {}
        i = 0
        while i < len(lines):
            line = lines[i]
            if line.startswith("+CMGL:"):
                parts  = line[7:].split(",")
                idx    = parts[0].strip()
                stat   = parts[1].strip().strip('"')
                number = parts[2].strip().strip('"')
                try:
                    scts = parts[4].strip().strip('"') if len(parts) > 4 else ""
                    ts   = scts[9:14] if len(scts) >= 14 else ""
                except Exception:
                    ts = ""
                i += 1
                text      = lines[i].strip() if i < len(lines) else ""
                direction = "in" if "REC" in stat else "out"
                unread    = stat == "REC UNREAD"

                if number not in threads_map:
                    threads_map[number] = {
                        "id": number, "thread_id": number,
                        "name": number, "number": number,
                        "color": "#1d4ed8", "messages": [], "unread": 0,
                    }
                threads_map[number]["messages"].append({
                    "id": idx, "dir": direction, "text": text,
                    "ts": ts, "status": "" if direction == "in" else "delivered",
                })
                if unread:
                    threads_map[number]["unread"] += 1
            i += 1
        return list(threads_map.values())

    def get_inbox(self) -> list:
        import copy
        with self._lock:
            return copy.deepcopy(self._inbox)

    def send_sms(self, number: str, text: str) -> dict:
        now = time.strftime("%H:%M")
        resp_q = queue.Queue()
        # send AT+CMGS — reader will see '>' and put it as final line
        self._cmd_queue.put((f'AT+CMGS="{number}"', resp_q))
        try:
            lines = resp_q.get(timeout=8)
        except queue.Empty:
            return {"ok": False, "error": "No > prompt"}

        if not any(">" in l for l in lines):
            return {"ok": False, "error": "Unexpected: " + str(lines)}

        # now send the text body
        resp_q2 = queue.Queue()
        self._cmd_queue.put((text + "\x1a", resp_q2))
        try:
            lines2 = resp_q2.get(timeout=15)
        except queue.Empty:
            return {"ok": False, "error": "Send timeout"}

        if not self._ok(lines2):
            err = next((l for l in lines2 if "ERROR" in l), "unknown")
            return {"ok": False, "error": err}

        mr_line = next((l for l in lines2 if l.startswith("+CMGS:")), "")
        msg_id  = mr_line.split(":")[-1].strip() if mr_line else str(uuid.uuid4())
        msg     = {"id": msg_id, "dir": "out", "text": text,
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

    def _handle_new_sms(self, idx: str):
        lines  = self._cmd(f"AT+CMGR={idx}")
        header = next((l for l in lines if l.startswith("+CMGR:")), "")
        text   = next((l for l in lines
                       if l and not l.startswith("+CMGR:") and l != "OK"), "")
        if not header:
            return
        parts  = header[7:].split(",")
        number = parts[1].strip().strip('"') if len(parts) > 1 else "unknown"
        ts     = time.strftime("%H:%M")
        msg    = {"id": idx, "dir": "in", "text": text, "ts": ts, "status": ""}

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
        self._push(json.dumps({"type": "sms", "from": number,
                                "name": number, "preview": text}))

    # ── Voice ─────────────────────────────────────────────────────────────────

    def dial(self, number: str) -> dict:
        lines = self._cmd(f"ATD{number};")
        if self._ok(lines):
            with self._lock:
                self._call_state = {"active": True, "number": number,
                                    "direction": "out", "started": time.time()}
            return {"ok": True, "number": number}
        return {"ok": False, "error": lines[-1] if lines else "no response"}

    def hangup(self) -> dict:
        lines = self._cmd("ATH")
        with self._lock:
            self._call_state = {"active": False, "number": None,
                                "direction": None, "started": None}
        return {"ok": self._ok(lines)}

    def answer(self) -> dict:
        lines = self._cmd("ATA")
        if self._ok(lines):
            with self._lock:
                self._call_state["started"] = time.time()
        return {"ok": self._ok(lines)}

    def call_status(self) -> dict:
        with self._lock:
            cs = dict(self._call_state)
        if cs["active"] and cs["started"]:
            cs["duration"] = int(time.time() - cs["started"])
        return cs

    # ── Modem status ──────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        creg = self._cmd("AT+CREG?")
        csq  = self._cmd("AT+CSQ")
        cops = self._cmd("AT+COPS?")
        cimi = self._cmd("AT+CIMI")

        rssi_raw = 99
        for l in csq:
            if l.startswith("+CSQ:"):
                try: rssi_raw = int(l.split(":")[1].split(",")[0].strip())
                except: pass

        dbm  = -113 + 2 * rssi_raw if rssi_raw < 99 else None
        bars = max(0, min(5, (rssi_raw * 5) // 31)) if rssi_raw < 99 else 0

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

        registered = any("+CREG: 0,1" in l or "+CREG: 0,5" in l for l in creg)
        roaming    = any("+CREG: 0,5" in l for l in creg)
        imsi       = next((l for l in cimi if l[:3].isdigit()), "")

        return {"ok": True, "signal_rssi": dbm, "signal_bars": bars,
                "operator": operator, "rat": rat, "registered": registered,
                "roaming": roaming, "imei": "", "sim_number": imsi, "dummy": False}

    def ussd(self, code: str) -> dict:
        lines = self._cmd(f'AT+CUSD=1,"{code}",15', timeout=15)
        resp  = next((l for l in lines if l.startswith("+CUSD:")), "")
        return {"ok": True, "response": resp}

    # ── SSE ───────────────────────────────────────────────────────────────────

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

    # ── Poll fallback ─────────────────────────────────────────────────────────

    def _poll_loop(self):
        """Poll for unread SMS every 15s as fallback for missed URCs."""
        while self._running:
            time.sleep(15)
            try:
                lines = self._cmd('AT+CMGL="REC UNREAD"')
                msgs  = self._parse_cmgl(lines)
                for t in msgs:
                    for m in t["messages"]:
                        with self._lock:
                            existing = next((x for x in self._inbox
                                             if x["number"] == t["number"]), None)
                            already  = existing and any(
                                x["id"] == m["id"] for x in existing["messages"])
                        if not already:
                            self._handle_new_sms(m["id"])
            except Exception as e:
                log.warning("Poll error: %s", e)

    def close(self):
        self._running = False
        self._ser.close()
