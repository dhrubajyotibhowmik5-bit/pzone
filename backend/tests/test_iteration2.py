"""Iteration 2 backend tests: Wishlist, Admin Upload, Email graceful skip."""
import os
import io
import uuid
import pytest
import requests

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
API = f"{BASE_URL}/api"
ADMIN_EMAIL = "admin@posterzone.com"
ADMIN_PASSWORD = "Admin@12345"
USER_EMAIL = "test@posterzone.com"
USER_PASSWORD = "Test@12345"


def _auth(t): return {"Authorization": f"Bearer {t}"}


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def user_token():
    r = requests.post(f"{API}/auth/login", json={"email": USER_EMAIL, "password": USER_PASSWORD}, timeout=15)
    if r.status_code != 200:
        r = requests.post(f"{API}/auth/signup", json={"email": USER_EMAIL, "password": USER_PASSWORD, "name": "Test Customer"}, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def sample_poster_id():
    r = requests.get(f"{API}/posters", timeout=10)
    assert r.status_code == 200
    items = r.json()
    assert len(items) > 0
    return items[0]["id"]


# ------- Wishlist -------
class TestWishlist:
    def test_wishlist_requires_auth(self, sample_poster_id):
        r = requests.get(f"{API}/wishlist", timeout=10)
        assert r.status_code == 401
        r = requests.post(f"{API}/wishlist/{sample_poster_id}", timeout=10)
        assert r.status_code == 401
        r = requests.delete(f"{API}/wishlist/{sample_poster_id}", timeout=10)
        assert r.status_code == 401

    def test_wishlist_add_get_delete(self, user_token, sample_poster_id):
        # clean slate
        requests.delete(f"{API}/wishlist/{sample_poster_id}", headers=_auth(user_token), timeout=10)

        # ADD
        r = requests.post(f"{API}/wishlist/{sample_poster_id}", headers=_auth(user_token), timeout=10)
        assert r.status_code == 200, r.text
        assert r.json()["poster_id"] == sample_poster_id

        # Idempotent - add again
        r2 = requests.post(f"{API}/wishlist/{sample_poster_id}", headers=_auth(user_token), timeout=10)
        assert r2.status_code == 200

        # GET returns full poster docs
        r = requests.get(f"{API}/wishlist", headers=_auth(user_token), timeout=10)
        assert r.status_code == 200
        items = r.json()
        assert isinstance(items, list)
        ids = [p["id"] for p in items]
        assert sample_poster_id in ids
        # confirm poster has title & image_url (full doc)
        p = next(x for x in items if x["id"] == sample_poster_id)
        assert "title" in p and "image_url" in p and "slug" in p

        # DELETE
        r = requests.delete(f"{API}/wishlist/{sample_poster_id}", headers=_auth(user_token), timeout=10)
        assert r.status_code == 200

        # verify removed
        r = requests.get(f"{API}/wishlist", headers=_auth(user_token), timeout=10)
        assert r.status_code == 200
        ids = [p["id"] for p in r.json()]
        assert sample_poster_id not in ids

    def test_wishlist_nonexistent_poster(self, user_token):
        r = requests.post(f"{API}/wishlist/does-not-exist-xyz", headers=_auth(user_token), timeout=10)
        assert r.status_code == 404


# ------- Admin upload -------
def _tiny_png_bytes() -> bytes:
    # 1x1 red PNG
    import base64
    b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg=="
    return base64.b64decode(b64)


class TestAdminUpload:
    def test_upload_requires_admin(self, user_token):
        files = {"file": ("t.png", _tiny_png_bytes(), "image/png")}
        r = requests.post(f"{API}/admin/upload", files=files, headers=_auth(user_token), timeout=30)
        assert r.status_code == 403

    def test_upload_unauth(self):
        files = {"file": ("t.png", _tiny_png_bytes(), "image/png")}
        r = requests.post(f"{API}/admin/upload", files=files, timeout=30)
        assert r.status_code == 401

    def test_upload_reject_non_image(self, admin_token):
        files = {"file": ("t.txt", b"hello world", "text/plain")}
        r = requests.post(f"{API}/admin/upload", files=files, headers=_auth(admin_token), timeout=30)
        assert r.status_code == 400

    def test_upload_reject_too_large(self, admin_token):
        big = b"\x00" * (8 * 1024 * 1024 + 100)
        files = {"file": ("big.png", big, "image/png")}
        r = requests.post(f"{API}/admin/upload", files=files, headers=_auth(admin_token), timeout=60)
        assert r.status_code == 400

    def test_upload_success_and_public_get(self, admin_token):
        files = {"file": ("hero.png", _tiny_png_bytes(), "image/png")}
        r = requests.post(f"{API}/admin/upload", files=files, headers=_auth(admin_token), timeout=60)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "url" in data and "path" in data
        assert data["url"].startswith("/api/files/")

        # public GET
        full = f"{BASE_URL}{data['url']}"
        g = requests.get(full, timeout=30)
        assert g.status_code == 200
        assert g.headers.get("Content-Type", "").startswith("image/")
        assert len(g.content) > 0

    def test_uploaded_image_usable_in_poster(self, admin_token):
        files = {"file": ("hero.jpg", _tiny_png_bytes(), "image/jpeg")}
        up = requests.post(f"{API}/admin/upload", files=files, headers=_auth(admin_token), timeout=60)
        assert up.status_code == 200
        url = up.json()["url"]
        slug = f"test-upload-{uuid.uuid4().hex[:6]}"
        payload = {
            "slug": slug, "title": "TEST Uploaded", "description": "d", "price": 299,
            "category": "Abstract", "tags": [], "image_url": url,
            "sizes": [{"label": "A4", "price_delta": 0}], "frame_options": [{"label": "None", "price_delta": 0}],
            "stock": 1, "featured": False,
        }
        r = requests.post(f"{API}/admin/posters", json=payload, headers=_auth(admin_token), timeout=15)
        assert r.status_code == 200
        pid = r.json()["id"]
        g = requests.get(f"{API}/posters/{slug}", timeout=10)
        assert g.status_code == 200
        assert g.json()["image_url"] == url
        # cleanup
        requests.delete(f"{API}/admin/posters/{pid}", headers=_auth(admin_token), timeout=10)


# ------- Email graceful skip -------
class TestEmailGracefulSkip:
    def test_checkout_no_500_when_resend_empty(self, user_token):
        payload = {
            "items": [{
                "poster_id": "p1", "title": "Test", "image_url": "https://x.jpg",
                "size": "A4", "frame": "No Frame", "quantity": 1, "unit_price": 499,
            }],
            "shipping_name": "T", "shipping_email": USER_EMAIL, "shipping_phone": "9999999999",
            "shipping_address": "1", "shipping_city": "Delhi", "shipping_state": "DL",
            "shipping_pincode": "110001", "origin_url": BASE_URL,
        }
        r = requests.post(f"{API}/orders/checkout", json=payload, headers=_auth(user_token), timeout=30)
        assert r.status_code == 200, r.text
        return r.json()["order_id"]

    def test_admin_shipped_status_no_500(self, admin_token, user_token):
        # create order
        payload = {
            "items": [{
                "poster_id": "p1", "title": "Test", "image_url": "https://x.jpg",
                "size": "A4", "frame": "No Frame", "quantity": 1, "unit_price": 599,
            }],
            "shipping_name": "T", "shipping_email": USER_EMAIL, "shipping_phone": "9999",
            "shipping_address": "1", "shipping_city": "Delhi", "shipping_state": "DL",
            "shipping_pincode": "110001", "origin_url": BASE_URL,
        }
        r = requests.post(f"{API}/orders/checkout", json=payload, headers=_auth(user_token), timeout=30)
        assert r.status_code == 200
        oid = r.json()["order_id"]
        # mark shipped triggers email send (should be skipped, no 500)
        r = requests.put(
            f"{API}/admin/orders/{oid}/status",
            json={"status": "shipped"},
            headers=_auth(admin_token),
            timeout=15,
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "shipped"
