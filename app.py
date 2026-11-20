import os, re, sqlite3, smtplib, time
from datetime import datetime
from email.message import EmailMessage
from functools import wraps
from flask import Flask, request, jsonify, session, render_template_string, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "campusresale.db")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-before-deployment")
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  MAX_CONTENT_LENGTH=5 * 1024 * 1024)
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
CATEGORIES = ["textbooks", "electronics", "calculators", "furniture", "bicycles",
              "clothing", "stationery", "other"]
CONDITION_FACTORS = {"like new": 0.90, "good": 0.75, "fair": 0.55, "poor": 0.35}
DEPRECIATION = {"textbooks": 0.30, "electronics": 0.25, "calculators": 0.20,
                "furniture": 0.12, "bicycles": 0.15, "clothing": 0.35,
                "stationery": 0.20, "other": 0.20}
DEFAULT_NEW_PRICE = {"textbooks": 800, "electronics": 5000, "calculators": 1200,
                     "furniture": 3000, "bicycles": 5000, "clothing": 1000,
                     "stationery": 300, "other": 1000}

def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con

def init_db():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users(
          id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
          password_hash TEXT NOT NULL, verified INTEGER DEFAULT 0, phone TEXT,
          created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS listings(
          id INTEGER PRIMARY KEY AUTOINCREMENT, seller_id INTEGER NOT NULL REFERENCES users(id),
          title TEXT NOT NULL, description TEXT, category TEXT NOT NULL, condition TEXT NOT NULL,
          price REAL NOT NULL, original_price REAL, age_months INTEGER DEFAULT 0, image TEXT,
          contact_phone TEXT NOT NULL, semester_end INTEGER DEFAULT 0,
          status TEXT DEFAULT 'active', buyer_id INTEGER REFERENCES users(id),
          sold_at TEXT, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS reports(
          id INTEGER PRIMARY KEY AUTOINCREMENT, listing_id INTEGER NOT NULL REFERENCES listings(id),
          reporter_id INTEGER NOT NULL REFERENCES users(id), reason TEXT,
          UNIQUE(listing_id, reporter_id));
        CREATE TABLE IF NOT EXISTS ratings(
          id INTEGER PRIMARY KEY AUTOINCREMENT, seller_id INTEGER NOT NULL REFERENCES users(id),
          rater_id INTEGER NOT NULL REFERENCES users(id), stars INTEGER NOT NULL,
          comment TEXT, UNIQUE(seller_id, rater_id));
        """)
        # Add a small set of clearly labelled sample listings on first run.
        # The demo account has a random password and cannot be used as a normal login.
        seed_time = datetime.now().isoformat(timespec="seconds")
        con.execute("""INSERT OR IGNORE INTO users(name,email,password_hash,verified,phone,created_at)
                    VALUES(?,?,?,?,?,?)""",
                    ("CampusResale Demo Seller", "demo@campusresale.local",
                     generate_password_hash(os.urandom(24).hex()), 1, "9876543210", seed_time))
        demo_id = con.execute("SELECT id FROM users WHERE email=?",
                              ("demo@campusresale.local",)).fetchone()["id"]
        seeded = con.execute("SELECT COUNT(*) n FROM listings WHERE seller_id=?", (demo_id,)).fetchone()["n"]
        samples = [
            ("Engineering Mathematics Textbook", "First-year engineering mathematics textbook. Sample listing for preview.", "textbooks", "good", 320, 650, 18, "9876543210", 1, "engineering_textbook.jpg"),
            ("Scientific Calculator", "Scientific calculator in good working condition. Sample listing.", "calculators", "like new", 650, 1200, 5, "9876543210", 0, "scientific_calculator.jpg"),
            ("Wireless Mouse", "Wireless USB mouse in good working condition. Sample listing.", "electronics", "good", 450, 900, 10, "9876543210", 0, "wireless_mouse.jpg"),
            ("Study Table", "Compact study table suitable for hostel rooms. Sample listing.", "furniture", "fair", 850, 1800, 24, "9876543210", 1, "study_table.jpg"),
            ("College Backpack", "Spacious backpack with laptop compartment. Sample listing.", "clothing", "good", 500, 1100, 12, "9876543210", 0, "college_backpack.jpg"),
            ("Cycle - Single Speed", "Well-maintained cycle, suitable for commuting around campus. Sample listing.", "bicycles", "fair", 2200, 5000, 20, "9876543210", 1, "campus_bicycle.jpg"),
        ]
        if seeded == 0:
            for title, description, category, condition, price, original, age, phone, semester, photo in samples:
                con.execute("""INSERT INTO listings(seller_id,title,description,category,condition,price,
                              original_price,age_months,image,contact_phone,semester_end,status,created_at)
                              VALUES(?,?,?,?,?,?,?,?,?,?,?,'active',?)""",
                            (demo_id,title,description,category,condition,price,original,age,
                             "/uploads/" + photo,phone,semester,seed_time))
        else:
            # Upgrade existing demo listings so earlier installations also receive photos.
            for title, description, category, condition, price, original, age, phone, semester, photo in samples:
                con.execute("UPDATE listings SET image=? WHERE seller_id=? AND title=? AND (image IS NULL OR image='')",
                            ("/uploads/" + photo, demo_id, title))
init_db()

def now():
    return datetime.now().isoformat(timespec="seconds")

def current_user():
    uid = session.get("user_id")
    if not uid: return None
    with db() as con:
        row = con.execute("SELECT id,name,email,phone FROM users WHERE id=?", (uid,)).fetchone()
        return dict(row) if row else None

def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not current_user(): return jsonify(error="Login required"), 401
        return fn(*args, **kwargs)
    return wrapped

def valid_phone(phone):
    return bool(re.fullmatch(r"[6-9]\d{9}", phone or ""))

def allowed_file(name):
    return "." in name and name.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def price_suggestion(category, condition, age_months, original_price=None):
    category = category if category in CATEGORIES else "other"
    condition = condition if condition in CONDITION_FACTORS else "good"
    original = float(original_price) if original_price not in (None, "", 0, "0") else DEFAULT_NEW_PRICE[category]
    age_years = max(0, int(age_months or 0)) / 12
    rule = original * ((1 - DEPRECIATION[category]) ** age_years) * CONDITION_FACTORS[condition]
    with db() as con:
        similar = con.execute("""SELECT price FROM listings
          WHERE category=? AND condition=? AND status='active'""", (category, condition)).fetchall()
    prices = [float(x["price"]) for x in similar]
    estimate = rule
    method = "rules"
    if len(prices) >= 5:
        prices.sort()
        n = len(prices)
        median = prices[n//2] if n % 2 else (prices[n//2-1] + prices[n//2]) / 2
        estimate = 0.60 * rule + 0.40 * median
        method = "60% rules + 40% similar-listing median"
    return {"low": round(estimate * .90, 2), "mid": round(estimate, 2),
            "high": round(estimate * 1.10, 2), "method": method,
            "similar_count": len(prices), "used_default_price": original_price in (None, "", 0, "0")}

def listing_dict(row, include_contact=False):
    d = dict(row)
    d["semester_end"] = bool(d["semester_end"])
    if not include_contact:
        d.pop("contact_phone", None)
        d.pop("seller_email", None)
    return d

@app.get("/")
def home():
    return render_template_string(PAGE, categories=CATEGORIES)

@app.get("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)

@app.post("/api/register")
def register():
    data = request.get_json(silent=True) or request.form
    name, email, password = (data.get("name","").strip(), data.get("email","").strip().lower(), data.get("password",""))
    phone = data.get("phone","").strip()
    domain = os.environ.get("COLLEGE_EMAIL_DOMAIN", "").strip().lower()
    if not name or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or len(password) < 8:
        return jsonify(error="Enter a name, valid email and password with at least 8 characters."), 400
    if domain and not email.endswith("@" + domain):
        return jsonify(error="Use your college email address."), 400
    if phone and not valid_phone(phone):
        return jsonify(error="Phone must be a valid 10-digit Indian mobile number."), 400
    try:
        with db() as con:
            cur = con.execute("INSERT INTO users(name,email,password_hash,verified,phone,created_at) VALUES(?,?,?,?,?,?)",
              (name,email,generate_password_hash(password),0,phone,now()))
            session["user_id"] = cur.lastrowid
    except sqlite3.IntegrityError:
        return jsonify(error="An account with this email already exists."), 409
    return jsonify(user=current_user()), 201

@app.post("/api/login")
def login():
    data=request.get_json(silent=True) or request.form
    with db() as con:
        user=con.execute("SELECT * FROM users WHERE email=?", (data.get("email","").strip().lower(),)).fetchone()
    if not user or not check_password_hash(user["password_hash"], data.get("password","")):
        return jsonify(error="Incorrect email or password."), 401
    session["user_id"]=user["id"]
    return jsonify(user=current_user())

@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)

@app.get("/api/me")
def me():
    user=current_user()
    return jsonify(user=user) if user else (jsonify(user=None), 200)

@app.get("/api/listings")
def get_listings():
    q=request.args.get("search","").strip()
    category=request.args.get("category","")
    condition=request.args.get("condition","")
    max_price=request.args.get("max_price","")
    semester=request.args.get("semester_end","")
    sort=request.args.get("sort","newest")
    sql="""SELECT l.*,u.name seller_name,u.email seller_email,
           (SELECT COUNT(*) FROM reports r WHERE r.listing_id=l.id) report_count,
           (SELECT AVG(stars) FROM ratings rt WHERE rt.seller_id=l.seller_id) seller_rating
           FROM listings l JOIN users u ON u.id=l.seller_id WHERE l.status='active'"""
    params=[]
    if q: sql += " AND (l.title LIKE ? OR l.description LIKE ?)"; params += [f"%{q}%",f"%{q}%"]
    if category in CATEGORIES: sql += " AND l.category=?"; params.append(category)
    if condition in CONDITION_FACTORS: sql += " AND l.condition=?"; params.append(condition)
    if max_price:
        try: sql += " AND l.price<=?"; params.append(float(max_price))
        except ValueError: pass
    if semester=="1": sql += " AND l.semester_end=1"
    sql += " ORDER BY l.price ASC" if sort=="price_low" else " ORDER BY l.price DESC" if sort=="price_high" else " ORDER BY l.created_at DESC"
    with db() as con: rows=con.execute(sql,params).fetchall()
    out=[]
    for r in rows:
        item=listing_dict(r, bool(current_user()))
        rec=price_suggestion(item["category"], item["condition"], item["age_months"], item["original_price"])
        item["price_check"] = item["price"] < 0.40 * rec["mid"]
        item["recommended_mid"] = rec["mid"]
        out.append(item)
    return jsonify(listings=out)

@app.get("/api/listings/<int:lid>")
def get_listing(lid):
    with db() as con:
        row=con.execute("""SELECT l.*,u.name seller_name,u.email seller_email,
          (SELECT COUNT(*) FROM reports r WHERE r.listing_id=l.id) report_count,
          (SELECT AVG(stars) FROM ratings rt WHERE rt.seller_id=l.seller_id) seller_rating
          FROM listings l JOIN users u ON u.id=l.seller_id WHERE l.id=? AND
          (l.status='active' OR l.seller_id=?)""",(lid,session.get("user_id",-1))).fetchone()
    if not row: return jsonify(error="Listing not found"),404
    return jsonify(listing=listing_dict(row,bool(current_user())))

@app.post("/api/listings")
@login_required
def create_listing():
    data=request.form
    title=data.get("title","").strip(); category=data.get("category","").lower()
    condition=data.get("condition","").lower(); phone=data.get("contact_phone","").strip()
    try:
        price=float(data.get("price","")); age=int(data.get("age_months","0") or 0)
        original=float(data.get("original_price") or 0) or None
    except ValueError: return jsonify(error="Enter valid price, original price and age."),400
    if not title or category not in CATEGORIES or condition not in CONDITION_FACTORS or price<=0 or age<0 or not valid_phone(phone):
        return jsonify(error="Check required fields and enter a valid 10-digit Indian mobile number."),400
    image=None
    file=request.files.get("image")
    if file and file.filename:
        if not allowed_file(file.filename): return jsonify(error="Image must be PNG, JPG, JPEG or WEBP."),400
        name=f"{int(time.time())}_{secure_filename(file.filename)}"
        file.save(os.path.join(UPLOAD_DIR,name)); image="/uploads/"+name
    with db() as con:
        cur=con.execute("""INSERT INTO listings(seller_id,title,description,category,condition,price,
          original_price,age_months,image,contact_phone,semester_end,status,created_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,'active',?)""",
          (current_user()["id"],title,data.get("description",""),category,condition,price,original,age,image,phone,
           1 if data.get("semester_end")=="1" else 0,now()))
    return jsonify(id=cur.lastrowid,ok=True),201

@app.post("/api/listings/<int:lid>/sold")
@login_required
def mark_sold(lid):
    data=request.get_json(silent=True) or {}
    buyer_email=data.get("buyer_email","").strip().lower()
    with db() as con:
        row=con.execute("SELECT * FROM listings WHERE id=?",(lid,)).fetchone()
        if not row: return jsonify(error="Listing not found"),404
        if row["seller_id"]!=current_user()["id"]: return jsonify(error="Only the seller can mark this item sold."),403
        buyer=con.execute("SELECT id FROM users WHERE email=?",(buyer_email,)).fetchone() if buyer_email else None
        con.execute("UPDATE listings SET status='sold',buyer_id=?,sold_at=? WHERE id=?",
                    (buyer["id"] if buyer else None,now(),lid))
    return jsonify(ok=True,buyer_recorded=bool(buyer))

@app.delete("/api/listings/<int:lid>")
@login_required
def delete_listing(lid):
    with db() as con:
        row=con.execute("SELECT * FROM listings WHERE id=?",(lid,)).fetchone()
        if not row:return jsonify(error="Listing not found"),404
        if row["seller_id"]!=current_user()["id"]:return jsonify(error="Only the seller can delete this listing."),403
        if row["buyer_id"] is not None:return jsonify(error="A listing recorded in a buyer's purchase history cannot be deleted."),409
        con.execute("DELETE FROM listings WHERE id=?",(lid,))
    return jsonify(ok=True)

@app.post("/api/listings/<int:lid>/report")
@login_required
def report_listing(lid):
    data=request.get_json(silent=True) or {}
    uid=current_user()["id"]
    with db() as con:
        row=con.execute("SELECT * FROM listings WHERE id=?",(lid,)).fetchone()
        if not row or row["status"]!="active":return jsonify(error="Listing not available."),404
        if row["seller_id"]==uid:return jsonify(error="You cannot report your own listing."),400
        try: con.execute("INSERT INTO reports(listing_id,reporter_id,reason) VALUES(?,?,?)",(lid,uid,data.get("reason","")))
        except sqlite3.IntegrityError:return jsonify(error="You have already reported this listing."),409
        count=con.execute("SELECT COUNT(*) n FROM reports WHERE listing_id=?",(lid,)).fetchone()["n"]
        if count>=3: con.execute("UPDATE listings SET status='flagged' WHERE id=?",(lid,))
    return jsonify(ok=True,report_count=count,hidden=count>=3)

@app.post("/api/users/<int:uid>/rate")
@login_required
def rate_seller(uid):
    data=request.get_json(silent=True) or {}
    try: stars=int(data.get("stars"))
    except: return jsonify(error="Rating must be 1 to 5."),400
    if stars<1 or stars>5 or uid==current_user()["id"]:return jsonify(error="Rating must be 1 to 5 and cannot rate yourself."),400
    with db() as con:
        if not con.execute("SELECT id FROM users WHERE id=?",(uid,)).fetchone():return jsonify(error="Seller not found."),404
        con.execute("""INSERT INTO ratings(seller_id,rater_id,stars,comment) VALUES(?,?,?,?)
          ON CONFLICT(seller_id,rater_id) DO UPDATE SET stars=excluded.stars,comment=excluded.comment""",
          (uid,current_user()["id"],stars,data.get("comment","")))
    return jsonify(ok=True)

@app.get("/api/price-suggest")
def price_suggest():
    return jsonify(price_suggestion(request.args.get("category","other"),
      request.args.get("condition","good"),request.args.get("age_months",0),
      request.args.get("original_price")))

@app.get("/api/account")
@login_required
def account():
    u=current_user()
    with db() as con:
        posted=con.execute("SELECT COUNT(*) n FROM listings WHERE seller_id=?",(u["id"],)).fetchone()["n"]
        selling=con.execute("SELECT COUNT(*) n FROM listings WHERE seller_id=? AND status='active'",(u["id"],)).fetchone()["n"]
        sold=con.execute("SELECT COUNT(*) n FROM listings WHERE seller_id=? AND status='sold'",(u["id"],)).fetchone()["n"]
        bought=con.execute("SELECT COUNT(*) n FROM listings WHERE buyer_id=?",(u["id"],)).fetchone()["n"]
    return jsonify(user=u,counters={"posted":posted,"for_sale":selling,"sold":sold,"bought":bought})

@app.post("/api/account")
@login_required
def update_account():
    data=request.get_json(silent=True) or {}
    name=data.get("name","").strip();phone=data.get("phone","").strip()
    if not name or (phone and not valid_phone(phone)):return jsonify(error="Enter a name and valid 10-digit phone number."),400
    with db() as con:con.execute("UPDATE users SET name=?,phone=? WHERE id=?",(name,phone,current_user()["id"]))
    return jsonify(user=current_user())

@app.get("/api/my/listings")
@login_required
def my_listings():
    with db() as con:
        rows=con.execute("""SELECT l.*,u.name seller_name FROM listings l JOIN users u ON u.id=l.seller_id
          WHERE l.seller_id=? ORDER BY l.created_at DESC""",(current_user()["id"],)).fetchall()
    return jsonify(listings=[listing_dict(r,True) for r in rows])

@app.get("/api/my/purchases")
@login_required
def my_purchases():
    with db() as con:
        rows=con.execute("""SELECT l.*,u.name seller_name FROM listings l JOIN users u ON u.id=l.seller_id
          WHERE l.buyer_id=? ORDER BY l.sold_at DESC""",(current_user()["id"],)).fetchall()
    return jsonify(listings=[listing_dict(r,True) for r in rows])

@app.post("/api/support")
@login_required
def support():
    uid=current_user()["id"];data=request.get_json(silent=True) or {}
    issue=data.get("issue","").strip()
    if len(issue)<5:return jsonify(error="Please describe your issue."),400
    last=session.get("last_support",0)
    if time.time()-last<60:return jsonify(error="Please wait one minute before sending another support message."),429
    host=os.environ.get("GMAIL_SMTP_HOST","smtp.gmail.com")
    port=int(os.environ.get("GMAIL_SMTP_PORT","587"))
    sender=os.environ.get("GMAIL_ADDRESS","")
    app_password=os.environ.get("GMAIL_APP_PASSWORD","")
    if not sender or not app_password:return jsonify(error="Support email is not configured. Set GMAIL_ADDRESS and GMAIL_APP_PASSWORD."),503
    user=current_user()
    msg=EmailMessage();msg["Subject"]="CampusResale customer support"
    msg["From"]=sender;msg["To"]="suhasraja10@gmail.com";msg["Reply-To"]=user["email"]
    msg.set_content(f"From: {user['name']} <{user['email']}>\nPhone: {user['phone'] or 'Not supplied'}\n\nIssue:\n{issue}")
    try:
        with smtplib.SMTP(host,port,timeout=15) as smtp:
            smtp.starttls();smtp.login(sender,app_password);smtp.send_message(msg)
    except Exception as exc:return jsonify(error="Unable to send support email. Check SMTP settings."),502
    session["last_support"]=time.time()
    return jsonify(ok=True)

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CampusResale | Campus community marketplace</title>
<style>
:root{--bg:#f7f4ed;--paper:#fffdf8;--ink:#171713;--muted:#777367;--gold:#b28a45;--line:#e8e0d1;--soft:#eee7d9;--shadow:0 14px 40px #33240d12}
[data-theme=dark]{--bg:#171713;--paper:#22221d;--ink:#f7f4ed;--muted:#b6b0a2;--gold:#d2ad6d;--line:#39372f;--soft:#2c2a23;--shadow:0 14px 40px #0005}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 Arial,sans-serif;transition:.2s}button,input,select,textarea{font:inherit}button{cursor:pointer}.wrap{max-width:1180px;margin:auto;padding:0 24px}.top{position:sticky;top:0;background:color-mix(in srgb,var(--bg) 92%,transparent);backdrop-filter:blur(16px);border-bottom:1px solid var(--line);z-index:5}.nav{min-height:76px;display:flex;align-items:center;justify-content:space-between;gap:18px}.brand{font-family:Georgia,serif;font-size:23px;font-weight:bold;text-decoration:none;color:var(--ink)}.brand i{font-style:normal;color:var(--gold)}.links{display:flex;gap:20px;align-items:center;flex-wrap:wrap}.links a{color:var(--muted);text-decoration:none;font-size:13px}.links a:hover{color:var(--gold)}.actions{display:flex;gap:8px;align-items:center}.btn{border:1px solid var(--line);background:var(--paper);color:var(--ink);border-radius:999px;padding:10px 16px;font-size:13px}.primary{background:var(--gold);border-color:var(--gold);color:#fff}.hero{padding:76px 0 58px;display:grid;grid-template-columns:1.25fr .75fr;gap:36px;align-items:center}.eyebrow{letter-spacing:2px;text-transform:uppercase;color:var(--gold);font-size:11px;font-weight:bold}.hero h1{font:clamp(42px,6vw,72px)/1.03 Georgia,serif;margin:16px 0}.hero p{max-width:570px;color:var(--muted);font-size:17px}.hero-card{background:var(--ink);color:var(--bg);padding:30px;border-radius:24px;min-height:260px;display:flex;flex-direction:column;justify-content:space-between;box-shadow:var(--shadow)}.hero-card b{font:32px Georgia,serif;color:var(--gold)}.section{padding:34px 0 65px}.section-head{display:flex;justify-content:space-between;align-items:end;gap:20px;margin-bottom:20px}.section h2{font:34px Georgia,serif;margin:4px 0}.muted{color:var(--muted)}.filters{display:grid;grid-template-columns:2fr repeat(4,1fr);gap:9px;margin:20px 0}.field input,.field select,.field textarea,.filters input,.filters select{width:100%;padding:12px 13px;border:1px solid var(--line);border-radius:10px;background:var(--paper);color:var(--ink);outline:none}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:18px}.item{background:var(--paper);border:1px solid var(--line);border-radius:17px;overflow:hidden;box-shadow:var(--shadow)}.item-img{height:170px;background:var(--soft);display:grid;place-items:center;font-size:54px;color:var(--gold);overflow:hidden}.item-img img{width:100%;height:100%;object-fit:cover}.item-body{padding:17px}.item h3{margin:0 0 4px;font-size:17px}.price{font-size:21px;font-weight:bold;color:var(--gold)}.tag{font-size:11px;background:var(--soft);padding:5px 9px;border-radius:30px;color:var(--muted);display:inline-block;margin:3px 3px 3px 0}.row{display:flex;align-items:center;justify-content:space-between;gap:10px}.empty{border:1px dashed var(--line);padding:38px;text-align:center;border-radius:16px;color:var(--muted)}.modal-bg{position:fixed;inset:0;background:#0009;display:none;align-items:center;justify-content:center;padding:18px;z-index:10}.modal{width:min(570px,100%);max-height:90vh;overflow:auto;background:var(--paper);border-radius:20px;padding:25px;box-shadow:var(--shadow)}.modal h2{font:30px Georgia,serif;margin:5px 0 15px}.modal .field{margin:12px 0}.field label{display:block;font-size:12px;color:var(--muted);margin-bottom:5px}.field textarea{min-height:90px}.field-row{display:grid;grid-template-columns:1fr 1fr;gap:12px}.notice{padding:12px;background:var(--soft);border-radius:10px;color:var(--muted);font-size:13px}.footer{border-top:1px solid var(--line);padding:30px 0;color:var(--muted);font-size:12px}.profile-stats{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.stat{padding:15px;background:var(--soft);border-radius:12px}.stat b{display:block;font-size:23px;color:var(--gold)}.hidden{display:none!important}
@media(max-width:850px){.hero{grid-template-columns:1fr;padding:50px 0}.grid{grid-template-columns:repeat(2,1fr)}.filters{grid-template-columns:repeat(2,1fr)}.filters input{grid-column:span 2}.nav{padding:12px 0;flex-wrap:wrap}.links{order:3;width:100%;overflow:auto;flex-wrap:nowrap;padding-bottom:5px}.links a{white-space:nowrap}}
@media(max-width:560px){.wrap{padding:0 16px}.grid{grid-template-columns:1fr}.filters{grid-template-columns:1fr 1fr}.filters input{grid-column:span 2}.hero-card{min-height:210px}.profile-stats{grid-template-columns:1fr 1fr}.section-head{align-items:start;flex-direction:column}.brand{font-size:20px}}
</style></head>
<body><div class="top"><div class="wrap nav"><a class="brand" href="#" onclick="home();return false">Campus<i>Resale</i></a>
<nav class="links" id="mainLinks"><a href="#market" onclick="home();return false">Marketplace</a></nav>
<div class="actions" id="authActions"><button class="btn" onclick="auth('login')">Log in</button><button class="btn primary" onclick="auth('register')">Sign up</button><button class="btn" onclick="toggleTheme()" title="Theme">◐</button></div></div></div>
<main class="wrap" id="main"><section class="hero"><div><div class="eyebrow">A campus community marketplace</div><h1>Good things deserve<br><span style="color:var(--gold)">a second life.</span></h1><p>Buy and sell pre-owned textbooks, electronics, calculators and more within your campus community.</p><button class="btn primary" onclick="document.getElementById('market').scrollIntoView({behavior:'smooth'})">Explore marketplace →</button></div><div class="hero-card"><div class="eyebrow">Pass it forward</div><b>From seniors<br>to juniors.</b><span>Find useful campus essentials. Give your unused items a new home.</span></div></section>
<section class="section" id="market"><div class="section-head"><div><div class="eyebrow">Discover</div><h2>Marketplace</h2><div class="muted">Explore items listed by your campus community.</div></div><button class="btn primary" onclick="protectedView('selling')">＋ Sell an item</button></div>
<div class="filters"><input id="search" placeholder="Search items..." oninput="loadListings()"><select id="category" onchange="loadListings()"><option value="">All categories</option>{{ categories|map('title')|list|join('') }}</select><select id="condition" onchange="loadListings()"><option value="">Any condition</option><option>like new</option><option>good</option><option>fair</option><option>poor</option></select><input id="maxPrice" type="number" placeholder="Maximum ₹" oninput="loadListings()"><select id="sort" onchange="loadListings()"><option value="newest">Newest</option><option value="price_low">Price: low to high</option><option value="price_high">Price: high to low</option></select></div>
<label class="muted"><input type="checkbox" id="semester" onchange="loadListings()"> Semester-end sale</label><div style="height:16px"></div><div id="items" class="grid"><div class="empty">Loading listings…</div></div></section></main>
<footer class="footer"><div class="wrap row"><span>© CampusResale · Built for campus communities</span><button class="btn" onclick="toggleTheme()">Toggle light / dark</button></div></footer>
<div class="modal-bg" id="modalBg" onclick="if(event.target===this)closeModal()"><div class="modal" id="modal"></div></div>
<div id="toast" style="position:fixed;bottom:20px;right:20px;background:var(--ink);color:var(--bg);padding:12px 18px;border-radius:12px;display:none;z-index:20"></div>
<script>
let me=null, itemsCache=[];
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money=n=>'₹'+Number(n||0).toLocaleString('en-IN');
async function api(url,opts={}){let r=await fetch(url,{...opts,headers:{...(opts.body instanceof FormData?{}:{'Content-Type':'application/json'}),...(opts.headers||{})}});let d=await r.json().catch(()=>({}));if(!r.ok)throw Error(d.error||'Request failed');return d}
function toast(t){$('toast').textContent=t;$('toast').style.display='block';setTimeout(()=>$('toast').style.display='none',2800)}
function modal(s){$('modal').innerHTML=s;$('modalBg').style.display='flex'}function closeModal(){$('modalBg').style.display='none'}
function toggleTheme(){document.documentElement.dataset.theme=document.documentElement.dataset.theme==='dark'?'light':'dark';localStorage.setItem('cr-theme',document.documentElement.dataset.theme)}
document.documentElement.dataset.theme=localStorage.getItem('cr-theme')||'light';
function auth(mode){let reg=mode==='register';modal(`<div class="eyebrow">CampusResale account</div><h2>${reg?'Create your account':'Welcome back'}</h2><form id="authForm">${reg?`<div class="field"><label>Full name</label><input name="name" required></div><div class="field"><label>Phone (optional)</label><input name="phone" inputmode="numeric" pattern="[6-9][0-9]{9}" placeholder="10-digit mobile"></div>`:''}<div class="field"><label>College email</label><input name="email" type="email" required></div><div class="field"><label>Password (minimum 8 characters)</label><input name="password" type="password" minlength="8" required></div><button class="btn primary">${reg?'Create account':'Log in'}</button></form><p class="muted">${reg?'Already registered?':'New to CampusResale?'} <a href="#" onclick="auth('${reg?'login':'register'}');return false">${reg?'Log in':'Sign up'}</a></p><div class="notice">Email-code verification is not part of this version.</div>`);
$('authForm').onsubmit=async e=>{e.preventDefault();let f=new FormData(e.target);try{let d=await api(reg?'/api/register':'/api/login',{method:'POST',body:JSON.stringify(Object.fromEntries(f))});me=d.user;closeModal();updateNav();toast('Welcome, '+me.name);loadListings()}catch(x){toast(x.message)}}
}
function updateNav(){
 let a=$('authActions'), links=$('mainLinks');
 links.innerHTML=me
  ? `<a href="#market" onclick="home();return false">Marketplace</a><a href="#" onclick="protectedView('selling');return false">Selling</a><a href="#" onclick="protectedView('sold');return false">Sold items</a><a href="#" onclick="protectedView('bought');return false">Bought items</a><a href="#" onclick="protectedView('profile');return false">My profile</a><a href="#" onclick="protectedView('support');return false">Customer support</a>`
  : `<a href="#market" onclick="home();return false">Marketplace</a>`;
 a.innerHTML=me?`<span class="muted">Hi, ${esc(me.name)}</span><button class="btn" onclick="logout()">Log out</button><button class="btn" onclick="toggleTheme()">◐</button>`:`<button class="btn" onclick="auth('login')">Log in</button><button class="btn primary" onclick="auth('register')">Sign up</button><button class="btn" onclick="toggleTheme()">◐</button>`
}
async function logout(){await api('/api/logout',{method:'POST'});me=null;updateNav();toast('Logged out');home()}
function home(){ $('main').innerHTML=document.querySelector('main').innerHTML;loadListings() }
function protectedView(view){if(!me){auth('login');toast('Please log in to access this section.');return}if(view==='selling')sellingView();else if(view==='profile')profile();else if(view==='support')supportForm();else myItems(view)}
async function loadListings(){let p=new URLSearchParams();if($('search'))p.set('search',$('search').value);if($('category'))p.set('category',$('category').value);if($('condition'))p.set('condition',$('condition').value);if($('maxPrice')&&$('maxPrice').value)p.set('max_price',$('maxPrice').value);if($('sort'))p.set('sort',$('sort').value);if($('semester')&&$('semester').checked)p.set('semester_end','1');try{let d=await api('/api/listings?'+p);itemsCache=d.listings;renderItems()}catch(e){$('items').innerHTML=`<div class="empty">${esc(e.message)}</div>`}}
function renderItems(){let el=$('items');if(!itemsCache.length){el.innerHTML='<div class="empty" style="grid-column:1/-1">No active listings found. Be the first to list an item.</div>';return}el.innerHTML=itemsCache.map(x=>`<article class="item"><div class="item-img">${x.image?`<img src="${esc(x.image)}">`:'✦'}</div><div class="item-body"><div class="row"><span class="tag">${esc(x.category)}</span><span class="tag">${esc(x.condition)}</span></div><h3>${esc(x.title)}</h3><div class="price">${money(x.price)}</div>${x.price_check?'<span class="tag" style="color:#a33">⚑ Check price</span>':''}<p class="muted">${esc(x.seller_name)} · ${x.semester_end?'Semester-end sale':''}</p><button class="btn" onclick="details(${x.id})">View details</button></div></article>`).join('')}
async function details(id){try{let d=await api('/api/listings/'+id),x=d.listing;modal(`<div class="eyebrow">${esc(x.category)} · ${esc(x.condition)}</div><h2>${esc(x.title)}</h2>${x.image?`<img src="${esc(x.image)}" style="width:100%;max-height:220px;object-fit:cover;border-radius:12px">`:''}<div class="price">${money(x.price)}</div><p>${esc(x.description)}</p><p class="muted">Seller: ${esc(x.seller_name)} · Rating: ${x.seller_rating?Number(x.seller_rating).toFixed(1)+' / 5':'Not rated'}</p>${me?`<p>Contact email: ${esc(x.seller_email||'Not available')}<br>Phone: ${esc(x.contact_phone||'Not available')}</p>`:'<div class="notice">Log in to see seller contact details.</div>'}<button class="btn" onclick="report(${id})">Report listing</button> <button class="btn" onclick="rate(${x.seller_id})">Rate seller</button> <button class="btn" onclick="closeModal()">Close</button>`)}catch(e){toast(e.message)}}
async function sellingView(){
 try{
  let d=await api('/api/my/listings'),active=d.listings.filter(x=>x.status==='active');
  modal(`<div class="eyebrow">Seller workspace</div><h2>Selling</h2><button class="btn primary" onclick="sellForm()">＋ Create new listing</button>${active.length?active.map(x=>`<div class="notice" style="margin:12px 0"><b>${esc(x.title)}</b> · ${money(x.price)}<div style="margin-top:8px"><button class="btn" onclick="recordBuyer(${x.id})">Mark sold / record buyer</button><button class="btn" onclick="deleteMyListing(${x.id})">Delete</button></div></div>`).join(''):'<p class="muted">You have no active listings.</p>'}<button class="btn" onclick="closeModal()">Close</button>`);
 }catch(e){toast(e.message)}
}
async function deleteMyListing(id){if(!confirm('Delete this listing?'))return;try{await api('/api/listings/'+id,{method:'DELETE'});toast('Listing deleted');sellingView();loadListings()}catch(e){toast(e.message)}}
function sellForm(){modal(`<div class="eyebrow">Create listing</div><h2>Sell an item</h2><form id="sellForm"><div class="field"><label>Item title *</label><input name="title" required></div><div class="field-row"><div class="field"><label>Category *</label><select name="category">${{{categories}}.map(c=>`<option>${c}</option>`).join('')}</select></div><div class="field"><label>Condition *</label><select name="condition"><option>like new</option><option>good</option><option>fair</option><option>poor</option></select></div></div><div class="field-row"><div class="field"><label>Age (months)</label><input name="age_months" type="number" min="0" value="0"></div><div class="field"><label>Original price (₹)</label><input name="original_price" type="number" min="0" id="originalPrice"></div></div><div class="field-row"><div class="field"><label>Selling price (₹) *</label><input name="price" type="number" min="1" required></div><div class="field"><label>Seller phone *</label><input name="contact_phone" pattern="[6-9][0-9]{9}" maxlength="10" required></div></div><div class="field"><button type="button" class="btn" onclick="suggestPrice()">Check recommended price</button><span id="priceHint" class="muted"></span></div><div class="field"><label>Photo (PNG/JPG/WEBP, max 5MB)</label><input name="image" type="file" accept="image/png,image/jpeg,image/webp"></div><div class="field"><label>Description</label><textarea name="description"></textarea></div><label><input type="checkbox" name="semester_end" value="1"> Semester-end sale</label><p><button class="btn primary">Publish listing</button></p></form>`);
$('sellForm').onsubmit=async e=>{e.preventDefault();try{let fd=new FormData(e.target);await api('/api/listings',{method:'POST',body:fd});closeModal();toast('Listing published');loadListings()}catch(x){toast(x.message)}}
}
async function suggestPrice(){let f=new FormData($('sellForm'));let p=new URLSearchParams({category:f.get('category'),condition:f.get('condition'),age_months:f.get('age_months'),original_price:f.get('original_price')});try{let d=await api('/api/price-suggest?'+p);$('priceHint').textContent=` Suggested: ${money(d.low)} – ${money(d.high)} (mid ${money(d.mid)}; ${d.method})`}catch(e){toast(e.message)}}
async function myItems(which){let d=await api(which==='sold'?'/api/my/listings':'/api/my/purchases');let arr=which==='sold'?d.listings.filter(x=>x.status==='sold'):d.listings;modal(`<div class="eyebrow">Your account</div><h2>${which==='sold'?'Sold items':'Bought items'}</h2>${arr.length?arr.map(x=>`<div class="notice" style="margin:9px 0"><b>${esc(x.title)}</b> · ${money(x.price)} ${which==='sold'?`<button class="btn" onclick="recordBuyer(${x.id})">Record buyer</button>`:''}</div>`).join(''):'<p class="muted">No records yet.</p>'}<button class="btn" onclick="closeModal()">Close</button>`)}
function recordBuyer(id){let email=prompt('Enter the buyer’s registered email address (optional if not registered):');if(email===null)return;api('/api/listings/'+id+'/sold',{method:'POST',body:JSON.stringify({buyer_email:email})}).then(()=>{toast('Marked sold.');closeModal();loadListings()}).catch(e=>toast(e.message))}
async function profile(){let d=await api('/api/account'),u=d.user,c=d.counters;modal(`<div class="eyebrow">Your account</div><h2>My profile</h2><div class="profile-stats">${[['Posted',c.posted],['For sale',c.for_sale],['Sold',c.sold],['Bought',c.bought]].map(([a,b])=>`<div class="stat"><b>${b}</b>${a}</div>`).join('')}</div><form id="profileForm"><div class="field"><label>Name</label><input name="name" value="${esc(u.name)}" required></div><div class="field"><label>Email</label><input value="${esc(u.email)}" disabled></div><div class="field"><label>Phone</label><input name="phone" value="${esc(u.phone||'')}" pattern="[6-9][0-9]{9}"></div><button class="btn primary">Save profile</button></form>`);$('profileForm').onsubmit=async e=>{e.preventDefault();try{let d=await api('/api/account',{method:'POST',body:JSON.stringify(Object.fromEntries(new FormData(e.target)))});me=d.user;updateNav();toast('Profile updated')}catch(x){toast(x.message)}}}
function supportForm(){modal(`<div class="eyebrow">We are here to help</div><h2>Customer support</h2><p class="muted">Your message will be sent to the CampusResale support email.</p><form id="supportForm"><div class="field"><label>Describe your issue</label><textarea name="issue" required minlength="5"></textarea></div><button class="btn primary">Send message</button></form>`);$('supportForm').onsubmit=async e=>{e.preventDefault();try{await api('/api/support',{method:'POST',body:JSON.stringify(Object.fromEntries(new FormData(e.target)))});closeModal();toast('Support message sent')}catch(x){toast(x.message)}}}
async function report(id){let reason=prompt('Why are you reporting this listing?');if(!reason)return;try{let d=await api('/api/listings/'+id+'/report',{method:'POST',body:JSON.stringify({reason})});closeModal();toast(d.hidden?'Listing hidden after reports.':'Report submitted.')}catch(e){toast(e.message)}}
async function rate(uid){let stars=prompt('Give this seller a rating from 1 to 5');if(!stars)return;let comment=prompt('Optional comment')||'';try{await api('/api/users/'+uid+'/rate',{method:'POST',body:JSON.stringify({stars,comment})});toast('Rating saved')}catch(e){toast(e.message)}}
(async()=>{try{let d=await api('/api/me');me=d.user;updateNav()}catch{}loadListings()})();
</script></body></html>"""

# Replace Jinja category options with a safe JSON literal for JS and real option markup
PAGE = PAGE.replace("{{ categories|map('title')|list|join('') }}",
    "{% for c in categories %}<option value='{{c}}'>{{c|title}}</option>{% endfor %}")
PAGE = PAGE.replace("${{{categories}}.map(c=>`<option>${c}</option>`).join('')}",
    "${JSON.parse(document.getElementById('category').options.length ? JSON.stringify(['textbooks','electronics','calculators','furniture','bicycles','clothing','stationery','other']) : '[]').map(c=>`<option>${c}</option>`).join('')}")
if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
