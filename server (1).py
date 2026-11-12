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
import bankapi

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


def bankapi_view(cfg: dict) -> dict:
    """템플릿에 넘길 값 (비밀번호·키는 절대 내려보내지 않음)"""
    acc = cfg.get("account_number", "")
    return {
        "enabled": bool(cfg.get("enabled")), "bank_code": cfg.get("bank_code", "NH"),
        "account_masked": ("•" * max(len(acc) - 4, 0) + acc[-4:]) if acc else "",
        "has_keys": bool(cfg.get("api_key") and cfg.get("secret_key")),
        "has_pw": bool(cfg.get("account_password")), "has_birth": bool(cfg.get("resident_number")),
        "interval_min": cfg.get("interval_min", 6),
        "last_poll": cfg.get("last_poll", ""), "last_status": cfg.get("last_status", ""),
        "ready": bankapi.is_configured(cfg),
    }


def mask_key(key: str) -> str:
    if not key:
        return ""
    return (key[:2] + "•" * max(len(key) - 6, 4) + key[-4:]) if len(key) > 8 else "•" * len(key)


@app.get("/sms-log", response_class=HTMLResponse)
async def sms_log_page(request: Request, _=Depends(check_auth)):
    names = {g["id"]: g["guild_name"] for g in db.get_all_guilds()}
    pending = [c for c in get_stats()["pending_charges"]]
    for c in pending:
        c["guild_name"] = names.get(c["guild_id"], c["guild_id"])
    return render(request, "sms_log.html", log=db.get_sms_log(100), pending=pending, names=names, page="sms")


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, _=Depends(check_auth)):
    guilds = db.get_all_guilds()
    settings_map = {g["id"]: db.get_settings(g["id"]) for g in guilds}
    key = db.get_sms_secret()
    key_source = "web" if db.get_config().get("sms_webhook_secret") else ("env" if key else "none")
    masked = (key[:2] + "•" * max(len(key) - 6, 4) + key[-4:]) if len(key) > 8 else ("•" * len(key))
    webhook_url = str(request.base_url).rstrip("/") + "/sms"
    return render(request, "settings.html", guilds=guilds, settings_map=settings_map, page="settings",
                  key_set=bool(key), key_masked=masked, key_source=key_source, webhook_url=webhook_url,
                  new_key=request.query_params.get("new_key"),
                  bankapi_map={g["id"]: bankapi_view(settings_map[g["id"]].get("bankapi", {})) for g in guilds},
                  banks=bankapi.BANKS,
                  guild_keys={g["id"]: mask_key(settings_map[g["id"]].get("sms_key", "")) for g in guilds},
                  new_key_gid=request.query_params.get("new_key_gid"))


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


@app.post("/api/settings/apikey")
async def save_apikey(request: Request, _=Depends(check_auth)):
    """자동충전 API 키(SMS 웹훅 시크릿) 저장 / 자동 생성 / 삭제"""
    form = await request.form()
    action = form.get("action", "save")
    if action == "generate":
        key = secrets.token_urlsafe(24)
        db.set_sms_secret(key)
        # 생성 직후 1회만 전체 키를 보여줌 (이후엔 마스킹)
        return RedirectResponse("/settings?msg=" + quote_plus("New API key generated.") + "&new_key=" + quote_plus(key),
                                status_code=303)
    if action == "clear":
        db.set_sms_secret("")
        return back("/settings", msg="Web API key removed (falls back to the SMS_WEBHOOK_SECRET env var, if any).")
    key = str(form.get("api_key", "")).strip()
    if len(key) < 8:
        return back("/settings", err="API key must be at least 8 characters.")
    if any(c.isspace() for c in key):
        return back("/settings", err="API key cannot contain spaces.")
    db.set_sms_secret(key)
    return back("/settings", msg="API key saved. Takes effect immediately.")


@app.post("/api/settings/guildkey/{gid}")
async def save_guild_key(gid: str, request: Request, _=Depends(check_auth)):
    """서버별 자동충전 API 키: 이 키로 들어온 문자는 이 서버만 처리"""
    if not guild_ok(gid):
        return back("/settings", err="Server not found.")
    form = await request.form()
    action = form.get("action", "save")
    if action == "generate":
        key = secrets.token_urlsafe(24)
        db.set_guild_sms_key(gid, key)
        return RedirectResponse("/settings?msg=" + quote_plus("Server API key generated.")
                                + "&new_key=" + quote_plus(key) + "&new_key_gid=" + quote_plus(gid), status_code=303)
    if action == "clear":
        db.set_guild_sms_key(gid, "")
        return back("/settings", msg="Server API key removed.")
    key = str(form.get("api_key", "")).strip()
    if len(key) < 8 or any(c.isspace() for c in key):
        return back("/settings", err="API key must be at least 8 characters, no spaces.")
    if key == db.get_sms_secret() or (db.find_guild_by_sms_key(key) not in (None, gid)):
        return back("/settings", err="This key is already used elsewhere. Use a different key.")
    db.set_guild_sms_key(gid, key)
    return back("/settings", msg="Server API key saved.")


