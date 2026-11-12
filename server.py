import os, sys, time, secrets
from pathlib import Path
from datetime import datetime, timedelta
from urllib.parse import quote_plus

from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import uvicorn

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import database as db
import sms_server

app = FastAPI(docs_url=None, redoc_url=None)

STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

DASHBOARD_USER = os.getenv("DASHBOARD_USER", "admin")
DASHBOARD_PASS = os.getenv("DASHBOARD_PASS", "admin1234")
SESSIONS: set = set()

GRADE_LABELS = {"VIP": "VIP", "우수구매자": "Elite Buyer", "구매자": "Buyer", "일반": "Standard"}
templates.env.filters["won"] = lambda v: "₩{:,}".format(int(v or 0))
templates.env.filters["grade"] = lambda g: GRADE_LABELS.get(g, g or "Standard")


# ── helpers ───────────────────────────────────────
class NotAuthenticated(Exception):
    pass


@app.exception_handler(NotAuthenticated)
async def _not_authenticated(request: Request, exc: NotAuthenticated):
    return RedirectResponse("/login", status_code=303)


def check_auth(request: Request):
    if request.cookies.get("session") not in SESSIONS:
        raise NotAuthenticated()


def render(request: Request, name: str, **ctx):
    ctx.update(request=request,
               msg=request.query_params.get("msg"),
               err=request.query_params.get("err"))
    return templates.TemplateResponse(name, ctx)


def back(path: str, msg: str = None, err: str = None):
    q = ""
    if err:
        q = "?err=" + quote_plus(err)
    elif msg:
        q = "?msg=" + quote_plus(msg)
    # 303 = always follow up with a GET after a form POST
    return RedirectResponse(path + q, status_code=303)


def to_int(value, default=0):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def to_float(value, default=0.0):
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def clean_ts(value) -> str:
    """datetime-local value ('2026-11-12T20:00') -> same string if valid, else ''"""
    v = str(value or "").strip()
    if not v:
        return ""
    try:
        datetime.fromisoformat(v)
        return v
    except ValueError:
        return ""


def clean_code(value) -> str:
    code = "".join(str(value or "").split()).upper()
    for ch in "/\\?#%'\"<>&":
        code = code.replace(ch, "")
    return code[:32]


def guild_ok(gid) -> bool:
    return bool(gid) and db.get_guild_info(gid) is not None


def get_stats():
    guilds = db.get_all_guilds()
    today = datetime.now().strftime("%Y-%m-%d")
    total_revenue = today_revenue = total_users = total_products = 0
    recent, pending_all, products, users = [], [], [], []

    days = [(datetime.now() - timedelta(days=364 - i)).strftime("%Y-%m-%d") for i in range(365)]
    series = {
        "revenue": {d: 0 for d in days},
        "charges": {d: 0 for d in days},
        "orders":  {d: 0 for d in days},
    }

    for g in guilds:
        gid = g["id"]
        gdb = db.load_db(gid)
        total_products += len(gdb.get("products", {}))
        for pid, p in gdb.get("products", {}).items():
            products.append({"id": pid, "guild_id": gid, **p})
        for uid, u in gdb.get("users", {}).items():
            total_users += 1
            users.append({"id": uid, "guild_id": gid, **u})
            for p in u.get("purchases", []):
                price = p.get("price", 0)
                at = p.get("purchased_at", "")
                total_revenue += price
                if at.startswith(today):
                    today_revenue += price
                d = at[:10]
                if d in series["revenue"]:
                    series["revenue"][d] += price
                    series["orders"][d] += 1
                recent.append({"username": u.get("username", "Unknown"), "product": p.get("product_name", ""),
                               "price": price, "date": at[:16].replace("T", " "), "guild_id": gid})
        for cid, c in gdb.get("pending_charges", {}).items():
            if c.get("status") == "pending":
                pending_all.append({"id": cid, "guild_id": gid, **c})
            elif c.get("status") == "approved":
                d = (c.get("approved_at") or c.get("created_at", ""))[:10]
                if d in series["charges"]:
                    series["charges"][d] += c.get("amount", 0)

    recent.sort(key=lambda x: x["date"], reverse=True)
    return {
        "total_revenue": total_revenue, "today_revenue": today_revenue,
        "total_users": total_users, "total_products": total_products,
        "chart": {"labels": days, **{k: list(v.values()) for k, v in series.items()}},
        "recent_purchases": recent[:10], "pending_charges": pending_all,
        "guilds": guilds, "products": products, "users": users,
    }


