from flask import Flask, render_template, request, jsonify, session, redirect, make_response
from flask_mail import Mail, Message
import sqlite3
import os
import random
from datetime import datetime, timedelta
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "salesiq-demo-secret-2024-xyz-secure")
app.config['TEMPLATES_AUTO_RELOAD'] = True

# ── Flask-Mail ────────────────────────────────────────────────────────────────
app.config['MAIL_SERVER']         = 'smtp.gmail.com'
app.config['MAIL_PORT']           = 587
app.config['MAIL_USE_TLS']        = True
app.config['MAIL_USE_SSL']        = False
app.config['MAIL_USERNAME']       = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD']       = os.environ.get('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_USERNAME')
mail = Mail(app)

DB_PATH = os.environ.get("DB_PATH", "salesiq.db")

PRODUCTS = [
    {"name": "Running Shoes",      "base": 248, "trend": 0.80, "vol": 0.14},
    {"name": "Yoga Mat",           "base": 92,  "trend": 0.05, "vol": 0.10},
    {"name": "Protein Powder",     "base": 172, "trend": 1.20, "vol": 0.18},
    {"name": "Water Bottle",       "base": 113, "trend": 0.10, "vol": 0.09},
    {"name": "Resistance Bands",   "base": 68,  "trend": -0.50,"vol": 0.13},
    {"name": "Jump Rope",          "base": 46,  "trend": -0.30,"vol": 0.16},
    {"name": "Sports Headphones",  "base": 198, "trend": 1.50, "vol": 0.22},
    {"name": "Foam Roller",        "base": 82,  "trend": 0.20, "vol": 0.10},
    {"name": "Compression Socks",  "base": 57,  "trend": -0.40,"vol": 0.12},
    {"name": "Gym Gloves",         "base": 49,  "trend": 0.10, "vol": 0.11},
]