@app.post("/api/settings/bankapi/{gid}")
async def save_bankapi(gid: str, request: Request, _=Depends(check_auth)):
    """계좌조회API 자동충전 설정 저장. 비밀 값(키·비밀번호·생년월일)은 비워두면 기존 값 유지"""
    if not guild_ok(gid):
        return back("/settings", err="Server not found.")
    form = await request.form()
    s = db.get_settings(gid)
    cfg = s.get("bankapi", {})

    def keep(name):  # 입력이 비어 있으면 기존 값 유지
        v = str(form.get(name, "")).strip()
        return v or cfg.get(name, "")

    bank = str(form.get("bank_code", "NH"))
    if bank not in bankapi.BANKS:
        return back("/settings", err="Unsupported bank. (NH / KB / WR / IBK)")
    acc = "".join(ch for ch in str(form.get("account_number", "")) if ch.isdigit()) or cfg.get("account_number", "")
    pw, birth = keep("account_password"), keep("resident_number")
    if pw and not (pw.isdigit() and len(pw) == 4):
        return back("/settings", err="Account password must be 4 digits.")
    if birth and not (birth.isdigit() and len(birth) == 6):
        return back("/settings", err="Birth date must be 6 digits (YYMMDD).")
    cfg.update({
        "enabled": form.get("enabled") == "1", "bank_code": bank, "account_number": acc,
        "api_key": keep("api_key"), "secret_key": keep("secret_key"),
        "account_password": pw, "resident_number": birth,
        "interval_min": max(bankapi.MIN_INTERVAL_MIN, min(60, to_int(form.get("interval_min"), 6))),
    })
    s["bankapi"] = cfg
    db.save_settings(gid, s)
    if cfg["enabled"] and not bankapi.is_configured(cfg):
        return back("/settings", err="Saved, but some fields are still empty — auto top-up will not run until all are filled.")
    return back("/settings", msg="Bank API settings saved.")


@app.post("/api/settings/bankapi-check/{gid}")
async def check_bankapi(gid: str, request: Request, _=Depends(check_auth)):
    """지금 바로 입금내역 조회 (연결 테스트 겸용). 같은 계좌 5분 1회 제한에 포함됨"""
    if not guild_ok(gid):
        return back("/settings", err="Server not found.")
    bot = getattr(request.app.state, "bot", None)
    msg = await bankapi.check_guild(bot, gid, force=True)
    bankapi._last_poll[gid] = __import__("time").monotonic()
    return back("/settings", err=msg) if msg.startswith(("조회 실패", "설정이")) else back("/settings", msg="Checked: " + msg)


@app.post("/api/sms/test")
async def sms_test(request: Request, _=Depends(check_auth)):
    """알림/문자 내용을 붙여넣어 인식 결과 확인 (충전은 하지 않음)"""
    form = await request.form()
    text = str(form.get("message", "")).strip()
    if not text:
        return back("/sms-log", err="Paste a notification / SMS text first.")
    parsed = sms_server.parse_sms(text)
    if not parsed:
        return back("/sms-log", err="Not recognized as a deposit (no deposit keyword, or it looks like an outgoing transfer).")
    hits = []
    for g in db.get_all_guilds():
        if db.is_guild_active(g["id"]):
            c = sms_server.find_charge_in_guild(g["id"], parsed["depositor_name"], parsed["amount"], text)
            if c:
                hits.append(f"{g['guild_name']} · {c['depositor_name']} · ₩{c['amount']:,}")
    res = f"Recognized: ₩{parsed['amount']:,} / name: {parsed['depositor_name']} / {parsed['bank']}. "
    res += ("Would credit → " + "; ".join(hits)) if hits else "No matching pending request right now (nothing credited)."
    return back("/sms-log", msg=res)


@app.post("/api/sms/link/{log_id}")
async def sms_link(log_id: str, request: Request, _=Depends(check_auth)):
    """매칭 실패한 입금 문자를 대기 중인 충전 요청에 수동으로 연결 → 승인"""
    entry = db.get_sms_log_entry(log_id)
    if not entry:
        return back("/sms-log", err="Log entry not found.")
    if entry.get("status") != "no_match":
        return back("/sms-log", err="This entry is already handled.")
    form = await request.form()
    try:
        gid, cid = str(form.get("charge", "")).split("|", 1)
    except ValueError:
        return back("/sms-log", err="Select a charge request.")
    if not guild_ok(gid):
        return back("/sms-log", err="Server not found.")
    charge = db.approve_charge(gid, cid)
    if charge is None:
        return back("/sms-log", err="Charge not found or already processed.")
    db.update_sms_log(log_id, status="matched",
                      matched=[{"guild_id": gid, "charge_id": cid, "user_id": charge["user_id"], "manual": True}])
    bot = getattr(request.app.state, "bot", None)
    if bot:
        try:
            await sms_server.send_dm(bot, charge["user_id"], charge["amount"], entry.get("bank", "수동"), charge)
        except Exception as e:
            print(f"DM 실패: {e}")
    note = f" Event bonus: +₩{charge['bonus']:,}." if charge.get("bonus") else ""
    return back("/sms-log", msg="Linked and credited ₩{:,}.".format(charge["amount"]) + note)


@app.post("/sms")
async def sms_endpoint(request: Request):
    """입금 문자 수신 (Render처럼 포트가 하나뿐인 호스팅용). 인증: x-webhook-secret 헤더"""
    if not sms_server.keys_configured():
        return JSONResponse({"error": "API key not configured"}, status_code=503)
    ok, only_guild = sms_server.authenticate(request.headers.get("x-webhook-secret", ""))
    if not ok:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    message = sms_server.extract_message(await request.body(), request.headers.get("content-type", ""))
    if not message:
        return JSONResponse({"error": "No message"}, status_code=400)

    bot = getattr(request.app.state, "bot", None)
    return JSONResponse(await sms_server.handle_sms(bot, str(message), only_guild))


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