# ── auth ──────────────────────────────────────────
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login")
async def login(request: Request):
    form = await request.form()
    ok_user = secrets.compare_digest(str(form.get("username", "")), DASHBOARD_USER)
    ok_pass = secrets.compare_digest(str(form.get("password", "")), DASHBOARD_PASS)
    if ok_user and ok_pass:
        token = secrets.token_hex(32)
        SESSIONS.add(token)
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie("session", token, httponly=True, samesite="lax")
        return resp
    return templates.TemplateResponse("login.html", {"request": request, "error": "Incorrect username or password."})


@app.get("/logout")
async def logout(request: Request):
    SESSIONS.discard(request.cookies.get("session"))
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("session")
    return resp


# ── pages ─────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, _=Depends(check_auth)):
    return render(request, "dashboard.html", stats=get_stats(), page="dashboard")


@app.get("/products", response_class=HTMLResponse)
async def products(request: Request, _=Depends(check_auth)):
    return render(request, "products.html", stats=get_stats(), guilds=db.get_all_guilds(), page="products")


@app.get("/users", response_class=HTMLResponse)
async def users(request: Request, _=Depends(check_auth)):
    return render(request, "users.html", stats=get_stats(), guilds=db.get_all_guilds(), page="users")


@app.get("/charges", response_class=HTMLResponse)
async def charges(request: Request, _=Depends(check_auth)):
    return render(request, "charges.html", stats=get_stats(), page="charges")


@app.get("/guilds", response_class=HTMLResponse)
async def guilds_page(request: Request, _=Depends(check_auth)):
    return render(request, "guilds.html", stats=get_stats(), page="guilds")


@app.get("/events", response_class=HTMLResponse)
async def events_page(request: Request, _=Depends(check_auth)):
    guilds = db.get_all_guilds()
    names = {g["id"]: g["guild_name"] for g in guilds}
    coupons = [{"guild_id": g["id"], "guild_name": names[g["id"]], **c} for g in guilds for c in db.get_coupons(g["id"])]
    events = [{"guild_id": g["id"], "guild_name": names[g["id"]], **e} for g in guilds for e in db.get_events(g["id"])]
    return render(request, "events.html", guilds=guilds, coupons=coupons, events=events, page="events")


@app.post("/api/coupon/add")
async def add_coupon(request: Request, _=Depends(check_auth)):
    form = await request.form()
    gid = form.get("guild_id")
    if not guild_ok(gid):
        return back("/events", err="Please select a valid server.")
    kind = "percent" if form.get("kind") == "percent" else "fixed"
    value = to_int(form.get("value"), 0)
    if value < 1 or (kind == "percent" and value > 100):
        return back("/events", err="Discount must be 1–100% or at least ₩1.")
    code = clean_code(form.get("code")) or secrets.token_hex(4).upper()
    ok = db.add_coupon(
        gid, code, kind, value,
        max_uses=max(0, to_int(form.get("max_uses"), 0)),
        per_user=max(0, to_int(form.get("per_user"), 1)),
        min_purchase=max(0, to_int(form.get("min_purchase"), 0)),
        expires_at=clean_ts(form.get("expires_at")),
    )
    if not ok:
        return back("/events", err=f"Coupon code {code} already exists on that server.")
    return back("/events", msg=f"Coupon {code} created.")


@app.post("/api/coupon/toggle/{gid}/{code}")
async def toggle_coupon(gid: str, code: str, _=Depends(check_auth)):
    if not guild_ok(gid):
        return back("/events", err="Server not found.")
    db.toggle_coupon(gid, code)
    return back("/events", msg="Coupon updated.")


@app.post("/api/coupon/delete/{gid}/{code}")
async def delete_coupon(gid: str, code: str, _=Depends(check_auth)):
    if not guild_ok(gid):
        return back("/events", err="Server not found.")
    db.delete_coupon(gid, code)
    return back("/events", msg="Coupon deleted.")


