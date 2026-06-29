"""
piphone — Flask backend (LIVE modem mode)
"""

import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(threadName)s] %(levelname)s %(name)s: %(message)s"
)

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import os, time

import db
from modem.at import ModemDriver

app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app)

db.init_db()
modem = ModemDriver()

# ── frontend ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("static", "index.html")

# ── SMS ───────────────────────────────────────────────────────────────────────

@app.route("/api/sms/inbox")
def sms_inbox():
    return jsonify(db.messages_get_inbox())

@app.route("/api/sms/send", methods=["POST"])
def sms_send():
    body   = request.get_json(force=True)
    number = body.get("number", "").strip()
    text   = body.get("text", "").strip()
    if not number or not text:
        return jsonify({"ok": False, "error": "number and text required"}), 400
    result = modem.send_sms(number, text)
    if result["ok"]:
        db.messages_add(number, "out", text,
                        status="delivered", sim_index=None)
    return jsonify(result)

@app.route("/api/sms/delete", methods=["POST"])
def sms_delete():
    body   = request.get_json(force=True)
    msg_id = body.get("id")
    db.messages_delete(msg_id)
    modem.delete_sms(msg_id)
    return jsonify({"ok": True})

# ── Contacts ──────────────────────────────────────────────────────────────────

@app.route("/api/contacts")
def contacts_list():
    return jsonify(db.contacts_all())

@app.route("/api/contacts/<contact_id>")
def contacts_get(contact_id):
    c = db.contacts_get(contact_id)
    if not c:
        return jsonify({"error": "not found"}), 404
    return jsonify(c)

@app.route("/api/contacts/add", methods=["POST"])
def contacts_add():
    b = request.get_json(force=True)
    c = db.contacts_add(
        first_name = b.get("first_name", "").strip(),
        last_name  = b.get("last_name",  "").strip(),
        number     = b.get("number",     "").strip(),
        email      = b.get("email",      "").strip(),
        color      = b.get("color", "#1d4ed8"),
    )
    return jsonify(c)

@app.route("/api/contacts/update", methods=["POST"])
def contacts_update():
    b = request.get_json(force=True)
    c = db.contacts_update(
        contact_id = b.get("id"),
        first_name = b.get("first_name", "").strip(),
        last_name  = b.get("last_name",  "").strip(),
        number     = b.get("number",     "").strip(),
        email      = b.get("email",      "").strip(),
        color      = b.get("color"),
    )
    if not c:
        return jsonify({"error": "not found"}), 404
    return jsonify(c)

@app.route("/api/contacts/delete", methods=["POST"])
def contacts_delete():
    b = request.get_json(force=True)
    db.contacts_delete(b.get("id"))
    return jsonify({"ok": True})

# ── Voice ─────────────────────────────────────────────────────────────────────

@app.route("/api/call/dial", methods=["POST"])
def call_dial():
    body   = request.get_json(force=True)
    number = body.get("number", "").strip()
    if not number:
        return jsonify({"ok": False, "error": "number required"}), 400
    result = modem.dial(number)
    if result["ok"]:
        db.calls_add(number, "out")
    return jsonify(result)

@app.route("/api/call/hangup", methods=["POST"])
def call_hangup():
    return jsonify(modem.hangup())

@app.route("/api/call/answer", methods=["POST"])
def call_answer():
    return jsonify(modem.answer())

@app.route("/api/call/status")
def call_status():
    return jsonify(modem.call_status())

@app.route("/api/call/log")
def call_log():
    return jsonify(db.calls_get_recent())

# ── Modem status ──────────────────────────────────────────────────────────────

@app.route("/api/modem/status")
def modem_status():
    return jsonify(modem.get_status())

@app.route("/api/modem/ussd", methods=["POST"])
def modem_ussd():
    body = request.get_json(force=True)
    return jsonify(modem.ussd(body.get("code", "").strip()))

# ── SSE ───────────────────────────────────────────────────────────────────────

@app.route("/api/events")
def events():
    def stream():
        q = modem.subscribe()
        try:
            while True:
                evt = q.get(timeout=30)
                yield f"data: {evt}\n\n"
        except Exception:
            yield 'data: {"type":"ping"}\n\n'
        finally:
            modem.unsubscribe(q)
    from flask import Response
    return Response(stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no"})

# ── Data / airplane mode ─────────────────────────────────────────────────────

import subprocess

_data_state = {"connected": False, "ip": None, "iface": None}

@app.route("/api/modem/data-up", methods=["POST"])
def data_up():
    """Called by wwan-up.sh when data connects."""
    b = request.get_json(force=True)
    _data_state.update({"connected": True,
                         "ip": b.get("ip"), "iface": b.get("iface")})
    return jsonify({"ok": True})

@app.route("/api/modem/data-down", methods=["POST"])
def data_down():
    """Called by wwan-down.sh when data disconnects."""
    _data_state.update({"connected": False, "ip": None, "iface": None})
    return jsonify({"ok": True})

@app.route("/api/modem/data-status")
def data_status():
    return jsonify(_data_state)

@app.route("/api/modem/airplane", methods=["POST"])
def airplane():
    """Toggle airplane mode (stops/starts wwan.service)."""
    b      = request.get_json(force=True)
    enable = b.get("enable", True)   # True = airplane ON (data OFF)
    action = "stop" if enable else "start"
    try:
        subprocess.run(["systemctl", action, "wwan.service"],
                       timeout=30, check=True)
        return jsonify({"ok": True, "airplane": enable})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.after_request
def no_cache(r):
    r.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    r.headers["Pragma"] = "no-cache"
    r.headers["Expires"] = "0"
    return r

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
