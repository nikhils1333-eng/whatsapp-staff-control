
from flask import Flask, request, jsonify, send_file, render_template_string
from flask_cors import CORS
import sqlite3, csv, io, os
from datetime import datetime

APP_PORT = 5000
DB_FILE = "orders.db"
app = Flask(__name__)
CORS(app)

def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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
        firm_name TEXT DEFAULT '',
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
        updated_at TEXT,
        loaded_at TEXT DEFAULT ''
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS staff_activity (
        firm_name TEXT DEFAULT '',
        staff_name TEXT,
        pc_name TEXT,
        last_seen TEXT,
        current_batch TEXT,
        current_sku TEXT DEFAULT '',
        current_order TEXT DEFAULT '',
        last_action TEXT DEFAULT '',
        PRIMARY KEY(firm_name, staff_name, pc_name)
    )""")
    for table, col, definition in [
        ("orders", "firm_name", "TEXT DEFAULT ''"),
        ("orders", "loaded_at", "TEXT DEFAULT ''"),
        ("staff_activity", "firm_name", "TEXT DEFAULT ''"),
        ("staff_activity", "current_sku", "TEXT DEFAULT ''"),
        ("staff_activity", "current_order", "TEXT DEFAULT ''"),
        ("staff_activity", "last_action", "TEXT DEFAULT ''"),
    ]:
        try:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()

def upsert_activity(cur, firm_name, staff_name, pc_name, batch_id, current_sku="", current_order="", last_action=""):
    t = now()
    cur.execute("""
    INSERT INTO staff_activity(firm_name, staff_name, pc_name, last_seen, current_batch, current_sku, current_order, last_action)
    VALUES(?,?,?,?,?,?,?,?)
    ON CONFLICT(firm_name, staff_name, pc_name) DO UPDATE SET
        last_seen=excluded.last_seen,
        current_batch=excluded.current_batch,
        current_sku=excluded.current_sku,
        current_order=excluded.current_order,
        last_action=excluded.last_action
    """, (firm_name, staff_name, pc_name, t, batch_id, current_sku, current_order, last_action))

@app.route("/")
def home():
    return '<h2>WhatsApp Staff Control Cloud V4 Running</h2><p><a href="/dashboard">Open Advanced Owner Dashboard</a></p>'

@app.route("/save-orders", methods=["POST"])
def save_orders():
    data = request.json or {}
    batch_id = data.get("batch_id", "")
    firm_name = data.get("firm_name", "Default Firm")
    staff_name = data.get("staff_name", "Unknown")
    pc_name = data.get("pc_name", "PC")
    orders = data.get("orders", [])
    if not batch_id or not orders:
        return jsonify({"ok": False, "error": "batch_id/orders missing"}), 400

    conn = db()
    cur = conn.cursor()
    t = now()

    for o in orders:
        order_id = str(o.get("order_id", "")).strip()
        if not order_id:
            continue
        cur.execute("""
        INSERT INTO orders(batch_id, firm_name, staff_name, pc_name, order_id, last6, sku, qty, raw_line, status, updated_at, loaded_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            batch_id, firm_name, staff_name, pc_name, order_id, order_id[-6:],
            str(o.get("sku", "UNKNOWN")).strip(),
            str(o.get("qty", "1")).strip(),
            str(o.get("raw_line", "")).strip(),
            "P", t, t
        ))

    upsert_activity(cur, firm_name, staff_name, pc_name, batch_id, "", "", f"Loaded {len(orders)} orders")
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "saved": len(orders)})

@app.route("/update-status", methods=["POST"])
def update_status():
    data = request.json or {}
    batch_id = data.get("batch_id", "")
    firm_name = data.get("firm_name", "Default Firm")
    staff_name = data.get("staff_name", "Unknown")
    pc_name = data.get("pc_name", "PC")
    order_id = str(data.get("order_id", "")).strip()
    status = data.get("status", "P")
    reason = data.get("reason", "")
    current_sku = data.get("current_sku", "")
    current_order = data.get("current_order", "")
    if status not in ["P", "R", "N", "S"]:
        return jsonify({"ok": False, "error": "Invalid status"}), 400

    t = now()
    conn = db()
    cur = conn.cursor()
    cur.execute("""
    UPDATE orders SET status=?, reason=?, checked_time=?, updated_at=?
    WHERE batch_id=? AND order_id=? AND firm_name=? AND staff_name=? AND pc_name=?
    """, (status, reason, t if status != "P" else "", t, batch_id, order_id, firm_name, staff_name, pc_name))
    upsert_activity(cur, firm_name, staff_name, pc_name, batch_id, current_sku, current_order, f"Marked {order_id[-6:]} as {status}")
    conn.commit()
    conn.close()
    return jsonify({"ok": True})

