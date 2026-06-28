"""
modem/at.py — SIM7600G-H driver.

Single modem thread owns the serial port exclusively.
No other thread ever calls ser.read() or ser.write().

API threads communicate via:
  _cmd_queue  : (command_str, response_queue)  → modem thread
  response_q  : list[str]                      → back to caller
"""

import serial, threading, time, queue
import json, uuid as _uuid_mod, logging
import os

STORE_FILE = os.path.join(os.path.dirname(__file__), "..", "messages.json")

log = logging.getLogger("modem.at")

FINAL = {"OK", "ERROR", "NO CARRIER", "BUSY", "NO ANSWER", "NO DIALTONE"}

def _is_final(line):
    return (line in FINAL
            or line.startswith("+CMS ERROR")
            or line.startswith("+CME ERROR"))



def find_modem_port(baudrate=115200, timeout=1.0):
    """
    Probe each /dev/ttyUSB* port and return the first one
    that responds to AT with OK. Returns None if not found.
    """
    import glob, serial, time
    candidates = sorted(glob.glob("/dev/ttyUSB*"))
    log.info("Probing ports: %s", candidates)
    for port in candidates:
        try:
            ser = serial.Serial(port, baudrate, timeout=timeout)
            time.sleep(0.2)
            ser.reset_input_buffer()
            ser.write(b"AT\r\n")
            time.sleep(0.3)
            resp = ser.read(64).decode(errors="replace")
            ser.close()
            if "OK" in resp:
                log.info("Modem found on %s", port)
                return port
            else:
                log.debug("%s → no OK (got %r)", port, resp)
        except Exception as e:
            log.debug("%s → %s", port, e)
    return None

