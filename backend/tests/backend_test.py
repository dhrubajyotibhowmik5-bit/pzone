"""Poster Zone backend regression tests."""
import os
import uuid
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://custom-posters-6.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"
ADMIN_EMAIL = "admin@posterzone.com"
ADMIN_PASSWORD = "Admin@12345"
USER_EMAIL = "test@posterzone.com"
USER_PASSWORD = "Test@12345"


@pytest.fixture(scope="session")
def admin_token():
    r = requests.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="session")
def user_token():
    r = requests.post(f"{API}/auth/login", json={"email": USER_EMAIL, "password": USER_PASSWORD}, timeout=15)
    if r.status_code != 200:
        # sign up
        r = requests.post(f"{API}/auth/signup", json={"email": USER_EMAIL, "password": USER_PASSWORD, "name": "Test Customer"}, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()["token"]


def auth(t): return {"Authorization": f"Bearer {t}"}


# ---------- Health ----------
def test_health():
    r = requests.get(f"{API}/", timeout=10)
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["service"] == "poster-zone"


# ---------- Catalog ----------
def test_list_posters_seeded():
    r = requests.get(f"{API}/posters", timeout=10)
    assert r.status_code == 200
    items = r.json()
    assert isinstance(items, list)
    assert len(items) >= 21, f"Expected >=21 posters, got {len(items)}"


def test_posters_filter_category():
    r = requests.get(f"{API}/posters", params={"category": "Movies"}, timeout=10)
    assert r.status_code == 200
    items = r.json()
    assert len(items) >= 3
    assert all(p["category"] == "Movies" for p in items)


def test_posters_search_q():
    r = requests.get(f"{API}/posters", params={"q": "tokyo"}, timeout=10)
    assert r.status_code == 200
    items = r.json()
    assert len(items) >= 1
    titles = " ".join(p["title"].lower() for p in items)
    assert "tokyo" in titles or any("tokyo" in t.lower() for p in items for t in p.get("tags", []))


def test_categories():
    r = requests.get(f"{API}/posters/categories", timeout=10)
    assert r.status_code == 200
    cats = r.json()
    assert isinstance(cats, list)
    assert len(cats) >= 10
    names = [c["name"] for c in cats]
    for expected in ["Movies", "Anime", "Music", "Motivational", "Sports", "Abstract", "Nature", "Minimal", "Travel", "Gaming"]:
        assert expected in names


def test_get_poster_by_slug():
    r = requests.get(f"{API}/posters/tokyo-neon-night", timeout=10)
    assert r.status_code == 200
    p = r.json()
    assert p["slug"] == "tokyo-neon-night"
    assert p["title"] == "Tokyo Neon"
    assert p["category"] == "Anime"
    assert len(p["sizes"]) == 3
    assert len(p["frame_options"]) == 4


def test_get_poster_not_found():
    r = requests.get(f"{API}/posters/does-not-exist-xyz", timeout=10)
    assert r.status_code == 404


# ---------- Auth ----------
def test_signup_and_me():
    email = f"TEST_{uuid.uuid4().hex[:8]}@posterzone.com"
    r = requests.post(f"{API}/auth/signup", json={"email": email, "password": "Pass@12345", "name": "New Test"}, timeout=10)
    assert r.status_code == 200
    d = r.json()
    assert "token" in d and d["user"]["email"] == email
    r2 = requests.get(f"{API}/auth/me", headers=auth(d["token"]), timeout=10)
    assert r2.status_code == 200
    assert r2.json()["email"] == email
    assert r2.json()["role"] == "user"


def test_login_admin_and_me(admin_token):
    r = requests.get(f"{API}/auth/me", headers=auth(admin_token), timeout=10)
    assert r.status_code == 200
    u = r.json()
    assert u["email"] == ADMIN_EMAIL
    assert u["role"] == "admin"


def test_login_invalid():
    r = requests.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong"}, timeout=10)
    assert r.status_code == 401


# ---------- Admin poster CRUD ----------
def test_admin_poster_crud(admin_token):
    slug = f"test-{uuid.uuid4().hex[:8]}"
    payload = {
        "slug": slug, "title": "TEST Poster", "description": "T", "price": 199,
        "category": "Abstract", "tags": ["test"], "image_url": "https://x.jpg",
        "sizes": [{"label": "A4", "price_delta": 0}], "frame_options": [{"label": "None", "price_delta": 0}],
        "stock": 5, "featured": False,
    }
    r = requests.post(f"{API}/admin/posters", json=payload, headers=auth(admin_token), timeout=10)
    assert r.status_code == 200, r.text
    pid = r.json()["id"]

    # Verify via GET slug
    g = requests.get(f"{API}/posters/{slug}", timeout=10)
    assert g.status_code == 200
    assert g.json()["title"] == "TEST Poster"

    # Update
    payload["title"] = "TEST Updated"
    payload["price"] = 249
    r = requests.put(f"{API}/admin/posters/{pid}", json=payload, headers=auth(admin_token), timeout=10)
    assert r.status_code == 200
    assert r.json()["title"] == "TEST Updated"
    assert r.json()["price"] == 249

    # Delete
    r = requests.delete(f"{API}/admin/posters/{pid}", headers=auth(admin_token), timeout=10)
    assert r.status_code == 200
    # Verify deleted
    g = requests.get(f"{API}/posters/{slug}", timeout=10)
    assert g.status_code == 404


def test_admin_forbidden_for_user(user_token):
    payload = {"slug": "x", "title": "x", "description": "x", "price": 1, "category": "x", "image_url": "x"}
    r = requests.post(f"{API}/admin/posters", json=payload, headers=auth(user_token), timeout=10)
    assert r.status_code == 403


def test_admin_orders_list(admin_token):
    r = requests.get(f"{API}/admin/orders", headers=auth(admin_token), timeout=10)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# ---------- Orders / Checkout ----------
@pytest.fixture(scope="session")
def created_order(user_token):
    payload = {
        "items": [{
            "poster_id": "p1", "title": "Tokyo Neon", "image_url": "https://x.jpg",
            "size": "A4", "frame": "No Frame", "quantity": 2, "unit_price": 399,
        }],
        "shipping_name": "T User", "shipping_email": USER_EMAIL, "shipping_phone": "9999999999",
        "shipping_address": "1 lane", "shipping_city": "Delhi", "shipping_state": "DL",
        "shipping_pincode": "110001", "origin_url": BASE_URL,
    }
    r = requests.post(f"{API}/orders/checkout", json=payload, headers=auth(user_token), timeout=30)
    assert r.status_code == 200, r.text
    return r.json()


def test_checkout_creates_stripe_session(created_order):
    assert "checkout_url" in created_order
    assert created_order["checkout_url"].startswith("https://checkout.stripe.com"), created_order["checkout_url"]
    assert created_order["session_id"]
    assert created_order["order_id"].startswith("PZ")


def test_payment_status_pending(created_order):
    r = requests.get(f"{API}/payments/status/{created_order['session_id']}", timeout=15)
    assert r.status_code == 200
    d = r.json()
    assert d["payment_status"] in ("pending", "initiated")
    assert d["order_id"] == created_order["order_id"]


def test_get_order_by_id(created_order, user_token):
    oid = created_order["order_id"]
    r = requests.get(f"{API}/orders/{oid}", headers=auth(user_token), timeout=10)
    assert r.status_code == 200
    o = r.json()
    assert o["order_id"] == oid
    assert o["status"] == "pending_payment"
    assert o["total"] == 798.0 + 49.0 or o["total"] == 798.0  # 2 * 399; shipping? subtotal < 999 -> +49
    assert o["subtotal"] == 798.0


def test_orders_mine(user_token, created_order):
    r = requests.get(f"{API}/orders/mine", headers=auth(user_token), timeout=10)
    assert r.status_code == 200
    orders = r.json()
    assert any(o["order_id"] == created_order["order_id"] for o in orders)


def test_admin_update_order_status(admin_token, created_order):
    r = requests.put(
        f"{API}/admin/orders/{created_order['order_id']}/status",
        json={"status": "processing"},
        headers=auth(admin_token),
        timeout=10,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "processing"


def test_checkout_empty_cart(user_token):
    payload = {
        "items": [],
        "shipping_name": "T", "shipping_email": USER_EMAIL, "shipping_phone": "9",
        "shipping_address": "a", "shipping_city": "c", "shipping_state": "s",
        "shipping_pincode": "1", "origin_url": BASE_URL,
    }
    r = requests.post(f"{API}/orders/checkout", json=payload, headers=auth(user_token), timeout=10)
    assert r.status_code == 400