@app.route("/heartbeat", methods=["POST"])
def heartbeat():
    data = request.json or {}
    firm_name = data.get("firm_name", "Default Firm")
    staff_name = data.get("staff_name", "Unknown")
    pc_name = data.get("pc_name", "PC")
    batch_id = data.get("batch_id", "")
    current_sku = data.get("current_sku", "")
    current_order = data.get("current_order", "")
    last_action = data.get("last_action", "Heartbeat")
    conn = db()
    cur = conn.cursor()
    upsert_activity(cur, firm_name, staff_name, pc_name, batch_id, current_sku, current_order, last_action)
    conn.commit()
    conn.close()
    return jsonify({"ok": True})

def build_where(args):
    where = []
    params = []
    firm = args.get("firm", "").strip()
    staff = args.get("staff", "").strip()
    pc = args.get("pc", "").strip()
    sku = args.get("sku", "").strip()
    status = args.get("status", "").strip()
    batch = args.get("batch", "").strip()

    if firm and firm != "ALL":
        where.append("firm_name=?")
        params.append(firm)
    if staff and staff != "ALL":
        where.append("staff_name=?")
        params.append(staff)
    if pc and pc != "ALL":
        where.append("pc_name=?")
        params.append(pc)
    if sku and sku != "ALL":
        where.append("sku=?")
        params.append(sku)
    if status and status != "ALL":
        where.append("status=?")
        params.append(status)
    if batch and batch != "ALL":
        where.append("batch_id=?")
        params.append(batch)

    return (" WHERE " + " AND ".join(where)) if where else "", params

