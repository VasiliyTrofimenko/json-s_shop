import os
import shutil
import sqlite3
from fastapi import FastAPI, File, UploadFile, Form, Request, HTTPException, Depends, Body
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from backend.database import SessionLocal, engine
from backend.models import Base, Product, Order
from bot.config import ADMIN_IDS

# Создаём папки для статики при первом запуске
os.makedirs("backend/static/uploads", exist_ok=True)

Base.metadata.create_all(bind=engine)

# Ensure new columns exist in existing SQLite DB (simple auto-migration)
def _ensure_order_columns():
    try:
        db_path = engine.url.database
        con = sqlite3.connect(db_path)
        cur = con.cursor()
        cur.execute("PRAGMA table_info(orders)")
        cols = [r[1] for r in cur.fetchall()]
        additions = [
            ("full_name", "TEXT"),
            ("address", "TEXT"),
            ("phone", "TEXT"),
        ]
        changed = False
        for name, ddl in additions:
            if name not in cols:
                cur.execute(f"ALTER TABLE orders ADD COLUMN {name} {ddl}")
                changed = True
        if changed:
            con.commit()
    except Exception:
        pass
    finally:
        try:
            con.close()
        except Exception:
            pass

_ensure_order_columns()

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

app.mount("/static", StaticFiles(directory="backend/static"), name="static")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.middleware("http")
async def admin_check(request: Request, call_next):
    """Проверка Telegram ID для админских маршрутов"""
    if request.url.path.startswith("/api/admin"):
        user_id = request.headers.get("X-Telegram-Id")
        try:
            uid = int(user_id) if user_id is not None else None
        except (ValueError, TypeError):
            uid = None
        if uid is None or uid not in ADMIN_IDS:
            return JSONResponse(status_code=403, content={"detail": "Access denied"})
    return await call_next(request)


@app.get("/api/products")
def get_products(db: Session = Depends(get_db)):
    return db.query(Product).filter(Product.is_active.is_(True)).all()


@app.post("/api/admin/upload")
async def upload_product(
    name: str = Form(...),
    description: str = Form(""),
    price: float = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Добавление товара"""
    path = f"backend/static/uploads/{file.filename}"
    with open(path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    product = Product(
        name=name,
        description=description,
        price=price,
        image=f"/static/uploads/{file.filename}"
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    return {"status": "ok", "id": product.id}


@app.get("/api/admin/orders")
def get_orders(db: Session = Depends(get_db)):
    """Список заказов"""
    return db.query(Order).order_by(Order.created_at.desc()).all()


@app.post("/api/admin/orders/update_status")
def update_status(order_id: int, status: str, db: Session = Depends(get_db)):
    """Обновление статуса заказа"""
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    order.status = status
    db.commit()
    return {"status": "ok", "order_id": order_id, "new_status": status}


@app.delete("/api/admin/orders/{order_id}")
def delete_order(order_id: int, db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    db.delete(order)
    db.commit()
    return {"status": "ok", "deleted": order_id}


# --- Public order creation ---
@app.post("/api/orders")
def create_order(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
):
    """Create an order from WebApp checkout form.
    Expected JSON: { items: [int], full_name: str, address: str, phone: str, user_id?: int }
    """
    items = payload.get("items") or []
    if not isinstance(items, list) or not all(isinstance(i, int) for i in items):
        raise HTTPException(status_code=422, detail="Invalid items")

    full_name = (payload.get("full_name") or "").strip()
    address = (payload.get("address") or "").strip()
    phone = (payload.get("phone") or "").strip()
    user_id = payload.get("user_id")

    if not full_name or not address or not phone:
        raise HTTPException(status_code=422, detail="Missing customer data")

    products = db.query(Product).filter(Product.id.in_(items)).all()
    if not products:
        raise HTTPException(status_code=404, detail="Products not found")
    total = sum(p.price for p in products)

    order = Order(
        user_id=user_id,
        items=",".join(str(i) for i in items),
        total=total,
        status="pending",
        full_name=full_name,
        address=address,
        phone=phone,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return {"status": "ok", "id": order.id}

# Mount the WebApp (frontend) at root LAST to avoid intercepting /api/* routes
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
