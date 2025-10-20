import os
import shutil
import sqlite3
import uuid
from datetime import datetime, timedelta

from fastapi import FastAPI, File, UploadFile, Form, Request, HTTPException, Depends, Body, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from backend.database import SessionLocal, engine
from backend.models import Base, Product, Order, User, Session as DbSession
import bcrypt


# Ensure upload directory exists
os.makedirs("backend/static/uploads", exist_ok=True)

Base.metadata.create_all(bind=engine)

# Ensure legacy columns for orders exist (simple auto-migration)
def _ensure_order_columns():
    try:
        db_path = engine.url.database
        con = sqlite3.connect(db_path)
        cur = con.cursor()
        cur.execute("PRAGMA table_info(orders)")
        cols = [r[1] for r in cur.fetchall()]
        additions = [("full_name", "TEXT"), ("address", "TEXT"), ("phone", "TEXT")]
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
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="backend/static"), name="static")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


SESSION_COOKIE = "session"


def _hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(pw: str, pw_hash: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode("utf-8"), pw_hash.encode("utf-8"))
    except Exception:
        return False


def _issue_session(db: Session, user_id: int) -> str:
    token = uuid.uuid4().hex
    expires = datetime.utcnow() + timedelta(days=7)
    sess = DbSession(user_id=user_id, token=token, expires_at=expires)
    db.add(sess)
    db.commit()
    return token


def _user_from_request(request: Request, db: Session):
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    sess = (
        db.query(DbSession)
        .filter(DbSession.token == token, DbSession.expires_at > datetime.utcnow())
        .first()
    )
    if not sess:
        return None
    return db.query(User).filter(User.id == sess.user_id).first()


@app.middleware("http")
async def admin_check(request: Request, call_next):
    if request.url.path.startswith("/api/admin"):
        db = SessionLocal()
        try:
            user = _user_from_request(request, db)
            if not user or not user.is_admin:
                return JSONResponse(status_code=403, content={"detail": "Access denied"})
        finally:
            db.close()
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
    path = f"backend/static/uploads/{file.filename}"
    with open(path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    product = Product(
        name=name,
        description=description,
        price=price,
        image=f"/static/uploads/{file.filename}",
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    return {"status": "ok", "id": product.id}


@app.get("/api/admin/orders")
def get_orders(db: Session = Depends(get_db)):
    return db.query(Order).order_by(Order.created_at.desc()).all()


@app.post("/api/admin/orders/update_status")
def update_status(order_id: int, status: str, db: Session = Depends(get_db)):
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


# --- Auth endpoints ---
@app.post("/api/auth/login")
def login(
    response: Response,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
):
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""
    telegram_id = payload.get("telegram_id")

    user = None
    if telegram_id is not None:
        try:
            user = db.query(User).filter(User.telegram_id == int(telegram_id)).first()
        except Exception:
            user = None
    elif username:
        user = db.query(User).filter(User.username == username).first()
    if not user:
        # Bootstrap: create first admin by Telegram ID
        total_users = db.query(User).count()
        if telegram_id is not None and total_users == 0 and password:
            user = User(telegram_id=int(telegram_id), is_admin=True, password_hash=_hash_password(password))
            db.add(user)
            db.commit()
            db.refresh(user)
        else:
            raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.password_hash or not _verify_password(password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = _issue_session(db, user.id)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=False,
        samesite="Lax",
        max_age=7 * 24 * 3600,
        path="/",
    )
    return {"status": "ok", "is_admin": user.is_admin}


@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"status": "ok"}


@app.get("/api/auth/me")
def me(request: Request, db: Session = Depends(get_db)):
    user = _user_from_request(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return {"id": user.id, "telegram_id": user.telegram_id, "username": user.username, "is_admin": user.is_admin}


@app.get("/api/my/orders")
def my_orders(request: Request, db: Session = Depends(get_db)):
    user = _user_from_request(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return db.query(Order).filter(Order.user_id == user.id).order_by(Order.created_at.desc()).all()


# Mount the WebApp (frontend) at root LAST to avoid intercepting /api/* routes
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