@app.route("/dashboard-data")
def dashboard_data():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
    SELECT firm_name, COUNT(*) total,
      SUM(CASE WHEN status='P' THEN 1 ELSE 0 END) pending,
      SUM(CASE WHEN status='R' THEN 1 ELSE 0 END) ready,
      SUM(CASE WHEN status='N' THEN 1 ELSE 0 END) not_found,
      SUM(CASE WHEN status='S' THEN 1 ELSE 0 END) uncomplete
    FROM orders GROUP BY firm_name ORDER BY firm_name
    """)
    firms = [dict(r) for r in cur.fetchall()]

    cur.execute("""
    SELECT firm_name, staff_name, pc_name, COUNT(*) total,
      SUM(CASE WHEN status='P' THEN 1 ELSE 0 END) pending,
      SUM(CASE WHEN status='R' THEN 1 ELSE 0 END) ready,
      SUM(CASE WHEN status='N' THEN 1 ELSE 0 END) not_found,
      SUM(CASE WHEN status='S' THEN 1 ELSE 0 END) uncomplete,
      MAX(updated_at) last_update,
      MIN(loaded_at) first_loaded
    FROM orders GROUP BY firm_name, staff_name, pc_name ORDER BY firm_name, staff_name
    """)
    staff = [dict(r) for r in cur.fetchall()]

    cur.execute("""
    SELECT firm_name, sku, COUNT(*) total,
      SUM(CASE WHEN status='P' THEN 1 ELSE 0 END) pending,
      SUM(CASE WHEN status='R' THEN 1 ELSE 0 END) ready,
      SUM(CASE WHEN status='N' THEN 1 ELSE 0 END) not_found,
      SUM(CASE WHEN status='S' THEN 1 ELSE 0 END) uncomplete
    FROM orders GROUP BY firm_name, sku ORDER BY firm_name, pending DESC, total DESC
    """)
    sku = [dict(r) for r in cur.fetchall()]

    cur.execute("""
    SELECT firm_name, batch_id, staff_name, pc_name, COUNT(*) total,
      SUM(CASE WHEN status='P' THEN 1 ELSE 0 END) pending,
      SUM(CASE WHEN status='R' THEN 1 ELSE 0 END) ready,
      SUM(CASE WHEN status='N' THEN 1 ELSE 0 END) not_found,
      SUM(CASE WHEN status='S' THEN 1 ELSE 0 END) uncomplete,
      MIN(loaded_at) loaded_at,
      MAX(updated_at) last_update
    FROM orders GROUP BY firm_name, batch_id, staff_name, pc_name ORDER BY loaded_at DESC
    """)
    batches = [dict(r) for r in cur.fetchall()]

    cur.execute("SELECT * FROM staff_activity ORDER BY firm_name, staff_name")
    activity = [dict(r) for r in cur.fetchall()]

    cur.execute("SELECT DISTINCT staff_name FROM orders ORDER BY staff_name")
    staff_names = [r[0] for r in cur.fetchall()]
    cur.execute("SELECT DISTINCT sku FROM orders ORDER BY sku")
    skus = [r[0] for r in cur.fetchall()]
    cur.execute("SELECT DISTINCT pc_name FROM orders ORDER BY pc_name")
    pcs = [r[0] for r in cur.fetchall()]

    conn.close()
    return jsonify({
        "firms": firms,
        "staff": staff,
        "sku": sku,
        "batches": batches,
        "activity": activity,
        "staff_names": staff_names,
        "skus": skus,
        "pcs": pcs,
        "server_time": now()
    })

@app.route("/download-report")
def download_report():
    report_type = request.args.get("type", "all").strip()
    where, params = build_where(request.args)

    extra_where = []
    if report_type == "ready":
        extra_where.append("status='R'")
    elif report_type == "pending":
        extra_where.append("status='P'")
    elif report_type == "not_found":
        extra_where.append("status='N'")
    elif report_type == "uncomplete":
        extra_where.append("status='S'")

    if extra_where:
        if where:
            where += " AND " + " AND ".join(extra_where)
        else:
            where = " WHERE " + " AND ".join(extra_where)

    conn = db()
    cur = conn.cursor()
    cur.execute(f"""
    SELECT firm_name, batch_id, staff_name, pc_name, order_id, last6, sku, qty, status, reason, checked_time, loaded_at, updated_at, raw_line
    FROM orders {where}
    ORDER BY firm_name, staff_name, pc_name, sku, order_id
    """, params)
    rows = cur.fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Firm", "Batch ID", "Staff", "PC", "Order ID", "Last 6", "SKU", "Qty", "Status", "Reason", "Checked Time", "Loaded At", "Updated At", "Raw Line"])
    for r in rows:
        writer.writerow([r["firm_name"], r["batch_id"], r["staff_name"], r["pc_name"], r["order_id"], r["last6"], r["sku"], r["qty"], r["status"], r["reason"], r["checked_time"], r["loaded_at"], r["updated_at"], r["raw_line"]])

    mem = io.BytesIO()
    mem.write(output.getvalue().encode("utf-8-sig"))
    mem.seek(0)
    filename = f"{report_type}_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return send_file(mem, mimetype="text/csv", as_attachment=True, download_name=filename)

@app.route("/download-final")
def download_final():
    where, params = build_where(request.args)
    pending_where = where
    pending_params = list(params)
    if pending_where:
        pending_where += " AND status='P'"
    else:
        pending_where = " WHERE status='P'"

    conn = db()
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM orders {pending_where}", pending_params)
    pending = cur.fetchone()[0]
    if pending > 0:
        conn.close()
        return jsonify({"ok": False, "error": f"Download locked. {pending} orders still pending in selected filter."}), 423

    cur.execute(f"""
    SELECT firm_name, batch_id, staff_name, pc_name, order_id, last6, sku, qty, status, reason, checked_time, loaded_at, updated_at
    FROM orders {where}
    ORDER BY firm_name, staff_name, pc_name, sku, order_id
    """, params)
    rows = cur.fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Firm", "Batch ID", "Staff", "PC", "Order ID", "Last 6", "SKU", "Qty", "Status", "Reason", "Checked Time", "Loaded At", "Updated At"])
    for r in rows:
        writer.writerow([r["firm_name"], r["batch_id"], r["staff_name"], r["pc_name"], r["order_id"], r["last6"], r["sku"], r["qty"], r["status"], r["reason"], r["checked_time"], r["loaded_at"], r["updated_at"]])

    mem = io.BytesIO()
    mem.write(output.getvalue().encode("utf-8-sig"))
    mem.seek(0)
    return send_file(mem, mimetype="text/csv", as_attachment=True, download_name="final_checked_report.csv")

@app.route("/dashboard")
def dashboard():
    return render_template_string("""