@app.post("/api/event/add")
async def add_event(request: Request, _=Depends(check_auth)):
    form = await request.form()
    gid = form.get("guild_id")
    if not guild_ok(gid):
        return back("/events", err="Please select a valid server.")
    name = str(form.get("name", "")).strip()
    multiplier = to_float(form.get("multiplier"), 2.0)
    if not name:
        return back("/events", err="Event name is required.")
    if not (1.1 <= multiplier <= 10):
        return back("/events", err="Multiplier must be between 1.1x and 10x.")
    start, end = clean_ts(form.get("start_at")), clean_ts(form.get("end_at"))
    if start and end and end <= start:
        return back("/events", err="End time must be after the start time.")
    db.add_charge_event(
        gid, name, multiplier,
        max_winners=max(0, to_int(form.get("max_winners"), 0)),
        min_amount=max(0, to_int(form.get("min_amount"), 0)),
        max_bonus=max(0, to_int(form.get("max_bonus"), 0)),
        start_at=start, end_at=end,
    )
    return back("/events", msg=f"Event “{name}” created.")


@app.post("/api/event/toggle/{gid}/{eid}")
async def toggle_event(gid: str, eid: str, _=Depends(check_auth)):
    if not guild_ok(gid):
        return back("/events", err="Server not found.")
    db.toggle_event(gid, eid)
    return back("/events", msg="Event updated.")


@app.post("/api/event/delete/{gid}/{eid}")
async def delete_event(gid: str, eid: str, _=Depends(check_auth)):
    if not guild_ok(gid):
        return back("/events", err="Server not found.")
    db.delete_event(gid, eid)
    return back("/events", msg="Event deleted.")


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, _=Depends(check_auth)):
    guilds = db.get_all_guilds()
    settings_map = {g["id"]: db.get_settings(g["id"]) for g in guilds}
    return render(request, "settings.html", guilds=guilds, settings_map=settings_map, page="settings")


# ── actions ───────────────────────────────────────
@app.post("/api/product/add")
async def add_product(request: Request, _=Depends(check_auth)):
    form = await request.form()
    gid = form.get("guild_id")
    if not guild_ok(gid):
        return back("/products", err="Please select a valid server (register one under Servers first).")
    name = str(form.get("name", "")).strip()
    price = to_int(form.get("price"), 0)
    if not name or price < 1:
        return back("/products", err="Product name and a price of at least ₩1 are required.")
    pid = f"P-{int(time.time() * 1000)}"
    db.save_product(gid, pid, {
        "category": str(form.get("category", "")).strip() or "General",
        "name": name, "price": price,
        "content": form.get("content", ""),
        "stock": to_int(form.get("stock"), -1),
    })
    return back("/products", msg="Product added.")


@app.post("/api/product/delete/{gid}/{pid}")
async def del_product(gid: str, pid: str, _=Depends(check_auth)):
    if not guild_ok(gid):
        return back("/products", err="Server not found.")
    db.delete_product(gid, pid)
    return back("/products", msg="Product deleted.")


@app.post("/api/charge/approve/{gid}/{cid}")
async def approve(gid: str, cid: str, request: Request, _=Depends(check_auth)):
    back_to = "/" if request.query_params.get("from") == "dashboard" else "/charges"
    if not guild_ok(gid):
        return back(back_to, err="Server not found.")
    charge = db.approve_charge(gid, cid)
    if charge is None:
        return back(back_to, err="Charge not found or already processed.")
    note = f" Event bonus: +₩{charge['bonus']:,} ({charge['event_name']})." if charge.get("bonus") else ""
    return back(back_to, msg="Charge approved and balance credited." + note)


@app.post("/api/charge/reject/{gid}/{cid}")
async def reject(gid: str, cid: str, request: Request, _=Depends(check_auth)):
    back_to = "/" if request.query_params.get("from") == "dashboard" else "/charges"
    if not guild_ok(gid):
        return back(back_to, err="Server not found.")
    if db.reject_charge(gid, cid) is None:
        return back(back_to, err="Charge not found or already processed.")
    return back(back_to, msg="Charge rejected.")


