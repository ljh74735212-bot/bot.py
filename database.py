import json
import os
import secrets
import time
from datetime import datetime

DATA_DIR = os.getenv("DATA_DIR") or os.path.join(os.path.dirname(__file__), "data")
MASTER_DB_PATH = os.path.join(DATA_DIR, "master.json")

os.makedirs(DATA_DIR, exist_ok=True)

# ── 마스터 DB (서버 구독 관리) ─────────────────────
def load_master() -> dict:
    if not os.path.exists(MASTER_DB_PATH):
        initial = {"guilds": {}}
        save_master(initial)
        return initial
    return json.load(open(MASTER_DB_PATH, "r", encoding="utf-8"))

def save_master(data: dict):
    with open(MASTER_DB_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_guild_info(guild_id: str) -> dict | None:
    return load_master()["guilds"].get(str(guild_id))

def register_guild(guild_id: str, guild_name: str, expires_at: str, admin_role_id: str = None):
    master = load_master()
    master["guilds"][str(guild_id)] = {
        "guild_id": str(guild_id),
        "guild_name": guild_name,
        "expires_at": expires_at,
        "admin_role_id": admin_role_id,
        "registered_at": datetime.now().isoformat(),
        "active": True,
    }
    save_master(master)

def deactivate_guild(guild_id: str):
    master = load_master()
    if str(guild_id) in master["guilds"]:
        master["guilds"][str(guild_id)]["active"] = False
        save_master(master)

def is_guild_active(guild_id: str) -> bool:
    info = get_guild_info(str(guild_id))
    if not info:
        return False
    if not info.get("active", True):
        return False
    expires = info.get("expires_at")
    if expires and expires != "permanent":
        from datetime import datetime
        try:
            if datetime.fromisoformat(expires) < datetime.now():
                return False
        except:
            pass
    return True

def get_all_guilds() -> list:
    return [
        {"id": gid, **g}
        for gid, g in load_master()["guilds"].items()
    ]

# ── 서버별 DB ──────────────────────────────────────
def guild_db_path(guild_id: str) -> str:
    return os.path.join(DATA_DIR, f"guild_{guild_id}.json")

def load_db(guild_id: str) -> dict:
    path = guild_db_path(str(guild_id))
    if not os.path.exists(path):
        initial = {"users": {}, "products": {}, "pending_charges": {}, "settings": {}, "coupons": {}, "events": {}}
        save_db(guild_id, initial)
        return initial
    data = json.load(open(path, "r", encoding="utf-8"))
    changed = False
    for key in ("settings", "coupons", "events"):
        if key not in data:
            data[key] = {}
            changed = True
    if changed:
        save_db(guild_id, data)
    return data

def save_db(guild_id: str, data: dict):
    with open(guild_db_path(str(guild_id)), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ── 설정 ──────────────────────────────────────────
def get_settings(guild_id: str) -> dict:
    return load_db(guild_id).get("settings", {})

def save_settings(guild_id: str, settings: dict):
    db = load_db(guild_id)
    db["settings"] = settings
    save_db(guild_id, db)

def get_bank_info(guild_id: str) -> dict:
    s = get_settings(guild_id)
    return {
        "name": s.get("bank_name", "은행명 미설정"),
        "account": s.get("bank_account", "계좌번호 미설정"),
        "holder": s.get("bank_holder", "예금주 미설정"),
    }

# ── 유저 ──────────────────────────────────────────
def get_user(guild_id: str, user_id: str, username: str = "알 수 없음") -> dict:
    db = load_db(guild_id)
    uid = str(user_id)
    if uid not in db["users"]:
        db["users"][uid] = {
            "id": uid,
            "username": username,
            "balance": 0,
            "total_purchase": 0,
            "grade": "일반",
            "purchases": [],
        }
        save_db(guild_id, db)
    return db["users"][uid]

def save_user(guild_id: str, user_id: str, user_data: dict):
    db = load_db(guild_id)
    db["users"][str(user_id)] = user_data
    save_db(guild_id, db)

# ── 상품 ──────────────────────────────────────────
def get_products(guild_id: str) -> dict:
    return load_db(guild_id)["products"]

def get_product(guild_id: str, product_id: str) -> dict | None:
    return load_db(guild_id)["products"].get(str(product_id))

def save_product(guild_id: str, product_id: str, product_data: dict):
    db = load_db(guild_id)
    db["products"][str(product_id)] = product_data
    save_db(guild_id, db)

def delete_product(guild_id: str, product_id: str):
    db = load_db(guild_id)
    db["products"].pop(str(product_id), None)
    save_db(guild_id, db)

def get_categories(guild_id: str) -> list:
    return list({p["category"] for p in get_products(guild_id).values()})

def get_products_by_category(guild_id: str, category: str) -> list:
    return [
        {"id": pid, **p}
        for pid, p in get_products(guild_id).items()
        if p["category"] == category
    ]

# ── 충전 ──────────────────────────────────────────
def add_pending_charge(guild_id: str, user_id: str, amount: int, depositor_name: str) -> str:
    db = load_db(guild_id)
    charge_id = f"CHG-{int(time.time() * 1000)}"
    db["pending_charges"][charge_id] = {
        "user_id": str(user_id),
        "amount": amount,
        "depositor_name": depositor_name,
        "created_at": datetime.now().isoformat(),
        "status": "pending",
    }
    save_db(guild_id, db)
    return charge_id

def get_pending_charges(guild_id: str) -> list:
    db = load_db(guild_id)
    return [
        {"id": cid, **c}
        for cid, c in db["pending_charges"].items()
        if c["status"] == "pending"
    ]

def approve_charge(guild_id: str, charge_id: str) -> dict | None:
    db = load_db(guild_id)
    charge = db["pending_charges"].get(charge_id)
    if not charge or charge.get("status") != "pending":
        return None  # missing or already handled (prevents double credit on double-click)
    charge["status"] = "approved"
    charge["approved_at"] = datetime.now().isoformat()
    uid = charge["user_id"]
    if uid not in db["users"]:
        db["users"][uid] = {
            "id": uid, "username": "Unknown", "balance": 0,
            "total_purchase": 0, "grade": "일반", "purchases": [],
        }
    db["users"][uid]["balance"] += charge["amount"]

    # 이벤트 보너스 (예: 선착순 N명 2배 충전) - 수동/자동 승인 모두 여기서 처리됨
    eligible = [
        ev for ev in db["events"].values()
        if event_status(ev) == "active"
        and uid not in ev.get("claimed_by", [])
        and charge["amount"] >= ev.get("min_amount", 0)
    ]
    if eligible:
        ev = max(eligible, key=lambda e: e.get("multiplier", 1))
        bonus = int(charge["amount"] * (ev.get("multiplier", 1) - 1))
        if ev.get("max_bonus", 0) > 0:
            bonus = min(bonus, ev["max_bonus"])
        if bonus > 0:
            db["users"][uid]["balance"] += bonus
            ev.setdefault("claimed_by", []).append(uid)
            charge["bonus"] = bonus
            charge["event_id"] = ev["id"]
            charge["event_name"] = ev["name"]

    save_db(guild_id, db)
    return charge

def reject_charge(guild_id: str, charge_id: str) -> dict | None:
    db = load_db(guild_id)
    charge = db["pending_charges"].get(charge_id)
    if not charge or charge.get("status") != "pending":
        return None
    charge["status"] = "rejected"
    save_db(guild_id, db)
    return charge

# ── 상태 계산 (쿠폰/이벤트 공용) ─────────────────
def _parse_ts(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None

def coupon_status(c: dict, now=None) -> str:
    now = now or datetime.now()
    if not c.get("active", True):
        return "paused"
    exp = _parse_ts(c.get("expires_at"))
    if exp and exp < now:
        return "expired"
    mx = c.get("max_uses", 0)
    if mx > 0 and c.get("used_count", 0) >= mx:
        return "full"
    return "active"

def event_status(ev: dict, now=None) -> str:
    now = now or datetime.now()
    if not ev.get("active", True):
        return "paused"
    start, end = _parse_ts(ev.get("start_at")), _parse_ts(ev.get("end_at"))
    if end and end < now:
        return "expired"
    mw = ev.get("max_winners", 0)
    if mw > 0 and len(ev.get("claimed_by", [])) >= mw:
        return "full"
    if start and start > now:
        return "scheduled"
    return "active"

# ── 할인 쿠폰 ─────────────────────────────────────
def add_coupon(guild_id, code, kind, value, max_uses=0, per_user=1, min_purchase=0, expires_at="") -> bool:
    code = code.strip().upper()
    d = load_db(guild_id)
    if code in d["coupons"]:
        return False
    d["coupons"][code] = {
        "code": code, "kind": kind,  # "percent" | "fixed"
        "value": value, "max_uses": max_uses, "per_user": per_user,
        "min_purchase": min_purchase, "expires_at": expires_at,
        "used_count": 0, "used_by": [], "active": True,
        "created_at": datetime.now().isoformat(),
    }
    save_db(guild_id, d)
    return True

def get_coupons(guild_id) -> list:
    items = [{**c, "status": coupon_status(c)} for c in load_db(guild_id)["coupons"].values()]
    return sorted(items, key=lambda c: c.get("created_at", ""), reverse=True)

def toggle_coupon(guild_id, code):
    d = load_db(guild_id)
    c = d["coupons"].get(code.strip().upper())
    if c:
        c["active"] = not c.get("active", True)
        save_db(guild_id, d)

def delete_coupon(guild_id, code):
    d = load_db(guild_id)
    d["coupons"].pop(code.strip().upper(), None)
    save_db(guild_id, d)

def _check_coupon(d: dict, code: str, user_id, price: int) -> dict:
    code = (code or "").strip().upper()
    c = d["coupons"].get(code)
    if not c:
        return {"ok": False, "reason": "존재하지 않는 쿠폰입니다."}
    reasons = {"paused": "사용할 수 없는 쿠폰입니다.", "expired": "만료된 쿠폰입니다.", "full": "선착순 마감된 쿠폰입니다."}
    st = coupon_status(c)
    if st != "active":
        return {"ok": False, "reason": reasons.get(st, "사용할 수 없는 쿠폰입니다.")}
    per_user = c.get("per_user", 1)
    if per_user > 0 and c.get("used_by", []).count(str(user_id)) >= per_user:
        return {"ok": False, "reason": "이미 사용한 쿠폰입니다."}
    if price < c.get("min_purchase", 0):
        return {"ok": False, "reason": f"{c['min_purchase']:,}원 이상 구매 시 사용할 수 있습니다."}
    discount = price * c["value"] // 100 if c["kind"] == "percent" else c["value"]
    return {"ok": True, "code": code, "discount": max(0, min(discount, price))}

def check_coupon(guild_id, code, user_id, price) -> dict:
    return _check_coupon(load_db(guild_id), code, user_id, price)

# ── 충전 이벤트 (선착순 N배 충전) ─────────────────
def add_charge_event(guild_id, name, multiplier, max_winners=0, min_amount=0, max_bonus=0, start_at="", end_at="") -> str:
    d = load_db(guild_id)
    eid = f"EV-{int(time.time() * 1000)}{secrets.token_hex(2)}"
    d["events"][eid] = {
        "id": eid, "name": name, "kind": "charge_bonus",
        "multiplier": multiplier, "max_winners": max_winners,
        "min_amount": min_amount, "max_bonus": max_bonus,
        "start_at": start_at, "end_at": end_at,
        "claimed_by": [], "active": True,
        "created_at": datetime.now().isoformat(),
    }
    save_db(guild_id, d)
    return eid

def get_events(guild_id) -> list:
    items = [{**e, "status": event_status(e)} for e in load_db(guild_id)["events"].values()]
    return sorted(items, key=lambda e: e.get("created_at", ""), reverse=True)

def toggle_event(guild_id, event_id):
    d = load_db(guild_id)
    ev = d["events"].get(event_id)
    if ev:
        ev["active"] = not ev.get("active", True)
        save_db(guild_id, d)

def delete_event(guild_id, event_id):
    d = load_db(guild_id)
    d["events"].pop(event_id, None)
    save_db(guild_id, d)

def active_charge_events(guild_id, user_id=None) -> list:
    """지금 진행 중이고(자리가 남아 있고) 이 유저가 아직 못 받은 이벤트 목록"""
    out = []
    for ev in load_db(guild_id)["events"].values():
        if event_status(ev) != "active":
            continue
        if user_id is not None and str(user_id) in ev.get("claimed_by", []):
            continue
        out.append(ev)
    return out

# ── 구매 ──────────────────────────────────────────
def purchase_product(guild_id: str, user_id: str, product_id: str, coupon_code: str = None) -> dict:
    db = load_db(guild_id)
    uid = str(user_id)
    user = db["users"].get(uid)
    product = db["products"].get(str(product_id))

    if not user or not product:
        return {"success": False, "reason": "유저 또는 상품 없음"}
    if product.get("stock", -1) == 0:
        return {"success": False, "reason": "재고 없음"}

    price = product["price"]
    discount = 0
    coupon = None
    if coupon_code:
        chk = _check_coupon(db, coupon_code, uid, price)
        if not chk["ok"]:
            return {"success": False, "reason": chk["reason"]}
        discount, coupon = chk["discount"], chk["code"]
    final = price - discount

    if user["balance"] < final:
        return {"success": False, "reason": "잔액 부족"}

    user["balance"] -= final
    user["total_purchase"] += final

    if coupon:
        c = db["coupons"][coupon]
        c["used_count"] = c.get("used_count", 0) + 1
        c.setdefault("used_by", []).append(uid)

    if product.get("stock", -1) > 0:
        product["stock"] -= 1

    tp = user["total_purchase"]
    if tp >= 50000:
        user["grade"] = "VIP"
    elif tp >= 10000:
        user["grade"] = "우수구매자"
    else:
        user["grade"] = "구매자"

    user["purchases"].insert(0, {
        "product_id": product_id,
        "product_name": product["name"],
        "price": final,
        "original_price": price,
        "coupon": coupon,
        "content": product["content"],
        "purchased_at": datetime.now().isoformat(),
    })

    save_db(guild_id, db)
    return {"success": True, "product": product, "content": product["content"],
            "paid": final, "discount": discount, "coupon": coupon}
