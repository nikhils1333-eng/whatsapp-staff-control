
from flask import Flask, request, jsonify, send_file, render_template_string
from flask_cors import CORS
import sqlite3, csv, io, os
from datetime import datetime

APP_PORT = 5000
DB_FILE = "orders.db"

app = Flask(__name__)
CORS(app)

def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_id TEXT,
        staff_name TEXT,
        pc_name TEXT,
        order_id TEXT,
        last6 TEXT,
        sku TEXT,
        qty TEXT,
        raw_line TEXT,
        status TEXT DEFAULT 'P',
        reason TEXT DEFAULT '',
        checked_time TEXT DEFAULT '',
        updated_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS staff_activity (
        staff_name TEXT,
        pc_name TEXT,
        last_seen TEXT,
        current_batch TEXT,
        PRIMARY KEY(staff_name, pc_name)
    )
    """)
    conn.commit()
    conn.close()

@app.route("/")
def home():
    return '<h2>WhatsApp Staff Control Server Running</h2><p>Open <a href="/dashboard">Dashboard</a></p>'

@app.route("/save-orders", methods=["POST"])
def save_orders():
    data = request.json or {}
    batch_id = data.get("batch_id", "")
    staff_name = data.get("staff_name", "Unknown")
    pc_name = data.get("pc_name", "PC")
    orders = data.get("orders", [])
    if not batch_id or not orders:
        return jsonify({"ok": False, "error": "batch_id/orders missing"}), 400
    conn = db()
    cur = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for o in orders:
        order_id = str(o.get("order_id", "")).strip()
        if not order_id:
            continue
        cur.execute("""
        INSERT INTO orders(batch_id, staff_name, pc_name, order_id, last6, sku, qty, raw_line, status, updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?)
        """, (batch_id, staff_name, pc_name, order_id, order_id[-6:], str(o.get("sku", "")).strip(), str(o.get("qty", "1")).strip(), str(o.get("raw_line", "")).strip(), "P", now))
    cur.execute("""
    INSERT INTO staff_activity(staff_name, pc_name, last_seen, current_batch)
    VALUES(?,?,?,?)
    ON CONFLICT(staff_name, pc_name) DO UPDATE SET last_seen=excluded.last_seen, current_batch=excluded.current_batch
    """, (staff_name, pc_name, now, batch_id))
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "saved": len(orders)})

@app.route("/update-status", methods=["POST"])
def update_status():
    data = request.json or {}
    batch_id = data.get("batch_id", "")
    order_id = str(data.get("order_id", "")).strip()
    status = data.get("status", "P")
    reason = data.get("reason", "")
    staff_name = data.get("staff_name", "Unknown")
    pc_name = data.get("pc_name", "PC")
    if status not in ["P", "R", "N", "S"]:
        return jsonify({"ok": False, "error": "Invalid status"}), 400
    if status == "S" and not reason.strip():
        return jsonify({"ok": False, "error": "Skip reason required"}), 400
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = db()
    cur = conn.cursor()
    cur.execute("""
    UPDATE orders SET status=?, reason=?, checked_time=?, updated_at=?
    WHERE batch_id=? AND order_id=? AND staff_name=? AND pc_name=?
    """, (status, reason, now if status != "P" else "", now, batch_id, order_id, staff_name, pc_name))
    cur.execute("""
    INSERT INTO staff_activity(staff_name, pc_name, last_seen, current_batch)
    VALUES(?,?,?,?)
    ON CONFLICT(staff_name, pc_name) DO UPDATE SET last_seen=excluded.last_seen, current_batch=excluded.current_batch
    """, (staff_name, pc_name, now, batch_id))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})

@app.route("/heartbeat", methods=["POST"])
def heartbeat():
    data = request.json or {}
    staff_name = data.get("staff_name", "Unknown")
    pc_name = data.get("pc_name", "PC")
    batch_id = data.get("batch_id", "")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = db()
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO staff_activity(staff_name, pc_name, last_seen, current_batch)
    VALUES(?,?,?,?)
    ON CONFLICT(staff_name, pc_name) DO UPDATE SET last_seen=excluded.last_seen, current_batch=excluded.current_batch
    """, (staff_name, pc_name, now, batch_id))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})