@app.post("/api/user/balance")
async def give_balance(request: Request, _=Depends(check_auth)):
    form = await request.form()
    gid = form.get("guild_id")
    uid = str(form.get("user_id", "")).strip()
    amount = to_int(form.get("amount"), 0)
    if not guild_ok(gid):
        return back("/users", err="Please select a valid server.")
    if not uid.isdigit() or amount == 0:
        return back("/users", err="Enter a numeric Discord user ID and a non-zero amount.")
    user = db.get_user(gid, uid, "Unknown")
    user["balance"] += amount
    db.save_user(gid, uid, user)
    return back("/users", msg=f"Added ₩{amount:,} to user {uid}.")


@app.post("/api/guild/register")
async def register_guild(request: Request, _=Depends(check_auth)):
    form = await request.form()
    gid = str(form.get("guild_id", "")).strip()
    if not gid.isdigit():
        return back("/guilds", err="Server ID must be numeric.")
    days = to_int(form.get("days"), 30)
    expires = "permanent" if days <= 0 else (datetime.now() + timedelta(days=days)).isoformat()
    db.register_guild(gid, str(form.get("guild_name", "")).strip() or gid, expires)
    return back("/guilds", msg="Server registered.")


@app.post("/api/guild/deactivate/{gid}")
async def deactivate(gid: str, _=Depends(check_auth)):
    if not guild_ok(gid):
        return back("/guilds", err="Server not found.")
    db.deactivate_guild(gid)
    return back("/guilds", msg="Server deactivated.")


@app.post("/api/settings/bank/{gid}")
async def save_bank(gid: str, request: Request, _=Depends(check_auth)):
    if not guild_ok(gid):
        return back("/settings", err="Server not found.")
    form = await request.form()
    s = db.get_settings(gid)
    s["bank_name"] = form.get("bank_name", "")
    s["bank_account"] = form.get("bank_account", "")
    s["bank_holder"] = form.get("bank_holder", "")
    db.save_settings(gid, s)
    return back("/settings", msg="Bank details saved.")


@app.post("/api/settings/charge/{gid}")
async def save_charge(gid: str, request: Request, _=Depends(check_auth)):
    if not guild_ok(gid):
        return back("/settings", err="Server not found.")
    form = await request.form()
    s = db.get_settings(gid)
    s["auto_charge"] = form.get("auto_charge") == "1"
    s["min_charge"] = to_int(form.get("min_charge"), 1000)
    s["max_charge"] = to_int(form.get("max_charge"), 0)
    s["admin_channel_id"] = str(form.get("admin_channel_id", "")).strip()
    db.save_settings(gid, s)
    return back("/settings", msg="Charge settings saved.")


@app.post("/sms")
async def sms_endpoint(request: Request):
    """입금 문자 수신 (Render처럼 포트가 하나뿐인 호스팅용). 인증: x-webhook-secret 헤더"""
    expected = os.getenv("SMS_WEBHOOK_SECRET", "")
    if not expected:
        return JSONResponse({"error": "SMS_WEBHOOK_SECRET not configured"}, status_code=503)
    if not secrets.compare_digest(request.headers.get("x-webhook-secret", ""), expected):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    message = ""
    try:
        form = await request.form()
        message = form.get("message") or form.get("text") or form.get("sms") or ""
    except Exception:
        pass
    if not message:
        try:
            body = await request.json()
            message = body.get("message") or body.get("text") or body.get("sms") or ""
        except Exception:
            pass
    if not message:
        return JSONResponse({"error": "No message"}, status_code=400)

    bot = getattr(request.app.state, "bot", None)
    return JSONResponse(await sms_server.handle_sms(bot, str(message)))


@app.get("/health")
async def health():
    return {"status": "ok"}


async def start_dashboard_async(bot=None):
    app.state.bot = bot
    # Render 등은 $PORT 하나만 외부에 열어줌 → 있으면 그걸 사용
    port = int(os.getenv("PORT") or os.getenv("DASHBOARD_PORT", 8080))
    if DASHBOARD_PASS == "admin1234":
        print("⚠️  DASHBOARD_PASS가 기본값입니다. 환경변수로 꼭 바꿔주세요!")
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning")
    server = uvicorn.Server(config)
    print(f"🌐 Dashboard: http://0.0.0.0:{port}")
    await server.serve()
