import json
import os
import time
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "data.json")

def load_db() -> dict:
    if not os.path.exists(DB_PATH):
        initial = {"users": {}, "products": {}, "pending_charges": {}, "settings": {}}
        save_db(initial)
        return initial
    data = json.load(open(DB_PATH, "r", encoding="utf-8"))
    if "settings" not in data:
        data["settings"] = {}
        save_db(data)
    return data

# ── 설정 ──────────────────────────────────────────
def get_settings() -> dict:
    return load_db().get("settings", {})

def save_settings(settings: dict):
    db = load_db()
    db["settings"] = settings
    save_db(db)

def get_bank_info() -> dict:
    s = get_settings()
    return {
        "name": s.get("bank_name", os.getenv("BANK_NAME", "은행명 미설정")),
        "account": s.get("bank_account", os.getenv("BANK_ACCOUNT", "계좌번호 미설정")),
        "holder": s.get("bank_holder", os.getenv("BANK_HOLDER", "예금주 미설정")),
    }

def save_db(data: dict):
    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ── 유저 ──────────────────────────────────────────
def get_user(user_id: str, username: str = "알 수 없음") -> dict:
    db = load_db()
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
        save_db(db)
    return db["users"][uid]

def save_user(user_id: str, user_data: dict):
    db = load_db()
    db["users"][str(user_id)] = user_data
    save_db(db)

# ── 상품 ──────────────────────────────────────────
def get_products() -> dict:
    return load_db()["products"]

def get_product(product_id: str) -> dict | None:
    return load_db()["products"].get(str(product_id))

def save_product(product_id: str, product_data: dict):
    db = load_db()
    db["products"][str(product_id)] = product_data
    save_db(db)

def delete_product(product_id: str):
    db = load_db()
    db["products"].pop(str(product_id), None)
    save_db(db)

def get_categories() -> list:
    products = get_products()
    return list({p["category"] for p in products.values()})

def get_products_by_category(category: str) -> list:
    return [
        {"id": pid, **p}
        for pid, p in get_products().items()
        if p["category"] == category
    ]

# ── 충전 ──────────────────────────────────────────
def add_pending_charge(user_id: str, amount: int, depositor_name: str) -> str:
    db = load_db()
    charge_id = f"CHG-{int(time.time() * 1000)}"
    db["pending_charges"][charge_id] = {
        "user_id": str(user_id),
        "amount": amount,
        "depositor_name": depositor_name,
        "created_at": datetime.now().isoformat(),
        "status": "pending",
    }
    save_db(db)
    return charge_id

def get_pending_charges() -> list:
    db = load_db()
    return [
        {"id": cid, **c}
        for cid, c in db["pending_charges"].items()
        if c["status"] == "pending"
    ]

def approve_charge(charge_id: str) -> dict | None:
    db = load_db()
    charge = db["pending_charges"].get(charge_id)
    if not charge:
        return None
    charge["status"] = "approved"
    uid = charge["user_id"]
    if uid in db["users"]:
        db["users"][uid]["balance"] += charge["amount"]
    save_db(db)
    return charge

def reject_charge(charge_id: str) -> dict | None:
    db = load_db()
    charge = db["pending_charges"].get(charge_id)
    if not charge:
        return None
    charge["status"] = "rejected"
    save_db(db)
    return charge

# ── 구매 ──────────────────────────────────────────
def purchase_product(user_id: str, product_id: str) -> dict:
    db = load_db()
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

    # 등급 업데이트
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

    save_db(db)
    return {"success": True, "product": product, "content": product["content"]}
