"""
piphone — Flask backend (LIVE modem mode)
Dummy mode: all modem calls are stubbed, swap in modem/at.py later.
"""

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import os, time, threading

from modem.at import ModemDriver

app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app)

modem = ModemDriver(port="/dev/ttyUSB2", baudrate=115200)

# ── serve the frontend ────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("static", "index.html")

# ── SMS ───────────────────────────────────────────────────────────────────────

@app.route("/api/sms/inbox")
def sms_inbox():
    """
    Returns all conversations as a list of thread objects.
    Real: AT+CMGL="ALL" parsed into threads.
    """
    return jsonify(modem.get_inbox())

@app.route("/api/sms/send", methods=["POST"])
def sms_send():
    """
    Body: { "number": "+972501234567", "text": "Hello" }
    Real: AT+CMGS="<number>" → send PDU/text → ctrl-z
    """
    body = request.get_json(force=True)
    number = body.get("number", "").strip()
    text   = body.get("text", "").strip()
    if not number or not text:
        return jsonify({"ok": False, "error": "number and text required"}), 400
    result = modem.send_sms(number, text)
    return jsonify(result)

@app.route("/api/sms/delete", methods=["POST"])
def sms_delete():
    """
    Body: { "id": "<msg_id>" }
    Real: AT+CMGD=<index>
    """
    body = request.get_json(force=True)
    msg_id = body.get("id")
    result = modem.delete_sms(msg_id)
    return jsonify(result)

# ── Voice ─────────────────────────────────────────────────────────────────────

@app.route("/api/call/dial", methods=["POST"])
def call_dial():
    """
    Body: { "number": "+972501234567" }
    Real: ATD<number>;
    """
    body = request.get_json(force=True)
    number = body.get("number", "").strip()
    if not number:
        return jsonify({"ok": False, "error": "number required"}), 400
    return jsonify(modem.dial(number))

@app.route("/api/call/hangup", methods=["POST"])
def call_hangup():
    """Real: ATH"""
    return jsonify(modem.hangup())

@app.route("/api/call/answer", methods=["POST"])
def call_answer():
    """Real: ATA"""
    return jsonify(modem.answer())

@app.route("/api/call/status")
def call_status():
    """Real: AT+CLCC"""
    return jsonify(modem.call_status())

# ── Modem / network status ────────────────────────────────────────────────────

@app.route("/api/modem/status")
def modem_status():
    """
    Real: AT+CSQ (signal), AT+CREG? (registration), AT+COPS? (operator),
          AT+CIMI (IMSI), AT+CGSN (IMEI)
    """
    return jsonify(modem.get_status())

@app.route("/api/modem/ussd", methods=["POST"])
def modem_ussd():
    """
    Body: { "code": "*100#" }
    Real: AT+CUSD=1,"*100#",15
    """
    body = request.get_json(force=True)
    code = body.get("code", "").strip()
    return jsonify(modem.ussd(code))

# ── Contacts (local JSON store, no modem needed) ──────────────────────────────

CONTACTS_FILE = os.path.join(os.path.dirname(__file__), "contacts.json")

@app.route("/api/contacts")
def contacts_list():
    import json
    if not os.path.exists(CONTACTS_FILE):
        return jsonify([])
    with open(CONTACTS_FILE) as f:
        return jsonify(json.load(f))

@app.route("/api/contacts/save", methods=["POST"])
def contacts_save():
    import json
    body = request.get_json(force=True)
    with open(CONTACTS_FILE, "w") as f:
        json.dump(body, f, indent=2)
    return jsonify({"ok": True})

# ── SSE push for incoming SMS / call ring ─────────────────────────────────────

@app.route("/api/events")
def events():
    """
    Server-sent events — frontend subscribes once and gets pushed:
      data: {"type":"sms","from":"+972...","preview":"Hey"}
      data: {"type":"ring","from":"+972..."}
    Real: background thread polls AT+CMGL / watches RING URC on ttyUSB2.
    """
    def stream():
        q = modem.subscribe()
        try:
            while True:
                evt = q.get(timeout=30)
                yield f"data: {evt}\n\n"
        except Exception:
            yield "data: {\"type\":\"ping\"}\n\n"
        finally:
            modem.unsubscribe(q)
    from flask import Response
    return Response(stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
