/**
 * common.js — shared across all piphone apps
 * Provides: API, Contacts, normalizeNumber, StatusBar, SSE, toast
 */

const API = "";  // same origin

// ── number normalization ──────────────────────────────────────────────────────
function normalizeNumber(raw) {
  let n = String(raw).replace(/[\s\-().]/g, "");
  if (n.startsWith("+"))  return n;                      // already E.164
  if (n.startsWith("00")) return "+" + n.slice(2);       // 00972... → +972...
  if (n.startsWith("972"))return "+" + n;                // 972... → +972...
  if (n.startsWith("0"))  return "+972" + n.slice(1);    // 05x → +9725x
  return "+972" + n;                                     // bare digits
}

function formatDisplay(number) {
  // +97250XXXXXXX → 050-XXX-XXXX
  const n = normalizeNumber(number);
  if (n.startsWith("+972") && n.length === 13) {
    const local = n.slice(4);  // 50XXXXXXX
    return "0" + local.slice(0, 2) + "-" + local.slice(2, 5) + "-" + local.slice(5);
  }
  return number;
}

// ── contacts ──────────────────────────────────────────────────────────────────
const Contacts = {
  _list: [],

  async load() {
    try {
      const r = await fetch(API + "/api/contacts");
      this._list = await r.json();
    } catch (e) {
      this._list = [];
    }
    return this._list;
  },

  async save() {
    await fetch(API + "/api/contacts/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(this._list),
    });
  },

  all() { return this._list; },

  get(id) { return this._list.find(c => c.id === id); },

  findByNumber(number) {
    const norm = normalizeNumber(number);
    return this._list.find(c => normalizeNumber(c.number) === norm);
  },

  resolveName(number) {
    const c = this.findByNumber(number);
    return c ? c.name : formatDisplay(number);
  },

  resolveColor(number) {
    const c = this.findByNumber(number);
    return c ? c.color : "#1d4ed8";
  },

  async add(name, number) {
    const c = {
      id: crypto.randomUUID(),
      name: name.trim(),
      number: normalizeNumber(number),
      color: COLORS[this._list.length % COLORS.length],
    };
    this._list.push(c);
    await this.save();
    return c;
  },

  async update(id, name, number) {
    const c = this._list.find(x => x.id === id);
    if (!c) return;
    c.name   = name.trim();
    c.number = normalizeNumber(number);
    await this.save();
    return c;
  },

  async remove(id) {
    this._list = this._list.filter(c => c.id !== id);
    await this.save();
  },

  initials(name) {
    return name.split(" ").map(w => w[0]).join("").slice(0, 2).toUpperCase();
  },
};

const COLORS = [
  "#1d4ed8","#7c3aed","#0891b2","#059669",
  "#b45309","#be185d","#dc2626","#0f766e",
];

// ── status bar ────────────────────────────────────────────────────────────────
const StatusBar = {
  async init() {
    this._tick();
    setInterval(() => this._tick(), 10000);
    this._fetchModem();
    setInterval(() => this._fetchModem(), 15000);
  },

  _tick() {
    const n = new Date();
    const el = document.getElementById("sbClock");
    if (el) el.textContent =
      String(n.getHours()).padStart(2,"0") + ":" +
      String(n.getMinutes()).padStart(2,"0");
  },

  async _fetchModem() {
    try {
      const r = await fetch(API + "/api/modem/status");
      const s = await r.json();
      const carrier = document.getElementById("sbCarrier");
      if (carrier) carrier.textContent = (s.operator||"---") + " · " + (s.rat||"?");
      const bars = document.querySelectorAll(".sig-bar");
      const n = Math.min(s.signal_bars || 0, 5);
      bars.forEach((b, i) => b.classList.toggle("on", i < n));
      const demo = document.getElementById("demoBadge");
      if (demo) demo.style.display = s.dummy ? "" : "none";
    } catch(e) {}
  },
};

