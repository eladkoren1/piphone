"""
modem/at.py — SIM7600G-H driver.
Key design:
  - One reader thread owns serial RX exclusively.
  - _cmd_lock ensures only one AT command is in flight at a time.
  - _resp_q is only written/read under _cmd_lock, so no races.
"""

import serial, threading, time, queue
import json, uuid as _uuid_mod, logging

log = logging.getLogger("modem.at")

FINAL = {"OK", "ERROR", "NO CARRIER", "BUSY", "NO ANSWER", "NO DIALTONE"}

def _is_final(line):
    return (line in FINAL
            or line.startswith("+CMS ERROR")
            or line.startswith("+CME ERROR"))


class ModemDriver:
    def __init__(self, port="/dev/ttyUSB2", baudrate=115200):
        self._port     = port
        self._baudrate = baudrate

        # one command at a time — held for the full duration of a _cmd() call
        self._cmd_lock  = threading.Lock()

        # response queue — only valid while _cmd_lock is held
        self._resp_q    = None

        # data locks
        self._data_lock = threading.Lock()
        self._sub_lock  = threading.Lock()

        self._subscribers = []
        self._call_state  = {"active": False, "number": None,
                             "direction": None, "started": None}
        self._inbox = []
        self._running = True

        self._ser = serial.Serial(port, baudrate, timeout=0.05)

        threading.Thread(target=self._reader_loop, daemon=True).start()

        self._init_modem()
        self._load_inbox()
        threading.Thread(target=self._poll_loop, daemon=True).start()

    # ── reader — sole owner of RX ─────────────────────────────────────────────

    def _reader_loop(self):
        buf = ""
        while self._running:
            try:
                raw = self._ser.read(256)
                if not raw:
                    continue
                buf += raw.decode(errors="replace")

                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    log.debug("← %r", line)

                    # if a command is in flight, route to its queue
                    rq = self._resp_q
                    if rq is not None:
                        rq.put(line)
                    else:
                        self._handle_urc(line)

            except serial.SerialException as e:
                log.error("Serial error: %s — reconnecting in 3s", e)
                time.sleep(3)
                self._reconnect()
            except Exception as e:
                log.warning("Reader error: %s", e)

    def _reconnect(self):
        try:
            self._ser.close()
            self._ser = serial.Serial(self._port, self._baudrate, timeout=0.05)
            log.info("Reconnected")
            self._init_modem()
        except Exception as e:
            log.error("Reconnect failed: %s", e)

    # ── send one AT command, collect response ─────────────────────────────────

    def _cmd(self, command, timeout=10.0):
        """
        Serialize all AT commands through _cmd_lock.
        Only one command is ever in flight — no _resp_q races possible.
        """
        with self._cmd_lock:
            self._resp_q = queue.Queue()
            log.debug("→ %r", command)
            self._ser.write((command + "\r\n").encode())

            lines    = []
            deadline = time.time() + timeout
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    log.warning("Timeout: %s", command)
                    break
                try:
                    line = self._resp_q.get(timeout=min(remaining, 1.0))
                    lines.append(line)
                    if _is_final(line) or line == ">":
                        break
                except queue.Empty:
                    continue

            self._resp_q = None
            return lines

    def _ok(self, lines):
        return "OK" in lines

    # ── URC handler (called from reader when no command in flight) ────────────

    def _handle_urc(self, line):
        log.debug("URC: %r", line)
        if line.startswith("+CMTI:"):
            idx = line.split(",")[-1].strip()
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

    def _init_modem(self):
        time.sleep(0.3)
        for cmd in ("ATE0", "AT+CMEE=2", "AT+CMGF=1",
                    "AT+CNMI=2,1,0,0,0", "AT+CLIP=1"):
            r = self._cmd(cmd)
            log.info("%s → %s", cmd, r)

    # ── SMS ───────────────────────────────────────────────────────────────────

    def _load_inbox(self):
        lines = self._cmd('AT+CMGL="ALL"', timeout=20)
        with self._data_lock:
            self._inbox = self._parse_cmgl(lines)
        log.info("Inbox: %d threads", len(self._inbox))

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
        now = time.strftime("%H:%M")
        with self._cmd_lock:
            # step 1: send AT+CMGS, wait for >
            self._resp_q = queue.Queue()
            self._ser.write(f'AT+CMGS="{number}"\r\n'.encode())
            lines1 = []
            deadline = time.time() + 8
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    self._resp_q = None
                    return {"ok": False, "error": "No > prompt (timeout)"}
                try:
                    line = self._resp_q.get(timeout=min(remaining, 1.0))
                    lines1.append(line)
                    if line == ">" or _is_final(line):
                        break
                except queue.Empty:
                    continue

            if ">" not in lines1:
                self._resp_q = None
                return {"ok": False, "error": "No prompt: " + str(lines1)}

            # step 2: send text + ctrl-z, wait for OK
            self._resp_q = queue.Queue()
            self._ser.write((text + "\x1a").encode())
            lines2   = []
            deadline = time.time() + 30
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    self._resp_q = None
                    return {"ok": False, "error": "Send timeout"}
                try:
                    line = self._resp_q.get(timeout=min(remaining, 1.0))
                    lines2.append(line)
                    if _is_final(line):
                        break
                except queue.Empty:
                    continue

            self._resp_q = None

        if not self._ok(lines2):
            err = next((l for l in lines2 if "ERROR" in l), "unknown")
            return {"ok": False, "error": err}

        mr  = next((l for l in lines2 if l.startswith("+CMGS:")), "")
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