class ModemDriver:
    def __init__(self, port=None, baudrate=115200):
        if port is None:
            port = find_modem_port(baudrate)
            if port is None:
                raise RuntimeError("No modem found on any /dev/ttyUSB* port")
        self._port     = port
        self._baudrate = baudrate

        # all commands go through here — (cmd_str, resp_queue)
        self._cmd_queue = queue.Queue()

        # data / subscriber guards
        self._data_lock = threading.Lock()
        self._sub_lock  = threading.Lock()

        self._subscribers = []
        self._call_state  = {"active": False, "number": None,
                             "direction": None, "started": None}
        self._inbox = []
        self._running = True

        self._ser = serial.Serial(port, baudrate, timeout=0.05)

        # THE modem thread — sole owner of _ser
        self._modem_thread = threading.Thread(
            target=self._modem_loop, name="modem", daemon=True)
        self._modem_thread.start()

        self._init_modem()
        self._load_inbox()

        threading.Thread(target=self._poll_loop, daemon=True).start()

    # ── modem thread — sole owner of serial port ──────────────────────────────

    def _modem_loop(self):
        """
        Owns _ser exclusively. Never accessed from any other thread.

        State machine:
          IDLE      — read for URCs, check cmd_queue
          COLLECT   — a command was sent, collect response lines until final
        """
        buf              = ""
        collecting       = False
        resp_q           = None
        resp_lines       = []
        sms_phase        = 0
        sms_text_pending = None

        while self._running:
            try:
                # ── check for a pending command (non-blocking) ────────────────
                if not collecting:
                    try:
                        item = self._cmd_queue.get_nowait()
                        cmd_str = item[0]

                        if cmd_str is None:
                            # cancel token
                            continue

                        elif cmd_str == "SMS":
                            # SMS 4-tuple: ("SMS", number, text, resp_q)
                            _, sms_number, sms_text, resp_q = item
                            log.info("→ SMS to %s", sms_number)
                            # step 1: send AT+CMGS
                            self._ser.write(
                                f'AT+CMGS="{sms_number}"\r\n'.encode())
                            resp_lines = []
                            sms_phase  = 1   # waiting for >
                            sms_text_pending = sms_text
                            collecting = True

                        else:
                            # normal command: (cmd_str, resp_q)
                            cmd_str, resp_q = item
                            sms_phase = 0
                            sms_text_pending = None
                            log.info("→ %r", cmd_str)
                            self._ser.write((cmd_str + "\r\n").encode())
                            resp_lines = []
                            collecting = True

                    except queue.Empty:
                        pass
                else:
                    # check if a cancel token arrived for the in-flight cmd
                    try:
                        token, _ = self._cmd_queue.get_nowait()
                        if token is None:
                            log.warning("Modem loop: cancelling in-flight command, flushing")
                            # send ESC to abort any modem prompt (e.g. >)
                            self._ser.write(b"\x1b")
                            time.sleep(0.1)
                            self._ser.reset_input_buffer()
                            collecting  = False
                            resp_q      = None
                            resp_lines  = []
                            buf         = ""
                        else:
                            # put it back — it's a real command
                            self._cmd_queue.put((token, _))
                    except queue.Empty:
                        pass

                # ── read whatever is available ────────────────────────────────
                raw = self._ser.read(256)   # timeout=0.05 so non-blocking-ish
                if raw:
                    buf += raw.decode(errors="replace")

                # detect bare '>' prompt (no newline) — flush it immediately
                if collecting and sms_phase == 1 and ">" in buf:
                    buf = buf.replace(">", "")
                    log.info("← '>'")
                    resp_lines.append(">")
                    log.info("→ <sms body + ctrl-z>")
                    self._ser.write((sms_text_pending + "\x1a").encode())
                    sms_phase = 2
                    continue

                # ── process complete lines ────────────────────────────────────
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    log.info("← %r", line)

                    if collecting:
                        resp_lines.append(line)

                        if sms_phase == 1 and _is_final(line):
                            # error before prompt
                            resp_q.put(list(resp_lines))
                            collecting = False
                            resp_q = None
                            resp_lines = []
                            sms_phase = 0

                        elif sms_phase == 2 and _is_final(line):
                            # SMS fully sent
                            resp_q.put(list(resp_lines))
                            collecting = False
                            resp_q = None
                            resp_lines = []
                            sms_phase = 0

                        elif sms_phase == 0 and (_is_final(line) or line == ">"):
                            # normal command complete
                            resp_q.put(list(resp_lines))
                            collecting = False
                            resp_q     = None
                            resp_lines = []
                    else:
                        self._handle_urc(line)

            except serial.SerialException as e:
                log.error("Serial error: %s — reconnecting in 3s", e)
                time.sleep(3)
                self._reconnect()
                buf        = ""
                collecting = False
                if resp_q:
                    resp_q.put([])   # unblock any waiting caller
                resp_q     = None
                resp_lines = []

            except Exception as e:
                log.warning("Modem loop error: %s", e)

    def _reconnect(self):
        try:
            self._ser.close()
            self._ser = serial.Serial(self._port, self._baudrate, timeout=0.05)
            log.info("Reconnected to %s", self._port)
            self._init_modem()
        except Exception as e:
            log.error("Reconnect failed: %s", e)

    # ── public command interface ───────────────────────────────────────────────

    def _cmd(self, command, timeout=10.0):
        """
        Post a command to the modem thread and block until response.
        Safe to call from any thread.
        """
        resp_q = queue.Queue()
        self._cmd_queue.put((command, resp_q))
        try:
            return resp_q.get(timeout=timeout)
        except queue.Empty:
            log.warning("Timeout: %s", command)
            # Post a cancel token so modem_loop stops collecting
            # and flushes whatever state the modem is in
            self._cmd_queue.put((None, None))
            return []

    def _ok(self, lines):
        return "OK" in lines

    # ── URC handler (called from modem thread only) ────────────────────────────

    def _handle_urc(self, line):
        log.info("URC: %r", line)
        if line.startswith("+CMTI:"):
            idx = line.split(",")[-1].strip()
            # run in a separate thread so modem_loop stays responsive
            threading.Thread(target=self._handle_new_sms,
                             args=(idx,), daemon=True).start()
        elif line == "RING":
            with self._data_lock:
                self._call_state.update({"active": True, "direction": "in"})
            self._push(json.dumps({"type": "ring", "from": "unknown"}))
        elif line.startswith("+CLIP:"):
            number = line.split('"')[1] if '"' in line else ""
            with self._data_lock:
                self._call_state["number"] = number
            self._push(json.dumps({"type": "ring", "from": number}))
        elif line == "NO CARRIER":
            with self._data_lock:
                self._call_state = {"active": False, "number": None,
                                    "direction": None, "started": None}
            self._push(json.dumps({"type": "hangup"}))

    # ── init ──────────────────────────────────────────────────────────────────


    def _load_store(self):
        """Load persisted messages from messages.json."""
        try:
            with open(STORE_FILE) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _save_store(self):
        """Persist current inbox to messages.json."""
        with self._data_lock:
            data = self._inbox[:]
        try:
            with open(STORE_FILE, "w") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            log.warning("Failed to save message store: %s", e)

    def _merge_inbox(self, from_sim):
        """
        Merge SIM messages into the persisted store.
        - Start with store as base (preserves sent messages + history)
        - Add any SIM messages not already in store (by id)
        - Mark SIM messages as no longer unread if already seen
        """
        store = self._load_store()

        # build lookup: number -> thread, message id -> message
        store_map = {t["number"]: t for t in store}

        for sim_thread in from_sim:
            number = sim_thread["number"]
            if number not in store_map:
                store_map[number] = sim_thread
            else:
                stored = store_map[number]
                stored_ids = {m["id"] for m in stored["messages"]}
                for m in sim_thread["messages"]:
                    if m["id"] not in stored_ids:
                        stored["messages"].append(m)
                # update unread count from SIM (source of truth for unread)
                stored["unread"] = sim_thread.get("unread", 0)

        return list(store_map.values())

    def _init_modem(self):
        time.sleep(0.3)
        for cmd in ("ATE0", "AT+CMEE=2", "AT+CMGF=1",
                    "AT+CNMI=2,1,0,0,0", "AT+CLIP=1"):
            r = self._cmd(cmd)
            log.info("%s → %s", cmd, r)

    # ── SMS ───────────────────────────────────────────────────────────────────

    def _load_inbox(self):
        lines = self._cmd('AT+CMGL="ALL"', timeout=20)
        from_sim = self._parse_cmgl(lines)
        merged   = self._merge_inbox(from_sim)
        with self._data_lock:
            self._inbox = merged
        self._save_store()
        log.info("Inbox: %d threads (%d from SIM, %d from store)",
                 len(merged), len(from_sim), len(self._load_store()))

    def _parse_cmgl(self, lines):
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

    def get_inbox(self):
        import copy
        with self._data_lock:
            return copy.deepcopy(self._inbox)

    def send_sms(self, number, text):
        """
        SMS send is a two-step exchange:
          1. AT+CMGS="number"  →  modem replies with >
          2. text + ctrl-z     →  modem replies with +CMGS: <mr> / OK

        We use a special "SMS" tuple so the modem thread handles both
        steps atomically without releasing collecting state between them.
        """
        now   = time.strftime("%H:%M")
        resp_q = queue.Queue()
        # post a 3-tuple to signal SMS mode: ('SMS', number, text)
        self._cmd_queue.put(("SMS", number, text, resp_q))
        try:
            lines = resp_q.get(timeout=30)
        except queue.Empty:
            self._cmd_queue.put((None, None))   # cancel token
            return {"ok": False, "error": "SMS send timeout"}

        if not self._ok(lines):
            err = next((l for l in lines if "ERROR" in l), "unknown")
            return {"ok": False, "error": err}

        mr  = next((l for l in lines if l.startswith("+CMGS:")), "")
        mid = mr.split(":")[-1].strip() if mr else str(_uuid_mod.uuid4())
        msg = {"id": mid, "dir": "out", "text": text,
               "ts": now, "status": "delivered"}

        with self._data_lock:
            t = next((x for x in self._inbox if x["number"] == number), None)
            if t:
                t["messages"].append(msg)
            else:
                self._inbox.insert(0, {
                    "id": number, "thread_id": number, "name": number,
                    "number": number, "color": "#1d4ed8",
                    "messages": [msg], "unread": 0,
                })
        self._save_store()
        return {"ok": True, "id": mid, "ts": now}

    def delete_sms(self, msg_id):
        return {"ok": self._ok(self._cmd(f"AT+CMGD={msg_id}"))}

    def _handle_new_sms(self, idx):
        lines  = self._cmd(f"AT+CMGR={idx}")
        header = next((l for l in lines if l.startswith("+CMGR:")), "")
        text   = next((l for l in lines
                       if l and not l.startswith("+") and l != "OK"), "")
        if not header:
            return
        parts  = header[7:].split(",")
        number = parts[1].strip().strip('"') if len(parts) > 1 else "unknown"
        ts     = time.strftime("%H:%M")
        msg    = {"id": idx, "dir": "in", "text": text, "ts": ts, "status": ""}
        with self._data_lock:
            t = next((x for x in self._inbox if x["number"] == number), None)
            if t:
                t["messages"].append(msg)
                t["unread"] = t.get("unread", 0) + 1
            else:
                self._inbox.insert(0, {
                    "id": number, "thread_id": number, "name": number,
                    "number": number, "color": "#059669",
                    "messages": [msg], "unread": 1,
                })
        self._save_store()
        self._push(json.dumps({"type": "sms", "from": number,
                               "name": number, "preview": text}))

    # ── Voice ──────────────────────────────────────────────────────────────────

    def dial(self, number):
        lines = self._cmd(f"ATD{number};")
        if self._ok(lines):
            with self._data_lock:
                self._call_state = {"active": True, "number": number,
                                    "direction": "out", "started": time.time()}
            return {"ok": True, "number": number}
        return {"ok": False, "error": lines[-1] if lines else "no response"}

    def hangup(self):
        lines = self._cmd("ATH")
        with self._data_lock:
            self._call_state = {"active": False, "number": None,
                                "direction": None, "started": None}
        return {"ok": self._ok(lines)}

    def answer(self):
        lines = self._cmd("ATA")
        if self._ok(lines):
            with self._data_lock:
                self._call_state["started"] = time.time()
        return {"ok": self._ok(lines)}

    def call_status(self):
        with self._data_lock:
            cs = dict(self._call_state)
        if cs["active"] and cs["started"]:
            cs["duration"] = int(time.time() - cs["started"])
        return cs

    # ── Modem status ───────────────────────────────────────────────────────────

    def get_status(self):
        creg = self._cmd("AT+CREG?")
        csq  = self._cmd("AT+CSQ")
        cops = self._cmd("AT+COPS?")
        cimi = self._cmd("AT+CIMI")

        rssi_raw = 99
        for l in csq:
            if l.startswith("+CSQ:"):
                try: rssi_raw = int(l.split(":")[1].split(",")[0].strip())
                except: pass

        dbm  = (-113 + 2 * rssi_raw) if rssi_raw < 99 else None
        bars = max(0, min(5, (rssi_raw * 5) // 31)) if rssi_raw < 99 else 0

        operator, rat_code = "Unknown", 2
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
        registered = any("+CREG: 0,1" in l or "+CREG: 0,5" in l for l in creg)
        roaming    = any("+CREG: 0,5" in l for l in creg)
        imsi       = next((l for l in cimi if l[:3].isdigit()), "")

        return {"ok": True, "signal_rssi": dbm, "signal_bars": bars,
                "operator": operator, "rat": rat_map.get(rat_code, str(rat_code)),
                "registered": registered, "roaming": roaming,
                "imei": "", "sim_number": imsi, "dummy": False}

    def ussd(self, code):
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

    def _push(self, payload):
        with self._sub_lock:
            for q in self._subscribers:
                try: q.put_nowait(payload)
                except queue.Full: pass

    # ── Poll fallback ──────────────────────────────────────────────────────────

    def _poll_loop(self):
        while self._running:
            time.sleep(15)
            try:
                lines = self._cmd('AT+CMGL="REC UNREAD"')
                for t in self._parse_cmgl(lines):
                    for m in t["messages"]:
                        with self._data_lock:
                            ex = next((x for x in self._inbox
                                       if x["number"] == t["number"]), None)
                            already = ex and any(x["id"] == m["id"]
                                                 for x in ex["messages"])
                        if not already:
                            self._handle_new_sms(m["id"])
            except Exception as e:
                log.warning("Poll error: %s", e)

    def close(self):
        self._running = False
        self._ser.close()
