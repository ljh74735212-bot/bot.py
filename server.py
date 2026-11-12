import os, sys, time
from pathlib import Path
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import secrets, uvicorn

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import database as db

app = FastAPI(docs_url=None, redoc_url=None)

STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

DASHBOARD_USER   = os.getenv("DASHBOARD_USER", "admin")
DASHBOARD_PASS   = os.getenv("DASHBOARD_PASS", "admin1234")
OWNER_ID         = os.getenv("OWNER_ID", "")
SESSIONS: set    = set()

def check_auth(request: Request):
    if request.cookies.get("session") not in SESSIONS:
        raise HTTPException(302, headers={"Location": "/login"})

def get_stats(guild_id: str = None):
    if guild_id:
        guilds = [db.get_guild_info(guild_id)] if db.get_guild_info(guild_id) else []
        guild_ids = [guild_id]
    else:
        guilds = db.get_all_guilds()
        guild_ids = [g["id"] for g in guilds]

    today = datetime.now().strftime("%Y-%m-%d")
    total_revenue = today_revenue = total_users = total_products = 0
    recent = []
    pending_all = []
    chart_data = {(datetime.now()-timedelta(days=29-i)).strftime("%Y-%m-%d"): 0 for i in range(30)}

    for gid in guild_ids:
        gdb = db.load_db(gid)
        total_products += len(gdb.get("products", {}))
        for u in gdb.get("users", {}).values():
            total_users += 1
            for p in u.get("purchases", []):
                total_revenue += p["price"]
                if p.get("purchased_at","").startswith(today):
                    today_revenue += p["price"]
                d = p.get("purchased_at","")[:10]
                if d in chart_data:
                    chart_data[d] += p["price"]
                recent.append({"username": u["username"], "product": p["product_name"], "price": p["price"], "date": p.get("purchased_at","")[:16].replace("T"," "), "guild_id": gid})
        for cid, c in gdb.get("pending_charges",{}).items():
            if c["status"] == "pending":
                pending_all.append({"id": cid, "guild_id": gid, **c})

    recent.sort(key=lambda x: x["date"], reverse=True)
    return {
        "total_revenue": total_revenue, "today_revenue": today_revenue,
        "total_users": total_users, "total_products": total_products,
        "chart_labels": list(chart_data.keys()), "chart_values": list(chart_data.values()),
        "recent_purchases": recent[:10], "pending_charges": pending_all,
        "guilds": db.get_all_guilds(),
        "products": [{"id":pid,"guild_id":gid,**p} for gid in guild_ids for pid,p in db.load_db(gid).get("products",{}).items()],
        "users": [{"id":uid,"guild_id":gid,**u} for gid in guild_ids for uid,u in db.load_db(gid).get("users",{}).items()],
    }

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
async def login(request: Request):
    form = await request.form()
    if form.get("username") == DASHBOARD_USER and form.get("password") == DASHBOARD_PASS:
        token = secrets.token_hex(32)
        SESSIONS.add(token)
        resp = RedirectResponse("/", status_code=302)
        resp.set_cookie("session", token, httponly=True)
        return resp
    return templates.TemplateResponse("login.html", {"request": request, "error": "아이디 또는 비밀번호 오류"})

@app.get("/logout")
async def logout(request: Request):
    SESSIONS.discard(request.cookies.get("session"))
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie("session")
    return resp

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, _=Depends(check_auth)):
    return templates.TemplateResponse("dashboard.html", {"request": request, "stats": get_stats(), "page": "dashboard"})

@app.get("/products", response_class=HTMLResponse)
async def products(request: Request, _=Depends(check_auth)):
    guilds = db.get_all_guilds()
    return templates.TemplateResponse("products.html", {"request": request, "stats": get_stats(), "guilds": guilds, "page": "products"})

@app.get("/users", response_class=HTMLResponse)
async def users(request: Request, _=Depends(check_auth)):
    guilds = db.get_all_guilds()
    return templates.TemplateResponse("users.html", {"request": request, "stats": get_stats(), "guilds": guilds, "page": "users"})

