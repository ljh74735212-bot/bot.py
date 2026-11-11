"""
관리자 웹 대시보드 (FastAPI)
봇의 data.json을 직접 읽어 실시간 통계/관리 제공
"""

import os
import sys
import json
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import secrets
import uvicorn

# 봇 루트 경로
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
import database as db

app = FastAPI(docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

DASHBOARD_USER = os.getenv("DASHBOARD_USER", "admin")
DASHBOARD_PASS = os.getenv("DASHBOARD_PASS", "admin1234")
SECRET_KEY = os.getenv("DASHBOARD_SECRET", "supersecretkey")

# ── 간단 세션 (쿠키) ──────────────────────────────
SESSIONS: set = set()

def check_auth(request: Request):
    token = request.cookies.get("session")
    if token not in SESSIONS:
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    return True

# ── 통계 계산 ─────────────────────────────────────
def get_stats():
    data = db.load_db()
    users = data.get("users", {})
    products = data.get("products", {})
    charges = data.get("pending_charges", {})

    today = datetime.now().strftime("%Y-%m-%d")

    total_revenue = sum(
        p["price"]
        for u in users.values()
        for p in u.get("purchases", [])
    )

    today_revenue = sum(
        p["price"]
        for u in users.values()
        for p in u.get("purchases", [])
        if p.get("purchased_at", "").startswith(today)
    )

    # 최근 30일 일별 매출
    chart_data = {}
    for i in range(30):
        d = (datetime.now() - timedelta(days=29 - i)).strftime("%Y-%m-%d")
        chart_data[d] = 0
    for u in users.values():
        for p in u.get("purchases", []):
            d = p.get("purchased_at", "")[:10]
            if d in chart_data:
                chart_data[d] += p["price"]

    # 최근 구매 10건
    recent = []
    for uid, u in users.items():
        for p in u.get("purchases", []):
            recent.append({
                "username": u["username"],
                "product": p["product_name"],
                "price": p["price"],
                "date": p.get("purchased_at", "")[:16].replace("T", " "),
            })
    recent.sort(key=lambda x: x["date"], reverse=True)

    return {
        "total_revenue": total_revenue,
        "today_revenue": today_revenue,
        "total_users": len(users),
        "total_products": len(products),
        "chart_labels": list(chart_data.keys()),
        "chart_values": list(chart_data.values()),
        "recent_purchases": recent[:10],
        "pending_charges": [
            {"id": cid, **c}
            for cid, c in charges.items()
            if c["status"] == "pending"
        ],
        "users": [
            {"id": uid, **u}
            for uid, u in users.items()
        ],
        "products": [
            {"id": pid, **p}
            for pid, p in products.items()
        ],
    }

# ── 라우트 ────────────────────────────────────────
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
async def login(request: Request):
    form = await request.form()
    username = form.get("username", "")
    password = form.get("password", "")
    if username == DASHBOARD_USER and password == DASHBOARD_PASS:
        token = secrets.token_hex(32)
        SESSIONS.add(token)
        resp = RedirectResponse("/", status_code=302)
        resp.set_cookie("session", token, httponly=True)
        return resp
    return templates.TemplateResponse("login.html", {"request": request, "error": "아이디 또는 비밀번호가 틀렸습니다."})

@app.get("/logout")
async def logout(request: Request):
    token = request.cookies.get("session")
    SESSIONS.discard(token)
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie("session")
    return resp

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, _=Depends(check_auth)):
    stats = get_stats()
    return templates.TemplateResponse("dashboard.html", {"request": request, "stats": stats, "page": "dashboard"})

@app.get("/products", response_class=HTMLResponse)
async def products_page(request: Request, _=Depends(check_auth)):
    stats = get_stats()
    return templates.TemplateResponse("products.html", {"request": request, "stats": stats, "page": "products"})

@app.get("/users", response_class=HTMLResponse)
async def users_page(request: Request, _=Depends(check_auth)):
    stats = get_stats()
    return templates.TemplateResponse("users.html", {"request": request, "stats": stats, "page": "users"})

@app.get("/charges", response_class=HTMLResponse)
async def charges_page(request: Request, _=Depends(check_auth)):
    stats = get_stats()
    return templates.TemplateResponse("charges.html", {"request": request, "stats": stats, "page": "charges"})

# ── API ───────────────────────────────────────────
@app.post("/api/product/add")
async def api_add_product(request: Request, _=Depends(check_auth)):
    form = await request.form()
    import time
    pid = f"P-{int(time.time() * 1000)}"
    db.save_product(pid, {
        "category": form.get("category"),
        "name": form.get("name"),
        "price": int(form.get("price", 0)),
        "content": form.get("content"),
        "stock": int(form.get("stock", -1)),
    })
    return RedirectResponse("/products", status_code=302)

@app.post("/api/product/delete/{pid}")
async def api_del_product(pid: str, _=Depends(check_auth)):
    db.delete_product(pid)
    return RedirectResponse("/products", status_code=302)

@app.post("/api/charge/approve/{cid}")
async def api_approve(cid: str, _=Depends(check_auth)):
    result = db.approve_charge(cid)
    if not result:
        raise HTTPException(400, "not found")
    return RedirectResponse("/charges", status_code=302)

@app.post("/api/charge/reject/{cid}")
async def api_reject(cid: str, _=Depends(check_auth)):
    result = db.reject_charge(cid)
    if not result:
        raise HTTPException(400, "not found")
    return RedirectResponse("/charges", status_code=302)

@app.post("/api/user/balance")
async def api_give_balance(request: Request, _=Depends(check_auth)):
    form = await request.form()
    uid = form.get("user_id")
    amount = int(form.get("amount", 0))
    user = db.get_user(uid)
    user["balance"] += amount
    db.save_user(uid, user)
    return RedirectResponse("/users", status_code=302)

@app.get("/api/stats")
async def api_stats(_=Depends(check_auth)):
    return get_stats()

def start_dashboard():
    port = int(os.getenv("DASHBOARD_PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")

async def start_dashboard_async():
    port = int(os.getenv("DASHBOARD_PORT", 8080))
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning")
    server = uvicorn.Server(config)
    print(f"🌐 대시보드 실행 중: http://0.0.0.0:{port}")
    await server.serve()