@app.route("/dashboard-data")
def dashboard_data():
    conn = db()
    cur = conn.cursor()
    cur.execute("""
    SELECT COUNT(*) total,
      SUM(CASE WHEN status='P' THEN 1 ELSE 0 END) pending,
      SUM(CASE WHEN status='R' THEN 1 ELSE 0 END) ready,
      SUM(CASE WHEN status='N' THEN 1 ELSE 0 END) not_found,
      SUM(CASE WHEN status='S' THEN 1 ELSE 0 END) skipped
    FROM orders
    """)
    overall = dict(cur.fetchone())
    cur.execute("""
    SELECT staff_name, pc_name, COUNT(*) total,
      SUM(CASE WHEN status='P' THEN 1 ELSE 0 END) pending,
      SUM(CASE WHEN status='R' THEN 1 ELSE 0 END) ready,
      SUM(CASE WHEN status='N' THEN 1 ELSE 0 END) not_found,
      SUM(CASE WHEN status='S' THEN 1 ELSE 0 END) skipped,
      MAX(updated_at) last_update
    FROM orders GROUP BY staff_name, pc_name ORDER BY staff_name
    """)
    staff = [dict(r) for r in cur.fetchall()]
    cur.execute("""
    SELECT sku, COUNT(*) total,
      SUM(CASE WHEN status='P' THEN 1 ELSE 0 END) pending,
      SUM(CASE WHEN status='R' THEN 1 ELSE 0 END) ready,
      SUM(CASE WHEN status='N' THEN 1 ELSE 0 END) not_found,
      SUM(CASE WHEN status='S' THEN 1 ELSE 0 END) skipped
    FROM orders GROUP BY sku ORDER BY pending DESC, total DESC
    """)
    sku = [dict(r) for r in cur.fetchall()]
    conn.close()
    return jsonify({"overall": overall, "staff": staff, "sku": sku, "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})

@app.route("/dashboard")
def dashboard():
    return """<!doctype html><html><head><title>Owner Live Dashboard</title><style>body{font-family:Arial;background:#f4f6f8;margin:0;padding:20px;color:#111}.cards{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:18px}.card{background:#fff;border-radius:12px;padding:16px;box-shadow:0 2px 8px #0001}.num{font-size:28px;font-weight:700}table{width:100%;border-collapse:collapse;background:white;border-radius:12px;overflow:hidden;margin-bottom:22px}th,td{padding:10px;border-bottom:1px solid #eee;text-align:left}th{background:#111;color:white}.ok{color:green;font-weight:bold}.warn{color:#c77a00;font-weight:bold}.bad{color:red;font-weight:bold}</style></head><body><h1>Owner Live Dashboard</h1><p>Server Time: <span id="serverTime">-</span></p><div class="cards"><div class="card"><div>Total</div><div class="num" id="total">0</div></div><div class="card"><div>Checked</div><div class="num" id="checked">0</div></div><div class="card"><div>Pending</div><div class="num bad" id="pending">0</div></div><div class="card"><div>Ready</div><div class="num ok" id="ready">0</div></div><div class="card"><div>Not Found + Skip</div><div class="num warn" id="nfskip">0</div></div></div><h2>Staff Wise Status</h2><table><thead><tr><th>Staff</th><th>PC</th><th>Total</th><th>Pending</th><th>Ready</th><th>Not Found</th><th>Skipped</th><th>Last Update</th></tr></thead><tbody id="staffBody"></tbody></table><h2>SKU Wise Status</h2><table><thead><tr><th>SKU</th><th>Total</th><th>Pending</th><th>Ready</th><th>Not Found</th><th>Skipped</th></tr></thead><tbody id="skuBody"></tbody></table><script>async function loadData(){const res=await fetch('/dashboard-data');const data=await res.json();const o=data.overall||{};const total=o.total||0,pending=o.pending||0,ready=o.ready||0,nf=o.not_found||0,sk=o.skipped||0;document.getElementById('serverTime').innerText=data.server_time;document.getElementById('total').innerText=total;document.getElementById('checked').innerText=total-pending;document.getElementById('pending').innerText=pending;document.getElementById('ready').innerText=ready;document.getElementById('nfskip').innerText=nf+sk;document.getElementById('staffBody').innerHTML=data.staff.map(s=>`<tr><td>${s.staff_name}</td><td>${s.pc_name}</td><td>${s.total||0}</td><td class="${(s.pending||0)>0?'bad':'ok'}">${s.pending||0}</td><td class="ok">${s.ready||0}</td><td>${s.not_found||0}</td><td>${s.skipped||0}</td><td>${s.last_update||''}</td></tr>`).join('');document.getElementById('skuBody').innerHTML=data.sku.map(s=>`<tr><td>${s.sku||'-'}</td><td>${s.total||0}</td><td class="${(s.pending||0)>0?'bad':'ok'}">${s.pending||0}</td><td class="ok">${s.ready||0}</td><td>${s.not_found||0}</td><td>${s.skipped||0}</td></tr>`).join('')}loadData();setInterval(loadData,3000);</script></body></html>"""

init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", APP_PORT))
    app.run(host="0.0.0.0", port=port, debug=False)