@app.get("/charges", response_class=HTMLResponse)
async def charges(request: Request, _=Depends(check_auth)):
    return templates.TemplateResponse("charges.html", {"request": request, "stats": get_stats(), "page": "charges"})

@app.get("/guilds", response_class=HTMLResponse)
async def guilds_page(request: Request, _=Depends(check_auth)):
    return templates.TemplateResponse("guilds.html", {"request": request, "stats": get_stats(), "page": "guilds"})

# API
@app.post("/api/product/add")
async def add_product(request: Request, _=Depends(check_auth)):
    form = await request.form()
    gid = form.get("guild_id")
    pid = f"P-{int(time.time()*1000)}"
    db.save_product(gid, pid, {"category": form.get("category"), "name": form.get("name"), "price": int(form.get("price",0)), "content": form.get("content"), "stock": int(form.get("stock",-1))})
    return RedirectResponse("/products", status_code=302)

@app.post("/api/product/delete/{gid}/{pid}")
async def del_product(gid: str, pid: str, _=Depends(check_auth)):
    db.delete_product(gid, pid)
    return RedirectResponse("/products", status_code=302)

@app.post("/api/charge/approve/{gid}/{cid}")
async def approve(gid: str, cid: str, _=Depends(check_auth)):
    db.approve_charge(gid, cid)
    return RedirectResponse("/charges", status_code=302)

@app.post("/api/charge/reject/{gid}/{cid}")
async def reject(gid: str, cid: str, _=Depends(check_auth)):
    db.reject_charge(gid, cid)
    return RedirectResponse("/charges", status_code=302)

@app.post("/api/user/balance")
async def give_balance(request: Request, _=Depends(check_auth)):
    form = await request.form()
    gid = form.get("guild_id"); uid = form.get("user_id"); amount = int(form.get("amount",0))
    user = db.get_user(gid, uid)
    user["balance"] += amount
    db.save_user(gid, uid, user)
    return RedirectResponse("/users", status_code=302)

@app.post("/api/guild/register")
async def register_guild(request: Request, _=Depends(check_auth)):
    form = await request.form()
    gid = form.get("guild_id"); days = int(form.get("days", 30))
    expires = "permanent" if days == 0 else (datetime.now() + timedelta(days=days)).isoformat()
    db.register_guild(gid, form.get("guild_name", gid), expires)
    return RedirectResponse("/guilds", status_code=302)

@app.post("/api/guild/deactivate/{gid}")
async def deactivate(gid: str, _=Depends(check_auth)):
    db.deactivate_guild(gid)
    return RedirectResponse("/guilds", status_code=302)

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, _=Depends(check_auth)):
    guilds = db.get_all_guilds()
    settings_map = {g["id"]: db.get_settings(g["id"]) for g in guilds}
    return templates.TemplateResponse("settings.html", {
        "request": request, "guilds": guilds,
        "settings_map": settings_map, "page": "settings"
    })

@app.post("/api/settings/bank/{gid}")
async def save_bank(gid: str, request: Request, _=Depends(check_auth)):
    form = await request.form()
    s = db.get_settings(gid)
    s["bank_name"]    = form.get("bank_name", "")
    s["bank_account"] = form.get("bank_account", "")
    s["bank_holder"]  = form.get("bank_holder", "")
    db.save_settings(gid, s)
    return RedirectResponse("/settings", status_code=302)

@app.post("/api/settings/charge/{gid}")
async def save_charge(gid: str, request: Request, _=Depends(check_auth)):
    form = await request.form()
    s = db.get_settings(gid)
    s["auto_charge"]       = form.get("auto_charge") == "1"
    s["min_charge"]        = int(form.get("min_charge", 1000))
    s["max_charge"]        = int(form.get("max_charge", 0))
    s["admin_channel_id"]  = form.get("admin_channel_id", "")
    db.save_settings(gid, s)
    return RedirectResponse("/settings", status_code=302)

@app.get("/health")
async def health(): return {"status": "ok"}

async def start_dashboard_async():
    port = int(os.getenv("DASHBOARD_PORT", 8080))
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning")
    server = uvicorn.Server(config)
    print(f"🌐 대시보드: http://0.0.0.0:{port}")
    await server.serve()
