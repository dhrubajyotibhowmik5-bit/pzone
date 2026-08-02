"""Poster Zone — FastAPI backend
E-commerce API: catalog, cart, orders, JWT+Google auth, Stripe checkout (Flow B / BYOK).
"""
import os
import io
import csv
import uuid
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional, Dict, Any

import bcrypt
import jwt
import stripe
from fastapi import FastAPI, APIRouter, HTTPException, Depends, Request, Response, Header, UploadFile, File
from fastapi.responses import JSONResponse
from asyncio import to_thread as asyncio_to_thread
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import certifi
from pydantic import BaseModel, Field, EmailStr, ConfigDict

from services import (
    init_storage, put_object, guess_ext, MIME_TYPES,
    send_email, order_placed_html, order_shipped_html,
)

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

# ---------- Config ----------
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]
JWT_SECRET = os.environ.get("JWT_SECRET", "change_me")
JWT_ALGO = "HS256"
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@posterzone.com")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Admin@12345")

# Stripe (official SDK — set your real secret key in the environment)
STRIPE_API_KEY = os.environ.get("STRIPE_API_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
stripe.api_key = STRIPE_API_KEY

# Google Sign-In (verifies ID tokens issued by Google Identity Services on the frontend)
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")

# ---------- DB ----------
client = AsyncIOMotorClient(MONGO_URL, tlsCAFile=certifi.where())
db = client[DB_NAME]

# ---------- App ----------
app = FastAPI(title="Poster Zone API")
api = APIRouter(prefix="/api")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("posterzone")


# =====================================================
# Models
# =====================================================
class PosterSize(BaseModel):
    label: str
    price_delta: float = 0.0


class Poster(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    slug: str
    title: str
    description: str
    price: float
    category: str
    tags: List[str] = []
    image_url: str
    sizes: List[PosterSize] = []
    frame_options: List[PosterSize] = []
    stock: int = 100
    featured: bool = False
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class PosterCreate(BaseModel):
    slug: str
    title: str
    description: str
    price: float
    category: str
    tags: List[str] = []
    image_url: str
    sizes: List[PosterSize] = []
    frame_options: List[PosterSize] = []
    stock: int = 100
    featured: bool = False


class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    name: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class User(BaseModel):
    user_id: str
    email: str
    name: str
    picture: Optional[str] = None
    role: str = "user"
    provider: str = "email"


class CartItem(BaseModel):
    poster_id: str
    title: str
    image_url: str
    size: str
    frame: str
    quantity: int
    unit_price: float


class OrderCreate(BaseModel):
    items: List[CartItem]
    shipping_name: str
    shipping_email: EmailStr
    shipping_phone: str
    shipping_address: str
    shipping_city: str
    shipping_state: str
    shipping_pincode: str
    origin_url: str


class OrderStatus(BaseModel):
    order_id: str
    status: str
    payment_status: str
    total: float


# =====================================================
# Helpers
# =====================================================
def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except Exception:
        return False


def create_jwt(user_id: str) -> str:
    payload = {"user_id": user_id, "exp": datetime.now(timezone.utc) + timedelta(days=7)}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def decode_jwt(token: str) -> Optional[str]:
    try:
        data = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
        return data.get("user_id")
    except Exception:
        return None


async def get_current_user(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> Optional[Dict[str, Any]]:
    """Resolve the current user from a `Bearer <jwt>` Authorization header.

    Both email/password login and Google Sign-In issue the same kind of JWT, so this is
    the single source of truth for auth. Using a header (instead of a cookie) avoids the
    SameSite / third-party-cookie problems that come up when the frontend (Netlify) and
    backend (Render) live on different domains.
    """
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        uid = decode_jwt(token)
        if uid:
            user = await db.users.find_one({"user_id": uid}, {"_id": 0, "password_hash": 0})
            if user:
                return user
    return None


async def require_user(user=Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


async def require_admin(user=Depends(require_user)):
    if user.get("role") != "admin":
        raise HTTPException(403, "Admin access required")
    return user


# =====================================================
# Routes — Health
# =====================================================
@api.get("/")
async def root():
    return {"service": "poster-zone", "status": "ok"}


# =====================================================
# Routes — Auth (Email / Password JWT)
# =====================================================
@api.post("/auth/signup")
async def signup(req: SignupRequest):
    existing = await db.users.find_one({"email": req.email})
    if existing:
        raise HTTPException(400, "Email already registered")
    uid = f"user_{uuid.uuid4().hex[:12]}"
    doc = {
        "user_id": uid,
        "email": req.email,
        "name": req.name,
        "picture": None,
        "role": "user",
        "provider": "email",
        "password_hash": hash_password(req.password),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.users.insert_one(doc)
    token = create_jwt(uid)
    return {"token": token, "user": {"user_id": uid, "email": req.email, "name": req.name, "role": "user"}}


@api.post("/auth/login")
async def login(req: LoginRequest):
    user = await db.users.find_one({"email": req.email})
    if not user or not user.get("password_hash"):
        raise HTTPException(401, "Invalid credentials")
    if not verify_password(req.password, user["password_hash"]):
        raise HTTPException(401, "Invalid credentials")
    token = create_jwt(user["user_id"])
    return {
        "token": token,
        "user": {
            "user_id": user["user_id"],
            "email": user["email"],
            "name": user["name"],
            "role": user.get("role", "user"),
            "picture": user.get("picture"),
        },
    }


@api.get("/auth/me")
async def me(user=Depends(require_user)):
    return user


@api.post("/auth/logout")
async def logout():
    """Stateless — the frontend simply discards its JWT. Kept as a route for compatibility."""
    return {"ok": True}


# =====================================================
# Routes — Google Sign-In
# =====================================================
@api.post("/auth/google")
async def google_login(request: Request):
    """Verify a Google ID token (the `credential` returned by Google Identity Services'
    Sign In With Google button / One Tap on the frontend) and log the user in.

    Requires GOOGLE_CLIENT_ID to be set. Get one at https://console.cloud.google.com/apis/credentials
    (OAuth client ID, type "Web application", with your Netlify URL as an authorized origin).
    """
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(501, "Google Sign-In is not configured on this server (missing GOOGLE_CLIENT_ID)")

    body = await request.json()
    credential = body.get("credential")
    if not credential:
        raise HTTPException(400, "credential required")

    from google.oauth2 import id_token as google_id_token
    from google.auth.transport import requests as google_requests

    try:
        idinfo = await asyncio_to_thread(
            google_id_token.verify_oauth2_token,
            credential, google_requests.Request(), GOOGLE_CLIENT_ID,
        )
    except Exception:
        raise HTTPException(401, "Invalid Google credential")

    email = idinfo.get("email")
    if not email:
        raise HTTPException(400, "Google account has no email")

    existing = await db.users.find_one({"email": email}, {"_id": 0})
    if existing:
        uid = existing["user_id"]
        await db.users.update_one(
            {"user_id": uid},
            {"$set": {"name": idinfo.get("name", existing.get("name")), "picture": idinfo.get("picture")}},
        )
    else:
        uid = f"user_{uuid.uuid4().hex[:12]}"
        await db.users.insert_one({
            "user_id": uid,
            "email": email,
            "name": idinfo.get("name", email),
            "picture": idinfo.get("picture"),
            "role": "user",
            "provider": "google",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

    token = create_jwt(uid)
    user_doc = await db.users.find_one({"user_id": uid}, {"_id": 0, "password_hash": 0})
    return {"token": token, "user": user_doc}


# =====================================================
# Routes — Posters (Public)
# =====================================================
@api.get("/posters")
async def list_posters(
    category: Optional[str] = None,
    q: Optional[str] = None,
    featured: Optional[bool] = None,
    limit: int = 100,
):
    query: Dict[str, Any] = {}
    if category and category != "all":
        query["category"] = category
    if featured is not None:
        query["featured"] = featured
    if q:
        query["$or"] = [
            {"title": {"$regex": q, "$options": "i"}},
            {"description": {"$regex": q, "$options": "i"}},
            {"tags": {"$regex": q, "$options": "i"}},
        ]
    items = await db.posters.find(query, {"_id": 0}).to_list(limit)
    return items


@api.get("/posters/categories")
async def get_categories():
    pipe = [{"$group": {"_id": "$category", "count": {"$sum": 1}}}, {"$sort": {"_id": 1}}]
    result = await db.posters.aggregate(pipe).to_list(100)
    return [{"name": r["_id"], "count": r["count"]} for r in result if r["_id"]]


@api.get("/posters/{slug}")
async def get_poster(slug: str):
    p = await db.posters.find_one({"slug": slug}, {"_id": 0})
    if not p:
        raise HTTPException(404, "Poster not found")
    return p


# =====================================================
# Routes — Admin: manage posters
# =====================================================
@api.post("/admin/posters")
async def admin_create_poster(req: PosterCreate, _admin=Depends(require_admin)):
    poster = Poster(**req.model_dump())
    doc = poster.model_dump()
    await db.posters.insert_one(doc)
    return poster


@api.put("/admin/posters/{poster_id}")
async def admin_update_poster(poster_id: str, req: PosterCreate, _admin=Depends(require_admin)):
    update = req.model_dump()
    result = await db.posters.update_one({"id": poster_id}, {"$set": update})
    if result.matched_count == 0:
        raise HTTPException(404, "Not found")
    doc = await db.posters.find_one({"id": poster_id}, {"_id": 0})
    return doc


@api.delete("/admin/posters/{poster_id}")
async def admin_delete_poster(poster_id: str, _admin=Depends(require_admin)):
    result = await db.posters.delete_one({"id": poster_id})
    if result.deleted_count == 0:
        raise HTTPException(404, "Not found")
    return {"ok": True}


@api.get("/admin/orders")
async def admin_orders(_admin=Depends(require_admin)):
    orders = await db.orders.find({}, {"_id": 0}).sort("created_at", -1).to_list(500)
    return orders


@api.put("/admin/orders/{order_id}/status")
async def admin_update_order_status(order_id: str, body: Dict[str, str], _admin=Depends(require_admin)):
    new_status = body.get("status")
    if new_status not in {"pending_payment", "awaiting_upi_verification", "placed", "processing", "shipped", "delivered", "cancelled"}:
        raise HTTPException(400, "Invalid status")
    order = await db.orders.find_one({"order_id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "Order not found")
    prev_status = order.get("status")
    await db.orders.update_one({"order_id": order_id}, {"$set": {"status": new_status}})
    # Trigger shipped email transition
    if new_status == "shipped" and prev_status != "shipped":
        to = order.get("shipping_address", {}).get("email")
        if to:
            await send_email(to, f"Your Poster Zone order {order_id} has shipped", order_shipped_html(order))
    return {"ok": True, "status": new_status}


@api.post("/admin/upload")
async def admin_upload(file: UploadFile = File(...), _admin=Depends(require_admin)):
    """Admin-only image upload for posters. Returns public URL served by this backend."""
    data = await file.read()
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(400, "File too large (max 8MB)")
    content_type = file.content_type or "application/octet-stream"
    if content_type not in MIME_TYPES.values():
        raise HTTPException(400, f"Unsupported type: {content_type}")
    ext = guess_ext(file.filename or "", content_type)
    path = f"{os.environ.get('STORAGE_APP_NAME', 'posterzone')}/posters/{uuid.uuid4().hex}.{ext}"
    try:
        result = await asyncio_to_thread(put_object, path, data, content_type)
    except Exception as e:
        log.error(f"upload failed: {e}")
        raise HTTPException(500, "Upload failed — storage unavailable")
    await db.uploaded_files.insert_one({
        "id": str(uuid.uuid4()),
        "storage_path": result["path"],
        "original_filename": file.filename,
        "content_type": content_type,
        "size": result.get("size"),
        "is_deleted": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    public_url = f"/api/files/{result['path']}"
    return {"url": public_url, "path": result["path"]}


@api.post("/admin/upload/bulk")
async def admin_upload_bulk(files: List[UploadFile] = File(...), _admin=Depends(require_admin)):
    """Admin-only bulk image upload. Returns list of {filename, url} or {filename, error}."""
    if len(files) > 30:
        raise HTTPException(400, "Too many files (max 30 per batch)")
    results = []
    for f in files:
        try:
            data = await f.read()
            if len(data) > 8 * 1024 * 1024:
                results.append({"filename": f.filename, "error": "File too large (max 8MB)"})
                continue
            content_type = f.content_type or "application/octet-stream"
            if content_type not in MIME_TYPES.values():
                results.append({"filename": f.filename, "error": f"Unsupported type: {content_type}"})
                continue
            ext = guess_ext(f.filename or "", content_type)
            path = f"{os.environ.get('STORAGE_APP_NAME', 'posterzone')}/posters/{uuid.uuid4().hex}.{ext}"
            result = await asyncio_to_thread(put_object, path, data, content_type)
            await db.uploaded_files.insert_one({
                "id": str(uuid.uuid4()),
                "storage_path": result["path"],
                "original_filename": f.filename,
                "content_type": content_type,
                "size": result.get("size"),
                "is_deleted": False,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            results.append({"filename": f.filename, "url": f"/api/files/{result['path']}", "path": result["path"]})
        except Exception as e:
            log.error(f"bulk upload failed for {f.filename}: {e}")
            results.append({"filename": f.filename, "error": "Upload failed"})
    return {"results": results}


@api.post("/admin/posters/bulk")
async def admin_posters_bulk(items: List[PosterCreate], _admin=Depends(require_admin)):
    """Bulk create posters. Skips slugs that already exist."""
    created = []
    errors = []
    for req in items:
        try:
            existing = await db.posters.find_one({"slug": req.slug})
            if existing:
                errors.append({"slug": req.slug, "error": "Slug already exists"})
                continue
            poster = Poster(**req.model_dump())
            await db.posters.insert_one(poster.model_dump())
            created.append(poster.model_dump())
        except Exception as e:
            errors.append({"slug": req.slug, "error": str(e)})
    return {"created": created, "errors": errors, "count": len(created)}


def _slugify(s: str) -> str:
    import re
    s = (s or "").lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:60] or f"poster-{uuid.uuid4().hex[:8]}"


DEFAULT_SIZES_JSON = [
    {"label": "A4 (8x11 in)", "price_delta": 0.0},
    {"label": "A3 (12x17 in)", "price_delta": 200.0},
    {"label": "A2 (17x23 in)", "price_delta": 500.0},
]
DEFAULT_FRAMES_JSON = []


@api.post("/admin/posters/import-csv")
async def admin_import_csv(file: UploadFile = File(...), _admin=Depends(require_admin)):
    """
    Import posters from a CSV file.
    Required columns: title, category, price, image_url
    Optional: description, slug, featured (true/false/yes/no/1/0), tags (comma-separated), stock
    """
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(400, "File must be a .csv")
    raw = await file.read()
    if len(raw) > 2 * 1024 * 1024:
        raise HTTPException(400, "CSV too large (max 2MB)")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("latin-1")
        except Exception:
            raise HTTPException(400, "Could not decode CSV — please save as UTF-8")

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(400, "CSV is empty or has no header row")
    fields = {f.strip().lower(): f for f in reader.fieldnames}
    required = {"title", "category", "price", "image_url"}
    missing = required - set(fields.keys())
    if missing:
        raise HTTPException(400, f"CSV is missing required columns: {', '.join(sorted(missing))}")

    def _pick(row: dict, key: str) -> str:
        col = fields.get(key)
        if not col:
            return ""
        return (row.get(col) or "").strip()

    def _bool(v: str) -> bool:
        return v.strip().lower() in {"1", "true", "yes", "y", "t"}

    created, errors, skipped = [], [], []
    seen_slugs = set()

    for i, row in enumerate(reader, start=2):  # start=2 (header is row 1)
        try:
            title = _pick(row, "title")
            category = _pick(row, "category")
            price_raw = _pick(row, "price")
            image_url = _pick(row, "image_url")
            if not title or not category or not price_raw or not image_url:
                errors.append({"row": i, "error": "Missing required field (title/category/price/image_url)"})
                continue
            try:
                price = float(price_raw.replace(",", ""))
            except ValueError:
                errors.append({"row": i, "title": title, "error": f"Invalid price: {price_raw}"})
                continue
            slug_raw = _pick(row, "slug") or title
            slug = _slugify(slug_raw)
            if slug in seen_slugs:
                skipped.append({"row": i, "title": title, "reason": "Duplicate slug in CSV"})
                continue
            existing = await db.posters.find_one({"slug": slug})
            if existing:
                skipped.append({"row": i, "title": title, "reason": "Slug already exists in catalog"})
                continue
            seen_slugs.add(slug)

            tags_raw = _pick(row, "tags")
            tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []
            description = _pick(row, "description") or f"Curated {category.lower()} print by Poster Zone."
            featured = _bool(_pick(row, "featured"))
            stock_raw = _pick(row, "stock")
            try:
                stock = int(stock_raw) if stock_raw else 100
            except ValueError:
                stock = 100

            poster = Poster(
                slug=slug,
                title=title,
                description=description,
                price=price,
                category=category,
                tags=tags,
                image_url=image_url,
                sizes=[PosterSize(**s) for s in DEFAULT_SIZES_JSON],
                frame_options=[PosterSize(**f) for f in DEFAULT_FRAMES_JSON],
                stock=stock,
                featured=featured,
            )
            await db.posters.insert_one(poster.model_dump())
            created.append({"row": i, "slug": slug, "title": title})
        except Exception as e:
            errors.append({"row": i, "error": str(e)})

    return {
        "count": len(created),
        "created": created,
        "skipped": skipped,
        "errors": errors,
    }


@api.get("/files/{path:path}")
async def public_file(path: str):
    """Public read for uploaded files (poster catalog is public)."""
    from services import get_object
    try:
        data, content_type = await asyncio_to_thread(get_object, path)
    except Exception:
        raise HTTPException(404, "File not found")
    return Response(content=data, media_type=content_type)


# =====================================================
# Routes — Orders + Stripe Checkout (Flow B)
# =====================================================
def _compute_total(items: List[CartItem]) -> float:
    return round(sum(i.unit_price * i.quantity for i in items), 2)


@api.post("/orders/checkout")
async def checkout(order: OrderCreate, request: Request, user=Depends(get_current_user)):
    if not order.items:
        raise HTTPException(400, "Cart is empty")
    subtotal = _compute_total(order.items)
    shipping = 0.0 if subtotal >= 999 else 49.0
    total = round(subtotal + shipping, 2)

    order_id = f"PZ{uuid.uuid4().hex[:10].upper()}"
    order_doc = {
        "order_id": order_id,
        "user_id": user["user_id"] if user else None,
        "items": [i.model_dump() for i in order.items],
        "subtotal": subtotal,
        "shipping": shipping,
        "total": total,
        "currency": "inr",
        "status": "pending_payment",
        "payment_method": "stripe",
        "payment_status": "pending",
        "shipping_address": {
            "name": order.shipping_name,
            "email": order.shipping_email,
            "phone": order.shipping_phone,
            "address": order.shipping_address,
            "city": order.shipping_city,
            "state": order.shipping_state,
            "pincode": order.shipping_pincode,
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.orders.insert_one(order_doc)

    if not STRIPE_API_KEY:
        raise HTTPException(500, "Card payments are not configured on this server (missing STRIPE_API_KEY)")

    success_url = f"{order.origin_url}/payment/success?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{order.origin_url}/payment/cancel"

    session = await asyncio_to_thread(
        stripe.checkout.Session.create,
        mode="payment",
        payment_method_types=["card"],
        line_items=[{
            "price_data": {
                "currency": "inr",
                "product_data": {"name": f"Poster Zone order {order_id}"},
                "unit_amount": int(round(total * 100)),
            },
            "quantity": 1,
        }],
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={
            "order_id": order_id,
            "user_id": user["user_id"] if user else "",
            "email": order.shipping_email,
        },
    )

    await db.payment_transactions.insert_one({
        "session_id": session.id,
        "order_id": order_id,
        "user_id": user["user_id"] if user else None,
        "amount": total,
        "currency": "inr",
        "status": "initiated",
        "payment_status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    await db.orders.update_one({"order_id": order_id}, {"$set": {"stripe_session_id": session.id}})
    return {"checkout_url": session.url, "session_id": session.id, "order_id": order_id}


@api.post("/orders/checkout/upi")
async def checkout_upi(order: OrderCreate, user=Depends(get_current_user)):
    """UPI checkout — user pays via UPI QR / VPA and clicks 'I've paid'. Admin verifies."""
    if not order.items:
        raise HTTPException(400, "Cart is empty")
    subtotal = _compute_total(order.items)
    shipping = 0.0 if subtotal >= 999 else 49.0
    total = round(subtotal + shipping, 2)
    order_id = f"PZ{uuid.uuid4().hex[:10].upper()}"
    order_doc = {
        "order_id": order_id,
        "user_id": user["user_id"] if user else None,
        "items": [i.model_dump() for i in order.items],
        "subtotal": subtotal,
        "shipping": shipping,
        "total": total,
        "currency": "inr",
        "status": "awaiting_upi_verification",
        "payment_method": "upi",
        "payment_status": "awaiting_verification",
        "shipping_address": {
            "name": order.shipping_name,
            "email": order.shipping_email,
            "phone": order.shipping_phone,
            "address": order.shipping_address,
            "city": order.shipping_city,
            "state": order.shipping_state,
            "pincode": order.shipping_pincode,
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.orders.insert_one(order_doc)
    # Notify admin via email if configured
    admin_email = os.environ.get("ADMIN_EMAIL")
    if admin_email:
        html = f"""<p>New UPI order <b>{order_id}</b> from {order.shipping_name} ({order.shipping_email}, {order.shipping_phone}) for ₹{int(total)}. Please verify UPI receipt and mark paid in the admin dashboard.</p>"""
        await send_email(admin_email, f"[Poster Zone] UPI order {order_id} awaiting verification", html)
    return {"order_id": order_id, "total": total, "status": "awaiting_verification"}


@api.post("/admin/orders/{order_id}/mark-upi-paid")
async def admin_mark_upi_paid(order_id: str, _admin=Depends(require_admin)):
    order = await db.orders.find_one({"order_id": order_id}, {"_id": 0})
    if not order:
        raise HTTPException(404, "Order not found")
    if order.get("payment_method") != "upi":
        raise HTTPException(400, "This order is not a UPI order")
    await db.orders.update_one(
        {"order_id": order_id},
        {"$set": {"payment_status": "paid", "status": "placed"}},
    )
    # Send order confirmation email to buyer
    to = order.get("shipping_address", {}).get("email")
    if to:
        order["payment_status"] = "paid"
        order["status"] = "placed"
        await send_email(to, f"Poster Zone — order {order_id} confirmed", order_placed_html(order))
    return {"ok": True, "order_id": order_id}


@api.get("/payments/status/{session_id}")
async def payment_status(session_id: str, request: Request):
    record = await db.payment_transactions.find_one({"session_id": session_id}, {"_id": 0})
    if not record:
        raise HTTPException(404, "Transaction not found")
    if record.get("payment_status") != "paid" and STRIPE_API_KEY:
        try:
            status = await asyncio_to_thread(stripe.checkout.Session.retrieve, session_id)
            if status.payment_status == "paid" or status.status == "complete":
                await db.payment_transactions.update_one(
                    {"session_id": session_id, "payment_status": {"$ne": "paid"}},
                    {"$set": {
                        "status": "completed",
                        "payment_status": "paid",
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }},
                )
                r = await db.orders.update_one(
                    {"stripe_session_id": session_id, "payment_status": {"$ne": "paid"}},
                    {"$set": {"payment_status": "paid", "status": "placed"}},
                )
                if r.modified_count > 0:
                    order = await db.orders.find_one({"stripe_session_id": session_id}, {"_id": 0})
                    if order:
                        to = order.get("shipping_address", {}).get("email")
                        if to:
                            await send_email(to, f"Poster Zone — order {order.get('order_id')} confirmed", order_placed_html(order))
                record = await db.payment_transactions.find_one({"session_id": session_id}, {"_id": 0})
        except Exception as e:
            log.warning("stripe status probe failed: %s", e)
    return {
        "session_id": record["session_id"],
        "status": record["status"],
        "payment_status": record["payment_status"],
        "order_id": record.get("order_id"),
    }


@api.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    body = await request.body()
    sig = request.headers.get("Stripe-Signature", "")
    if not STRIPE_WEBHOOK_SECRET:
        log.warning("Stripe webhook received but STRIPE_WEBHOOK_SECRET is not set — ignoring")
        raise HTTPException(400, "Webhook not configured")
    try:
        event = await asyncio_to_thread(
            stripe.Webhook.construct_event, body, sig, STRIPE_WEBHOOK_SECRET,
        )
    except Exception as e:
        log.error("webhook error: %s", e)
        raise HTTPException(400, "Invalid webhook")

    if event["type"] in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        session_obj = event["data"]["object"]
        session_id = session_obj["id"]
        payment_status = session_obj.get("payment_status")
        if payment_status == "paid":
            await db.payment_transactions.update_one(
                {"session_id": session_id, "payment_status": {"$ne": "paid"}},
                {"$set": {"status": "completed", "payment_status": "paid",
                          "updated_at": datetime.now(timezone.utc).isoformat()}},
            )
            r = await db.orders.update_one(
                {"stripe_session_id": session_id, "payment_status": {"$ne": "paid"}},
                {"$set": {"payment_status": "paid", "status": "placed"}},
            )
            if r.modified_count > 0:
                order = await db.orders.find_one({"stripe_session_id": session_id}, {"_id": 0})
                if order:
                    to = order.get("shipping_address", {}).get("email")
                    if to:
                        await send_email(to, f"Poster Zone — order {order.get('order_id')} confirmed", order_placed_html(order))
    return {"status": "ok"}


@api.get("/orders/mine")
async def my_orders(user=Depends(require_user)):
    orders = await db.orders.find({"user_id": user["user_id"]}, {"_id": 0}).sort("created_at", -1).to_list(200)
    return orders


@api.get("/orders/{order_id}")
async def get_order(order_id: str, user=Depends(get_current_user)):
    o = await db.orders.find_one({"order_id": order_id}, {"_id": 0})
    if not o:
        raise HTTPException(404, "Order not found")
    # Allow guest to view via order_id (they got it from success page); admin or owner also OK
    return o


# =====================================================
# Routes — Wishlist
# =====================================================
@api.get("/wishlist")
async def wishlist_list(user=Depends(require_user)):
    docs = await db.wishlists.find({"user_id": user["user_id"]}, {"_id": 0}).to_list(500)
    poster_ids = [d["poster_id"] for d in docs]
    if not poster_ids:
        return []
    posters = await db.posters.find({"id": {"$in": poster_ids}}, {"_id": 0}).to_list(500)
    return posters


@api.post("/wishlist/{poster_id}")
async def wishlist_add(poster_id: str, user=Depends(require_user)):
    poster = await db.posters.find_one({"id": poster_id}, {"_id": 0})
    if not poster:
        raise HTTPException(404, "Poster not found")
    await db.wishlists.update_one(
        {"user_id": user["user_id"], "poster_id": poster_id},
        {"$setOnInsert": {
            "user_id": user["user_id"],
            "poster_id": poster_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }},
        upsert=True,
    )
    return {"ok": True, "poster_id": poster_id}


@api.delete("/wishlist/{poster_id}")
async def wishlist_remove(poster_id: str, user=Depends(require_user)):
    await db.wishlists.delete_one({"user_id": user["user_id"], "poster_id": poster_id})
    return {"ok": True}


# =====================================================
# Register + Middleware
# =====================================================
app.include_router(api)

# CORS — reflect origin for cross-subdomain cookies
cors_origins = os.environ.get("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=cors_origins if cors_origins != ["*"] else ["*"],
    allow_origin_regex=".*" if cors_origins == ["*"] else None,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =====================================================
# Startup — seed data
# =====================================================
@app.on_event("startup")
async def startup():
    if JWT_SECRET == "change_me":
        log.warning("JWT_SECRET is using the default value — set a strong random JWT_SECRET before going live")
    if not STRIPE_API_KEY:
        log.warning("STRIPE_API_KEY not set — card checkout endpoints will return 500 until configured")
    from seed import seed_all
    await seed_all(db)
    # Init object storage (best effort — logs which mode it's using)
    try:
        init_storage()
    except Exception as e:
        log.warning(f"storage init at startup failed: {e}")
    log.info("Startup complete — Poster Zone ready")


@app.on_event("shutdown")
async def shutdown():
    client.close()
