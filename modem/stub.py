"""
modem/stub.py — dummy modem driver
All methods return the same shape as modem/at.py will.
Swap: in app.py change `from modem.stub import ModemStub` →
      `from modem.at import ModemDriver as ModemStub`
"""

import json, queue, time, threading
from copy import deepcopy

_INBOX = [
    {
        "id": "1",
        "thread_id": "amir",
        "name": "Amir",
        "number": "+972501234567",
        "color": "#1d4ed8",
        "messages": [
            {"id": "1a", "dir": "in",  "text": "Coming tonight?",    "ts": "18:42", "status": ""},
            {"id": "1b", "dir": "out", "text": "Yeah, leaving soon", "ts": "18:44", "status": "read"},
            {"id": "1c", "dir": "in",  "text": "On my way, 5 min",  "ts": "18:58", "status": ""},
        ],
        "unread": 1,
    },
    {
        "id": "2",
        "thread_id": "mom",
        "name": "Mom",
        "number": "+972523456789",
        "color": "#b45309",
        "messages": [
            {"id": "2a", "dir": "in",  "text": "Did you eat today?",    "ts": "12:10", "status": ""},
            {"id": "2b", "dir": "out", "text": "Yes mom",               "ts": "12:15", "status": "read"},
            {"id": "2c", "dir": "in",  "text": "Call me when you can",  "ts": "14:30", "status": ""},
        ],
        "unread": 0,
    },
    {
        "id": "3",
        "thread_id": "dan",
        "name": "Dan S.",
        "number": "+972545678901",
        "color": "#0891b2",
        "messages": [
            {"id": "3a", "dir": "out", "text": "SIM module working yet?",         "ts": "Yesterday", "status": "read"},
            {"id": "3b", "dir": "in",  "text": "Still setting up",                "ts": "Yesterday", "status": ""},
            {"id": "3c", "dir": "out", "text": "I have the AT command list",      "ts": "Yesterday", "status": "read"},
            {"id": "3d", "dir": "in",  "text": "Will check and get back",         "ts": "Yesterday", "status": ""},
        ],
        "unread": 0,
    },
    {
        "id": "4",
        "thread_id": "019",
        "name": "019 Mobile",
        "number": "019",
        "color": "#be185d",
        "messages": [
            {"id": "4a", "dir": "in", "text": "Your SIM is active. Bal: \u20aa48.50", "ts": "Today", "status": ""},
        ],
        "unread": 1,
    },
    {
        "id": "5",
        "thread_id": "noa",
        "name": "Noa",
        "number": "+972526543210",
        "color": "#7c3aed",
        "messages": [
            {"id": "5a", "dir": "out", "text": "Negev photos are on Nextcloud",      "ts": "Sun", "status": "read"},
            {"id": "5b", "dir": "in",  "text": "Milky Way ones are amazing!",        "ts": "Sun", "status": ""},
            {"id": "5c", "dir": "in",  "text": "NPF rule really helped",             "ts": "Sun", "status": ""},
        ],
        "unread": 0,
    },
]

_STATUS = {
    "ok": True,
    "signal_rssi": -71,        # dBm  (AT+CSQ raw → converted)
    "signal_bars": 4,          # 0-5
    "operator": "019 Mobile",
    "rat": "LTE",              # Radio Access Technology
    "registered": True,
    "roaming": False,
    "imei": "000000000000000", # dummy
    "sim_number": "+972500000000",
    "dummy": True,             # flag so UI can show "DEMO" badge
}


class ModemStub:
    def __init__(self):
        self._inbox = deepcopy(_INBOX)
        self._call_state = {"active": False, "number": None, "direction": None, "started": None}
        self._subscribers = []
        self._lock = threading.Lock()

    # ── SMS ──────────────────────────────────────────────────────────────────

    def get_inbox(self):
        with self._lock:
            return deepcopy(self._inbox)

    def send_sms(self, number: str, text: str) -> dict:
        """Stub: append to the matching thread or create a new one."""
        import uuid
        now = time.strftime("%H:%M")
        msg = {"id": str(uuid.uuid4()), "dir": "out", "text": text, "ts": now, "status": "sent"}
        with self._lock:
            thread = next((t for t in self._inbox if t["number"] == number), None)
            if thread:
                thread["messages"].append(msg)
            else:
                self._inbox.insert(0, {
                    "id": str(uuid.uuid4()),
                    "thread_id": number,
                    "name": number,
                    "number": number,
                    "color": "#1d4ed8",
                    "messages": [msg],
                    "unread": 0,
                })
        # simulate delivery tick after 1s
        threading.Timer(1.0, self._mark_delivered, args=[msg["id"]]).start()
        return {"ok": True, "id": msg["id"], "ts": now, "dummy": True}

    def _mark_delivered(self, msg_id):
        with self._lock:
            for t in self._inbox:
                for m in t["messages"]:
                    if m["id"] == msg_id:
                        m["status"] = "delivered"
        self._push(json.dumps({"type": "delivered", "id": msg_id}))

    def delete_sms(self, msg_id: str) -> dict:
        with self._lock:
            for t in self._inbox:
                t["messages"] = [m for m in t["messages"] if m["id"] != msg_id]
        return {"ok": True}

    # ── Voice ─────────────────────────────────────────────────────────────────

    def dial(self, number: str) -> dict:
        self._call_state = {"active": True, "number": number,
                            "direction": "out", "started": time.time()}
        return {"ok": True, "number": number, "dummy": True}

    def hangup(self) -> dict:
        self._call_state = {"active": False, "number": None, "direction": None, "started": None}
        return {"ok": True}

    def answer(self) -> dict:
        if self._call_state["active"]:
            self._call_state["started"] = time.time()
        return {"ok": True}

    def call_status(self) -> dict:
        cs = dict(self._call_state)
        if cs["active"] and cs["started"]:
            cs["duration"] = int(time.time() - cs["started"])
        return cs

    # ── Modem info ────────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        return dict(_STATUS)

    def ussd(self, code: str) -> dict:
        return {"ok": True, "response": f"[stub] USSD {code} → balance: \u20aa48.50", "dummy": True}

    # ── SSE subscriptions ─────────────────────────────────────────────────────

    def subscribe(self):
        q = queue.Queue()
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            self._subscribers = [s for s in self._subscribers if s is not q]

    def _push(self, payload: str):
        with self._lock:
            for q in self._subscribers:
                try:
                    q.put_nowait(payload)
                except queue.Full:
                    pass

    # ── Test helper: simulate an incoming SMS ─────────────────────────────────

    def inject_sms(self, number: str, name: str, text: str):
        """Call this from a test script or future URC handler."""
        import uuid
        now = time.strftime("%H:%M")
        msg = {"id": str(uuid.uuid4()), "dir": "in", "text": text, "ts": now, "status": ""}
        with self._lock:
            thread = next((t for t in self._inbox if t["number"] == number), None)
            if thread:
                thread["messages"].append(msg)
                thread["unread"] = thread.get("unread", 0) + 1
            else:
                self._inbox.insert(0, {
                    "id": str(uuid.uuid4()),
                    "thread_id": number,
                    "name": name,
                    "number": number,
                    "color": "#059669",
                    "messages": [msg],
                    "unread": 1,
                })
        self._push(json.dumps({"type": "sms", "from": number, "name": name, "preview": text}))