// ── toast ─────────────────────────────────────────────────────────────────────
function toast(msg, ms = 2500) {
  const el = document.getElementById("toast");
  if (!el) return;
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove("show"), ms);
}

// ── SSE ───────────────────────────────────────────────────────────────────────
const SSE = {
  _handlers: {},

  on(type, fn) { this._handlers[type] = fn; },

  connect() {
    const es = new EventSource(API + "/api/events");
    es.onmessage = async e => {
      try {
        const d = JSON.parse(e.data);
        const h = this._handlers[d.type];
        if (h) await h(d);
      } catch(_) {}
    };
    es.onerror = () => setTimeout(() => this.connect(), 5000);
  },
};

// ── shared CSS string (injected by each page) ─────────────────────────────────
const BASE_CSS = `
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
:root{
  --bg:#0f0f12;--panel:#17171c;--border-c:#25252e;
  --bubble-out:#1d4ed8;--bubble-in:#22222a;
  --txt:#eeeef2;--muted:#7070a0;--green:#22c55e;--red:#ef4444;
  --input-bg:#1c1c24;--accent:#1d4ed8;
}
html,body{width:100%;height:100%;overflow:hidden;background:var(--bg);
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  font-size:13px;color:var(--txt)}
.screen{width:100%;height:100vh;display:flex;flex-direction:column;overflow:hidden}
.sbar{height:20px;background:var(--panel);display:flex;align-items:center;
  justify-content:space-between;padding:0 8px;font-size:10px;color:var(--muted);
  flex-shrink:0;border-bottom:0.5px solid var(--border-c)}
.sig{display:flex;align-items:flex-end;gap:2px}
.sig-bar{width:3px;border-radius:1px;background:var(--muted)}
.sig-bar.on{background:var(--green)}
.demo-badge{background:#7c3aed22;color:#a78bfa;font-size:9px;
  padding:1px 4px;border-radius:3px;border:0.5px solid #7c3aed55}
.topbar{height:44px;background:var(--panel);display:flex;align-items:center;
  padding:0 10px;gap:8px;border-bottom:0.5px solid var(--border-c);flex-shrink:0}
.topbar-title{font-size:15px;font-weight:500;flex:1}
.ibtn{width:32px;height:32px;border-radius:8px;border:0.5px solid var(--border-c);
  background:transparent;color:var(--muted);display:flex;align-items:center;
  justify-content:center;cursor:pointer;font-size:18px;flex-shrink:0}
.ibtn:active{background:var(--border-c);color:var(--txt)}
.view{flex:1;display:flex;flex-direction:column;overflow:hidden}
.hidden{display:none!important}
.toast{position:absolute;bottom:60px;left:50%;transform:translateX(-50%);
  background:#1e293b;color:var(--txt);font-size:11px;padding:6px 14px;
  border-radius:20px;border:0.5px solid var(--border-c);
  opacity:0;transition:opacity 0.2s;pointer-events:none;white-space:nowrap;z-index:99}
.toast.show{opacity:1}
.av{border-radius:50%;display:flex;align-items:center;justify-content:center;
  font-weight:500;color:#fff;flex-shrink:0}
input,textarea{font-family:inherit;font-size:13px;color:var(--txt);
  background:var(--input-bg);border:0.5px solid var(--border-c);
  border-radius:8px;padding:8px 10px;outline:none;width:100%}
input:focus,textarea:focus{border-color:#1d4ed855}
input::placeholder,textarea::placeholder{color:var(--muted)}
.btn{padding:10px 16px;border-radius:10px;border:none;font-size:13px;
  font-family:inherit;cursor:pointer}
.btn-primary{background:var(--accent);color:#fff}
.btn-secondary{background:var(--border-c);color:var(--muted)}
.btn-danger{background:#7f1d1d;color:#fca5a5}
.btn-primary:active,.btn-secondary:active,.btn-danger:active{opacity:0.8}
`;
