from flask import Flask, render_template, request, jsonify, session, redirect, make_response
import resend
import sqlite3
import io
import os
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
import random
import traceback
from datetime import datetime, timedelta
from functools import wraps

try:
    import pandas as pd
    PANDAS_OK = True
except ImportError:
    PANDAS_OK = False

app = Flask(__name__, static_folder='static')
app.secret_key = os.environ.get("SECRET_KEY", "salesiq-demo-secret-2024-xyz-secure")
app.config['TEMPLATES_AUTO_RELOAD'] = True

resend.api_key = os.environ.get('RESEND_API_KEY')

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

    c.execute("""CREATE TABLE IF NOT EXISTS sales_data (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        date         TEXT NOT NULL,
        product_name TEXT NOT NULL,
        units_sold   INTEGER NOT NULL,
        revenue      REAL NOT NULL,
        customer_id  TEXT NOT NULL,
        uploaded_at  TEXT NOT NULL
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS app_state (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS upgrade_requests (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        name       TEXT NOT NULL,
        email      TEXT NOT NULL,
        company    TEXT NOT NULL,
        message    TEXT,
        created_at TEXT NOT NULL
    )""")

    c.execute("SELECT COUNT(*) FROM sales_data")
    if c.fetchone()[0] == 0:
        _seed(c)
        c.execute("INSERT OR REPLACE INTO app_state (key, value) VALUES ('data_source', 'sample')")

    conn.commit()
    conn.close()


def _seed(cursor):
    random.seed(42)
    start = datetime.now() - timedelta(days=90)
    customers = [f"CUST{str(i).zfill(4)}" for i in range(1, 601)]
    now = datetime.now().isoformat()

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
                    "INSERT INTO sales_data (date,product_name,units_sold,revenue,customer_id,uploaded_at)"
                    " VALUES (?,?,?,?,?,?)",
                    (date_str, p["name"], units, round(rev, 2), random.choice(customers), now),
                )


def get_data_source():
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT value FROM app_state WHERE key='data_source'")
        row = c.fetchone()
        conn.close()
        return row['value'] if row else 'sample'
    except Exception:
        return 'sample'


def _date_filter(data_source, days=90):
    """Return (where_clause, params) for the active data source."""
    if data_source == 'sample':
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        return "WHERE date >= ?", (cutoff,)
    return "", ()


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
    data_source = get_data_source()
    return render_template("index.html", page="dashboard", data_source=data_source)


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


@app.route("/upload", methods=["GET", "POST"])
@login_required
def upload():
    if request.method != "POST":
        return render_template("index.html", page="upload")

    try:
        if "file" not in request.files:
            return jsonify({"success": False, "error": "No file provided"})

        f = request.files["file"]
        filename = f.filename or ""
        if not filename.lower().endswith(".csv"):
            return jsonify({"success": False, "error": "Please upload a CSV file"})

        raw_bytes = f.read()
        if not raw_bytes:
            return jsonify({"success": False, "error": "The uploaded file is empty"})

        # Parse CSV — use pandas if available, else fall back to manual parse
        rows = _parse_csv(raw_bytes)
        if isinstance(rows, str):
            return jsonify({"success": False, "error": rows})

        required = {'date', 'product_name', 'units_sold', 'revenue', 'customer_id'}
        missing = required - set(rows[0].keys()) if rows else required
        if missing:
            return jsonify({"success": False,
                            "error": "CSV must have columns: date, product_name, units_sold, revenue, customer_id"})

        rows = rows[:50]
        now = datetime.now().isoformat()

        conn = get_db()
        c = conn.cursor()
        c.execute("DELETE FROM sales_data")

        ok = 0
        for row in rows:
            try:
                c.execute(
                    "INSERT INTO sales_data (date,product_name,units_sold,revenue,customer_id,uploaded_at)"
                    " VALUES (?,?,?,?,?,?)",
                    (
                        str(row['date']).strip(),
                        str(row['product_name']).strip(),
                        int(float(str(row['units_sold']))),
                        float(str(row['revenue'])),
                        str(row['customer_id']).strip(),
                        now,
                    ),
                )
                ok += 1
            except Exception:
                pass

        c.execute("INSERT OR REPLACE INTO app_state (key, value) VALUES ('data_source', 'uploaded')")
        conn.commit()
        conn.close()

        return jsonify({"success": True, "rows": ok, "message": f"{ok} rows uploaded successfully"})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": f"Server error: {e}"})


