import json
import os
import time
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
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
        initial = {"users": {}, "products": {}, "pending_charges": {}, "settings": {}}
        save_db(guild_id, initial)
        return initial
    data = json.load(open(path, "r", encoding="utf-8"))
    if "settings" not in data:
        data["settings"] = {}
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
    if not charge:
        return None
    charge["status"] = "approved"
    uid = charge["user_id"]
    if uid in db["users"]:
        db["users"][uid]["balance"] += charge["amount"]
    save_db(guild_id, db)
    return charge

def reject_charge(guild_id: str, charge_id: str) -> dict | None:
    db = load_db(guild_id)
    charge = db["pending_charges"].get(charge_id)
    if not charge:
        return None
    charge["status"] = "rejected"
    save_db(guild_id, db)
    return charge

# ── 구매 ──────────────────────────────────────────
def purchase_product(guild_id: str, user_id: str, product_id: str) -> dict:
    db = load_db(guild_id)
    uid = str(user_id)
    user = db["users"].get(uid)
    product = db["products"].get(str(product_id))

    if not user or not product:
        return {"success": False, "reason": "유저 또는 상품 없음"}
    if product.get("stock", -1) == 0:
        return {"success": False, "reason": "재고 없음"}
    if user["balance"] < product["price"]:
        return {"success": False, "reason": "잔액 부족"}

    user["balance"] -= product["price"]
    user["total_purchase"] += product["price"]

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
        "price": product["price"],
        "content": product["content"],
        "purchased_at": datetime.now().isoformat(),
    })

    save_db(guild_id, db)
    return {"success": True, "product": product, "content": product["content"]}