<!doctype html><html><head><title>Advanced Owner Dashboard V4</title><meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{font-family:Arial;background:#f3f5f7;margin:0;color:#111}.header{background:#111;color:#fff;padding:16px 22px;display:flex;justify-content:space-between;align-items:center}.wrap{padding:18px}.cards{display:grid;grid-template-columns:repeat(6,1fr);gap:12px;margin-bottom:16px}.card{background:white;border-radius:14px;padding:16px;box-shadow:0 2px 10px #0001}.card .title{font-size:13px;color:#666}.num{font-size:28px;font-weight:800;margin-top:4px}.green{color:green;font-weight:bold}.red{color:red;font-weight:bold}.yellow{color:#c77a00;font-weight:bold}.blue{color:#0b5ed7;font-weight:bold}.controls{display:flex;gap:10px;align-items:center;margin:14px 0;flex-wrap:wrap}select,input,button{padding:10px;border-radius:10px;border:1px solid #ccc}button{background:#111;color:white;font-weight:bold;cursor:pointer;border:0}.btn2{background:#0b5ed7}.btn3{background:green}.btn4{background:#c77a00}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}.panel{background:white;border-radius:14px;padding:14px;box-shadow:0 2px 10px #0001;margin-bottom:16px;overflow:auto}table{width:100%;border-collapse:collapse;font-size:13px}th{background:#111;color:white;padding:9px;text-align:left;position:sticky;top:0}td{border-bottom:1px solid #eee;padding:8px}.badge{padding:4px 8px;border-radius:999px;font-size:12px;font-weight:bold}.online{background:#e6f7eb;color:green}.idle{background:#fff3cd;color:#9a6a00}.offline{background:#fdeaea;color:red}.small{font-size:12px;color:#666}.progress{height:8px;background:#eee;border-radius:999px;overflow:hidden}.bar{height:8px;background:#111}.warning{background:#fff3cd;padding:10px;border-radius:10px;margin-bottom:10px}.danger{background:#fdeaea;padding:10px;border-radius:10px;margin-bottom:10px}@media(max-width:900px){.cards{grid-template-columns:repeat(2,1fr)}.grid2{grid-template-columns:1fr}}
</style></head><body>
<div class="header"><div><h2 style="margin:0">Advanced Owner Dashboard V4</h2><div class="small" style="color:#ccc">Firm wise • Staff wise • SKU wise • Reports • Live activity</div></div><div id="serverTime">-</div></div>
<div class="wrap">
<div class="controls">
<label><b>Firm:</b></label><select id="firmFilter"><option value="ALL">ALL FIRMS</option></select>
<label><b>Staff:</b></label><select id="staffFilter"><option value="ALL">ALL STAFF</option></select>
<label><b>SKU:</b></label><select id="skuFilter"><option value="ALL">ALL SKU</option></select>
<input id="searchBox" placeholder="Search anything">
<button onclick="downloadFinal()" class="btn3">Final CSV Locked</button>
<button onclick="downloadReport('all')" class="btn2">All CSV</button>
<button onclick="downloadReport('ready')" class="btn3">Ready CSV</button>
<button onclick="downloadReport('pending')" class="btn4">Pending CSV</button>
<button onclick="downloadReport('not_found')" class="btn4">Not Found CSV</button>
<button onclick="downloadReport('uncomplete')" class="btn4">Uncomplete CSV</button>
<span id="downloadMsg"></span>
</div>
<div id="alerts"></div>
<div class="cards"><div class="card"><div class="title">Total</div><div class="num" id="total">0</div></div><div class="card"><div class="title">Checked</div><div class="num blue" id="checked">0</div></div><div class="card"><div class="title">Ready</div><div class="num green" id="ready">0</div></div><div class="card"><div class="title">Not Found</div><div class="num yellow" id="nf">0</div></div><div class="card"><div class="title">Uncomplete</div><div class="num red" id="uc">0</div></div><div class="card"><div class="title">Pending</div><div class="num red" id="pending">0</div></div></div>
<div class="panel"><h3>Firm Wise Summary</h3><table><thead><tr><th>Firm</th><th>Total</th><th>Ready</th><th>NF</th><th>Uncomplete</th><th>Pending</th><th>Completion</th></tr></thead><tbody id="firmBody"></tbody></table></div>
<div class="grid2"><div class="panel"><h3>Live Staff Activity</h3><table><thead><tr><th>Status</th><th>Firm</th><th>Staff</th><th>PC</th><th>Current SKU</th><th>Current Order</th><th>Last Seen</th><th>Action</th></tr></thead><tbody id="activityBody"></tbody></table></div>
<div class="panel"><h3>Staff Wise Performance</h3><table><thead><tr><th>Firm</th><th>Staff</th><th>PC</th><th>Total</th><th>Ready</th><th>NF</th><th>UC</th><th>Pending</th><th>Completion</th></tr></thead><tbody id="staffBody"></tbody></table></div></div>
<div class="panel"><h3>SKU Wise Production Status</h3><table><thead><tr><th>Firm</th><th>SKU</th><th>Total</th><th>Ready</th><th>NF</th><th>Uncomplete</th><th>Pending</th><th>Completion</th></tr></thead><tbody id="skuBody"></tbody></table></div>
<div class="panel"><h3>Batch History</h3><table><thead><tr><th>Firm</th><th>Batch</th><th>Staff</th><th>PC</th><th>Total</th><th>Ready</th><th>NF</th><th>UC</th><th>Pending</th><th>Loaded</th><th>Last Update</th></tr></thead><tbody id="batchBody"></tbody></table></div>
</div>
<script>
let lastData=null;function safe(n){return n||0}function pct(r,t){return t?Math.round((r/t)*100):0}function progress(p){return `<div class="progress"><div class="bar" style="width:${p}%"></div></div><span class="small">${p}%</span>`}function badge(ls){if(!ls)return'<span class="badge offline">Offline</span>';let d=(Date.now()-new Date(ls.replace(' ','T')).getTime())/1000;if(d<30)return'<span class="badge online">Online</span>';if(d<180)return'<span class="badge idle">Idle</span>';return'<span class="badge offline">Offline</span>'}function filters(r){let f=document.getElementById('firmFilter').value;let st=document.getElementById('staffFilter').value;let sk=document.getElementById('skuFilter').value;let q=document.getElementById('searchBox').value.toLowerCase().trim();if(f!=='ALL'&&r.firm_name!==f)return false;if(st!=='ALL'&&r.staff_name!==st)return false;if(sk!=='ALL'&&r.sku!==sk)return false;if(q&&!JSON.stringify(r).toLowerCase().includes(q))return false;return true}function calc(rows){return rows.reduce((a,r)=>({total:a.total+safe(r.total),ready:a.ready+safe(r.ready),not_found:a.not_found+safe(r.not_found),uncomplete:a.uncomplete+safe(r.uncomplete),pending:a.pending+safe(r.pending)}),{total:0,ready:0,not_found:0,uncomplete:0,pending:0})}
async function loadData(){let res=await fetch('/dashboard-data');lastData=await res.json();renderData()}
function fillSelect(id, values, label){let el=document.getElementById(id), cur=el.value;el.innerHTML=`<option value="ALL">${label}</option>`+values.filter(Boolean).map(v=>`<option value="${v}">${v}</option>`).join('');if([...el.options].some(o=>o.value===cur))el.value=cur}
function renderData(){let data=lastData;if(!data)return;fillSelect('firmFilter',[...new Set(data.firms.map(x=>x.firm_name||'Default Firm'))].sort(),'ALL FIRMS');fillSelect('staffFilter',data.staff_names.sort(),'ALL STAFF');fillSelect('skuFilter',data.skus.sort(),'ALL SKU');document.getElementById('serverTime').innerText=data.server_time;let firmRows=data.firms.filter(filters);let o=calc(firmRows);document.getElementById('total').innerText=safe(o.total);document.getElementById('checked').innerText=safe(o.total)-safe(o.pending);document.getElementById('ready').innerText=safe(o.ready);document.getElementById('nf').innerText=safe(o.not_found);document.getElementById('uc').innerText=safe(o.uncomplete);document.getElementById('pending').innerText=safe(o.pending);let alerts=[];if(safe(o.pending)>0)alerts.push(`<div class="warning">⚠ Pending orders remaining: <b>${o.pending}</b>. Final locked CSV selected filters me pending 0 hone par hi download hoga.</div>`);let offline=(data.activity||[]).filter(a=>badge(a.last_seen).includes('Offline')).length;if(offline>0)alerts.push(`<div class="danger">🔴 ${offline} staff offline/idle long time. Live Staff Activity check karo.</div>`);document.getElementById('alerts').innerHTML=alerts.join('');
document.getElementById('firmBody').innerHTML=data.firms.filter(filters).map(r=>`<tr><td><b>${r.firm_name||'Default Firm'}</b></td><td>${safe(r.total)}</td><td class="green">${safe(r.ready)}</td><td class="yellow">${safe(r.not_found)}</td><td class="red">${safe(r.uncomplete)}</td><td class="red">${safe(r.pending)}</td><td>${progress(pct(safe(r.ready),safe(r.total)))}</td></tr>`).join('');
document.getElementById('activityBody').innerHTML=data.activity.filter(filters).map(r=>`<tr><td>${badge(r.last_seen)}</td><td>${r.firm_name||''}</td><td><b>${r.staff_name}</b></td><td>${r.pc_name}</td><td class="blue">${r.current_sku||''}</td><td>${r.current_order||''}</td><td>${r.last_seen||''}</td><td>${r.last_action||''}</td></tr>`).join('');
document.getElementById('staffBody').innerHTML=data.staff.filter(filters).map(r=>`<tr><td>${r.firm_name||''}</td><td><b>${r.staff_name}</b></td><td>${r.pc_name}</td><td>${safe(r.total)}</td><td class="green">${safe(r.ready)}</td><td class="yellow">${safe(r.not_found)}</td><td class="red">${safe(r.uncomplete)}</td><td class="red">${safe(r.pending)}</td><td>${progress(pct(safe(r.ready),safe(r.total)))}</td></tr>`).join('');
document.getElementById('skuBody').innerHTML=data.sku.filter(filters).map(r=>`<tr><td>${r.firm_name||''}</td><td><b>${r.sku||'-'}</b></td><td>${safe(r.total)}</td><td class="green">${safe(r.ready)}</td><td class="yellow">${safe(r.not_found)}</td><td class="red">${safe(r.uncomplete)}</td><td class="red">${safe(r.pending)}</td><td>${progress(pct(safe(r.ready),safe(r.total)))}</td></tr>`).join('');
document.getElementById('batchBody').innerHTML=data.batches.filter(filters).map(r=>`<tr><td>${r.firm_name||''}</td><td>${r.batch_id||''}</td><td>${r.staff_name}</td><td>${r.pc_name}</td><td>${safe(r.total)}</td><td class="green">${safe(r.ready)}</td><td class="yellow">${safe(r.not_found)}</td><td class="red">${safe(r.uncomplete)}</td><td class="red">${safe(r.pending)}</td><td>${r.loaded_at||''}</td><td>${r.last_update||''}</td></tr>`).join('')}
function params(){let p=new URLSearchParams();let f=document.getElementById('firmFilter').value, st=document.getElementById('staffFilter').value, sk=document.getElementById('skuFilter').value;if(f!=='ALL')p.set('firm',f);if(st!=='ALL')p.set('staff',st);if(sk!=='ALL')p.set('sku',sk);return p}
async function downloadReport(t){let p=params();p.set('type',t);window.location='/download-report?'+p.toString()}
async function downloadFinal(){let p=params();let msg=document.getElementById('downloadMsg');let res=await fetch('/download-final?'+p.toString());if(res.status===423){let d=await res.json();msg.innerText='❌ '+d.error;msg.className='red';return}let blob=await res.blob();let a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='final_checked_report.csv';a.click();msg.innerText='✅ Download started';msg.className='green'}
['firmFilter','staffFilter','skuFilter'].forEach(id=>document.getElementById(id).addEventListener('change',renderData));document.getElementById('searchBox').addEventListener('input',renderData);loadData();setInterval(loadData,3000);
</script></body></html>
    """)

init_db()
if __name__ == "__main__":
    port = int(os.environ.get("PORT", APP_PORT))
    app.run(host="0.0.0.0", port=port, debug=False)