def _parse_csv(raw_bytes):
    """Parse CSV bytes → list of dicts. Returns error string on failure."""
    if PANDAS_OK:
        try:
            df = pd.read_csv(io.BytesIO(raw_bytes))
            return df.to_dict(orient='records')
        except Exception as e:
            pass  # fall through to manual parser

    # Manual fallback parser
    try:
        text = raw_bytes.decode('utf-8', errors='replace')
        lines = [l for l in text.splitlines() if l.strip()]
        if len(lines) < 2:
            return "File appears empty or has no data rows"
        header = [h.strip().strip('"').lower() for h in lines[0].split(',')]
        result = []
        for line in lines[1:]:
            parts = [p.strip().strip('"') for p in line.split(',')]
            if len(parts) >= len(header):
                result.append(dict(zip(header, parts)))
        return result
    except Exception as e:
        return f"Could not parse CSV: {e}"


@app.route("/integrations")
@login_required
def integrations():
    return render_template("index.html", page="integrations")


@app.route("/upgrade")
@login_required
def upgrade():
    return render_template("index.html", page="upgrade")


# ── API endpoints — pure SQL, no pandas ──────────────────────────────────────

@app.route("/api/dashboard")
@login_required
def api_dashboard():
    try:
        data_source = get_data_source()
        where, params = _date_filter(data_source, days=90)

        conn = get_db()
        c = conn.cursor()

        c.execute(f"SELECT SUM(revenue) FROM sales_data {where}", params)
        total_rev = round(c.fetchone()[0] or 0, 2)

        c.execute(
            f"SELECT product_name, SUM(revenue) as r FROM sales_data {where}"
            f" GROUP BY product_name ORDER BY r DESC LIMIT 1", params
        )
        best_row = c.fetchone()
        best_product = best_row["product_name"] if best_row else "N/A"

        c.execute(
            f"SELECT AVG(dr) FROM (SELECT date, SUM(revenue) as dr FROM sales_data {where} GROUP BY date) t",
            params,
        )
        avg_daily = round(c.fetchone()[0] or 0, 2)

        c.execute(
            f"SELECT date, SUM(revenue) as rev FROM sales_data {where} GROUP BY date ORDER BY date",
            params,
        )
        actual_rows = c.fetchall()
        conn.close()

        labels, actual_vals, forecast_vals = [], [], []
        for row in actual_rows:
            labels.append(row["date"])
            actual_vals.append(round(row["rev"], 2))
            forecast_vals.append(None)

        fcast_7 = forecast_series([r["rev"] for r in actual_rows], 7)
        last_date = datetime.strptime(actual_rows[-1]["date"], "%Y-%m-%d") if actual_rows else datetime.now()
        for d, val in enumerate(fcast_7, 1):
            labels.append((last_date + timedelta(days=d)).strftime("%Y-%m-%d"))
            actual_vals.append(None)
            forecast_vals.append(val)

        return jsonify({
            "total_rev": total_rev,
            "predicted_7day": round(sum(fcast_7), 2),
            "best_product": best_product,
            "avg_daily": avg_daily,
            "data_source": data_source,
            "chart": {"labels": labels, "actual": actual_vals, "forecast": forecast_vals},
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/forecast")
@login_required
def api_forecast():
    try:
        data_source = get_data_source()
        where_30, params_30 = _date_filter(data_source, days=30)

        conn = get_db()
        c = conn.cursor()

        c.execute("SELECT DISTINCT product_name FROM sales_data")
        products = [r["product_name"] for r in c.fetchall()]

        results = []
        for name in products:
            if where_30:
                c.execute(
                    f"SELECT SUM(revenue) FROM sales_data {where_30} AND product_name=?",
                    params_30 + (name,),
                )
            else:
                c.execute("SELECT SUM(revenue) FROM sales_data WHERE product_name=?", (name,))
            last_30 = round(c.fetchone()[0] or 0, 2)

            c.execute(
                "SELECT date, SUM(revenue) as rev FROM sales_data WHERE product_name=?"
                " GROUP BY date ORDER BY date",
                (name,),
            )
            rows = c.fetchall()
            daily = [r["rev"] for r in rows]
            fcast_7 = sum(forecast_series(daily, 7))

            if len(daily) >= 14:
                recent7 = sum(daily[-7:]) / 7
                prev7   = sum(daily[-14:-7]) / 7
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

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/products")
@login_required
def api_products():
    try:
        data_source = get_data_source()
        where, params = _date_filter(data_source, days=90)

        conn = get_db()
        c = conn.cursor()
        c.execute(
            f"""SELECT product_name,
                      SUM(units_sold)              as units,
                      SUM(revenue)                 as revenue,
                      SUM(revenue)/SUM(units_sold) as avg_order
               FROM sales_data {where}
               GROUP BY product_name ORDER BY revenue DESC LIMIT 10""",
            params,
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

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/reset", methods=["POST"])
@login_required
def api_reset():
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("DELETE FROM sales_data")
        _seed(c)
        c.execute("INSERT OR REPLACE INTO app_state (key, value) VALUES ('data_source', 'sample')")
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/download-template")
@login_required
def download_template():
    today = datetime.now()
    rows = [
        ((today - timedelta(days=5)).strftime("%Y-%m-%d"), "Running Shoes",     3, 299.97, "CUST0001"),
        ((today - timedelta(days=5)).strftime("%Y-%m-%d"), "Yoga Mat",          1,  49.99, "CUST0002"),
        ((today - timedelta(days=4)).strftime("%Y-%m-%d"), "Protein Powder",    2,  89.98, "CUST0003"),
        ((today - timedelta(days=4)).strftime("%Y-%m-%d"), "Water Bottle",      4,  79.96, "CUST0004"),
        ((today - timedelta(days=3)).strftime("%Y-%m-%d"), "Sports Headphones", 1, 149.99, "CUST0005"),
    ]
    lines = ["date,product_name,units_sold,revenue,customer_id"] + [
        f"{r[0]},{r[1]},{r[2]},{r[3]},{r[4]}" for r in rows
    ]
    resp = make_response("\n".join(lines))
    resp.headers["Content-Type"] = "text/csv"
    resp.headers["Content-Disposition"] = "attachment; filename=salesiq_template.csv"
    return resp


@app.route('/contact', methods=['POST'])
def contact():
    name    = request.form.get('name',    '').strip()
    email   = request.form.get('email',   '').strip()
    company = request.form.get('company', '').strip()
    message = request.form.get('message', '').strip()

    if not name or not email:
        return jsonify({'success': False, 'error': 'Please fill in all required fields.'})

    conn = get_db()
    conn.execute(
        "INSERT INTO upgrade_requests (name,email,company,message,created_at) VALUES (?,?,?,?,?)",
        (name, email, company, message, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()

    try:
        resend.Emails.send({
            "from": "SalesIQ <onboarding@resend.dev>",
            "to": ["koynaduttaxox05@gmail.com"],
            "subject": f"New SalesIQ Pro Inquiry — {name}",
            "html": f"""
                <h2>New inquiry from SalesIQ</h2>
                <p><strong>Name:</strong> {name}</p>
                <p><strong>Email:</strong> {email}</p>
                <p><strong>Company:</strong> {company}</p>
                <p><strong>Message:</strong> {message or '(none provided)'}</p>
            """,
        })
    except Exception as e:
        print(f"Email error: {e}")

    return jsonify({'success': True})


@app.route("/api/upgrade", methods=["POST"])
def api_upgrade():
    data = request.get_json(silent=True) or {}
    name    = (data.get("name")    or "").strip()
    email   = (data.get("email")   or "").strip()
    company = (data.get("company") or "").strip()
    message = (data.get("message") or "").strip()

    if not name or not email:
        return jsonify({"error": "Name and email are required"}), 400

    conn = get_db()
    conn.execute(
        "INSERT INTO upgrade_requests (name,email,company,message,created_at) VALUES (?,?,?,?,?)",
        (name, email, company, message, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()

    try:
        resend.Emails.send({
            "from": "SalesIQ <onboarding@resend.dev>",
            "to": ["koynaduttaxox05@gmail.com"],
            "subject": f"New SalesIQ Pro Inquiry — {name}",
            "html": f"""
                <h2>New inquiry from SalesIQ</h2>
                <p><strong>Name:</strong> {name}</p>
                <p><strong>Email:</strong> {email}</p>
                <p><strong>Company:</strong> {company}</p>
                <p><strong>Message:</strong> {message or '(none provided)'}</p>
            """,
        })
    except Exception as e:
        print(f"Email error: {e}")

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