# ── Database ──────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS sales (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        date         TEXT NOT NULL,
        product_name TEXT NOT NULL,
        units_sold   INTEGER NOT NULL,
        revenue      REAL NOT NULL,
        customer_id  TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS upgrade_requests (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        name       TEXT NOT NULL,
        email      TEXT NOT NULL,
        company    TEXT NOT NULL,
        message    TEXT,
        created_at TEXT NOT NULL
    )""")
    c.execute("SELECT COUNT(*) FROM sales")
    if c.fetchone()[0] == 0:
        _seed(c)
    conn.commit()
    conn.close()


def _seed(cursor):
    random.seed(42)
    start = datetime.now() - timedelta(days=90)
    customers = [f"CUST{str(i).zfill(4)}" for i in range(1, 601)]

    for day in range(90):
        dt = start + timedelta(days=day)
        date_str = dt.strftime("%Y-%m-%d")
        weekend = dt.weekday() >= 5

        for p in PRODUCTS:
            trend_mul = 1 + (p["trend"] / 100 * day)
            noise = 1 + random.gauss(0, p["vol"])
            wk_mul = 0.78 if weekend else 1.0
            daily_rev = max(10.0, p["base"] * trend_mul * noise * wk_mul)

            num_tx = random.randint(2, 6)
            for _ in range(num_tx):
                units = random.randint(1, 3)
                rev = daily_rev / num_tx * random.uniform(0.82, 1.18)
                cursor.execute(
                    "INSERT INTO sales (date,product_name,units_sold,revenue,customer_id) VALUES (?,?,?,?,?)",
                    (date_str, p["name"], units, round(rev, 2), random.choice(customers)),
                )


# ── Auth ──────────────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        session["user"] = "demo"
        return f(*args, **kwargs)
    return wrapped


# ── Forecasting helpers ───────────────────────────────────────────────────────

def _linreg(xs, ys):
    n = len(xs)
    if n < 2:
        return 0.0, ys[0] if ys else 0.0
    xm = sum(xs) / n
    ym = sum(ys) / n
    num = sum((xs[i] - xm) * (ys[i] - ym) for i in range(n))
    den = sum((xi - xm) ** 2 for xi in xs)
    slope = num / den if den else 0.0
    return slope, ym - slope * xm


def forecast_series(daily_revs, days_ahead):
    if not daily_revs:
        return [0.0] * days_ahead
    n = len(daily_revs)
    alpha = 0.28
    ema = daily_revs[0]
    for r in daily_revs[1:]:
        ema = alpha * r + (1 - alpha) * ema

    window = daily_revs[-min(30, n):]
    wn = len(window)
    slope, intercept = _linreg(list(range(wn)), window)

    result = []
    for d in range(1, days_ahead + 1):
        trend_val = intercept + slope * (wn + d - 1)
        blended = 0.38 * ema + 0.62 * trend_val
        result.append(round(max(0.0, blended), 2))
    return result


def _product_daily(product_name):
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "SELECT date, SUM(revenue) as rev FROM sales WHERE product_name=? GROUP BY date ORDER BY date",
        (product_name,),
    )
    rows = c.fetchall()
    conn.close()
    return rows


# ── Page routes ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("landing.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        data = request.get_json(silent=True) or request.form
        if data.get("username") == "demo" and data.get("password") == "demo123":
            session["user"] = "demo"
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": "Invalid username or password"}), 401
    return render_template("index.html", page="login")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.route("/dashboard")
@login_required
def dashboard():
    return render_template("index.html", page="dashboard")


@app.route("/forecast")
@login_required
def forecast():
    return render_template("index.html", page="forecast")


@app.route("/products")
@login_required
def products():
    return render_template("index.html", page="products")


@app.route("/ltv")
@login_required
def ltv():
    return render_template("index.html", page="ltv")


@app.route("/upload")
@login_required
def upload():
    return render_template("index.html", page="upload")


@app.route("/integrations")
@login_required
def integrations():
    return render_template("index.html", page="integrations")


@app.route("/upgrade")
@login_required
def upgrade():
    return render_template("index.html", page="upgrade")


# ── API endpoints ─────────────────────────────────────────────────────────────

@app.route("/api/dashboard")
@login_required
def api_dashboard():
    conn = get_db()
    c = conn.cursor()
    cutoff_90 = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")

    c.execute("SELECT SUM(revenue) FROM sales WHERE date>=?", (cutoff_90,))
    total_rev = round(c.fetchone()[0] or 0, 2)

    c.execute(
        "SELECT product_name, SUM(revenue) as r FROM sales WHERE date>=? GROUP BY product_name ORDER BY r DESC LIMIT 1",
        (cutoff_90,),
    )
    best_row = c.fetchone()
    best_product = best_row["product_name"] if best_row else "N/A"

    c.execute(
        """SELECT AVG(dr) FROM (
              SELECT date, SUM(revenue) as dr FROM sales WHERE date>=? GROUP BY date
           ) t""",
        (cutoff_90,),
    )
    avg_daily = round(c.fetchone()[0] or 0, 2)

    c.execute(
        "SELECT date, SUM(revenue) as rev FROM sales WHERE date>=? GROUP BY date ORDER BY date",
        (cutoff_90,),
    )
    actual_rows = c.fetchall()
    conn.close()

    labels, actual_vals, forecast_vals = [], [], []
    for row in actual_rows:
        labels.append(row["date"])
        actual_vals.append(round(row["rev"], 2))
        forecast_vals.append(None)

    daily_revs = [r["rev"] for r in actual_rows]
    fcast_7 = forecast_series(daily_revs, 7)
    last_date = datetime.strptime(actual_rows[-1]["date"], "%Y-%m-%d") if actual_rows else datetime.now()
    for d, val in enumerate(fcast_7, 1):
        labels.append((last_date + timedelta(days=d)).strftime("%Y-%m-%d"))
        actual_vals.append(None)
        forecast_vals.append(val)

    predicted_7day = round(sum(fcast_7), 2)

    return jsonify({
        "total_rev": total_rev,
        "predicted_7day": predicted_7day,
        "best_product": best_product,
        "avg_daily": avg_daily,
        "chart": {"labels": labels, "actual": actual_vals, "forecast": forecast_vals},
    })


@app.route("/api/forecast")
@login_required
def api_forecast():
    conn = get_db()
    c = conn.cursor()
    cutoff_30 = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

    results = []
    for p in PRODUCTS:
        name = p["name"]
        c.execute("SELECT SUM(revenue) FROM sales WHERE product_name=? AND date>=?", (name, cutoff_30))
        last_30 = round(c.fetchone()[0] or 0, 2)

        rows = _product_daily(name)
        daily = [r["rev"] for r in rows]
        fcast_7 = sum(forecast_series(daily, 7))

        if len(daily) >= 14:
            recent7 = sum(daily[-7:]) / 7
            prev7 = sum(daily[-14:-7]) / 7
            pct = ((recent7 - prev7) / prev7 * 100) if prev7 else 0
        else:
            pct = 0

        if pct > 3:
            trend, trend_cls = "↑ Growing", "trend-up"
        elif pct < -3:
            trend, trend_cls = "↓ Declining", "trend-down"
        else:
            trend, trend_cls = "→ Stable", "trend-flat"

        results.append({
            "product": name,
            "last_30": last_30,
            "forecast_7": round(fcast_7, 2),
            "trend": trend,
            "trend_cls": trend_cls,
        })

    conn.close()
    return jsonify({"products": results})


@app.route("/api/products")
@login_required
def api_products():
    conn = get_db()
    c = conn.cursor()
    cutoff = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    c.execute(
        """SELECT product_name,
                  SUM(units_sold)              as units,
                  SUM(revenue)                 as revenue,
                  SUM(revenue)/SUM(units_sold) as avg_order
           FROM sales WHERE date>=?
           GROUP BY product_name ORDER BY revenue DESC LIMIT 10""",
        (cutoff,),
    )
    rows = c.fetchall()
    conn.close()
    return jsonify({
        "products": [
            {
                "name": r["product_name"],
                "units": int(r["units"]),
                "revenue": round(r["revenue"], 2),
                "avg_order": round(r["avg_order"], 2),
            }
            for r in rows
        ]
    })


@app.route("/api/upload", methods=["POST"])
@login_required
def api_upload():
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400
    f = request.files["file"]
    if not f.filename.lower().endswith(".csv"):
        return jsonify({"error": "Please upload a CSV file"}), 400

    raw = f.read().decode("utf-8", errors="replace")
    lines = [l for l in raw.strip().splitlines() if l.strip()]
    if len(lines) < 2:
        return jsonify({"error": "File appears empty or has no data rows"}), 400

    header = lines[0]
    data_lines = lines[1:]
    total_in_file = len(data_lines)
    was_limited = total_in_file > 50
    data_lines = data_lines[:50]

    conn = get_db()
    c = conn.cursor()
    ok = err = 0
    for line in data_lines:
        try:
            parts = [x.strip().strip('"') for x in line.split(",")]
            if len(parts) < 5:
                err += 1
                continue
            date_val, prod, units, rev, cust = parts[0], parts[1], int(parts[2]), float(parts[3]), parts[4]
            c.execute(
                "INSERT INTO sales (date,product_name,units_sold,revenue,customer_id) VALUES (?,?,?,?,?)",
                (date_val, prod, units, rev, cust),
            )
            ok += 1
        except Exception:
            err += 1
    conn.commit()
    conn.close()

    return jsonify({
        "ok": True,
        "processed": ok,
        "errors": err,
        "total_in_file": total_in_file,
        "was_limited": was_limited,
    })


@app.route("/api/download-template")
@login_required
def download_template():
    csv_lines = [
        "date,product_name,units_sold,revenue,customer_id",
        "2024-03-01,Running Shoes,3,299.97,CUST0001",
        "2024-03-01,Yoga Mat,1,49.99,CUST0002",
        "2024-03-02,Protein Powder,2,89.98,CUST0003",
        "2024-03-02,Water Bottle,4,79.96,CUST0004",
        "2024-03-03,Sports Headphones,1,149.99,CUST0005",
    ]
    resp = make_response("\n".join(csv_lines))
    resp.headers["Content-Type"] = "text/csv"
    resp.headers["Content-Disposition"] = "attachment; filename=salesiq_template.csv"
    return resp


@app.route('/contact', methods=['POST'])
def contact():
    name    = request.form.get('name',    '').strip()
    email   = request.form.get('email',   '').strip()
    company = request.form.get('company', '').strip()
    message = request.form.get('message', '').strip()

    if not name or not email or not message:
        return jsonify({'success': False, 'error': 'Please fill in all required fields.'})

    # Always persist the lead to the database
    conn = get_db()
    conn.execute(
        "INSERT INTO upgrade_requests (name,email,company,message,created_at) VALUES (?,?,?,?,?)",
        (name, email, company, message, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()

    # Send email only if credentials are configured
    if app.config.get('MAIL_USERNAME') and app.config.get('MAIL_PASSWORD'):
        try:
            msg = Message(
                subject=f'New SalesIQ Pro Inquiry — {name}',
                sender=app.config['MAIL_USERNAME'],
                recipients=['koynaduttaxox05@gmail.com'],
                body=(
                    f"New SalesIQ Pro Inquiry\n{'='*40}\n\n"
                    f"Name:    {name}\n"
                    f"Email:   {email}\n"
                    f"Company: {company}\n\n"
                    f"Message:\n{message or '(none provided)'}\n\n"
                    f"{'='*40}\nSent from SalesIQ contact form\n"
                ),
            )
            mail.send(msg)
        except Exception as e:
            print(f"Email error: {str(e)}")

    return jsonify({'success': True})


@app.route("/api/upgrade", methods=["POST"])
def api_upgrade():
    data = request.get_json(silent=True) or {}
    name    = (data.get("name")    or "").strip()
    email   = (data.get("email")   or "").strip()
    company = (data.get("company") or "").strip()
    message = (data.get("message") or "").strip()

    if not name or not email or not company:
        return jsonify({"error": "Name, email, and company are required"}), 400

    conn = get_db()
    conn.execute(
        "INSERT INTO upgrade_requests (name,email,company,message,created_at) VALUES (?,?,?,?,?)",
        (name, email, company, message, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()

    try:
        if app.config.get('MAIL_USERNAME') and app.config.get('MAIL_PASSWORD'):
            msg = Message(
                subject=f"New SalesIQ Pro Inquiry — {name}",
                sender=app.config['MAIL_USERNAME'],
                recipients=['koynaduttaxox05@gmail.com'],
            )
            msg.body = (
                f"New SalesIQ Pro Inquiry\n"
                f"{'='*40}\n\n"
                f"Name:    {name}\n"
                f"Email:   {email}\n"
                f"Company: {company}\n\n"
                f"Message:\n{message or '(none provided)'}\n\n"
                f"{'='*40}\n"
                f"Submitted via SalesIQ Demo\n"
            )
            mail.send(msg)
    except Exception:
        pass

    return jsonify({"ok": True})


@app.route('/admin/requests')
def admin_requests():
    key = request.args.get('key', '')
    if key != 'salesiq-admin':
        return '<h3 style="font-family:sans-serif;padding:40px;">Unauthorized — add <code>?key=salesiq-admin</code> to the URL.</h3>', 401

    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM upgrade_requests ORDER BY created_at DESC"
    ).fetchall()
    conn.close()

    rows_html = ''.join(f'''
    <tr>
      <td>{r["id"]}</td>
      <td>{r["name"]}</td>
      <td><a href="mailto:{r["email"]}" style="color:#a78bfa">{r["email"]}</a></td>
      <td>{r["company"] or "—"}</td>
      <td style="max-width:300px;word-break:break-word">{r["message"] or "—"}</td>
      <td style="white-space:nowrap">{r["created_at"][:16]}</td>
    </tr>''' for r in rows)

    return f'''<!DOCTYPE html>
<html><head><title>Demo Requests — Admin</title>
<style>
  body{{font-family:Inter,sans-serif;background:#060810;color:#f8fafc;padding:40px;margin:0}}
  h1{{font-size:28px;font-weight:800;margin-bottom:8px}}
  .sub{{color:#64748b;margin-bottom:32px;font-size:14px}}
  table{{width:100%;border-collapse:collapse;font-size:14px}}
  th{{background:rgba(124,58,237,0.2);padding:12px 16px;text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.8px;color:#a78bfa}}
  td{{padding:12px 16px;border-bottom:1px solid rgba(255,255,255,0.06);vertical-align:top}}
  tr:hover td{{background:rgba(255,255,255,0.03)}}
  .empty{{text-align:center;padding:60px;color:#64748b}}
</style></head>
<body>
  <h1>Demo Call Requests</h1>
  <p class="sub">{len(rows)} submission{"s" if len(rows)!=1 else ""} total</p>
  <table>
    <thead><tr><th>#</th><th>Name</th><th>Email</th><th>Company</th><th>Message</th><th>Submitted</th></tr></thead>
    <tbody>{"".join(rows_html) if rows else '<tr><td colspan="6" class="empty">No submissions yet.</td></tr>'}</tbody>
  </table>
</body></html>'''


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
